"""
Tests for the per-worktree instance registry (``mascope_cli.instance``).

Hermetic: the registry is a JSON file under a temp home, so no Docker,
Postgres, or real ``MASCOPE_PATH`` is needed. ``worktree`` and ``home`` are
passed explicitly to keep allocation deterministic, and the host port probe
reports every port free unless a test says otherwise.
"""

import json
import socket

import pytest
from loguru import logger

from mascope_cli import instance as inst


@pytest.fixture(autouse=True)
def _ports_free(monkeypatch):
    """Keep allocation independent of what listens on the test host."""
    monkeypatch.setattr(inst, "_port_in_use", lambda port: False)


def _alloc(home, path):
    return inst.resolve_or_allocate(worktree=path, home=home)


def _taken(*ports):
    """A probe that reports ``ports`` in use and every other port free."""
    return lambda port: port in ports


def test_allocates_slot_zero_and_derives_ports(tmp_path):
    got = _alloc(tmp_path, "/work/alice")

    assert got.slot == 0
    assert got.env == "wt-alice"
    assert got.api_port == inst.API_PORT_BASE
    assert got.frontend_port == inst.FRONTEND_PORT_BASE


def test_second_worktree_gets_next_slot(tmp_path):
    first = _alloc(tmp_path, "/work/alice")
    second = _alloc(tmp_path, "/work/bob")

    assert (first.slot, second.slot) == (0, 1)
    assert second.env == "wt-bob"
    assert second.api_port == inst.API_PORT_BASE + 1
    assert second.frontend_port == inst.FRONTEND_PORT_BASE + 1


def test_same_worktree_is_idempotent(tmp_path):
    first = _alloc(tmp_path, "/work/alice")
    again = _alloc(tmp_path, "/work/alice")

    assert first == again
    assert len(inst.list_instances(home=tmp_path)) == 1


def test_env_name_disambiguated_on_basename_clash(tmp_path):
    a = _alloc(tmp_path, "/work/one/proj")
    b = _alloc(tmp_path, "/work/two/proj")

    assert a.env == "wt-proj"
    assert b.env != a.env
    assert b.env.startswith("wt-proj-")


def test_release_frees_slot_for_reuse(tmp_path):
    _alloc(tmp_path, "/work/alice")  # slot 0
    bob = _alloc(tmp_path, "/work/bob")  # slot 1

    released = inst.release(bob.env, home=tmp_path)
    assert released.slot == 1

    reused = _alloc(tmp_path, "/work/carol")
    assert reused.slot == 1  # lowest free slot is reclaimed


def test_release_unknown_env_returns_none(tmp_path):
    assert inst.release("wt-nope", home=tmp_path) is None


def test_registry_persists_to_disk(tmp_path):
    _alloc(tmp_path, "/work/alice")

    registry = json.loads((tmp_path / ".runtime" / "instances.json").read_text())
    [record] = registry["instances"].values()
    assert record == {
        "slot": 0,
        "env": "wt-alice",
        "api_port": inst.API_PORT_BASE,
        "frontend_port": inst.FRONTEND_PORT_BASE,
    }


def test_provision_creates_runnable_env_dirs(tmp_path):
    got = _alloc(tmp_path, "/work/alice")
    inst.provision(got, home=tmp_path)

    env_dir = tmp_path / ".runtime" / "env" / "wt-alice"
    # The backend startup and file-converter need these to exist to run.
    for sub in ("filestore", "filestreams", "logs", "temp", "agents/file"):
        assert (env_dir / sub).is_dir(), sub


def test_max_slots_exhausted_raises(tmp_path):
    for i in range(inst.MAX_SLOTS):
        _alloc(tmp_path, f"/work/proj{i}")

    with pytest.raises(inst.InstanceError):
        _alloc(tmp_path, "/work/one-too-many")


def test_worktree_key_falls_back_to_cwd_outside_git(tmp_path, monkeypatch):
    # Simulate git being unavailable so the fallback path is exercised.
    def _no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(inst.subprocess, "run", _no_git)

    assert inst.worktree_key(cwd=str(tmp_path)) == str(tmp_path.resolve())


