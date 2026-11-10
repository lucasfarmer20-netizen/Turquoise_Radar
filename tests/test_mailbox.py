"""Mailbox poller tests with a fake IMAP connection; no network."""

from radar import mailbox
from radar.db import AlertMessage, Setting, database
from radar.intake.registry import seed_sources


def _msg(from_addr, subject, body, uid):
    raw = (
        f"From: Alerts <{from_addr}>\r\nTo: turquoise.radar8@gmail.com\r\n"
        f"Subject: {subject}\r\nDate: Mon, 09 Nov 2026 20:00:00 -0900\r\n"
        f"Message-ID: <m{uid}@test>\r\nContent-Type: text/plain\r\n\r\n{body}\r\n"
    ).encode()
    return uid, raw


class FakeConn:
    def __init__(self, messages):
        self.messages = dict(messages)
        self.logged_out = False

    def logout(self):
        self.logged_out = True


def _patch_imap(monkeypatch, messages):
    conn = FakeConn(messages)
    monkeypatch.setattr(mailbox, "connect", lambda cfg: conn)
    monkeypatch.setattr(
        mailbox,
        "fetch_new",
        lambda c, since, limit: [(u, r) for u, r in sorted(c.messages.items()) if u > since][
            :limit
        ],
    )
    return conn


CFG = dict(host="imap.test", user="u", password="p", folder="INBOX", max_per_poll=300)


def test_route_matches_parent_domains(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'm.db'}")
    with sessions.begin() as s:
        seed_sources(s)
        domains = mailbox.domain_map(s)
    assert mailbox.route("alerts.hibid.com", domains) == "hibid"
    assert mailbox.route("shopgoodwill.com", domains) == "shopgoodwill"
    assert mailbox.route("mail.auctionzip.com", domains) == "auctionzip"
    assert mailbox.route("proxibid.com", domains) == "proxibid"
    assert mailbox.route("newsletter.example.com", domains) is None
    assert mailbox.route(None, domains) is None
    engine.dispose()


def test_poll_imports_routes_and_tracks_uid(tmp_path, monkeypatch):
    engine, sessions = database(f"sqlite:///{tmp_path / 'm.db'}")
    msgs = [
        _msg(
            "noreply@shopgoodwill.com",
            "Personal Shopper: navaho",
            "Navaho cuff bracelet\nhttps://shopgoodwill.com/item/123456",
            11,
        ),
        _msg("promo@newsletter.example.com", "Deals!", "spam", 12),
        _msg(
            "alerts@hibid.com", "New lots: old pawn", "Old pawn ring https://hibid.com/lot/555", 13
        ),
    ]
    conn = _patch_imap(monkeypatch, msgs)
    stats = mailbox.poll(sessions, CFG)
    assert conn.logged_out
    assert stats["fetched"] == 3 and stats["unknown_sender"] == 1
    assert stats["imported"] == 2 and stats["failed"] == 0
    assert set(stats["by_source"]) == {"shopgoodwill", "hibid"}
    with sessions() as s:
        assert s.query(AlertMessage).count() == 2
        assert mailbox.last_uid(s) == 13
        state = s.get(Setting, mailbox.UID_SETTING_ID).data
        assert state["last_stats"]["unknown_domains"] == {"newsletter.example.com": 1}
    # second poll: nothing newer than uid 13
    stats = mailbox.poll(sessions, CFG)
    assert stats["fetched"] == 0
    with sessions() as s:
        assert s.query(AlertMessage).count() == 2
    engine.dispose()


def test_unconfigured_poll_is_noop(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'm.db'}")
    assert mailbox.poll(sessions, {**CFG, "password": ""}) is None
    engine.dispose()


def test_one_bad_message_does_not_block_the_rest(tmp_path, monkeypatch):
    engine, sessions = database(f"sqlite:///{tmp_path / 'm.db'}")
    msgs = [
        _msg("alerts@hibid.com", "ok", "fine https://hibid.com/lot/1", 1),
        _msg("alerts@invaluable.com", "boom", "x", 2),
    ]
    _patch_imap(monkeypatch, msgs)
    real = mailbox.import_alerts

    def flaky(session, source_id, chunk):
        if source_id == "invaluable":
            raise RuntimeError("parser exploded")
        return real(session, source_id, chunk)

    monkeypatch.setattr(mailbox, "import_alerts", flaky)
    stats = mailbox.poll(sessions, CFG)
    assert stats["imported"] == 1 and stats["failed"] == 1
    with sessions() as s:
        assert mailbox.last_uid(s) == 2
    engine.dispose()
