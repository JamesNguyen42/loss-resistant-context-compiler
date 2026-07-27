from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from context_compiler import LocalAIConnector

import ctxc_openhands.host_guard as host_guard
from ctxc_openhands.callback import DurableEventCallback, IntegrationPoisonedError
from ctxc_openhands.compatibility import default_pin_manifest_path
from ctxc_openhands.event_map import SUPPORTED_EVENT_KINDS
from ctxc_openhands.host_guard import (
    PINNED_EVENT_CLASS_NAMES,
    GuardedLocalConversation,
    HostBypassRefusedError,
    LiveCompatibilityError,
    LiveRequestAccountingUnavailableError,
    PinnedHostAPI,
    _mint_pinned_host_api,
    _read_reviewed_source,
    _strict_manifest_value,
    inspect_live_compatibility,
    pinned_callback,
)
from ctxc_openhands.session import OpenHandsSession
from ctxc_openhands.storage import SQLiteGenerationStore


class FakeLocalConversation:
    def __init__(self) -> None:
        self.state = {"status": "idle"}
        self.calls: list[tuple] = []

    def send_message(self, message, sender=None):  # type: ignore[no-untyped-def]
        self.calls.append(("send_message", message, sender))

    def run(self) -> None:
        self.calls.append(("run",))

    async def arun(self) -> None:
        self.calls.append(("arun",))


class FakeLLM:
    pass


class FakeDistribution:
    def __init__(self, root: Path) -> None:
        self.root = root

    def locate_file(self, relative: str) -> Path:
        return self.root / relative


_REVIEWED_PATH = "openhands-sdk/openhands/sdk/llm/llm.py"


def _event_types() -> dict[str, type]:
    return {name: type(name, (), {}) for name in PINNED_EVENT_CLASS_NAMES}


def _api(*, event_types: dict[str, type] | None = None) -> PinnedHostAPI:
    return _mint_pinned_host_api(
        event_types=_event_types() if event_types is None else event_types,
        local_conversation_type=FakeLocalConversation,
        llm_type=FakeLLM,
    )


def _session(tmp_path: Path, *, suffix: str = "default") -> OpenHandsSession:
    return OpenHandsSession(
        session_id=f"host-guard-{suffix}",
        store=SQLiteGenerationStore(tmp_path / f"store-{suffix}.sqlite3"),
        connector_factory=LocalAIConnector,
    )


def _callback(
    tmp_path: Path,
    api: PinnedHostAPI,
    *,
    suffix: str = "default",
) -> DurableEventCallback:
    return pinned_callback(_session(tmp_path, suffix=suffix), api)


def test_closed_host_class_inventory_matches_event_mapper() -> None:
    assert frozenset(PINNED_EVENT_CLASS_NAMES) == SUPPORTED_EVENT_KINDS


def test_pinned_api_detaches_and_freezes_exact_event_inventory() -> None:
    source = _event_types()
    expected_message = source["MessageEvent"]
    api = _api(event_types=source)

    source["MessageEvent"] = type("MessageEvent", (), {})
    assert api.event_types["MessageEvent"] is expected_message
    frozen = api.event_types
    with pytest.raises(TypeError):
        frozen["MessageEvent"] = source["MessageEvent"]  # type: ignore[index]


def test_pinned_api_rejects_invalid_inventory_and_non_types() -> None:
    missing = _event_types()
    missing.pop("MessageEvent")
    with pytest.raises(TypeError, match="exactly the supported"):
        _api(event_types=missing)

    wrong_name = _event_types()
    wrong_name["MessageEvent"] = type("NotMessageEvent", (), {})
    with pytest.raises(TypeError, match="exact-named"):
        _api(event_types=wrong_name)

    with pytest.raises(TypeError, match="local_conversation_type"):
        _mint_pinned_host_api(
            event_types=_event_types(),
            local_conversation_type=FakeLocalConversation(),  # type: ignore[arg-type]
            llm_type=FakeLLM,
        )
    with pytest.raises(TypeError, match="llm_type"):
        _mint_pinned_host_api(
            event_types=_event_types(),
            local_conversation_type=FakeLocalConversation,
            llm_type=FakeLLM(),  # type: ignore[arg-type]
        )


