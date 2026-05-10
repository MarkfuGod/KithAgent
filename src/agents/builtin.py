"""
Built-in Agent Threads — pre-loaded 'system services'.

These are analogous to kernel threads in an OS — they handle
common tasks that any external caller might need.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from pathlib import Path
from typing import Any

from src.agents.base import AgentTask, BaseAgent


class FileSearchAgent(BaseAgent):
    """Search the indexed file system."""
    name = "file_search"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        query = task.input_data.get("query", "")
        file_type = task.input_data.get("file_type")
        limit = task.input_data.get("limit", 20)
        results = await memory.search_files(query, file_type=file_type, limit=limit)
        return {"matches": results, "count": len(results)}


class FileReadAgent(BaseAgent):
    """Read a specific file's content and metadata."""
    name = "file_read"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        path = task.input_data.get("path", "")
        info = await memory.get_file_info(path)
        if info:
            return {"found": True, **info}
        return {"found": False, "path": path}


class KnowledgeQueryAgent(BaseAgent):
    """Query the knowledge base."""
    name = "knowledge_query"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        category = task.input_data.get("category")
        limit = task.input_data.get("limit", 50)
        results = await memory.query_knowledge(category=category, limit=limit)
        return {"entries": results, "count": len(results)}


class KnowledgeStoreAgent(BaseAgent):
    """Store new knowledge into the knowledge base."""
    name = "knowledge_store"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        category = str(task.input_data.get("category") or "").strip()
        content = str(task.input_data.get("content") or "").strip()
        if not category:
            raise ValueError("knowledge.store requires non-empty 'category'")
        if not content:
            raise ValueError("knowledge.store requires non-empty 'content'")
        await memory.store_knowledge(
            knowledge_id=task.input_data.get("id", task.task_id),
            category=category,
            content=content,
            source_path=task.input_data.get("source", ""),
            metadata=task.input_data.get("metadata"),
        )
        return {"stored": True}


class ContextAgent(BaseAgent):
    """Save/load agent session context — enables cross-session memory."""
    name = "context"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        action = task.input_data.get("action", "load")
        session_id = task.input_data.get("session_id", "")

        if action == "save":
            await memory.save_context(
                session_id=session_id,
                agent_name=task.caller,
                context=task.input_data.get("context_data", {}),
                ttl=task.input_data.get("ttl", 3600),
            )
            return {"saved": True, "session_id": session_id}
        else:
            ctx = await memory.load_context(session_id)
            return {"session_id": session_id, "context": ctx}


class CapabilitiesListAgent(BaseAgent):
    """Expose Kith's gateway-style capabilities as explicit, auditable nodes."""
    name = "capabilities_list"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        return {
            "contract_version": "kith.capabilities.v1",
            "version": "0.1",
            "generated_at": time.time(),
            "capabilities": [
                {
                    "id": "local_memory.profile",
                    "status": "available",
                    "sensitivity": "personal",
                    "commands": ["profile.summary", "memory.review", "memory.feedback"],
                    "permission": "User can review, confirm, reject, or hide memories.",
                },
                {
                    "id": "local_evidence.files",
                    "status": "available",
                    "sensitivity": "source_scoped",
                    "commands": ["sources.get", "file.search", "file.read", "knowledge.query"],
                    "permission": "Only user-approved source folders are indexed.",
                },
                {
                    "id": "agent_context.handoff",
                    "status": "available",
                    "sensitivity": "workspace_context",
                    "commands": ["context.agent_brief", "report.brief", "report.project"],
                    "permission": "Callers receive source-backed context, not hidden filesystem access.",
                },
                {
                    "id": "model_routing.tasks",
                    "status": "available",
                    "sensitivity": "configuration",
                    "commands": ["settings.model", "settings.model.get"],
                    "permission": "Local/API model routing remains explicit and user controlled.",
                },
                {
                    "id": "external_events.normalized",
                    "status": "planned",
                    "sensitivity": "platform_messages",
                    "commands": ["external.event.ingest"],
                    "permission": "Future messaging/device events should be normalized before scheduling.",
                },
                {
                    "id": "media_evidence.ingest",
                    "status": "planned",
                    "sensitivity": "media",
                    "commands": ["media.ingest", "media.transcribe", "media.summarize"],
                    "permission": "Media should become local evidence objects with retention controls.",
                },
                {
                    "id": "device_nodes.capture",
                    "status": "planned_sensitive",
                    "sensitivity": "camera_microphone_screen_location",
                    "commands": ["screen.snapshot", "camera.snap", "mic.record", "browser.current_tab"],
                    "permission": "Must require explicit opt-in, foreground indicators, payload limits, and audit logs.",
                },
            ],
        }


