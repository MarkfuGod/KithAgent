"""
Web Dashboard — visual debugging console for AgentOS.

Reads directly from ~/.agent_sys/memory.db (works even without daemon running).
Optionally connects to the running daemon for live status.

Usage:
    agent-sys dashboard            # start on default port 7438
    agent-sys dashboard --port 9000
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from aiohttp import web

logger = logging.getLogger("agent_sys.dashboard")

DB_PATH = Path.home() / ".agent_sys" / "memory.db"
LLM_CONFIG_PATH = Path.home() / ".agent_sys" / "llm_config.yaml"
_DAEMON_HTTP_PORT = 7437


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _safe_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


# ── API handlers ──────────────────────────────────────────────

async def api_overview(request: web.Request) -> web.Response:
    db = _get_db()
    try:
        total_files = db.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
        summarized = db.execute(
            "SELECT COUNT(*) FROM file_index WHERE semantic_summary != '' AND semantic_summary IS NOT NULL"
        ).fetchone()[0]
        knowledge_count = db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]

        type_dist = db.execute(
            "SELECT file_type, COUNT(*) as cnt FROM file_index GROUP BY file_type ORDER BY cnt DESC"
        ).fetchall()

        priority_dist = db.execute(
            "SELECT priority, COUNT(*) as cnt FROM file_index GROUP BY priority ORDER BY priority"
        ).fetchall()

        total_size = db.execute("SELECT SUM(size_bytes) FROM file_index").fetchone()[0] or 0

        recent_24h = db.execute(
            "SELECT COUNT(*) FROM file_index WHERE modified_at > ?",
            (time.time() - 86400,)
        ).fetchone()[0]

        # daemon status
        daemon_status = None
        pid_file = Path("/tmp/agent_sys.pid")
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                daemon_status = {"running": True, "pid": pid}
            except (ProcessLookupError, ValueError):
                daemon_status = {"running": False, "pid": None}
        else:
            daemon_status = {"running": False, "pid": None}

        return web.json_response({
            "total_files": total_files,
            "summarized_files": summarized,
            "unsummarized_files": total_files - summarized,
            "knowledge_entries": knowledge_count,
            "total_size_bytes": total_size,
            "recent_24h_modified": recent_24h,
            "file_type_distribution": [
                {"type": r[0] or "unknown", "count": r[1]} for r in type_dist
            ],
            "priority_distribution": [
                {"priority": r[0], "count": r[1]} for r in priority_dist
            ],
            "daemon": daemon_status,
        })
    finally:
        db.close()


async def api_directory_tree(request: web.Request) -> web.Response:
    depth = int(request.query.get("depth", "2"))
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT path, file_type, size_bytes FROM file_index"
        ).fetchall()

        from collections import defaultdict
        dir_stats: dict[str, dict] = defaultdict(lambda: {
            "code": 0, "document": 0, "image": 0, "data": 0, "config": 0, "other": 0,
            "total": 0, "total_size": 0,
        })

        _code = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".java", ".c", ".cpp", ".swift", ".kt", ".rb", ".php"}
        _doc = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".md", ".txt", ".rst", ".tex"}
        _img = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
        _data = {".csv", ".xml"}
        _config = {".json", ".yaml", ".yml", ".toml"}

        home = str(Path.home())
        for path, ftype, size in rows:
            rel = path[len(home):] if path.startswith(home) else path
            parts = rel.strip("/").split("/")
            dir_key = "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "~"

            s = dir_stats[dir_key]
            s["total"] += 1
            s["total_size"] += (size or 0)

            ext = (ftype or "").lower()
            if ext in _code:
                s["code"] += 1
            elif ext in _doc:
                s["document"] += 1
            elif ext in _img:
                s["image"] += 1
            elif ext in _data:
                s["data"] += 1
            elif ext in _config:
                s["config"] += 1
            else:
                s["other"] += 1

        result = []
        for d, s in sorted(dir_stats.items(), key=lambda x: x[1]["total"], reverse=True):
            s["directory"] = d
            result.append(s)

        return web.json_response(result[:80])
    finally:
        db.close()


async def api_knowledge(request: web.Request) -> web.Response:
    category = request.query.get("category")
    limit = int(request.query.get("limit", "30"))
    db = _get_db()
    try:
        if category:
            rows = db.execute(
                "SELECT id, category, content, source_path, created_at, updated_at FROM knowledge WHERE category = ? ORDER BY updated_at DESC LIMIT ?",
                (category, limit)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, category, content, source_path, created_at, updated_at FROM knowledge ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()

        entries = []
        for r in rows:
            entries.append({
                "id": r[0], "category": r[1],
                "content": _safe_json(r[2]),
                "source": r[3],
                "created_at": r[4], "updated_at": r[5],
            })

        categories = db.execute(
            "SELECT category, COUNT(*) FROM knowledge GROUP BY category ORDER BY COUNT(*) DESC"
        ).fetchall()

        return web.json_response({
            "entries": entries,
            "categories": [{"name": c[0], "count": c[1]} for c in categories],
        })
    finally:
        db.close()


async def api_recent_files(request: web.Request) -> web.Response:
    hours = float(request.query.get("hours", "24"))
    limit = int(request.query.get("limit", "100"))
    db = _get_db()
    try:
        cutoff = time.time() - (hours * 3600)
        rows = db.execute(
            """SELECT path, file_type, modified_at, size_bytes, priority,
                      COALESCE(semantic_summary, '') as semantic_summary
               FROM file_index WHERE modified_at > ?
               ORDER BY modified_at DESC LIMIT ?""",
            (cutoff, limit)
        ).fetchall()

        home = str(Path.home())
        return web.json_response([
            {
                "path": r[0].replace(home, "~"),
                "full_path": r[0],
                "type": r[1], "modified_at": r[2],
                "size": r[3], "priority": r[4],
                "summary": r[5][:200] if r[5] else "",
            }
            for r in rows
        ])
    finally:
        db.close()


async def api_scheduling(request: web.Request) -> web.Response:
    limit = int(request.query.get("limit", "20"))
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT id, content, created_at FROM knowledge WHERE category = 'scheduling_decision' ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()

        return web.json_response([
            {"id": r[0], "decision": _safe_json(r[1]), "time": r[2]}
            for r in rows
        ])
    finally:
        db.close()


async def api_summary_progress(request: web.Request) -> web.Response:
    db = _get_db()
    try:
        total = db.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
        done = db.execute(
            "SELECT COUNT(*) FROM file_index WHERE semantic_summary != '' AND semantic_summary IS NOT NULL"
        ).fetchone()[0]

        by_type = db.execute(
            """SELECT file_type,
                      COUNT(*) as total,
                      SUM(CASE WHEN semantic_summary != '' AND semantic_summary IS NOT NULL THEN 1 ELSE 0 END) as done
               FROM file_index
               GROUP BY file_type
               ORDER BY total DESC"""
        ).fetchall()

        return web.json_response({
            "total": total,
            "summarized": done,
            "pending": total - done,
            "percent": round(done / total * 100, 1) if total > 0 else 0,
            "by_type": [
                {"type": r[0] or "unknown", "total": r[1], "done": r[2],
                 "percent": round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0}
                for r in by_type
            ],
        })
    finally:
        db.close()


async def api_file_search(request: web.Request) -> web.Response:
    q = request.query.get("q", "")
    if not q or len(q) < 2:
        return web.json_response([])

    home = str(Path.home())
    use_vector = len(q.split()) >= 3

    if use_vector:
        try:
            from src.memory.embeddings import embed_text, is_available, cosine_similarity
            if is_available():
                q_emb = embed_text(q)
                if q_emb:
                    db = _get_db()
                    try:
                        rows = db.execute(
                            """SELECT path, file_type, size_bytes, priority,
                                      COALESCE(semantic_summary, '') as ss,
                                      COALESCE(summary, '') as s, embedding
                               FROM file_index
                               WHERE embedding IS NOT NULL AND embedding != ''"""
                        ).fetchall()
                        scored = []
                        for r in rows:
                            try:
                                score = cosine_similarity(q_emb, r[6])
                                scored.append((score, r))
                            except Exception:
                                continue
                        scored.sort(key=lambda x: x[0], reverse=True)
                        results = [
                            {
                                "path": r[0].replace(home, "~"),
                                "type": r[1], "size": r[2], "priority": r[3],
                                "summary": (r[4] or r[5] or "")[:200],
                                "score": round(score, 4),
                                "search_mode": "vector",
                            }
                            for score, r in scored[:50]
                            if score > 0.25
                        ]
                        if results:
                            return web.json_response(results)
                    finally:
                        db.close()
        except Exception as e:
            logger.warning("Vector search failed, falling back to LIKE: %s", e)

    db = _get_db()
    try:
        rows = db.execute(
            """SELECT path, file_type, size_bytes, priority,
                      COALESCE(semantic_summary, '') as ss, COALESCE(summary, '') as s
               FROM file_index
               WHERE path LIKE ? OR summary LIKE ? OR semantic_summary LIKE ?
               ORDER BY priority ASC, modified_at DESC LIMIT 50""",
            (f"%{q}%", f"%{q}%", f"%{q}%")
        ).fetchall()

        return web.json_response([
            {
                "path": r[0].replace(home, "~"),
                "type": r[1], "size": r[2], "priority": r[3],
                "summary": (r[4] or r[5] or "")[:200],
                "search_mode": "keyword",
            }
            for r in rows
        ])
    finally:
        db.close()


async def api_triage(request: web.Request) -> web.Response:
    db = _get_db()
    try:
        # Check if triage_status column exists
        cols = {r[1] for r in db.execute("PRAGMA table_info(file_index)").fetchall()}
        if "triage_status" not in cols:
            return web.json_response({"available": False})

        stats = db.execute(
            """SELECT
                 CASE WHEN triage_status = '' OR triage_status IS NULL THEN 'untriaged'
                      ELSE triage_status END as status,
                 COUNT(*) as cnt
               FROM file_index GROUP BY status ORDER BY cnt DESC"""
        ).fetchall()

        # Top directories by triage status
        by_dir = db.execute(
            """SELECT triage_status, COUNT(*) as cnt,
                      SUBSTR(path, 1, INSTR(SUBSTR(path, 2), '/') + 1) as top_dir
               FROM file_index
               WHERE triage_status != '' AND triage_status IS NOT NULL
               GROUP BY triage_status, top_dir
               ORDER BY cnt DESC LIMIT 30"""
        ).fetchall()

        return web.json_response({
            "available": True,
            "distribution": {r[0]: r[1] for r in stats},
            "by_directory": [
                {"status": r[0], "count": r[1], "directory": r[2]}
                for r in by_dir
            ],
        })
    finally:
        db.close()


async def api_llm_config_get(request: web.Request) -> web.Response:
    """Read current LLM configuration."""
    import yaml

    if not LLM_CONFIG_PATH.exists():
        return web.json_response({"exists": False, "config": {}})

    try:
        with open(LLM_CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}

        safe_config = {
            "default_provider": config.get("default_provider", ""),
            "providers": {},
        }
        for name, pcfg in config.get("providers", {}).items():
            safe_config["providers"][name] = {
                "base_url": pcfg.get("base_url", ""),
                "api_key_env": pcfg.get("api_key_env", ""),
                "models": pcfg.get("models", {}),
                "has_key": bool(config.get("env_vars", {}).get(pcfg.get("api_key_env", ""))),
            }

        return web.json_response({"exists": True, "config": safe_config})
    except Exception as e:
        return web.json_response({"exists": False, "error": str(e)})


async def api_llm_config_save(request: web.Request) -> web.Response:
    """Save LLM configuration changes."""
    import yaml

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        existing = {}
        if LLM_CONFIG_PATH.exists():
            with open(LLM_CONFIG_PATH) as f:
                existing = yaml.safe_load(f) or {}

        if "default_provider" in body:
            existing["default_provider"] = body["default_provider"]

        if "providers" in body:
            if "providers" not in existing:
                existing["providers"] = {}
            for name, pcfg in body["providers"].items():
                if name not in existing["providers"]:
                    existing["providers"][name] = {}
                p = existing["providers"][name]
                if "base_url" in pcfg:
                    p["base_url"] = pcfg["base_url"]
                if "api_key_env" in pcfg:
                    p["api_key_env"] = pcfg["api_key_env"]
                if "models" in pcfg:
                    p["models"] = pcfg["models"]

        if "env_vars" in body:
            if "env_vars" not in existing:
                existing["env_vars"] = {}
            for k, v in body["env_vars"].items():
                if v:
                    existing["env_vars"][k] = v

        if "delete_provider" in body:
            pname = body["delete_provider"]
            existing.get("providers", {}).pop(pname, None)

        LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LLM_CONFIG_PATH, "w") as f:
            yaml.dump(existing, f, default_flow_style=False)
        os.chmod(str(LLM_CONFIG_PATH), 0o600)

        return web.json_response({"success": True, "message": "Config saved. Restart daemon to apply."})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_scheduling_strategy(request: web.Request) -> web.Response:
    """Get available scheduling strategies and current selection."""
    from src.kernel.cron import SCHEDULING_STRATEGIES

    import yaml
    config_path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
    current = "balanced"
    try:
        if config_path.exists():
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            current = raw.get("cron", {}).get("adaptive", {}).get("strategy", "balanced")
    except Exception:
        pass

    strategies = []
    for name, s in SCHEDULING_STRATEGIES.items():
        entry = {"name": name, "description": s.get("description", "")}
        entry["policies"] = {k: v for k, v in s.items() if k != "description"}
        strategies.append(entry)

    return web.json_response({
        "current": current,
        "strategies": strategies,
    })


async def api_scheduling_strategy_set(request: web.Request) -> web.Response:
    """Update the scheduling strategy in default.yaml."""
    import yaml
    from src.kernel.cron import SCHEDULING_STRATEGIES

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    name = body.get("strategy", "")
    if name not in SCHEDULING_STRATEGIES:
        return web.json_response({"error": f"Unknown strategy: {name}"}, status=400)

    config_path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

        raw.setdefault("cron", {}).setdefault("adaptive", {})["strategy"] = name

        with open(config_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return web.json_response({"success": True, "strategy": name, "message": "Strategy updated. Restart daemon to apply."})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_embedding_info(request: web.Request) -> web.Response:
    """Return current embedding provider info."""
    from src.memory import embeddings
    return web.json_response(embeddings.get_provider_info())


async def api_llm_routing_get(request: web.Request) -> web.Response:
    """Read per-function LLM routing + defaults + providers from default.yaml."""
    import yaml
    config_path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        llm = raw.get("llm", {})
        providers = {}
        for pname, pcfg in llm.get("providers", {}).items():
            tiers = list((pcfg.get("models") or {}).keys())
            providers[pname] = {"tiers": tiers, "models": pcfg.get("models", {})}
        return web.json_response({
            "default_provider": llm.get("default_provider", ""),
            "providers": providers,
            "defaults": llm.get("defaults", {}),
            "functions": llm.get("functions", {}),
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_llm_routing_save(request: web.Request) -> web.Response:
    """Update per-function LLM routing and/or defaults in default.yaml."""
    import yaml
    config_path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

        llm = raw.setdefault("llm", {})

        if "defaults" in body:
            llm["defaults"] = body["defaults"]
        if "functions" in body:
            llm["functions"] = body["functions"]

        with open(config_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return web.json_response({"success": True, "message": "Routing updated. Restart daemon to apply."})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_embedding_config_get(request: web.Request) -> web.Response:
    """Read embedding config from default.yaml."""
    import yaml
    from src.memory import embeddings
    config_path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        emb_cfg = raw.get("memory", {}).get("embedding", {})
        live_info = embeddings.get_provider_info()
        return web.json_response({
            "config": emb_cfg,
            "live": live_info,
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_embedding_config_save(request: web.Request) -> web.Response:
    """Update embedding config in default.yaml."""
    import yaml
    config_path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

        raw.setdefault("memory", {})["embedding"] = body.get("embedding", {})

        with open(config_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return web.json_response({"success": True, "message": "Embedding config updated. Restart daemon to apply."})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_daemon_status(request: web.Request) -> web.Response:
    """Try to get live status from the running daemon."""
    try:
        import aiohttp as ah
        async with ah.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{_DAEMON_HTTP_PORT}/status", timeout=ah.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return web.json_response({"connected": True, **data})
    except Exception:
        pass
    return web.json_response({"connected": False})


async def api_trigger_agent(request: web.Request) -> web.Response:
    """Manually trigger an agent via the daemon's syscall HTTP endpoint."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    agent_name = body.get("agent", "")
    input_data = body.get("input_data", {})

    if not agent_name:
        return web.json_response({"error": "Missing 'agent' parameter"}, status=400)

    allowed_agents = {
        "triage", "summarizer", "behavior_analyzer",
        "priority_classifier", "report_generator", "profile_builder",
    }
    if agent_name not in allowed_agents:
        return web.json_response({"error": f"Agent '{agent_name}' not in allowed list"}, status=400)

    syscall_payload = {
        "call_type": "agent.submit",
        "params": {
            "agent_name": agent_name,
            "input_data": input_data,
        },
        "caller": "dashboard",
    }

    try:
        import aiohttp as ah
        async with ah.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{_DAEMON_HTTP_PORT}/syscall",
                json=syscall_payload,
                timeout=ah.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("success"):
                    return web.json_response({
                        "success": True,
                        "task_id": data.get("data", {}).get("task_id"),
                        "message": f"Agent '{agent_name}' submitted successfully",
                    })
                return web.json_response({
                    "success": False,
                    "error": data.get("error", "Unknown error from daemon"),
                })
    except Exception as e:
        return web.json_response({
            "success": False,
            "error": f"Cannot reach daemon: {e}. Is it running?",
        }, status=503)


