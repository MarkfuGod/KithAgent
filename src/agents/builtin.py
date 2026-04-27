"""
Built-in Agent Threads — pre-loaded 'system services'.

These are analogous to kernel threads in an OS — they handle
common tasks that any external caller might need.
"""

from __future__ import annotations

import json
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
        await memory.store_knowledge(
            knowledge_id=task.input_data.get("id", task.task_id),
            category=task.input_data["category"],
            content=task.input_data["content"],
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
