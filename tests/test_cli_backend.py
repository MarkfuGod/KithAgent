from __future__ import annotations

import argparse
import io

from src.cli import (
    _choose_many,
    _format_statusline,
    _first_insight_payload_from_args,
    _normalize_first_insight_answers,
    _print_home,
    _print_backend_status,
    _split_cli_values,
)
from src.cli_ui import CLIUI


def test_split_cli_values_handles_common_separators() -> None:
    assert _split_cli_values(["developer, creator", "AI tools, local automation / backend"]) == [
        "developer",
        "creator",
        "AI tools",
        "local automation",
        "backend",
    ]


def test_first_insight_payload_from_args_builds_backend_payload() -> None:
    args = argparse.Namespace(
        roles=["developer, creator"],
        goals=["ship Kith CLI"],
        interests=["AI tools; local automation"],
        current_focus=["backend experience"],
        planning_style="detailed",
        suggestion_cadence="weekly",
        include_browser_history=True,
        history_days=14,
        history_limit=25,
        yes=True,
        json=False,
    )
    payload = _first_insight_payload_from_args(args, CLIUI(force_plain=True, stdout=io.StringIO()))

    assert payload == {
        "answers": {
            "roles": ["developer", "creator"],
            "goals": ["ship Kith CLI"],
            "interests": ["AI tools", "local automation"],
            "current_focus": ["backend experience"],
            "planning_style": "detailed",
            "suggestion_cadence": "weekly",
        },
        "include_browser_history": True,
        "history_days": 14,
        "history_limit": 25,
    }


def test_normalize_first_insight_answers_falls_back_to_daily_cadence() -> None:
    answers = _normalize_first_insight_answers({
        "roles": "developer",
        "goals": "",
        "interests": None,
        "current_focus": [],
        "planning_style": "",
        "suggestion_cadence": "hourly",
    })

    assert answers["roles"] == ["developer"]
    assert answers["planning_style"] == "lightweight"
    assert answers["suggestion_cadence"] == "daily"


def test_print_backend_status_plain_output() -> None:
    out = io.StringIO()
    ui = CLIUI(force_plain=True, stdout=out)
    _print_backend_status(
        {
            "name": "AgentOS",
            "version": "0.7.0",
            "pid": 123,
            "running": True,
            "subsystems": ["memory", "scheduler", "syscall"],
            "filesystem": {
                "files_indexed": 10,
                "scan_in_progress": False,
                "scan_progress": 10,
                "realtime_watcher": True,
            },
            "memory": {
                "indexed_files": 10,
                "summarized_files": 3,
                "knowledge_entries": 2,
                "document_chunks": 5,
                "cache_items": 1,
            },
            "scheduler": {"active_tasks": 1, "queue_size": 0},
            "cron": {"strategy": "balanced"},
            "llm": {"available_providers": ["openai_compatible"]},
            "rag": {"chunks": 5, "pending": 2},
            "first_insight": {"ready": True},
        },
        ui,
    )

    rendered = out.getvalue()
    assert "Kith Backend" in rendered
    assert "Filesystem" in rendered
    assert "openai_compatible" in rendered
    assert "ready" in rendered


def test_choose_many_supports_numbered_options_and_custom(monkeypatch) -> None:
    answers = iter(["1, o", "jazz, gardening"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    selected = _choose_many(
        CLIUI(force_plain=True, stdout=io.StringIO()),
        "Interests",
        ["reading", "music"],
    )

    assert selected == ["reading", "jazz", "gardening"]


def test_format_statusline_online_and_offline() -> None:
    assert _format_statusline(None) == "Kith offline"
    assert _format_statusline({
        "running": True,
        "pid": 123,
        "memory": {"indexed_files": 42},
        "llm": {"available_providers": ["openai_compatible"]},
        "first_insight": {"ready": True},
        "filesystem": {"scan_in_progress": False},
    }) == "Kith online pid:123 files:42 openai_compatible insight:ready scan:idle"


def test_print_home_plain_output() -> None:
    out = io.StringIO()
    ui = CLIUI(force_plain=True, stdout=out)
    config = argparse.Namespace(
        kernel=argparse.Namespace(socket_path="/tmp/agent_sys.sock"),
    )
    _print_home(
        ui,
        config,
        {
            "running": True,
            "filesystem": {"files_indexed": 3},
            "memory": {"indexed_files": 3},
            "llm": {"available_providers": ["openai_compatible"]},
            "first_insight": {"ready": False},
        },
    )

    rendered = out.getvalue()
    assert "Kith Agent" in rendered
    assert "Command Center" in rendered
    assert "Start" in rendered
    assert "agent-sys dashboard" in rendered
    assert "agent-sys doctor" in rendered