def test_same_named_impostors_cannot_forge_verified_host_token() -> None:
    assert "PinnedHostAPI" not in host_guard.__all__
    impostors = _event_types()
    with pytest.raises(TypeError, match="only be minted"):
        PinnedHostAPI(
            event_types=impostors,
            local_conversation_type=FakeLocalConversation,
            llm_type=FakeLLM,
        )


def test_guard_rejects_plain_and_mismatched_callbacks(tmp_path: Path) -> None:
    first_api = _api()
    second_api = _api()
    plain = DurableEventCallback(_session(tmp_path, suffix="plain"))
    with pytest.raises(TypeError, match="pinned_callback"):
        GuardedLocalConversation(
            FakeLocalConversation(),
            api=first_api,
            callback=plain,
        )

    first_callback = _callback(tmp_path, first_api, suffix="first")
    with pytest.raises(TypeError, match="this verified host API"):
        GuardedLocalConversation(
            FakeLocalConversation(),
            api=second_api,
            callback=first_callback,
        )


def test_exact_event_identity_rejects_subclass_and_same_named_impostor() -> None:
    api = _api()
    expected = api.event_types["MessageEvent"]
    api.validate_event_type(expected())

    subclass = type("MessageEventSubclass", (expected,), {})
    with pytest.raises(TypeError, match="unsupported pinned"):
        api.validate_event_type(subclass())

    impostor = type("MessageEvent", (), {})
    with pytest.raises(TypeError, match="exact pinned SDK class identity"):
        api.validate_event_type(impostor())


def test_live_inspection_does_not_import_openhands() -> None:
    before = {name for name in sys.modules if name == "openhands" or name.startswith("openhands.")}

    report = inspect_live_compatibility()

    after = {name for name in sys.modules if name == "openhands" or name.startswith("openhands.")}
    assert after == before
    assert report.passed is False
    assert report.live_execution_claimed is False
    assert report.reviewed_file_count == 23
    assert any(
        issue.startswith("retained compatibility blocker: hash-pinned-wheelhouse-absent")
        for issue in report.issues
    )


def test_verified_manifest_reread_rejects_byte_substitution(tmp_path: Path) -> None:
    raw = default_pin_manifest_path().read_bytes()
    target = tmp_path / "pin.json"
    target.write_bytes(raw)
    expected_sha256 = hashlib.sha256(raw).hexdigest()
    assert (
        _strict_manifest_value(
            target,
            expected_sha256=expected_sha256,
        )["kind"]
        == "ctxc.openhands.compatibility-pin"
    )

    target.write_bytes(raw + b" ")
    with pytest.raises(LiveCompatibilityError, match="changed during live inspection"):
        _strict_manifest_value(target, expected_sha256=expected_sha256)


def _reviewed_source(tmp_path: Path) -> tuple[FakeDistribution, Path]:
    root = tmp_path / "site-packages"
    source = root / "openhands" / "sdk" / "llm" / "llm.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"reviewed source bytes")
    return FakeDistribution(root), source


