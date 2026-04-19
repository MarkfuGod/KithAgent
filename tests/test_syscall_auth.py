"""Syscall server authentication gate.

We don't spin up the real socket/HTTP servers here — `_check_auth` is a
pure method, so we exercise it directly against constructed requests.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.kernel.config import SyscallConfig
from src.syscall.protocol import SyscallRequest, SyscallType
from src.syscall.server import SyscallServer


def _make_server(tmp_path: Path, *, require_auth: bool,
                 allow_unix_anonymous: bool,
                 allowed_callers: list[str] | None = None) -> SyscallServer:
    cfg = SyscallConfig(
        auth_token_path=tmp_path / "token",
        allowed_callers=allowed_callers if allowed_callers is not None
                        else ["cursor", "cli"],
        require_auth=require_auth,
        allow_unix_anonymous=allow_unix_anonymous,
    )
    kernel = SimpleNamespace(config=SimpleNamespace(kernel=SimpleNamespace(
        socket_path=tmp_path / "agent_sys.sock",
    )))
    srv = SyscallServer(cfg, kernel)
    # Manually load/create a token (normally done in `.start()`).
    srv._auth_token = srv._load_or_create_token()
    return srv


def _req(call_type: str = "file.search", caller: str = "cursor") -> SyscallRequest:
    return SyscallRequest(call_type=call_type, caller=caller, params={})


def test_public_calls_bypass_auth(tmp_path: Path) -> None:
    srv = _make_server(tmp_path, require_auth=True, allow_unix_anonymous=False)
    err = srv._check_auth(
        _req(SyscallType.SYS_PING.value, caller="anyone"),
        transport="http",
    )
    assert err is None


def test_http_requires_token_when_enabled(tmp_path: Path) -> None:
    srv = _make_server(tmp_path, require_auth=True, allow_unix_anonymous=True)

    bad = srv._check_auth(_req(), transport="http", http_token=None)
    assert bad is not None and "X-Agent-Token" in bad

    good = srv._check_auth(_req(), transport="http", http_token=srv._auth_token)
    assert good is None


def test_http_rejects_unknown_caller(tmp_path: Path) -> None:
    srv = _make_server(
        tmp_path, require_auth=True, allow_unix_anonymous=True,
        allowed_callers=["cursor"],
    )
    err = srv._check_auth(
        _req(caller="random_script"),
        transport="http",
        http_token=srv._auth_token,
    )
    assert err is not None and "not allowed" in err


def test_unix_anonymous_allowed_by_default(tmp_path: Path) -> None:
    srv = _make_server(tmp_path, require_auth=True, allow_unix_anonymous=True)
    err = srv._check_auth(_req(), transport="unix")
    assert err is None


def test_unix_requires_token_when_anonymous_disabled(tmp_path: Path) -> None:
    srv = _make_server(tmp_path, require_auth=True, allow_unix_anonymous=False)

    err = srv._check_auth(_req(), transport="unix")
    assert err is not None and "unix socket auth required" in err

    # With the token embedded in params.
    r = _req()
    r.params = {"_auth_token": srv._auth_token}
    assert srv._check_auth(r, transport="unix") is None


def test_auth_disabled_still_checks_caller(tmp_path: Path) -> None:
    """require_auth=False must NOT silently allow arbitrary callers —
    the `allowed_callers` list is still enforced."""
    srv = _make_server(
        tmp_path, require_auth=False, allow_unix_anonymous=True,
        allowed_callers=["cursor"],
    )
    ok = srv._check_auth(_req(caller="cursor"), transport="http")
    assert ok is None

    blocked = srv._check_auth(_req(caller="evil"), transport="http")
    assert blocked is not None and "not allowed" in blocked