class AgentContextBriefAgent(BaseAgent):
    """Build a Hermes-style session-aware handoff for external agents."""
    name = "agent_context_brief"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context.get("memory")
        caller = str(task.input_data.get("caller") or task.caller or "external-agent")
        workspace = str(task.input_data.get("workspace") or "").strip()
        user_task = str(task.input_data.get("task") or "").strip()
        session_id = str(task.input_data.get("session_id") or task.task_id)
        surface = str(task.input_data.get("surface") or "agent")
        workspace_path = _expand_path(workspace) if workspace else ""
        workspace_hash = hashlib.sha1((workspace_path or "global").encode()).hexdigest()[:10]
        session_key = f"kith:{surface}:{caller}:{workspace_hash}:{session_id}"

        warnings: list[str] = []
        facts = await _safe_memory_list(memory, "list_profile_facts", warnings, limit=20)
        recent = await _safe_memory_list(memory, "get_recently_modified_files", warnings, hours=168, limit=50)
        context_briefs = await _safe_memory_list(memory, "query_knowledge", warnings, category="context_brief", limit=2)
        behavior = await _safe_memory_list(memory, "query_knowledge", warnings, category="behavior_insight", limit=2)

        workspace_files = await _safe_memory_list(
            memory,
            "list_workspace_files",
            warnings,
            workspace_path,
            limit=24,
        ) if workspace_path else []
        if not workspace_files and workspace_path:
            workspace_files = [
                file for file in recent
                if str(file.get("path", "")).startswith(workspace_path)
            ][:24]

        confirmed = [fact for fact in facts if fact.get("status") == "confirmed"]
        inferred = [fact for fact in facts if fact.get("status") == "inferred"]
        payload = {
            "contract_version": "kith.context_brief.v1",
            "session_key": session_key,
            "session": {
                "key": session_key,
                "source_type": "desktop" if surface == "desktop" else "agent",
                "platform": surface,
                "caller": caller,
                "workspace_hash": workspace_hash,
                "session_id": session_id,
            },
            "caller": caller,
            "surface": surface,
            "workspace": workspace_path or workspace,
            "task": user_task,
            "generated_at": time.time(),
            "evidence_policy": {
                "scope": "approved_sources_only",
                "workspace_scoped": bool(workspace_path),
                "rule": "Use only returned evidence unless the user explicitly asks Kith to search more.",
            },
            "evidence": {
                "status": _evidence_status(memory, workspace_path, workspace_files),
                "workspace_file_count": len(workspace_files),
                "recent_file_count": len(recent),
                "profile_fact_count": len(facts),
                "warnings": warnings,
            },
            "context_apis": [
                {"name": "kith_brief", "syscall": "context.agent_brief", "status": "available"},
                {"name": "kith_search_files", "syscall": "file.search", "status": "available"},
                {"name": "kith_read_evidence", "syscall": "file.read", "status": "available"},
                {"name": "kith_profile", "syscall": "profile.summary", "status": "available"},
                {"name": "kith_recent_focus", "syscall": "assistant.insights", "status": "available"},
            ],
            "profile": {
                "confirmed_facts": confirmed[:8],
                "inferred_facts": inferred[:8],
            },
            "recent_files": recent[:12],
            "workspace_files": workspace_files,
            "context_briefs": [_decode_knowledge(entry) for entry in context_briefs],
            "behavior_insights": [_decode_knowledge(entry) for entry in behavior],
        }
        payload["handoff_prompt"] = self._handoff_prompt(payload)
        return payload

    def _handoff_prompt(self, payload: dict[str, Any]) -> str:
        workspace_files = payload["workspace_files"][:8]
        recent_files = payload["recent_files"][:8]
        facts = payload["profile"]["confirmed_facts"] or payload["profile"]["inferred_facts"]
        lines = [
            "Use this Kith local-memory brief before acting.",
            f"Contract: {payload.get('contract_version', 'kith.context_brief.v1')}",
            f"Session key: {payload['session_key']}",
            f"Caller: {payload['caller']}",
        ]
        if payload.get("workspace"):
            lines.append(f"Workspace: {payload['workspace']}")
        if payload.get("task"):
            lines.append(f"User task: {payload['task']}")
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        if evidence.get("status"):
            lines.append(f"Evidence status: {evidence['status']}")
        if workspace_files:
            lines.append("Important workspace files:")
            lines.extend(f"- {file.get('path')}" for file in workspace_files)
        elif recent_files:
            lines.append("Recent local files:")
            lines.extend(f"- {file.get('path')}" for file in recent_files)
        if facts:
            lines.append("User memory signals:")
            lines.extend(f"- {fact.get('statement')}" for fact in facts[:6])
        context_apis = payload.get("context_apis") or []
        if context_apis:
            lines.append("Available Kith context APIs:")
            lines.extend(
                f"- {item.get('name')} via {item.get('syscall')}"
                for item in context_apis[:6]
                if isinstance(item, dict)
            )
        lines.append("Rules: cite local evidence when you use it; ask before broad scanning; do not assume files outside approved sources were searched.")
        return "\n".join(lines)


