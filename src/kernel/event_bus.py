"""
EventBus — lightweight pub/sub for agent lifecycle events.

Provides real-time visibility into what the system is doing:
  - Task lifecycle (started, completed, failed)
  - LLM requests and responses (model, tokens, latency)
  - Agent progress (triage batch N/M, summarize file X)
  - Cron scheduling decisions

Subscribers receive events via asyncio.Queue (used by SSE endpoint
in the dashboard for real-time streaming).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger("agent_sys.kernel.event_bus")

RING_BUFFER_SIZE = 500


@dataclass
class AgentEvent:
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        payload = json.dumps({"type": self.event_type, "data": self.data, "ts": self.timestamp}, default=str)
        return f"event: {self.event_type}\ndata: {payload}\n\n"

    def to_dict(self) -> dict:
        return {"type": self.event_type, "data": self.data, "ts": self.timestamp}


class EventBus:
    """Broadcast events to all subscribers and keep a ring buffer for reconnects."""

    def __init__(self, buffer_size: int = RING_BUFFER_SIZE):
        self._subscribers: list[asyncio.Queue[AgentEvent]] = []
        self._buffer: deque[AgentEvent] = deque(maxlen=buffer_size)
        self._lock = asyncio.Lock()

    async def emit(self, event: AgentEvent) -> None:
        self._buffer.append(event)
        async with self._lock:
            dead: list[asyncio.Queue] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    async def emit_dict(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        await self.emit(AgentEvent(event_type=event_type, data=data or {}))

    def subscribe(self, replay_buffer: bool = True) -> asyncio.Queue[AgentEvent]:
        q: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=1000)
        if replay_buffer:
            for event in self._buffer:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    break
        self._subscribers.append(q)
        logger.debug("New subscriber (total=%d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue[AgentEvent]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def recent_events(self, limit: int = 50, event_type: str | None = None) -> list[dict]:
        events = list(self._buffer)
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return [e.to_dict() for e in events[-limit:]]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