def _stat_with_reparse_flag(info: os.stat_result) -> SimpleNamespace:
    return SimpleNamespace(
        st_dev=info.st_dev,
        st_ino=info.st_ino,
        st_mode=info.st_mode,
        st_size=info.st_size,
        st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def test_reviewed_source_reader_accepts_stable_regular_bytes(tmp_path: Path) -> None:
    distribution, source = _reviewed_source(tmp_path)

    assert _read_reviewed_source(distribution, _REVIEWED_PATH) == source.read_bytes()


def test_reviewed_source_reader_rejects_terminal_symbolic_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution, source = _reviewed_source(tmp_path)
    real_lstat = os.lstat

    def symbolic_terminal(path: os.PathLike[str] | str) -> os.stat_result:
        info = real_lstat(path)
        if Path(path) == source:
            values = list(info)
            values[0] = stat.S_IFLNK | 0o777
            return os.stat_result(values)
        return info

    monkeypatch.setattr(host_guard.os, "lstat", symbolic_terminal)
    with pytest.raises(LiveCompatibilityError, match="symbolic or a reparse point"):
        _read_reviewed_source(distribution, _REVIEWED_PATH)


def test_reviewed_source_reader_rejects_intermediate_reparse_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution, source = _reviewed_source(tmp_path)
    real_lstat = os.lstat

    def reparse_parent(path: os.PathLike[str] | str) -> os.stat_result:
        info = real_lstat(path)
        if Path(path) == source.parent:
            return _stat_with_reparse_flag(info)  # type: ignore[return-value]
        return info

    monkeypatch.setattr(host_guard.os, "lstat", reparse_parent)
    with pytest.raises(LiveCompatibilityError, match="symbolic or a reparse point"):
        _read_reviewed_source(distribution, _REVIEWED_PATH)


def test_reviewed_source_reader_rejects_post_check_replacement_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution, source = _reviewed_source(tmp_path)
    replacement = source.with_name("replacement.py")
    replacement.write_bytes(b"replacement source bytes")
    real_open = os.open
    replaced = False

    def replacing_open(
        path: os.PathLike[str] | str,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        nonlocal replaced
        if Path(path) == source and not replaced:
            replaced = True
            os.replace(replacement, source)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(host_guard.os, "open", replacing_open)
    with pytest.raises(LiveCompatibilityError, match="changed before descriptor open"):
        _read_reviewed_source(distribution, _REVIEWED_PATH)
    assert replaced is True


def test_reviewed_source_reader_rejects_post_open_path_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution, _source = _reviewed_source(tmp_path)
    real_snapshot = host_guard._snapshot_reviewed_source
    calls = 0

    def changed_snapshot(
        root: Path,
        path: Path,
        *,
        manifest_path: str,
    ):
        nonlocal calls
        calls += 1
        identities = real_snapshot(root, path, manifest_path=manifest_path)
        if calls != 2:
            return identities
        terminal = identities[-1]
        changed = terminal.__class__(
            device=terminal.device,
            inode=terminal.inode + 1,
            file_type=terminal.file_type,
            size=terminal.size,
        )
        return (*identities[:-1], changed)

    monkeypatch.setattr(host_guard, "_snapshot_reviewed_source", changed_snapshot)
    with pytest.raises(LiveCompatibilityError, match="path changed while hashing"):
        _read_reviewed_source(distribution, _REVIEWED_PATH)
    assert calls == 2


def test_reviewed_source_reader_rejects_distribution_escape(tmp_path: Path) -> None:
    root = tmp_path / "site-packages"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"outside")

    class EscapingDistribution:
        def locate_file(self, relative: str) -> Path:
            if not relative:
                return root
            return outside

    with pytest.raises(LiveCompatibilityError, match="escapes installed distribution"):
        _read_reviewed_source(EscapingDistribution(), _REVIEWED_PATH)


def test_guard_refuses_unaccounted_run_and_bypass_methods(tmp_path: Path) -> None:
    api = _api()
    conversation = FakeLocalConversation()
    guard = GuardedLocalConversation(
        conversation,
        api=api,
        callback=_callback(tmp_path, api),
    )

    guard.send_message("hello", sender="tester")
    with pytest.raises(LiveRequestAccountingUnavailableError, match="refused"):
        guard.run()
    with pytest.raises(LiveRequestAccountingUnavailableError, match="refused"):
        asyncio.run(guard.arun())
    with pytest.raises(HostBypassRefusedError, match="stateless and unrecorded"):
        guard.ask_agent("question")
    with pytest.raises(HostBypassRefusedError, match="bypasses normal events"):
        guard.execute_tool("bash", object())

    assert conversation.calls == [("send_message", "hello", "tester")]


def test_poisoned_callback_blocks_host_before_accounting_refusal(tmp_path: Path) -> None:
    api = _api()
    callback = _callback(tmp_path, api)
    callback(
        {
            "kind": "FutureEvent",
            "id": "future-1",
            "timestamp": "2026-07-27T12:00:00+00:00",
            "source": "agent",
        }
    )
    guard = GuardedLocalConversation(
        FakeLocalConversation(),
        api=api,
        callback=callback,
    )

    with pytest.raises(IntegrationPoisonedError, match="reconcile"):
        guard.run()