class FileListAgent(BaseAgent):
    """List files matching directory/type/triage filters — metadata listing, not content search."""
    name = "file_list"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        directory = task.input_data.get("directory", "")
        file_type = task.input_data.get("file_type")
        triage_status = task.input_data.get("triage_status")
        limit = task.input_data.get("limit", 100)

        assert memory._db
        sql = "SELECT path, file_type, size_bytes, modified_at, priority, triage_status FROM file_index WHERE 1=1"
        params: list[Any] = []

        if directory:
            sql += " AND path LIKE ?"
            params.append(f"{directory}%")
        if file_type:
            sql += " AND file_type = ?"
            params.append(file_type)
        if triage_status:
            sql += " AND triage_status = ?"
            params.append(triage_status)

        sql += " ORDER BY modified_at DESC LIMIT ?"
        params.append(limit)

        rows = memory._db.execute(sql, params).fetchall()
        files = [
            {"path": r[0], "file_type": r[1], "size_bytes": r[2],
             "modified_at": r[3], "priority": r[4], "triage_status": r[5]}
            for r in rows
        ]
        return {"files": files, "count": len(files)}


class AgentSubmitAgent(BaseAgent):
    """Proxy: submit an arbitrary agent task via syscall."""
    name = "agent_submit"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        scheduler = context.get("scheduler")
        if not scheduler:
            return {"error": "Scheduler not available"}

        target_name = task.input_data.get("agent_name", "")
        if not target_name:
            return {"error": "Missing 'agent_name' parameter"}

        agent = scheduler._find_agent(target_name)
        if not agent:
            return {"error": f"No agent registered for: {target_name}"}

        child = AgentTask(
            name=target_name,
            priority=task.input_data.get("priority", 1),
            input_data=task.input_data.get("input_data", {}),
            caller=task.caller or "syscall",
        )
        submitted = await scheduler.submit(child, context)
        return {
            "submitted": True,
            "task_id": submitted.task_id,
            "name": submitted.name,
            "state": submitted.state.value,
        }


