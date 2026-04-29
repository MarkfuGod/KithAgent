import asyncio

from src.kernel.config import CronConfig
from src.kernel.cron import CronScheduler


class _NoLLMKernel:
    def __init__(self):
        self.filesystem = None

    def get_scheduler(self):
        return type("Scheduler", (), {"_active_tasks": {}})()

    def get_llm(self):
        return None

    def get_filesystem(self):
        return self.filesystem


class _RecordingCron(CronScheduler):
    def __init__(self):
        super().__init__(CronConfig(), kernel=_NoLLMKernel())  # type: ignore[arg-type]
        self.dispatched: list[str] = []

    async def _dispatch(self, agent_name: str, input_data: dict | None = None) -> None:
        self.dispatched.append(agent_name)
        self._last_run[agent_name] = 1000 + len(self.dispatched)


def test_staged_cron_runs_triage_before_summarizer():
    async def run_case():
        cron = _RecordingCron()
        await cron._dispatch_stages(
            [
                [{"name": "triage", "input_data": {"timeout": 300}}],
                [{"name": "summarizer", "input_data": {"timeout": 300}}],
            ],
            active_names=set(),
        )
        assert cron.dispatched == ["triage", "summarizer"]
        assert cron._last_run["triage"] < cron._last_run["summarizer"]

    asyncio.run(run_case())


def test_default_cron_config_chains_summarizer_after_triage():
    from src.kernel.config import load_config

    jobs = load_config().cron.jobs
    triggers = {job["agent"]: job["trigger"] for job in jobs}
    assert triggers["triage"] == "after_scan"
    assert triggers["summarizer"] == "after:triage"


def test_adaptive_loop_does_not_gate_on_first_insight(monkeypatch):
    async def run_case():
        cron = _RecordingCron()
        cron._running = True

        async def no_sleep(_seconds):
            return None

        async def fail_if_called():
            raise AssertionError("First Insight readiness must not gate adaptive dispatch")

        async def gather_snapshot():
            return {}

        async def decide(_snapshot):
            return {
                "mode": "light",
                "next_interval_minutes": 30,
                "agents_to_run": [{"name": "triage", "input_data": {}}],
            }

        async def persist(_decision, _snapshot):
            return None

        async def maybe_add(decision, _snapshot, _active_names):
            return decision

        async def dispatch(agent_name: str, input_data: dict | None = None) -> None:
            cron.dispatched.append(agent_name)
            cron._running = False

        monkeypatch.setattr("src.kernel.cron.asyncio.sleep", no_sleep)
        cron._first_insight_ready = fail_if_called  # type: ignore[method-assign]
        cron._gather_activity_snapshot = gather_snapshot  # type: ignore[method-assign]
        cron._llm_decide_schedule = decide  # type: ignore[method-assign]
        cron._persist_decision = persist  # type: ignore[method-assign]
        cron._maybe_add_rag_indexer = maybe_add  # type: ignore[method-assign]
        cron._dispatch = dispatch  # type: ignore[method-assign]

        await cron._adaptive_loop()
        assert cron.dispatched == ["triage"]

    asyncio.run(run_case())


def test_initial_after_scan_dispatches_without_first_insight(monkeypatch):
    async def run_case():
        kernel = _NoLLMKernel()
        kernel.filesystem = type("FS", (), {"_stats": {"last_scan": 123.0}})()
        cron = _RecordingCron()
        cron.kernel = kernel  # type: ignore[assignment]
        cron._running = True

        async def no_sleep(_seconds):
            return None

        async def fail_if_called():
            raise AssertionError("First Insight readiness must not gate after-scan dispatch")

        async def dispatch(agent_name: str, input_data: dict | None = None) -> None:
            cron.dispatched.append(agent_name)
            cron._running = False

        monkeypatch.setattr("src.kernel.cron.asyncio.sleep", no_sleep)
        cron._first_insight_ready = fail_if_called  # type: ignore[method-assign]
        cron._dispatch = dispatch  # type: ignore[method-assign]

        await cron._after_scan_loop("triage")
        assert cron.dispatched == ["triage"]

    asyncio.run(run_case())
