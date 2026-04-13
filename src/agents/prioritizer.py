"""
PriorityClassifierAgent — classifies files into priority tiers.

Priority levels:
  P0 (Hot):  Actively edited files, current project focus
  P1 (Warm): Recently accessed / referenced files
  P2 (Cold): Old, rarely touched, archived files

Uses modification timestamps + behavior insights.
Runs after BehaviorAnalyzerAgent and bulk-updates the file_index.priority column.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.agents.base import AgentTask, BaseAgent

logger = logging.getLogger("agent_sys.agents.prioritizer")

# Thresholds in seconds
_HOT_THRESHOLD = 3 * 24 * 3600    # modified in last 3 days
_WARM_THRESHOLD = 30 * 24 * 3600   # modified in last 30 days


class PriorityClassifierAgent(BaseAgent):
    """Classify all indexed files into P0/P1/P2 priority tiers."""
    name = "priority_classifier"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        now = time.time()

        hot_threshold = task.input_data.get("hot_days", 3) * 86400
        warm_threshold = task.input_data.get("warm_days", 30) * 86400

        all_files = await memory.get_all_file_paths_with_priority()

        updates: list[tuple[str, int]] = []
        counts = {0: 0, 1: 0, 2: 0}

        for path, modified_at, current_priority in all_files:
            if not modified_at:
                new_priority = 2
            else:
                age = now - modified_at
                if age < hot_threshold:
                    new_priority = 0
                elif age < warm_threshold:
                    new_priority = 1
                else:
                    new_priority = 2

            counts[new_priority] += 1
            if new_priority != current_priority:
                updates.append((path, new_priority))

        if updates:
            await memory.batch_update_priorities(updates)

        result = {
            "total_files": len(all_files),
            "updated": len(updates),
            "distribution": {"P0_hot": counts[0], "P1_warm": counts[1], "P2_cold": counts[2]},
        }
        logger.info(
            "Priority classification: %d files, %d updated — P0=%d P1=%d P2=%d",
            len(all_files), len(updates), counts[0], counts[1], counts[2],
        )
        return result
