"""
TriageAgent — LLM-driven file importance classification.

Instead of blindly summarizing all 200K+ files, this agent looks at file
paths and directory context to decide what actually matters for understanding
the user as a person.

Strategy:
  1. Gather untriaged files in batches
  2. Group by top-level directory for context
  3. For each group, ask LLM: which files reveal the user's identity,
     interests, skills, and work patterns?
  4. Assign triage_status:
     - 'high':   User's own code, personal docs, study notes, project configs
     - 'medium': Useful context (deps configs, project data files)
     - 'low':    Generic library/framework code, standard boilerplate
     - 'skip':   Third-party source trees, generated code, binary metadata
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.llm.base import LLMMessage

logger = logging.getLogger("agent_sys.agents.triage")

# TODO: TriageAgent 需要优化，代码处理还是太多了，可以考虑发现是代码项目，先看readme总结一下，然后跳过
# 等都triage完事了再看代码是什么
# 然后可以给一个UI，就是这个triage的优先级，默认就是你给的，然后用户可以调整
# TODO：然后extension 那里，加一个让用户可以决定总结文件类型的偏好
# FIX: 还有我发现，比如triage这个/Users/markfugod/.cursor/extensions/saoudrizwan.claude-dev-3.78.0-universal/tests/e2e/cli/package.json
# 的时候，为什么不直接skip呢？或者说你看一下有没有skip，因为我看她确实是在调用api来分析了，应该直接跳过啊

# TODO: 现在的逻辑应该是，agentos启动后，有一个cpu就是llm，来看看现在需要做什么，然后比如第一次启动
# llm就应该发现我要先做file index，然后index之后，发现要对文件进行总结，这是调用triage，然后triage其实
# 也是llm驱动，先读取index中的metadata，结合用户意愿，排列哪些文件总结优先级高，哪些低，高的先总结
# 总结的时候调用多个sub summarizer并行进行总结，然后汇总，因为是按index来吗，启用多少个subagent由发起这个
# 总结任务的agent来，就像curosr的agent设计一样，比如我遍历codebase时候，主模型通常会唤起三个小模型然后并行探索，最后返回，怎么总结，总结策略也是按用户的意愿，是激进还是平衡，还是保守，还是根据文件类型来决定
# 然后其他任务就是按整个cpu llm的想法和用户意愿来，就是感觉要出一个profile了，就出一个，用户要求analyze了
# 就分配分析的任务，当然定期画像更新和brief还是按设定走，有文件改动分析大改动还是小改动，有不同的策略，不要傻傻
# 的改一个字就直接整个这个index范围内就要重新总结一遍，当然这个改动大小也要有llm来决定，因为有可能出现字符改动少
# 但是字义或者内容改动就大，所以要智能判断。然后重新index，然后triage，summarize。。。。
# 当然上述操作不可能是严格并行的，应该是像操作系统并发调度一样，
# 我的说法是这样，你分析分析，有没有逻辑上的漏洞，当然这只是我的想法，我说的不一定都对，你要有自己的思考
# 比如完完全全按照操作系统映射就非常合理吗

# 我的终极设想就是让你的电脑安装10分钟agentos之后，就了解了你这个人，大概的喜好，然后就像人与人交往一样，有个
# 渐进的过程，一步一步，就像我上面说的流程一样，一步一步的深入了解你，对你有更全面的认知

# FIX：然后我发现个功能bug，我在一个terminal开启agent-sys时候，他应该在终端关闭后也一直在后台运行
# 我在打开另一个terminal开启agentsys的时候，他会自动杀掉我之前开启的agent-sys，这不对啊，一点容错也没有



_TRIAGE_MISSION = """You are the file importance triage system for AgentOS.

MISSION: AgentOS exists to gradually understand the user as a person — their
skills, interests, work patterns, projects, and values — by reading files on
their machine. Your job is to decide which files are worth spending LLM tokens
to summarize, based on how much they reveal about THIS USER as a person.

Classify each file as:
- "high": User's own creation — personal projects, study notes, original code,
  documents they wrote, creative work, config reflecting personal preferences.
  These files help build a picture of who this person IS.
- "medium": Provides useful context but isn't unique to the person — dependency
  configs (package.json, requirements.txt), data files, standard project scaffolding.
- "low": Generic/standard code that could belong to anyone — typical library
  patterns, common templates, standard framework boilerplate the user didn't write.
- "skip": Definitely NOT worth summarizing — third-party library source code,
  vendored dependencies, generated/compiled output, dataset files that are just
  raw data rows, binary metadata stubs.

CRITICAL SIGNALS for "skip":
- Paths containing: site-packages, vendor, third_party, __pycache__, .git/objects
- IDE extension directories (.cursor/extensions, .vscode/extensions)
- Files inside large known framework directories (e.g. PyTorch source, node_modules)
- Auto-generated files (*.pb.go, *_generated.*, migrations with numeric prefixes)
- Large dataset .txt/.csv with generic names (data_0001.txt, train.txt, labels.txt)