class AgentTaskStatusAgent(BaseAgent):
    """Proxy: query task status by task_id."""
    name = "agent_task_status"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        scheduler = context.get("scheduler")
        if not scheduler:
            return {"error": "Scheduler not available"}

        task_id = task.input_data.get("task_id", "")
        if not task_id:
            return {"error": "Missing 'task_id' parameter"}

        found = scheduler.get_task(task_id)
        if not found:
            return {"found": False, "task_id": task_id}

        return {
            "found": True,
            "task_id": found.task_id,
            "name": found.name,
            "state": found.state.value,
            "priority": found.priority,
            "caller": found.caller,
            "elapsed": round(found.elapsed() or 0, 2),
            "error": found.error,
            "has_result": found.result is not None,
        }


class SystemStatusAgent(BaseAgent):
    """Report system status — like 'top' or 'htop' for AgentOS."""
    name = "system_status"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        kernel = context.get("kernel")
        memory = context.get("memory")
        scheduler = context.get("scheduler")
        fs = context.get("filesystem")

        status = {}
        if kernel and hasattr(kernel, "status"):
            status["kernel"] = kernel.status()
        if memory and hasattr(memory, "stats"):
            status["memory"] = await memory.stats()
        if scheduler and hasattr(scheduler, "status"):
            status["scheduler"] = scheduler.status()
        if fs and hasattr(fs, "status"):
            status["filesystem"] = fs.status()
        return status


def _expand_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


async def _safe_memory_list(
    memory: Any,
    method_name: str,
    warnings: list[str],
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    if not memory:
        warning = "memory_unavailable"
        if warning not in warnings:
            warnings.append(warning)
        return []
    method = getattr(memory, method_name, None)
    if not callable(method):
        warnings.append(f"{method_name}_unavailable")
        return []
    try:
        result = method(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, list) else []
    except Exception as exc:
        warnings.append(f"{method_name}_failed:{exc}")
        return []


def _evidence_status(memory: Any, workspace_path: str, workspace_files: list[dict[str, Any]]) -> str:
    if not memory:
        return "memory_unavailable"
    if not workspace_path:
        return "no_workspace"
    if workspace_files:
        return "workspace_evidence"
    return "empty_workspace"


def _decode_knowledge(entry: dict[str, Any]) -> Any:
    content = entry.get("content")
    if not isinstance(content, str):
        return entry
    try:
        return json.loads(content)
    except Exception:
        return content


from src.agents.summarizer import SummarizerAgent
from src.agents.analyzer import BehaviorAnalyzerAgent
from src.agents.prioritizer import PriorityClassifierAgent
from src.agents.reporter import ReportGeneratorAgent
from src.agents.profile_builder import ProfileBuilderAgent
from src.agents.triage import TriageAgent
from src.agents.assistant import AssistantAgent
from src.agents.rag_indexer import RagIndexerAgent


BUILTIN_AGENTS: list[BaseAgent] = [
    # v0.1 — rule-based
    FileSearchAgent(),
    FileReadAgent(),
    FileListAgent(),
    KnowledgeQueryAgent(),
    KnowledgeStoreAgent(),
    ContextAgent(),
    CapabilitiesListAgent(),
    AgentContextBriefAgent(),
    SystemStatusAgent(),
    # v0.2 — LLM-powered smart agents
    SummarizerAgent(),
    BehaviorAnalyzerAgent(),
    PriorityClassifierAgent(),
    ReportGeneratorAgent(),
    ProfileBuilderAgent(),
    # v0.3 — LLM triage (decides what's worth summarizing)
    TriageAgent(),
    # v0.8 — delayed background chunk RAG
    RagIndexerAgent(),
    # v0.5 — agent management syscalls
    AgentSubmitAgent(),
    AgentTaskStatusAgent(),
    # v0.7 — consumer desktop facade
    AssistantAgent(),
]
