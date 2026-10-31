"""Bounded, identified, robots-aware HTTP. No retries, credentials or proxy bypass."""

import ipaddress
import json
import logging
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

UA = "TurquoiseRadar-JRI/0.1 (+https://github.com/lucasfarmer20-netizen/Turquoise_Radar)"


class StopCollection(Exception):
    def __init__(self, category, status=None):
        super().__init__(category)
        self.category = category
        self.status = status


def valid_url(url, hosts):
    try:
        p = urlsplit(url)
        if (
            p.scheme != "https"
            or p.hostname not in hosts
            or p.username
            or p.password
            or p.port not in (None, 443)
            or p.fragment
        ):
            raise ValueError()
    except ValueError:
        raise StopCollection("URL_NOT_APPROVED") from None
    if p.hostname == "localhost" or p.hostname.endswith(".localhost"):
        raise StopCollection("URL_NOT_APPROVED")
    try:
        address = ipaddress.ip_address(p.hostname)
    except ValueError:
        address = None
    if address is not None:
        raise StopCollection("URL_NOT_APPROVED")
    return p


class PoliteHTTP:
    def __init__(
        self,
        c,
        source,
        *,
        transport=None,
        clock=time.time,
        sleep=time.sleep,
        max_requests=2000,
        max_bytes=1024 * 1024 * 1024,
    ):
        logging.getLogger("httpx").setLevel(logging.CRITICAL)
        logging.getLogger("httpcore").setLevel(logging.CRITICAL)
        self.c, self.source, self.clock, self.sleep = c, source, clock, sleep
        self.client = httpx.Client(
            timeout=25,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": UA, "Accept": "*/*"},
        )
        self.robots = {}
        self.requests = self.bytes = 0
        self.max_requests, self.max_bytes = max_requests, max_bytes

    def close(self):
        self.client.close()

    def delay(self, host, interval):
        row = self.c.execute("SELECT next_at FROM rate_state WHERE host=?", (host,)).fetchone()
        delay = max(0, (row["next_at"] if row else 0) - self.clock())
        if delay > 30:
            raise StopCollection("RATE_COOLDOWN")
        if delay:
            self.sleep(delay)
        self.c.execute(
            "INSERT INTO rate_state VALUES (?,?) ON CONFLICT(host) "
            "DO UPDATE SET next_at=excluded.next_at",
            (host, self.clock() + interval),
        )
        self.c.commit()

    def raw(self, url, cap, interval):
        host = valid_url(url, self.source.hosts).hostname
        if self.requests >= self.max_requests or self.bytes >= self.max_bytes:
            raise StopCollection("BATCH_NETWORK_LIMIT")
        self.delay(host, interval)
        self.requests += 1
        try:
            with self.client.stream("GET", url) as response:
                status = response.status_code
                if status in (429, 503):
                    retry = response.headers.get("retry-after", "")
                    try:
                        seconds = float(retry)
                    except ValueError:
                        try:
                            seconds = (
                                parsedate_to_datetime(retry) - datetime.now(UTC)
                            ).total_seconds()
                        except (ValueError, TypeError, OverflowError):
                            seconds = 60
                    if not 0 <= seconds <= 86400 * 365:
                        seconds = 60
                    self.c.execute(
                        "UPDATE rate_state SET next_at=? WHERE host=?",
                        (self.clock() + max(60, seconds), host),
                    )
                    self.c.commit()
                if status >= 400:
                    return status, b"", None
                if status in (301, 302, 303, 307, 308):
                    return status, b"", response.headers.get("location")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > cap:
                    raise StopCollection("RESPONSE_TOO_LARGE", status)
                body = bytearray()
                for block in response.iter_bytes(chunk_size=65536):
                    self.bytes += len(block)
                    if len(body) + len(block) > cap or self.bytes > self.max_bytes:
                        raise StopCollection("RESPONSE_TOO_LARGE", status)
                    body.extend(block)
                return status, bytes(body), None
        except httpx.ProxyError:
            raise StopCollection("NETWORK_PROXY_BLOCK") from None
        except httpx.TimeoutException:
            raise StopCollection("NETWORK_TIMEOUT") from None
        except httpx.HTTPError:
            raise StopCollection("NETWORK_TRANSPORT") from None

    def rules(self, url):
        p = valid_url(url, self.source.hosts)
        if p.hostname not in self.robots:
            robots_url = f"https://{p.hostname}/robots.txt"
            status, body, _ = self.raw(robots_url, 512000, self.source.interval_seconds)
            parser = RobotFileParser()
            if status == 404:
                parser.parse([])
            elif status == 200:
                text = body.decode("utf-8", errors="replace")
                if "<html" in text.lower():
                    raise StopCollection("ROBOTS_UNAVAILABLE", status)
                parser.parse(text.splitlines())
            else:
                # Fail closed for auth/proxy errors, server errors and robots redirects.
                raise StopCollection("ROBOTS_UNAVAILABLE", status)
            interval = max(self.source.interval_seconds, parser.crawl_delay(UA) or 0)
            rate = parser.request_rate(UA)
            if rate:
                interval = max(interval, rate.seconds / rate.requests)
            # The robots request preceded knowledge of its policy. Apply the newly
            # learned spacing before the first content request, including after restart.
            self.c.execute(
                "UPDATE rate_state SET next_at=max(next_at,?) WHERE host=?",
                (self.clock() + interval, p.hostname),
            )
            self.c.commit()
            self.robots[p.hostname] = parser
        return self.robots[p.hostname]

    def get(self, url, cap=8 * 1024 * 1024):
        for _ in range(4):
            rules = self.rules(url)
            if not rules.can_fetch(UA, url):
                raise StopCollection("ROBOTS_DENIED")
            interval = max(self.source.interval_seconds, rules.crawl_delay(UA) or 0)
            rate = rules.request_rate(UA)
            if rate:
                interval = max(interval, rate.seconds / rate.requests)
            status, body, redirect = self.raw(url, cap, interval)
            if redirect:
                url = urljoin(url, redirect)
                valid_url(url, self.source.hosts)
                continue
            if status != 200:
                raise StopCollection("HTTP_ERROR", status)
            return body
        raise StopCollection("REDIRECT_LIMIT")

    def json(self, url):
        try:
            result = json.loads(self.get(url))
            if not isinstance(result, dict):
                raise ValueError()
            if result.get("error"):
                raise StopCollection("REMOTE_API_ERROR")
            return result
        except (ValueError, UnicodeError):
            raise StopCollection("INVALID_METADATA") from None