CRITICAL SIGNALS for "high":
- Files in the user's own project root (README, main.py, setup.py they wrote)
- Personal documents (resume, notes, essays, plans)
- Config files reflecting personal choices (.zshrc, .gitconfig, custom scripts)
- Study/learning materials the user collected or annotated

HOW TO USE USER PREFERENCES (if provided below):
- Treat them as priority HINTS, not hard rules.
- A file type the user considers "usually unimportant" (e.g. .txt) can still
  be classified "high" if its path/name clearly indicates personal value
  (e.g. `journal.txt`, `resume.txt`, `interview_notes.txt`).
- A file type the user considers "important" can still be "low" or "skip" if
  it's obviously generated or third-party.
- When unsure between two adjacent tiers, lean toward the user's preference.

For efficiency, classify entire directory subtrees at once using the "bulk"
field. Only use "individual" for files that clearly differ from their directory
pattern.

Output a JSON object:
{
  "bulk": [
    {"prefix": "<directory prefix>", "status": "skip", "reason": "third-party library source"},
    {"prefix": "<directory prefix>", "status": "high", "reason": "user's active project"}
  ],
  "individual": [
    {"path": "<path>", "status": "high|medium|low|skip"}
  ]
}

Output ONLY valid JSON."""

# Backstop: used when config.triage.skip_path_patterns is empty. Kept minimal
# since the real source of truth should be config/default.yaml.
_FALLBACK_SKIP_PATTERNS = [
    "site-packages/",
    "/vendor/",
    "/third_party/",
    "/.git/objects/",
    "/node_modules/",
    "/__pycache__/",
    "/.cursor/extensions/",
    "/.vscode/extensions/",
]

_BATCH_SIZE = 500
_LLM_BATCH_SIZE = 200
_DEFAULT_TIME_BUDGET = 300


def _build_triage_prompt(hints: list[str] | None, type_priority: dict[str, int] | None) -> str:
    """Compose the triage system prompt with user hints + file-type preferences."""
    parts = [_TRIAGE_MISSION]

    if hints:
        parts.append("\n\n=== USER PREFERENCES (hints, not rules) ===")
        for h in hints:
            parts.append(f"- {h}")

    if type_priority:
        ranked = sorted(type_priority.items(), key=lambda x: -int(x[1]))
        pref_line = ", ".join(f"{ext}={prio}" for ext, prio in ranked[:12])
        parts.append(
            "\n\nFile-type priority hints (higher = more likely worth user attention):\n"
            + pref_line
        )

    return "\n".join(parts)


class TriageAgent(BaseAgent):
    """LLM-driven file importance triage — decides what's worth summarizing."""
    name = "triage"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        llm = context.get("llm")

        triage_cfg = self._get_triage_config(context)
        skip_patterns = triage_cfg.get("skip_path_patterns") or _FALLBACK_SKIP_PATTERNS
        type_priority = triage_cfg.get("file_type_priority") or {}
        hints = triage_cfg.get("hints") or []

        batch_size = task.input_data.get("batch_size", _BATCH_SIZE)
        time_budget = task.input_data.get("time_budget", _DEFAULT_TIME_BUDGET)
        start_time = time.time()

        # Phase 1: rule-based fast triage using config-driven skip patterns.
        rule_results = await self._rule_based_pass(memory, skip_patterns)

        if not llm or not llm.available_providers():
            logger.warning("No LLM available — triage used rules only")
            return {**rule_results, "llm_triaged": 0, "mode": "rules_only"}

        # Compose system prompt once per run (cheap, but keeps logic out of hot loop).
        system_prompt = _build_triage_prompt(hints, type_priority)

        # Phase 2: LLM triage for remaining untriaged files, ordered by user
        # type priority so important kinds get analyzed first when budget runs out.
        llm_triaged = 0
        llm_errors = 0

        while True:
            elapsed = time.time() - start_time
            if elapsed >= time_budget:
                logger.info("Triage time budget exhausted after %.0fs", elapsed)
                break

            files = await memory.get_untriaged_files(
                limit=batch_size,
                type_priority=type_priority or None,
            )
            if not files:
                logger.info("All files triaged")
                break

            groups = self._group_by_directory(files)

            event_bus = context.get("event_bus")
            for dir_prefix, dir_files in groups.items():
                if time.time() - start_time >= time_budget:
                    break

                try:
                    result = await self._triage_group(
                        dir_prefix, dir_files, memory, llm, system_prompt
                    )
                    llm_triaged += result.get("classified", 0)
                    if event_bus:
                        await event_bus.emit_dict("triage.batch_progress", {
                            "directory": dir_prefix,
                            "classified": llm_triaged,
                            "batch_files": len(dir_files),
                            "elapsed_s": round(time.time() - start_time, 1),
                        })
                except Exception as e:
                    llm_errors += 1
                    logger.warning("Triage failed for %s: %s", dir_prefix, e)

            if len(files) < batch_size:
                break

        elapsed = time.time() - start_time
        triage_stats = await memory.get_triage_stats()

        return {
            **rule_results,
            "llm_triaged": llm_triaged,
            "llm_errors": llm_errors,
            "elapsed_seconds": round(elapsed, 1),
            "triage_distribution": triage_stats,
        }

    def _get_triage_config(self, context: dict[str, Any]) -> dict:
        """Pull user-tunable triage settings from kernel config with safe fallbacks."""
        kernel = context.get("kernel")
        cfg = getattr(kernel, "config", None) if kernel else None
        tcfg = getattr(cfg, "triage", None) if cfg else None
        if not tcfg:
            return {}
        return {
            "skip_path_patterns": list(getattr(tcfg, "skip_path_patterns", []) or []),
            "file_type_priority": dict(getattr(tcfg, "file_type_priority", {}) or {}),
            "hints": list(getattr(tcfg, "hints", []) or []),
        }

    async def _rule_based_pass(self, memory, skip_patterns: list[str]) -> dict:
        """Fast pass: skip files whose path contains any configured noise pattern.

        Uses SQL LIKE substring match (not prefix) so patterns like
        '.cursor/extensions/' catch the pattern anywhere in the path.
        """
        total_marked = 0
        for pattern in skip_patterns:
            if not pattern:
                continue
            count = await memory.batch_update_triage_by_prefix(
                f"%{pattern}", "skip"
            )
            if count > 0:
                total_marked += count
                logger.debug("Rule-based skip: %s → %d files", pattern, count)

        if total_marked > 0:
            logger.info(
                "Rule-based triage: marked %d files as 'skip' (patterns=%d)",
                total_marked, len(skip_patterns),
            )

        return {"rule_based_skipped": total_marked}

    def _group_by_directory(self, files: list[dict], depth: int = 3) -> dict[str, list[dict]]:
        """Group files by directory prefix for batch LLM processing."""
        home = str(Path.home())
        groups: dict[str, list[dict]] = defaultdict(list)

        for f in files:
            path = f["path"]
            rel = path[len(home):] if path.startswith(home) else path
            parts = rel.strip("/").split("/")
            prefix = "/".join(parts[:min(depth, len(parts) - 1)]) or "root"
            groups[prefix].append(f)

        return dict(groups)

    async def _triage_group(
        self, dir_prefix: str, files: list[dict], memory, llm, system_prompt: str
    ) -> dict:
        """Ask LLM to triage a batch of files from the same directory context."""
        home = str(Path.home())
        file_lines = []
        for f in files[:_LLM_BATCH_SIZE]:
            rel = f["path"][len(home):] if f["path"].startswith(home) else f["path"]
            size_kb = (f.get("size_bytes") or 0) / 1024
            file_lines.append(f"  {rel}  ({f.get('file_type', '?')}, {size_kb:.0f}KB)")

        prompt = (
            f"Directory context: ~/{dir_prefix}\n"
            f"Files to triage ({len(file_lines)}):\n"
            + "\n".join(file_lines)
        )

        response = await llm.complete(
            messages=[
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=prompt),
            ],
            task_type="classify",
            max_tokens=2000,
            temperature=0.1,
        )

        decision = self._parse_decision(response.content.strip())
        classified = await self._apply_decision(decision, files, memory, home)

        logger.debug(
            "Triaged ~/%s: %d files classified", dir_prefix, classified
        )
        return {"classified": classified}

    def _parse_decision(self, text: str) -> dict:
        """Parse LLM triage decision JSON, with tolerance for markdown fences."""
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        for end in range(len(text) - 1, 0, -1):
            if text[end] == "}":
                try:
                    return json.loads(text[:end + 1])
                except json.JSONDecodeError:
                    continue
        return {"bulk": [], "individual": []}

    async def _apply_decision(
        self, decision: dict, files: list[dict], memory, home: str
    ) -> int:
        """Apply bulk prefix rules and individual overrides to the DB."""
        valid_statuses = {"high", "medium", "low", "skip"}

        # Build a path→status map starting from bulk rules
        path_status: dict[str, str] = {}

        bulk_rules = decision.get("bulk", [])
        for rule in bulk_rules:
            prefix = rule.get("prefix", "")
            status = rule.get("status", "").lower()
            if status not in valid_statuses or not prefix:
                continue

            # Expand prefix to full path if it's relative
            if prefix.startswith("~/"):
                full_prefix = home + prefix[1:]
            elif not prefix.startswith("/"):
                full_prefix = home + "/" + prefix
            else:
                full_prefix = prefix

            # Apply to matching files in this batch
            for f in files:
                if f["path"].startswith(full_prefix):
                    path_status[f["path"]] = status

            # Also bulk-update the DB for files not in this batch
            count = await memory.batch_update_triage_by_prefix(full_prefix, status)
            if count > 0:
                logger.debug("Bulk triage: %s → '%s' (%d files)", prefix, status, count)

        # Individual overrides
        individual = decision.get("individual", [])
        for item in individual:
            path = item.get("path", "")
            status = item.get("status", "").lower()
            if status not in valid_statuses or not path:
                continue

            if path.startswith("~/"):
                full_path = home + path[1:]
            elif not path.startswith("/"):
                full_path = home + "/" + path
            else:
                full_path = path

            path_status[full_path] = status

        # Apply remaining individual updates
        updates = [(p, s) for p, s in path_status.items()]
        if updates:
            await memory.batch_update_triage(updates)

        return len(updates)
