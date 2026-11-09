"""Closed synthetic cohort only; no network or owner files."""

import json
import secrets

import pytest

from radar.discovery.local_deletion_slice import (
    SyntheticPilot,
    SyntheticRunner,
    canonical,
    seal,
    unseal,
)


@pytest.fixture
def env(tmp_path):
    key = secrets.token_bytes(32)
    runner = SyntheticRunner(tmp_path / "runner", key)
    pilot = SyntheticPilot(tmp_path / "pilot", key, runner.exchange)
    return key, runner, pilot


def denied(pilot):
    with pytest.raises(ValueError):
        pilot.read()
    assert pilot.blocked
    assert (pilot.root / ".blocked").exists()
    with pytest.raises(ValueError):
        pilot.read()


def test_clear_then_delete_all_retained_copies(env):
    _, runner, pilot = env
    assert pilot.read().startswith(b"\x89PNG")
    runner.notice()
    denied(pilot)
    pilot.cleanup()
    assert {p.name for p in pilot.root.iterdir()} == {".blocked"}
    assert len(pilot.expected) == 5  # DB originals/history, backup, export, two photos.


@pytest.mark.parametrize("direction", ["request", "response"])
def test_transport_tampering(env, direction):
    _, runner, pilot = env
    runner.notice()

    def exchange(frame):
        raw = frame if direction == "request" else runner.exchange(frame)
        envelope = json.loads(raw)
        envelope["body"]["result" if direction == "response" else "plan"] = "CLEAR"
        corrupt = canonical(envelope)
        return runner.exchange(corrupt) if direction == "request" else corrupt

    pilot.exchange = exchange
    denied(pilot)


def test_wrong_channel_key(env):
    _, runner, pilot = env
    other = SyntheticRunner(pilot.root.parent / "wrong-runner", secrets.token_bytes(32))
    pilot.exchange = other.exchange
    denied(pilot)


@pytest.mark.parametrize("field", ["session", "nonce", "inventory", "plan", "identities"])
def test_even_authentic_reply_to_different_query_is_denied(env, field):
    key, runner, pilot = env

    def exchange(frame):
        query = unseal(key, b"request", frame)
        query[field] = (
            [{"userId": "SYNTHETIC-other", "username": "SYNTHETIC-other"}]
            if field == "identities"
            else "a" * 64
        )
        return runner.exchange(seal(key, b"request", query))

    pilot.exchange = exchange
    denied(pilot)


def test_saved_response_cannot_be_reused(env):
    _, runner, pilot = env
    saved = []

    def exchange(frame):
        if not saved:
            saved.append(runner.exchange(frame))
        return saved[0]

    pilot.exchange = exchange
    assert pilot.read()
    runner.notice()
    denied(pilot)


def test_delayed_response_denied(env):
    _, _, pilot = env
    times = iter([0, 6])
    pilot.clock = lambda: next(times)
    denied(pilot)


def test_outage_latches_denial(env):
    _, _, pilot = env
    pilot.exchange = lambda _: (_ for _ in ()).throw(OSError("offline"))
    denied(pilot)


def test_stale_receiver_proof_denied(env):
    _, runner, pilot = env
    runner.pull = lambda _: b'{"payload":{},"signature":"old"}'
    denied(pilot)


@pytest.mark.parametrize(
    "copy", ["pilot.db", "backup.db", "export.json", "photo.png", "photo-copy.png"]
)
def test_any_retained_copy_mutation_denies(env, copy):
    _, _, pilot = env
    (pilot.root / copy).write_bytes(b"SYNTHETIC changed")
    denied(pilot)


@pytest.mark.parametrize("kind", ["export", "sidecar", "symlink", "hardlink"])
def test_unknown_copies_prevent_clearance_and_cleanup_claim(env, kind):
    _, _, pilot = env
    target = pilot.root / ("pilot.db-wal" if kind == "sidecar" else "extra")
    if kind == "symlink":
        target.symlink_to(pilot.root / "photo.png")
    elif kind == "hardlink":
        target.hardlink_to(pilot.root / "photo.png")
    else:
        target.write_bytes(b"SYNTHETIC copy")
    denied(pilot)
    with pytest.raises(ValueError):
        pilot.cleanup()
    assert target.exists()


def test_write_during_match_is_denied(env):
    _, runner, pilot = env

    def exchange(frame):
        answer = runner.exchange(frame)
        (pilot.root / "export.json").write_text("SYNTHETIC changed")
        return answer

    pilot.exchange = exchange
    denied(pilot)


def test_local_identity_substitution_denied(env):
    _, _, pilot = env
    pilot.identities[0]["userId"] = "SYNTHETIC-other"
    denied(pilot)


def test_username_only_denied_even_empty_ledger(env):
    _, _, pilot = env
    del pilot.identities[0]["userId"]
    denied(pilot)


def test_existing_directory_never_opened(env):
    key, runner, pilot = env
    with pytest.raises(FileExistsError):
        SyntheticPilot(pilot.root, key, runner.exchange)
    assert pilot.read()


def test_username_event_ambiguity_suppresses(env):
    _, runner, pilot = env
    runner.notice("SYNTHETIC-seller", field="username")
    denied(pilot)


def test_repeated_request_rejected_by_runner(env):
    _, runner, pilot = env
    frames = []

    def exchange(frame):
        frames.append(frame)
        return runner.exchange(frame)

    pilot.exchange = exchange
    assert pilot.read()
    with pytest.raises(ValueError, match="Consumed"):
        runner.exchange(frames[0])


@pytest.mark.parametrize("defect", ["username", "oversized", "extra", "real"])
def test_fixed_runner_interface_rejects_invalid_requests(env, defect):
    key, runner, pilot = env

    def exchange(frame):
        query = unseal(key, b"request", frame)
        if defect == "username":
            del query["identities"][0]["userId"]
        elif defect == "oversized":
            query["identities"] *= 13
        elif defect == "extra":
            query["command"] = "arbitrary command"
        else:
            query["identities"][0]["userId"] = "real-account"
        return runner.exchange(seal(key, b"request", query))

    pilot.exchange = exchange
    denied(pilot)


def test_prior_valid_receiver_bundle_cannot_hide_new_event(env):
    _, runner, pilot = env
    fresh_pull = runner.pull
    saved = []

    def pull(nonce):
        if not saved:
            saved.append(fresh_pull(nonce))
        return saved[0]

    runner.pull = pull
    assert pilot.read()
    runner.notice()
    denied(pilot)
