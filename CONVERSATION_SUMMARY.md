# AgentOS — 对话总结

## 项目是什么

AgentOS 是一个 **系统级 Agent 守护进程**，核心思想是把传统操作系统的架构映射到 LLM Agent 系统上。

它开机即运行、常驻后台，持续索引用户的文件和数据，构建个人知识库。当用户使用 Cursor、Claude Code、OpenClaw 等 Agent 工具时，它们可以直接调用 SysAgent 的 API（Unix Socket / HTTP），无需每次重新扫描文件系统。

### 核心映射

| 传统 OS | AgentOS |
|---|---|
| 内核 (Kernel) | `SysAgentKernel` — 守护进程，管理所有子系统 |
| 线程 (Thread) | `AgentTask` — 最小执行单元 |
| 进程调度器 | `AgentScheduler` — 优先级队列 + 并发控制 |
| 文件系统 (VFS) | `FileSystemWatcher` — 实时监控 + 定时全量扫描 |
| 内存/缓存 | `MemoryStore` — LRU 热缓存 + SQLite 冷存储 |
| 系统调用 | `SyscallServer` — Unix Socket / HTTP API |
| Cron 定时器 | `CronScheduler` — 定时触发智能 Agent |

---

## 这次对话做了什么

### v0.1（第一轮对话）— 从零搭建骨架

1. **设计了整体架构**，创建了项目目录结构
2. **实现了 6 个核心模块**：
   - `src/kernel/` — 守护进程 + 配置加载
   - `src/filesystem/` — 文件监控与索引（watchdog 实时 + 定时全扫）
   - `src/memory/` — LRU 缓存 + SQLite 持久化
   - `src/scheduler/` — 优先级队列 + 并发池
   - `src/syscall/` — 协议定义 + Socket/HTTP 服务端 + 客户端 SDK
   - `src/agents/` — 6 个规则驱动的内置 Agent
3. **成功启动**：索引了用户 **160,429 个文件**
4. **修复了 watchdog bug**：子线程无法获取 event loop 的问题

### v0.2（第二轮对话）— 从索引到理解

按照 [plan](/.cursor/plans/agentos_llm_evolution_fdac4bb4.plan.md) 实现了 3 个阶段：

#### Phase 1: LLM 统一抽象层

- **`src/llm/base.py`** — `LLMProvider` 统一接口
- **`src/llm/openai_adapter.py`** — 支持 GPT-4o / GPT-4.1（SDK 或 HTTP fallback）
- **`src/llm/claude_adapter.py`** — 支持 Claude Sonnet / Opus（SDK 或 HTTP fallback）
- **`src/llm/compatible_adapter.py`** — 任何 OpenAI 兼容 API（DeepSeek, Ollama, Groq...）
- **`src/llm/router.py`** — `ModelRouter` 按任务类型路由到 provider + model tier
- **配置**：`config/default.yaml` 新增 `llm:` 段（provider、model、routing 全可配）

#### Phase 2: 智能 Agent + Cron 调度

- **`src/agents/summarizer.py`** — LLM 语义文件摘要（替代规则提取）
- **`src/agents/analyzer.py`** — 用户行为分析（活跃目录、语言偏好、工作模式）
- **`src/agents/prioritizer.py`** — 文件优先级分类（P0 热/P1 温/P2 冷）
- **`src/kernel/cron.py`** — `CronScheduler`，支持 5 种触发器（interval/daily/weekly/after_scan/after:agent）

#### Phase 3: 报告 + 画像 + 新 Syscall

- **`src/agents/reporter.py`** — 日报 / 项目画像 / 上下文简报
- **`src/agents/profile_builder.py`** — 个人画像（语言、框架、项目、编码风格）
- **新增 7 个 Syscall 类型**：`report.daily`, `report.project`, `report.brief`, `profile.get`, `analyze.behavior`, `classify.priority`, `summarize.file`
- **客户端 SDK** 更新：Async + Sync 各 7 个新方法
- **CLI 新增 5 个命令**：`report`, `profile`, `summarize`, `analyze`, `classify`

#### 数据库迁移