async def api_reload_config(request: web.Request) -> web.Response:
    """Ask the daemon to hot-reload its config (LLM router, embeddings, strategy)."""
    try:
        import aiohttp as ah
        async with ah.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{_DAEMON_HTTP_PORT}/reload",
                timeout=ah.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("reloaded"):
                    return web.json_response({
                        "success": True,
                        "message": "Config reloaded successfully",
                        **data,
                    })
                return web.json_response({"success": False, "error": data.get("error", "Unknown")})
    except Exception as e:
        return web.json_response({
            "success": False,
            "error": f"Cannot reach daemon: {e}. Is it running?",
        }, status=503)


async def api_events_sse(request: web.Request) -> web.StreamResponse:
    """SSE endpoint — streams real-time events from the daemon's EventBus.

    When running standalone (no in-process event_bus), proxies SSE from
    the daemon's HTTP endpoint at 127.0.0.1:7437/events.
    """
    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await resp.prepare(request)

    event_bus = request.app.get("event_bus")
    if event_bus:
        queue = event_bus.subscribe(replay_buffer=False)
        try:
            recent = event_bus.recent_events(limit=30)
            for evt in recent:
                line = json.dumps(evt, default=str)
                await resp.write(f"event: {evt['type']}\ndata: {line}\n\n".encode())

            while True:
                try:
                    import asyncio
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    await resp.write(event.to_sse().encode())
                except asyncio.TimeoutError:
                    await resp.write(b": keepalive\n\n")
                except ConnectionResetError:
                    break
        finally:
            event_bus.unsubscribe(queue)
        return resp

    # Standalone mode: proxy SSE from the daemon's HTTP server
    try:
        import aiohttp as ah
        async with ah.ClientSession() as session:
            daemon_url = f"http://127.0.0.1:{_DAEMON_HTTP_PORT}/events"
            async with session.get(daemon_url, timeout=ah.ClientTimeout(total=0, sock_read=0)) as upstream:
                if upstream.status != 200:
                    await resp.write(
                        b"event: error\ndata: {\"msg\":\"Daemon not reachable for SSE proxy\"}\n\n"
                    )
                    return resp

                await resp.write(
                    b"event: info\ndata: {\"msg\":\"Connected to daemon SSE (proxy mode)\"}\n\n"
                )
                async for chunk in upstream.content.iter_any():
                    try:
                        await resp.write(chunk)
                    except ConnectionResetError:
                        break
    except Exception as e:
        msg = json.dumps({"msg": f"SSE proxy failed: {e}"})
        await resp.write(f"event: error\ndata: {msg}\n\n".encode())

    return resp


