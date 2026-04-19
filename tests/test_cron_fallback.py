"""CronScheduler._default_decision — rule-based fallback behaviour.

The fallback runs when either the LLM-driven adaptive decision fails or
no LLM is configured. We want to be sure that:

- Without an LLM, LLM-dependent agents (summarizer, behavior_analyzer,
  report_generator, profile_builder) are NOT scheduled.
- Rule-based agents (triage, priority_classifier) still run.
- The backoff interval becomes the configured max to avoid livelocking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.kernel.config import AdaptiveConfig, CronConfig
from src.kernel.cron import CronScheduler


class _KernelStub:
    """Minimal kernel providing only what `_default_decision` reads."""

    def __init__(self, *, has_llm: bool):
        self._has_llm = has_llm

    def get_llm(self):
        if not self._has_llm:
            return None
        return SimpleNamespace(available_providers=lambda: ["fake"])


def _make_scheduler(has_llm: bool, strategy: str = "balanced") -> CronScheduler:
    cfg = CronConfig(
        enabled=True,
        jobs=[],
        adaptive=AdaptiveConfig(strategy=strategy),
    )
    return CronScheduler(cfg, _KernelStub(has_llm=has_llm))


def test_default_decision_skips_llm_agents_when_no_llm() -> None:
    sched = _make_scheduler(has_llm=False)
    # Use deep-hour so the balanced strategy schedules more agents; LLM-gated
    # ones must still be filtered out.
    decision = sched._default_decision({
        "current_hour": sched.config.adaptive.deep_analysis_hour,
        "files_modified_last_30min": 0,
    })

    names = {a["name"] for a in decision["agents_to_run"]}
    # Rule-based agents are still allowed.
    assert "triage" in names
    assert "priority_classifier" in names, \
        "priority_classifier is rule-based; it must run even without an LLM"
    # LLM-dependent agents must be filtered out.
    assert "summarizer" not in names
    assert "behavior_analyzer" not in names
    assert "report_generator" not in names
    assert "profile_builder" not in names

    assert decision["mode"] == "rules_only"
    assert decision["next_interval_minutes"] == sched.config.adaptive.max_interval_minutes


def test_default_decision_allows_llm_agents_when_llm_present() -> None:
    sched = _make_scheduler(has_llm=True)
    decision = sched._default_decision({
        "current_hour": sched.config.adaptive.deep_analysis_hour,  # deep hour
        "files_modified_last_30min": 0,
    })

    names = {a["name"] for a in decision["agents_to_run"]}
    # Deep hour + balanced strategy should schedule summarizer + behavior.
    assert "triage" in names
    assert "summarizer" in names
    assert "behavior_analyzer" in names
    assert decision["mode"] in {"deep", "light", "rules_only"}
    assert decision["mode"] != "rules_only", \
        "with an LLM available the fallback must not claim rules_only mode"