- `file_index` 表新增 3 列：`priority`, `semantic_summary`, `last_accessed_at`
- 自动迁移：`_migrate_schema()` 检测旧表并 ALTER TABLE
- `search_files` 现在按 priority 排序，同时搜索 semantic_summary

#### Bug 修复

- watchdog 子线程 event loop 问题（`asyncio.get_running_loop()` 捕获主线程 loop）
- `pyproject.toml` build-backend 路径错误
- SQLite 迁移顺序问题：先 ALTER TABLE 再 CREATE INDEX

### v0.2.1（第三轮对话）— LLM 启动引导

#### 交互式 LLM 选择

启动时自动检测是否有可用的 LLM provider，如果没有检测到（无 API Key），会交互式提示用户选择：

1. **本地 Ollama** — 指向 `localhost:11434`，用户可自选模型（llama3, mistral, gemma2 等）
2. **远程 API** — 输入 OpenAI / Anthropic / 其他兼容 API 的 key，当场注入环境变量
3. **跳过** — 不用 LLM，纯规则模式运行

涉及文件：
- **`src/cli.py`** — 新增 `_check_llm_and_prompt()`, `_setup_ollama()`, `_setup_api_key()` 三个函数
- **`src/llm/router.py`** — 新增 `check_llm_availability()` 工具函数

---

## 当前项目结构

```
agent_sys/
├── config/default.yaml              # 系统配置（含 LLM + Cron）
├── src/
│   ├── kernel/
│   │   ├── config.py                # 配置加载（含 LLMConfig, CronConfig）
│   │   ├── daemon.py                # 守护进程主循环
│   │   └── cron.py                  # 定时调度器 [v0.2]
│   ├── llm/                         # [v0.2] LLM 统一抽象层
│   │   ├── base.py                  # LLMProvider 接口
│   │   ├── openai_adapter.py        # OpenAI
│   │   ├── claude_adapter.py        # Claude
│   │   ├── compatible_adapter.py    # OpenAI 兼容 API
│   │   └── router.py               # 任务类型→模型路由
│   ├── filesystem/watcher.py        # 文件监控与索引
│   ├── memory/store.py              # LRU + SQLite（含迁移）
│   ├── scheduler/pool.py            # 优先级队列 + 并发池
│   ├── syscall/
│   │   ├── protocol.py              # 18 个 Syscall 类型
│   │   ├── server.py                # Socket/HTTP 服务端
│   │   └── client.py               # Async + Sync 客户端 SDK
│   ├── agents/
│   │   ├── base.py                  # AgentTask + BaseAgent
│   │   ├── builtin.py               # 注册全部 11 个 Agent
│   │   ├── summarizer.py            # [v0.2] LLM 文件摘要
│   │   ├── analyzer.py              # [v0.2] 行为分析
│   │   ├── prioritizer.py           # [v0.2] 优先级分类
│   │   ├── reporter.py              # [v0.2] 日报/画像/简报
│   │   └── profile_builder.py       # [v0.2] 个人画像
│   └── cli.py                       # 命令行入口（11 个子命令）
├── pyproject.toml
├── requirements.txt
└── README.md
```

## 如何运行

```bash
# 安装
pip install -e ".[full]"

# 启动（如果没有检测到 LLM API Key，会自动提示选择）
agent-sys start
# → 选 [1] 本地 Ollama（需先 ollama serve）
# → 选 [2] 输入 API Key（OpenAI / Anthropic / 其他）
# → 选 [3] 跳过，纯规则模式

# 或者提前设置好 key，就不会弹出选择
export OPENAI_API_KEY="sk-..."
# 或
export ANTHROPIC_API_KEY="sk-ant-..."
agent-sys start

# 新终端中使用
agent-sys ping
agent-sys status
agent-sys search "database"
agent-sys report daily
agent-sys profile
agent-sys analyze
agent-sys classify
```

## 下一步可做的

- 向量化语义搜索（embedding 替代 LIKE）
- 主动推送通知（异常检测）
- 对话记忆 Agent（跨会话记住历史）
- 自动化 Agent（检测重复操作，生成脚本）
- Ollama 深度适配（自动检测可用模型、health check、按模型能力选 tier）
- MLX 本地推理适配
- 插件系统（第三方注册自定义 Agent）