async def serve_dashboard(request: web.Request) -> web.Response:
    html_path = Path(__file__).parent / "dashboard.html"
    return web.Response(text=html_path.read_text(), content_type="text/html")


async def serve_static(request: web.Request) -> web.Response:
    filename = request.match_info["filename"]
    static_dir = Path(__file__).parent
    safe_path = (static_dir / filename).resolve()
    if not str(safe_path).startswith(str(static_dir.resolve())):
        return web.Response(status=403)
    if not safe_path.exists():
        return web.Response(status=404)
    content_types = {".js": "application/javascript", ".css": "text/css", ".png": "image/png"}
    ct = content_types.get(safe_path.suffix, "application/octet-stream")
    return web.Response(body=safe_path.read_bytes(), content_type=ct)


def create_app(event_bus=None) -> web.Application:
    app = web.Application()
    if event_bus:
        app["event_bus"] = event_bus
    app.router.add_get("/", serve_dashboard)
    app.router.add_get("/static/{filename}", serve_static)
    app.router.add_get("/api/overview", api_overview)
    app.router.add_get("/api/directories", api_directory_tree)
    app.router.add_get("/api/knowledge", api_knowledge)
    app.router.add_get("/api/recent", api_recent_files)
    app.router.add_get("/api/scheduling", api_scheduling)
    app.router.add_get("/api/summary-progress", api_summary_progress)
    app.router.add_get("/api/search", api_file_search)
    app.router.add_get("/api/triage", api_triage)
    app.router.add_get("/api/llm-config", api_llm_config_get)
    app.router.add_post("/api/llm-config", api_llm_config_save)
    app.router.add_get("/api/daemon", api_daemon_status)
    app.router.add_get("/api/events", api_events_sse)
    app.router.add_get("/api/scheduling-strategy", api_scheduling_strategy)
    app.router.add_post("/api/scheduling-strategy", api_scheduling_strategy_set)
    app.router.add_get("/api/embedding-info", api_embedding_info)
    app.router.add_get("/api/llm-routing", api_llm_routing_get)
    app.router.add_post("/api/llm-routing", api_llm_routing_save)
    app.router.add_get("/api/embedding-config", api_embedding_config_get)
    app.router.add_post("/api/embedding-config", api_embedding_config_save)
    app.router.add_post("/api/trigger-agent", api_trigger_agent)
    app.router.add_post("/api/reload-config", api_reload_config)
    return app


def run_dashboard(port: int = 7438) -> None:
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        print("Start the daemon first with: agent-sys start")
        return

    app = create_app()
    print(f"""
╔══════════════════════════════════════════════════╗
║         AgentOS — Dashboard                      ║
║                                                  ║
║   URL:  http://127.0.0.1:{port:<5}                  ║
║   DB:   {str(DB_PATH):<40} ║
║                                                  ║
║   Open in your browser to explore.               ║
║   Press Ctrl+C to stop.                          ║
╚══════════════════════════════════════════════════╝
    """)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", "-p", type=int, default=7438)
    args = p.parse_args()
    run_dashboard(args.port)
