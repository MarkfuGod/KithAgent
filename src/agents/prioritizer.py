"""
PriorityClassifierAgent — classifies ALL file types into priority tiers.

Priority levels:
  P0 (Hot):  Actively edited files — code, documents, images, anything recent
  P1 (Warm): Recently accessed / referenced files across all categories
  P2 (Cold): Old, rarely touched, archived files

Uses modification timestamps. Applies to all indexed files regardless of type —
a recently edited PDF or image is just as "hot" as a recently edited .py file.

Runs after BehaviorAnalyzerAgent and bulk-updates the file_index.priority column.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.agents.base import AgentTask, BaseAgent

logger = logging.getLogger("agent_sys.agents.prioritizer")


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
        by_category = {
            "code": {0: 0, 1: 0, 2: 0},
            "document": {0: 0, 1: 0, 2: 0},
            "image": {0: 0, 1: 0, 2: 0},
            "other": {0: 0, 1: 0, 2: 0},
        }

        _code_ext = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".java", ".c", ".cpp", ".swift"}
        _doc_ext = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".md", ".txt"}
        _img_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

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

            ext = path.rsplit(".", 1)[-1] if "." in path else ""
            ext = f".{ext}"
            if ext in _code_ext:
                cat = "code"
            elif ext in _doc_ext:
                cat = "document"
            elif ext in _img_ext:
                cat = "image"
            else:
                cat = "other"
            by_category[cat][new_priority] += 1

            if new_priority != current_priority:
                updates.append((path, new_priority))

        if updates:
            await memory.batch_update_priorities(updates)

        result = {
            "total_files": len(all_files),
            "updated": len(updates),
            "distribution": {"P0_hot": counts[0], "P1_warm": counts[1], "P2_cold": counts[2]},
            "by_category": {
                cat: {"P0": v[0], "P1": v[1], "P2": v[2]}
                for cat, v in by_category.items()
            },
        }
        logger.info(
            "Priority classification: %d files, %d updated — P0=%d P1=%d P2=%d "
            "(code: %d/%d/%d, doc: %d/%d/%d, img: %d/%d/%d)",
            len(all_files), len(updates), counts[0], counts[1], counts[2],
            by_category["code"][0], by_category["code"][1], by_category["code"][2],
            by_category["document"][0], by_category["document"][1], by_category["document"][2],
            by_category["image"][0], by_category["image"][1], by_category["image"][2],
        )
        return result
