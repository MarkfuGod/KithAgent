"""Consumer-facing Jarvis agent for the desktop app."""

from __future__ import annotations

import asyncio
import json
import logging
import hashlib
import time
from pathlib import Path
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.llm.base import LLMMessage

logger = logging.getLogger("agent_sys.agents.assistant")

_SYSTEM = """你是 Kith，一款本地优先的 Mac 个人助理。
你的目标是像 Jarvis 一样基于用户授权的本地资料理解这个人，但必须保持谦逊：
- 明确区分“用户确认的事实”和“从文件/行为推断出的可能性”。
- 不要暴露 syscall、PID、token、SQLite 等开发者术语。
- 回答要具体、温暖、可行动，避免神秘化或过度诊断。
- 如果证据不足，就说明还需要更多授权资料或用户确认。
"""


class AssistantAgent(BaseAgent):
    """A product-level facade over profile, memory, sources, and settings."""

    name = "assistant"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        action = task.input_data.get("action", "chat")
        if action == "chat":
            return await self._chat(task, context)
        if action == "profile_summary":
            return await self._profile_summary(task, context)
        if action == "memory_review":
            return await self._memory_review(task, context)
        if action == "memory_feedback":
            return await self._memory_feedback(task, context)
        if action == "sources_get":
            return await self._sources_get(context)
        if action == "sources_configure":
            return await self._sources_configure(task, context)
        if action == "settings_model":
            return await self._settings_model(task, context)
        if action == "onboarding_bootstrap":
            return await self._onboarding_bootstrap(task, context)
        return {"error": f"Unknown assistant action: {action}"}

    async def _profile_summary(self, task: AgentTask, context: dict[str, Any]) -> dict:
        memory = context["memory"]
        if task.input_data.get("rebuild"):
            from src.agents.profile_builder import ProfileBuilderAgent
            await ProfileBuilderAgent().execute(task, context)

        profile_entries = await memory.query_knowledge(category="user_profile", limit=1)
        profile = self._decode_entry(profile_entries[0]) if profile_entries else {}
        facts = await memory.list_profile_facts(limit=80)
        stats = await memory.stats()
        return {"profile": profile, "facts": facts, "stats": stats}

    async def _memory_review(self, task: AgentTask, context: dict[str, Any]) -> dict:
        memory = context["memory"]
        facts = await memory.list_profile_facts(
            status=task.input_data.get("status"),
            limit=int(task.input_data.get("limit", 50)),
            include_hidden=True,
        )
        knowledge = await memory.query_knowledge(limit=20)
        return {"facts": facts, "knowledge": knowledge}

    async def _memory_feedback(self, task: AgentTask, context: dict[str, Any]) -> dict:
        memory = context["memory"]
        fact_id = task.input_data.get("fact_id", "")
        status = task.input_data.get("status", "inferred")
        updated = await memory.update_profile_fact_status(fact_id, status)
        return {"updated": updated, "fact_id": fact_id, "status": status}

    async def _chat(self, task: AgentTask, context: dict[str, Any]) -> dict:
        memory = context["memory"]
        llm = context.get("llm")
        message = str(task.input_data.get("message") or "").strip()
        history = task.input_data.get("history") or []
        if not message:
            return {"answer": "你可以直接问我：你觉得我是个什么样的人？"}

        facts = await memory.list_profile_facts(limit=30)
        recent = await memory.get_recently_modified_files(hours=168, limit=30)
        profile_entries = await memory.query_knowledge(category="user_profile", limit=1)
        behavior_entries = await memory.query_knowledge(category="behavior_insight", limit=3)
        brief_entries = await memory.query_knowledge(category="context_brief", limit=2)
        rag_evidence = await self._retrieve_rag_evidence(task, context, message)

        context_packet = {
            "confirmed_facts": [f for f in facts if f.get("status") == "confirmed"],
            "inferred_facts": [f for f in facts if f.get("status") == "inferred"][:18],
            "profile": self._decode_entry(profile_entries[0]) if profile_entries else {},
            "behavior_insights": [self._decode_entry(e) for e in behavior_entries],
            "context_briefs": [self._decode_entry(e) for e in brief_entries],
            "recent_files": [
                {"path": f.get("path"), "file_type": f.get("file_type"), "modified_at": f.get("modified_at")}
                for f in recent[:20]
            ],
            "retrieved_evidence": rag_evidence,
        }

        if llm and llm.available_providers():
            try:
                messages = [
                    LLMMessage(role="system", content=_SYSTEM),
                    LLMMessage(
                        role="user",
                        content=(
                            "这是你当前掌握的本地上下文。请只基于这些证据回答，"
                            "并在必要时说明哪些是推断。"
                            "如果使用 retrieved_evidence，请用 [S1] 这样的来源编号标注关键结论。\n\n"
                            f"{json.dumps(context_packet, ensure_ascii=False, indent=2)}"
                        ),
                    ),
                ]
                for item in history[-6:]:
                    role = item.get("role") if isinstance(item, dict) else None
                    content = item.get("content") if isinstance(item, dict) else None
                    if role in {"user", "assistant"} and content:
                        messages.append(LLMMessage(role=role, content=str(content)))
                messages.append(LLMMessage(role="user", content=message))
                resp = await llm.complete(
                    messages=messages,
                    task_type="assistant",
                    max_tokens=900,
                    temperature=0.35,
                )
                return {
                    "answer": resp.content,
                    "context": self._context_digest(context_packet),
                    "sources": rag_evidence,
                }
            except Exception as e:
                logger.warning("Assistant LLM response failed: %s", e)

        return {
            "answer": self._fallback_answer(message, context_packet),
            "context": self._context_digest(context_packet),
            "sources": rag_evidence,
        }

    async def _retrieve_rag_evidence(self, task: AgentTask, context: dict[str, Any], message: str) -> list[dict]:
        kernel = context.get("kernel")
        memory = context.get("memory")
        config = getattr(kernel, "config", None) if kernel else None
        memory_cfg = getattr(config, "memory", None)
        rag_cfg = getattr(memory_cfg, "rag", None)
        if not memory or not rag_cfg or not getattr(rag_cfg, "enabled", True):
            return []
        timeout = float(task.input_data.get("rag_timeout", 4.0))
        try:
            results = await asyncio.wait_for(
                memory.hybrid_search_chunks(
                    message,
                    limit=int(getattr(rag_cfg, "assistant_top_k", 6)),
                    fts_limit=int(getattr(rag_cfg, "fts_top_k", 20)),
                    vector_limit=int(getattr(rag_cfg, "vector_top_k", 20)),
                    min_score=float(getattr(rag_cfg, "min_score", 0.05)),
                    allowed_triage_statuses=getattr(rag_cfg, "allowed_triage_statuses", ["high", "medium"]),
                ),
                timeout=timeout,
            )
        except Exception as e:
            logger.debug("Assistant RAG retrieval skipped: %s", e)
            return []
        evidence = []
        for item in results:
            evidence.append({
                "source_id": item.get("source_id"),
                "path": item.get("path"),
                "file_type": item.get("file_type"),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
                "snippet": item.get("content"),
                "score": item.get("hybrid_score"),
                "modes": item.get("modes", []),
            })
        return evidence

    async def _sources_get(self, context: dict[str, Any]) -> dict:
        from src.kernel.user_settings import load_scan_settings
        kernel = context.get("kernel")
        default_paths = [str(p) for p in getattr(kernel.config.filesystem, "watch_paths", [])] if kernel else []
        return load_scan_settings(default_paths)

    async def _sources_configure(self, task: AgentTask, context: dict[str, Any]) -> dict:
        from pathlib import Path
        from src.kernel.user_settings import save_scan_settings

        kernel = context.get("kernel")
        memory = context["memory"]
        saved = save_scan_settings(task.input_data.get("watch_paths") or [])
        if kernel:
            kernel.config.filesystem.watch_paths = [Path(p) for p in saved["watch_paths"]]
        pruned = await memory.prune_out_of_scope(saved["watch_paths"])
        return {**saved, "pruned_files": pruned, "restart_recommended": True}

    async def _settings_model(self, task: AgentTask, context: dict[str, Any]) -> dict:
        from src.kernel.user_settings import save_model_settings

        result = save_model_settings(task.input_data)
        kernel = context.get("kernel")
        if kernel:
            try:
                reload_result = await kernel.reload_config()
                result["reload"] = reload_result
            except Exception as e:
                result["reload_error"] = str(e)
        return result

    async def _onboarding_bootstrap(self, task: AgentTask, context: dict[str, Any]) -> dict:
        """Build a useful first profile in minutes from explicit answers + safe local signals."""
        memory = context["memory"]
        llm = context.get("llm")
        started = time.time()
        run_id = f"first_insight_{int(started)}_{hashlib.sha1(str(started).encode()).hexdigest()[:8]}"

        answers = task.input_data.get("answers") or {}
        if not isinstance(answers, dict):
            raise ValueError("answers must be an object")

        include_browser = bool(task.input_data.get("include_browser_history", False))
        browser_summary = {
            "enabled": include_browser,
            "sources": [],
            "entries_count": 0,
            "bookmarks_count": 0,
            "downloads_count": 0,
            "top_domains": [],
            "topics": [],
            "sample_titles": [],
            "bookmarks": [],
            "downloads": [],
        }
        await memory.start_insight_run(
            run_id,
            "first_insight",
            input_counts={
                "answer_fields": len([k for k, v in answers.items() if self._as_list(v)]),
                "browser_history_requested": int(include_browser),
            },
            metadata={"phase": "started"},
        )

        event_bus = context.get("event_bus")
        if include_browser:
            from src.ingest.browser_history import BrowserHistoryIngestor
            browser_summary = await BrowserHistoryIngestor().collect(
                days=int(task.input_data.get("history_days", 30)),
                limit=int(task.input_data.get("history_limit", 500)),
            )

        stats = await memory.stats()
        recent = await memory.get_recently_modified_files(hours=168, limit=40)
        folder_summary = self._folder_structure_summary(recent)
        quick_profile = self._rule_based_onboarding_profile(answers, browser_summary, stats, recent)

        if llm and llm.available_providers():
            try:
                quick_profile = await self._llm_onboarding_profile(llm, answers, browser_summary, stats, recent)
            except Exception as e:
                logger.warning("Onboarding LLM profile failed: %s", e)

        topics = self._first_insight_topics(quick_profile, browser_summary)
        suggestions = self._first_insight_suggestions(answers, quick_profile, browser_summary, folder_summary)
        sources = await self._store_first_insight_sources(memory, run_id, browser_summary, recent, folder_summary)

        await memory.store_knowledge(
            knowledge_id="onboarding_profile_current",
            category="onboarding_profile",
            content=json.dumps(
                {
                    "run_id": run_id,
                    "profile": quick_profile,
                    "topics": topics,
                    "suggestions": suggestions,
                    "sources": sources,
                },
                ensure_ascii=False,
            ),
            metadata={
                "generated_at": started,
                "browser_history": include_browser,
                "entries_count": browser_summary.get("entries_count", 0),
                "run_id": run_id,
            },
        )
        # Seed the same surface the chat/profile summary already read, so the
        # product feels personalized before deep indexing finishes.
        await memory.store_knowledge(
            knowledge_id="user_profile_current",
            category="user_profile",
            content=json.dumps(quick_profile, ensure_ascii=False),
            metadata={"generated_at": started, "source": "first_insight", "run_id": run_id},
        )
        profile_facts = await self._store_onboarding_facts(memory, answers, browser_summary, quick_profile, run_id)
        await self._store_first_insight_items(memory, run_id, topics, suggestions)

        elapsed = round(time.time() - started, 2)
        output_summary = {
            "topics": [t["topic"] for t in topics[:5]],
            "suggestions": [s["statement"] for s in suggestions[:5]],
            "facts": len(profile_facts),
        }
        await memory.finish_insight_run(
            run_id,
            status="completed",
            output_summary=output_summary,
            metadata={
                "elapsed_seconds": elapsed,
                "browser_entries": browser_summary.get("entries_count", 0),
                "bookmarks": browser_summary.get("bookmarks_count", 0),
                "downloads": browser_summary.get("downloads_count", 0),
                "recent_files": len(recent),
            },
        )
        if event_bus:
            await event_bus.emit_dict("profile.seeded", {
                "run_id": run_id,
                "facts": len(profile_facts),
                "topics": len(topics),
                "suggestions": len(suggestions),
            })
            for suggestion in suggestions[:3]:
                await event_bus.emit_dict("suggestion.created", suggestion)

        return {
            "ready": True,
            "phase": "first_insight",
            "run_id": run_id,
            "elapsed_seconds": elapsed,
            "profile": quick_profile,
            "topics": topics,
            "suggestions": suggestions,
            "sources": sources,
            "profile_facts": profile_facts,
            "browser_history": {
                "enabled": include_browser,
                "sources_count": len(browser_summary.get("sources", [])),
                "entries_count": browser_summary.get("entries_count", 0),
                "bookmarks_count": browser_summary.get("bookmarks_count", 0),
                "downloads_count": browser_summary.get("downloads_count", 0),
                "top_domains": browser_summary.get("top_domains", [])[:10],
            },
            "next_actions": self._onboarding_next_actions(quick_profile, browser_summary),
        }

    def _rule_based_onboarding_profile(
        self,
        answers: dict,
        browser_summary: dict,
        stats: dict,
        recent_files: list[dict],
    ) -> dict:
        goals = self._as_list(answers.get("goals"))
        roles = self._as_list(answers.get("roles"))
        interests = self._as_list(answers.get("interests"))
        current_focus = self._as_list(answers.get("current_focus"))
        planning_style = str(answers.get("planning_style") or "").strip()
        topics = [t["topic"] for t in browser_summary.get("topics", [])[:12] if t.get("topic")]
        domains = [d["domain"] for d in browser_summary.get("top_domains", [])[:8] if d.get("domain")]

        inferred_interests = list(dict.fromkeys(interests + topics[:8]))
        summary_bits = []
        if roles:
            summary_bits.append("、".join(roles[:2]))
        if current_focus:
            summary_bits.append(f"近期关注 {current_focus[0]}")
        elif topics:
            summary_bits.append(f"最近在看 {topics[0]}")
        summary = "，".join(summary_bits) or "刚完成初始化，正在形成第一版个人画像"

        return {
            "identity": {
                "summary": summary,
                "roles": roles or ["unknown"],
            },
            "goals": goals,
            "interests": {
                "explicit": interests,
                "inferred_from_browser": topics[:12],
                "current_focus": current_focus,
            },
            "planning": {
                "style": planning_style or "lightweight",
                "suggestion_cadence": str(answers.get("suggestion_cadence") or "daily"),
            },
            "digital_footprint": {
                "indexed_files": stats.get("indexed_files", 0),
                "recent_files": len(recent_files),
                "browser_entries": browser_summary.get("entries_count", 0),
                "bookmarks": browser_summary.get("bookmarks_count", 0),
                "downloads": browser_summary.get("downloads_count", 0),
                "top_domains": domains,
            },
            "expertise_areas": inferred_interests[:10],
            "confidence": {
                "explicit_answers": "high" if answers else "low",
                "browser_history": "medium" if browser_summary.get("entries_count") else "none",
                "file_index": "medium" if stats.get("indexed_files", 0) else "low",
            },
        }

    async def _llm_onboarding_profile(
        self,
        llm,
        answers: dict,
        browser_summary: dict,
        stats: dict,
        recent_files: list[dict],
    ) -> dict:
        prompt = {
            "user_answers": answers,
            "browser_summary": {
                "entries_count": browser_summary.get("entries_count", 0),
                "top_domains": browser_summary.get("top_domains", [])[:15],
                "topics": browser_summary.get("topics", [])[:20],
                "sample_titles": browser_summary.get("sample_titles", [])[:20],
                "bookmarks_count": browser_summary.get("bookmarks_count", 0),
                "downloads_count": browser_summary.get("downloads_count", 0),
                "bookmark_samples": browser_summary.get("bookmarks", [])[:12],
                "download_samples": browser_summary.get("downloads", [])[:12],
            },
            "local_index_stats": stats,
            "recent_files_sample": [
                {"path": f.get("path"), "file_type": f.get("file_type")}
                for f in recent_files[:20]
            ],
        }
        resp = await llm.complete(
            messages=[
                LLMMessage(role="system", content=(
                    "你是 Kith 的快速初始化画像器。请在隐私克制前提下生成一个可纠正的初版用户画像。"
                    "只基于用户显式回答、浏览历史聚合和本地文件统计，不要臆测敏感属性。"
                    "输出 JSON，字段包含 identity, goals, interests, planning, digital_footprint, expertise_areas, confidence。"
                )),
                LLMMessage(role="user", content=json.dumps(prompt, ensure_ascii=False, indent=2)),
            ],
            task_type="profile",
            max_tokens=1200,
            temperature=0.25,
        )
        return json.loads(resp.content)

    async def _store_onboarding_facts(
        self,
        memory,
        answers: dict,
        browser_summary: dict,
        profile: dict,
        run_id: str,
    ) -> list[dict]:
        stored: list[dict] = []
        for key, category, template in [
            ("roles", "role", "你确认自己的角色包含 {value}"),
            ("goals", "goal", "你希望 Kith 帮你：{value}"),
            ("current_focus", "current_focus", "你近期关注：{value}"),
            ("interests", "interest.explicit", "你明确提到对 {value} 感兴趣"),
        ]:
            for value in self._as_list(answers.get(key))[:12]:
                statement = template.format(value=value)
                fact_id = self._fact_id(category, statement)
                await memory.upsert_profile_fact(
                    fact_id=fact_id,
                    category=category,
                    statement=statement,
                    source_type="user_confirmed",
                    source_ref=run_id,
                    confidence=0.95,
                    status="confirmed",
                    metadata={"field": key},
                )
                stored.append({
                    "id": fact_id,
                    "category": category,
                    "statement": statement,
                    "confidence": 0.95,
                    "status": "confirmed",
                    "source_type": "user_confirmed",
                    "source_ref": run_id,
                })

        for topic in browser_summary.get("topics", [])[:10]:
            value = topic.get("topic")
            if not value:
                continue
            statement = f"你最近可能在关注 {value}"
            fact_id = self._fact_id("interest.browser", statement)
            await memory.upsert_profile_fact(
                fact_id=fact_id,
                category="interest.browser",
                statement=statement,
                source_type="browser_history",
                source_ref=run_id,
                confidence=0.55,
                status="inferred",
                metadata={"count": topic.get("count", 0)},
            )
            stored.append({
                "id": fact_id,
                "category": "interest.browser",
                "statement": statement,
                "confidence": 0.55,
                "status": "inferred",
                "source_type": "browser_history",
                "source_ref": run_id,
            })

        identity = profile.get("identity", {}) if isinstance(profile, dict) else {}
        summary = identity.get("summary") if isinstance(identity, dict) else None
        if summary:
            statement = str(summary)
            fact_id = self._fact_id("identity.quick", statement)
            await memory.upsert_profile_fact(
                fact_id=fact_id,
                category="identity.quick",
                statement=statement,
                source_type="quick_onboarding",
                source_ref=run_id,
                confidence=0.7,
                status="inferred",
                metadata={"field": "identity.summary"},
            )
            stored.append({
                "id": fact_id,
                "category": "identity.quick",
                "statement": statement,
                "confidence": 0.7,
                "status": "inferred",
                "source_type": "quick_onboarding",
                "source_ref": run_id,
            })
        return stored

    async def _store_first_insight_sources(
        self,
        memory,
        run_id: str,
        browser_summary: dict,
        recent_files: list[dict],
        folder_summary: list[dict],
    ) -> list[dict]:
        sources: list[dict] = []

        for domain in browser_summary.get("top_domains", [])[:20]:
            name = domain.get("domain")
            if not name:
                continue
            record_id = self._fact_id("source.browser_domain", f"{run_id}:{name}")
            await memory.upsert_source_record(
                record_id=record_id,
                source_type="browser_domain",
                source_ref=run_id,
                title=name,
                domain=name,
                metadata={"count": domain.get("count", 0)},
            )
            sources.append({"id": record_id, "source_type": "browser_domain", "domain": name, "count": domain.get("count", 0)})

        for bookmark in browser_summary.get("bookmarks", [])[:20]:
            title = bookmark.get("title") or bookmark.get("domain") or ""
            domain = bookmark.get("domain") or ""
            if not title and not domain:
                continue
            record_id = self._fact_id("source.bookmark", f"{run_id}:{title}:{domain}")
            await memory.upsert_source_record(
                record_id=record_id,
                source_type="browser_bookmark",
                source_ref=run_id,
                title=title,
                domain=domain,
                metadata={"folder": bookmark.get("folder", "")},
            )
            sources.append({"id": record_id, "source_type": "browser_bookmark", "title": title, "domain": domain})

        for download in browser_summary.get("downloads", [])[:20]:
            target = download.get("target_path") or ""
            domain = download.get("domain") or ""
            if not target and not domain:
                continue
            record_id = self._fact_id("source.download", f"{run_id}:{target}:{domain}")
            await memory.upsert_source_record(
                record_id=record_id,
                source_type="browser_download",
                source_ref=run_id,
                title=Path(target).name if target else domain,
                domain=domain,
                path=target,
                occurred_at=float(download.get("start_time") or 0),
            )
            sources.append({"id": record_id, "source_type": "browser_download", "path": target, "domain": domain})

        for file_info in recent_files[:20]:
            path = file_info.get("path") or ""
            if not path:
                continue
            record_id = self._fact_id("source.recent_file", f"{run_id}:{path}")
            await memory.upsert_source_record(
                record_id=record_id,
                source_type="recent_file",
                source_ref=run_id,
                title=Path(path).name,
                path=path,
                occurred_at=float(file_info.get("modified_at") or 0),
                metadata={"file_type": file_info.get("file_type", "")},
            )
            sources.append({"id": record_id, "source_type": "recent_file", "path": path, "file_type": file_info.get("file_type", "")})

        for folder in folder_summary[:10]:
            path = folder.get("path") or ""
            if not path:
                continue
            record_id = self._fact_id("source.folder", f"{run_id}:{path}")
            await memory.upsert_source_record(
                record_id=record_id,
                source_type="folder_activity",
                source_ref=run_id,
                title=path,
                path=path,
                metadata={"count": folder.get("count", 0)},
            )
            sources.append({"id": record_id, "source_type": "folder_activity", "path": path, "count": folder.get("count", 0)})

        return sources[:80]

    async def _store_first_insight_items(
        self,
        memory,
        run_id: str,
        topics: list[dict],
        suggestions: list[dict],
    ) -> None:
        for topic in topics:
            statement = f"最近关注主题：{topic.get('topic')}"
            await memory.upsert_insight_item(
                item_id=self._fact_id("insight.topic", f"{run_id}:{statement}"),
                run_id=run_id,
                item_type="topic",
                statement=statement,
                source_type=topic.get("source_type", ""),
                source_ref=run_id,
                confidence=float(topic.get("confidence", 0.5)),
                status="inferred",
                metadata=topic,
            )
        for suggestion in suggestions:
            await memory.upsert_insight_item(
                item_id=suggestion["id"],
                run_id=run_id,
                item_type="suggestion",
                statement=suggestion["statement"],
                source_type=suggestion.get("source_type", ""),
                source_ref=run_id,
                confidence=float(suggestion.get("confidence", 0.6)),
                status="inferred",
                metadata=suggestion,
            )

    def _folder_structure_summary(self, recent_files: list[dict]) -> list[dict]:
        counts: dict[str, int] = {}
        for f in recent_files:
            path = str(f.get("path") or "")
            if not path:
                continue
            parent = str(Path(path).parent)
            home = str(Path.home())
            if parent.startswith(home):
                parent = "~" + parent[len(home):]
            counts[parent] = counts.get(parent, 0) + 1
        return [
            {"path": path, "count": count}
            for path, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:12]
        ]

    def _first_insight_topics(self, profile: dict, browser_summary: dict) -> list[dict]:
        topics: list[dict] = []
        seen: set[str] = set()
        for topic in browser_summary.get("topics", [])[:12]:
            value = str(topic.get("topic") or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            topics.append({
                "topic": value,
                "count": topic.get("count", 0),
                "source_type": "browser_history",
                "confidence": 0.55,
            })

        interests = profile.get("interests", {}) if isinstance(profile, dict) else {}
        for value in self._as_list(interests.get("current_focus")) + self._as_list(interests.get("explicit")):
            if value in seen:
                continue
            seen.add(value)
            topics.append({
                "topic": value,
                "source_type": "user_confirmed",
                "confidence": 0.9,
            })
        return topics[:12]

    def _first_insight_suggestions(
        self,
        answers: dict,
        profile: dict,
        browser_summary: dict,
        folder_summary: list[dict],
    ) -> list[dict]:
        suggestions: list[dict] = []

        def add(statement: str, reason: str, source_type: str, confidence: float = 0.65) -> None:
            if not statement:
                return
            suggestions.append({
                "id": self._fact_id("suggestion", statement),
                "statement": statement,
                "reason": reason,
                "source_type": source_type,
                "confidence": confidence,
                "status": "inferred",
            })

        focus = self._as_list(answers.get("current_focus"))
        goals = self._as_list(answers.get("goals"))
        if focus:
            add(
                f"今天先围绕「{focus[0]}」列一个 3 步推进清单。",
                "来自你的初始化回答，适合作为 First Insight 的立即行动项。",
                "user_confirmed",
                0.85,
            )
        if goals:
            add(
                f"把「{goals[0]}」拆成一个本周可执行计划，并让我每天检查一次进展。",
                "来自你的目标偏好，属于可纠正的建议。",
                "user_confirmed",
                0.8,
            )

        top_domains = browser_summary.get("top_domains", [])
        if top_domains:
            domain = top_domains[0].get("domain")
            if domain:
                add(
                    f"你最近频繁查看 {domain}，可以让我帮你整理其中最相关的主题和资料。",
                    "来自浏览历史的域名聚合，不包含 cookies 或登录态。",
                    "browser_history",
                    0.6,
                )

        if folder_summary:
            folder = folder_summary[0].get("path")
            if folder:
                add(
                    f"你最近活跃在 {folder}，建议先整理这个工作区的待办和关键文件。",
                    "来自最近修改文件的目录聚合。",
                    "recent_file",
                    0.6,
                )

        add(
            "检查这些初步记忆，点准确 / 不准 / 别记这个，之后我会按你的反馈修正。",
            "First Insight 应该可纠正，而不是一次性定论。",
            "product_guidance",
            0.9,
        )
        return suggestions[:5]

    def _onboarding_next_actions(self, profile: dict, browser_summary: dict) -> list[str]:
        actions = ["先给出一份今天/本周的轻量规划建议"]
        if not browser_summary.get("entries_count"):
            actions.append("如用户同意，可导入浏览历史聚合来增强兴趣画像")
        if profile.get("goals"):
            actions.append("围绕用户目标生成 3 个可执行下一步")
        actions.append("继续后台索引文件，稍后刷新深度画像")
        return actions

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, list):
            items = value
        else:
            items = [str(value)]
        return [str(item).strip() for item in items if str(item).strip()]

    @staticmethod
    def _fact_id(category: str, statement: str) -> str:
        digest = hashlib.sha1(f"{category}:{statement}".encode("utf-8")).hexdigest()[:16]
        return f"onboarding_{digest}"

    def _decode_entry(self, entry: dict) -> Any:
        try:
            return json.loads(entry.get("content", ""))
        except Exception:
            return entry.get("content", "")

    def _context_digest(self, packet: dict) -> dict:
        return {
            "confirmed_facts": len(packet.get("confirmed_facts", [])),
            "inferred_facts": len(packet.get("inferred_facts", [])),
            "recent_files": len(packet.get("recent_files", [])),
            "retrieved_evidence": len(packet.get("retrieved_evidence", [])),
        }

    def _fallback_answer(self, message: str, packet: dict) -> str:
        confirmed = packet.get("confirmed_facts", [])
        inferred = packet.get("inferred_facts", [])
        recent = packet.get("recent_files", [])
        lines = [
            "我现在处在本地轻量模式，只能基于已整理的事实做保守回答。",
            "",
        ]
        if confirmed:
            lines.append("我确定知道的：")
            lines.extend(f"- {fact['statement']}" for fact in confirmed[:5])
        if inferred:
            lines.append("我推断到的：")
            lines.extend(f"- {fact['statement']}" for fact in inferred[:7])
        if recent:
            lines.append(f"最近一周我看到 {len(recent)} 个活跃文件，这能帮助我判断你的当前关注点。")
        if not confirmed and not inferred:
            lines.append("目前证据还不够。你可以先在 Sources & Privacy 里选择资料范围，然后生成画像。")
        lines.append("")
        lines.append(f"关于“{message}”，我建议先生成或校正画像，这样我会更像真正了解你的 Jarvis。")
        return "\n".join(lines)