def test_taken_api_port_skips_slot(tmp_path):
    got = inst.resolve_or_allocate(
        worktree="/work/alice", home=tmp_path, port_in_use=_taken(inst.API_PORT_BASE)
    )

    assert got.slot == 1
    assert got.api_port == inst.API_PORT_BASE + 1
    assert got.frontend_port == inst.FRONTEND_PORT_BASE + 1


def test_taken_frontend_port_skips_slot(tmp_path):
    got = inst.resolve_or_allocate(
        worktree="/work/alice",
        home=tmp_path,
        port_in_use=_taken(inst.FRONTEND_PORT_BASE),
    )

    assert got.slot == 1


def test_default_probe_is_used_when_none_is_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "_port_in_use", _taken(inst.API_PORT_BASE))

    assert _alloc(tmp_path, "/work/alice").slot == 1


def test_skipped_slot_is_not_recorded_and_is_reused_once_free(tmp_path):
    inst.resolve_or_allocate(
        worktree="/work/alice", home=tmp_path, port_in_use=_taken(inst.API_PORT_BASE)
    )

    registry = json.loads((tmp_path / ".runtime" / "instances.json").read_text())
    assert [rec["slot"] for rec in registry["instances"].values()] == [1]
    # The other process has gone: slot 0 is offered again.
    assert _alloc(tmp_path, "/work/bob").slot == 0


def test_skips_past_registry_and_host_taken_slots(tmp_path):
    _alloc(tmp_path, "/work/alice")  # slot 0, in the registry
    got = inst.resolve_or_allocate(
        worktree="/work/bob",
        home=tmp_path,
        port_in_use=_taken(inst.API_PORT_BASE + 1, inst.FRONTEND_PORT_BASE + 2),
    )

    assert got.slot == 3


def test_skip_lands_past_a_registry_slot_behind_it(tmp_path):
    api_0_taken = _taken(inst.API_PORT_BASE)
    inst.resolve_or_allocate(
        worktree="/work/alice", home=tmp_path, port_in_use=api_0_taken
    )
    # Slot 0 is still held on the host and slot 1 by alice in the registry.
    got = inst.resolve_or_allocate(
        worktree="/work/bob", home=tmp_path, port_in_use=api_0_taken
    )

    assert got.slot == 2


def test_skipped_slot_is_logged_at_info(tmp_path):
    messages = []
    sink = logger.add(messages.append, level="INFO", format="{level} {message}")
    try:
        inst.resolve_or_allocate(
            worktree="/work/alice",
            home=tmp_path,
            port_in_use=_taken(inst.API_PORT_BASE),
        )
    finally:
        logger.remove(sink)

    [message] = messages
    assert message.startswith("INFO ")
    assert "slot 0" in message
    assert str(inst.API_PORT_BASE) in message


def test_existing_binding_on_taken_slot_resolves_without_probe(tmp_path):
    first = _alloc(tmp_path, "/work/alice")  # slot 0

    def _probe(port):
        raise AssertionError(f"an existing binding must not probe port {port}")

    again = inst.resolve_or_allocate(
        worktree="/work/alice", home=tmp_path, port_in_use=_probe
    )

    assert again == first


def test_all_slots_taken_on_host_raises_naming_the_skip(tmp_path):
    for i in range(inst.MAX_SLOTS // 2):
        _alloc(tmp_path, f"/work/proj{i}")

    with pytest.raises(inst.InstanceError, match="ports are in use"):
        inst.resolve_or_allocate(
            worktree="/work/one-too-many", home=tmp_path, port_in_use=lambda p: True
        )
    # Nothing was recorded for the failed allocation.
    assert len(inst.list_instances(home=tmp_path)) == inst.MAX_SLOTS // 2


def test_port_probe_sees_a_loopback_only_listener(monkeypatch):
    monkeypatch.undo()  # the real probe, not the autouse stand-in
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        assert inst._port_in_use(port)
    assert not inst._port_in_use(port)
