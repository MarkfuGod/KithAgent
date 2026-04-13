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
            kid=task.input_data.get("id", task.task_id),
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


BUILTIN_AGENTS: list[BaseAgent] = [
    # v0.1 — rule-based
    FileSearchAgent(),
    FileReadAgent(),
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
]
