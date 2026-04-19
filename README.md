# agent-sys

**一个常驻本地的文件索引 + LLM RPC 守护进程。**

agent-sys 在后台持续索引你选择的目录（默认 `~/Documents` 和 `~/Desktop`，首次启动会让你确认/修改），用 LLM 为文件打标签、做摘要、抽取知识，并通过 Unix Socket / HTTP 暴露一套查询接口。

当你用 Cursor、Claude Code 这类 Agent 工具时，它们可以直接调用 agent-sys，拿到关于你本地文件和工作习惯的上下文，而不必每次从零开始扫。

> 下面的模块/概念沿用了一些 OS 风格的命名（Kernel / Scheduler / Syscall / Cron / Memory），只是叙事方便——底层是再普通不过的"任务队列 + RPC + SQLite + LLM 路由"。不要把这套比喻当成硬约束。

## 模块速览

```
┌─────────────────────────────────────────────────────────────┐
│                    External Agents                          │
│            (Cursor / Claude Code / OpenClaw)                │
│                         │                                   │
│                    ┌────▼────┐                              │
│                    │ Syscall │  ← 系统调用层 (API)          │
│                    │  API    │    Unix Socket / HTTP         │
│                    └────┬────┘                              │
│                         │                                   │
│  ┌──────────────────────▼──────────────────────────┐       │
│  │              Kernel (SysAgent Daemon)            │       │
│  │                                                  │       │
│  │  ┌───────────┐  ┌───────────┐  ┌────────────┐  │       │
│  │  │ Scheduler │  │  Memory   │  │ FileSystem │  │       │
│  │  │ (调度器)   │  │ (记忆存储) │  │ (文件监控)  │  │       │
│  │  └─────┬─────┘  └───────────┘  └────────────┘  │       │
│  │        │                                        │       │
│  │  ┌─────▼─────────────────────────────────────┐  │       │
│  │  │           Smart Agent Pool                │  │       │
│  │  │ [Triage] [Summarizer] [Analyzer]          │  │       │
│  │  │ [Reporter] [ProfileBuilder] [Prioritizer] │  │       │
│  │  └─────────────────────┬─────────────────────┘  │       │
│  │                        │                        │       │
│  │  ┌─────────────────────▼─────────────────────┐  │       │
│  │  │        LLM Router (多模型路由)              │  │       │
│  │  │  fast ─→ qwen-plus / gpt-4o-mini          │  │       │
│  │  │  strong ─→ gpt-4o / claude-opus            │  │       │
│  │  │  vision ─→ qwen-vl-plus                   │  │       │
│  │  │  anthropic_compat ─→ MiniMax-M2.7          │  │       │
│  │  └───────────────────────────────────────────┘  │       │
│  │                                                  │       │
│  │  ┌───────────────────────────────────────────┐  │       │
│  │  │  Adaptive Cron (LLM 决定何时运行什么)       │  │       │
│  │  └───────────────────────────────────────────┘  │       │
│  └─────────────────────────────────────────────────┘       │
│                                                             │
│                  ~/.agent_sys/memory.db                     │
│                    (持久化知识库)                             │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Web Dashboard (http://127.0.0.1:7438)              │   │
│  │  可视化文件分布 · 报告浏览 · LLM 配置 · 分诊状态     │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

| 模块 | 实际是什么 | OS 比喻 |
|---|---|---|
| `SysAgentKernel` | 守护进程主入口，负责子系统生命周期 | Kernel / init |
| `AgentTask` | 调度器吃的最小工作单元 | Thread |
| `AgentScheduler` | 优先级队列 + semaphore + per-task timeout | 进程调度器 |
| `FileSystemWatcher` | `os.walk` 首次扫描 + `watchdog` 监听增量 | VFS |
| `MemoryStore` | LRU 热缓存 + SQLite 冷存储 | 内存/磁盘 |
| `SyscallServer` | Unix Socket / HTTP RPC（19 个端点，外部 agent 通常用 HTTP 更方便） | 系统调用 |
| `CronScheduler` | LLM 驱动的策略引擎 + 规则 fallback，决定"何时跑哪个 agent" | Cron |

## 快速开始

```bash
# 安装
cd agent_sys
pip install -e ".[full]"

# 安装多模态依赖（PDF/Word 文件解析）
pip install PyMuPDF python-docx

# 启动（首次会提示配置 LLM API Key，配置后自动持久化）
agent-sys start

# 或作为后台守护进程启动
agent-sys start -d

# 检测到已有 daemon 时，start 会拒绝启动（避免多 terminal 误杀）
agent-sys start          # → "already running (PID xxx), use 'agent-sys stop' first"
agent-sys start --force  # → 显式覆盖，SIGTERM 旧进程后启动新的
```

## Web 仪表盘

```bash
agent-sys dashboard                 # 启动 http://127.0.0.1:7438
agent-sys dashboard --port 9000     # 自定义端口
```

**7 个标签页**：

- **Overview** — 文件总量、总结进度、知识条目、daemon 状态、类型分布饼图、优先级柱图、最近修改
- **Files & Directories** — 文件搜索 + 目录组成堆叠条形图
- **LLM Config** — 可视化编辑 provider、模型、API key（保存到 `~/.agent_sys/llm_config.yaml`）
- **Triage** — 分诊分布图 + pipeline 流程 + 状态说明
- **Knowledge Base** — 按 category 浏览所有报告、分析、调度决策
- **Scheduling** — 自适应调度决策历史
- **Summary Progress** — 按文件类型的总结进度

仪表盘直接读 SQLite，**daemon 不运行也能用**。

## 智能 Agent 功能

启动后 daemon 自动在后台运行以下 agent，由 LLM 自适应调度：

### 智能分诊 — Triage（v0.4 新增，v0.6 使命驱动增强）

```bash
agent-sys triage                    # 运行 LLM 分诊
agent-sys triage --batch-size 1000  # 大批量
```

解决核心问题：21 万文件中大量第三方库源码不值得浪费 LLM token 总结。v0.6 进一步分离"过滤"和"排序"：过滤靠规则，排序靠**使命感 + 用户意愿**。

- **Phase 1 — 规则快跳（零 LLM 成本）**：`config/default.yaml` 的 `triage.skip_path_patterns` 列出的路径子串（`site-packages/`、`.cursor/extensions/`、`node_modules/` 等）直接标 `skip`
- **Phase 2 — 加权排序 + LLM 语义分诊**：
  - `triage.file_type_priority` 决定"先分析谁"（`.md=9 .docx=9 .py=7 .txt=3 .csv=2`）——token 预算有限时，高优先类型先被判断
  - `triage.hints` 注入自然语言偏好到 LLM prompt（例如"我的 txt 通常是临时草稿"）
  - LLM 按目录分组批量决策，四级分类：
    - `high` — 用户原创代码、个人文档、学习笔记、个性化配置
    - `medium` — 依赖配置、数据文件、项目脚手架
    - `low` — 通用库代码、标准模板
    - `skip` — 第三方源码、生成文件、原始数据集
- **用户偏好 ≠ 硬规则**：`.txt` 被标为低优先，但一个叫 `journal.txt` 的文件仍然可以被 LLM 判为 `high`。偏好只影响排序和边界判断，语义判断优先
- Summarizer 自动优先处理 `high` → `medium`，完全跳过 `skip` / `low`

**如何调整偏好**：直接编辑 `config/default.yaml` 的 `triage:` 块。例如：

```yaml
triage:
  file_type_priority:
    .md: 9       # 我主要用 markdown 记录
    .py: 8       # Python 项目优先
    .txt: 2      # txt 基本不重要
  hints:
    - "Downloads 文件夹里的 PDF 多是学习资料，值得总结"
    - "带 test_ 前缀的 python 文件通常是测试，可以降级"
```

### 文件总结 — Summarizer

```bash
agent-sys summarize                 # 手动触发，显示每个文件的摘要
agent-sys summarize --batch-size 50 # 指定批次大小
```

- 支持 **代码**（读内容 + LLM 总结）、**文档**（PDF/DOCX 文本提取）、**图片**（视觉模型）
- 增量处理：每批 30 个文件，按 code/doc/image 比例分配，处理完自动下次继续
- 两种模式：`deep`（读文件内容，适合夜间）和 `light`（只看 metadata，适合白天）
- **尊重 triage 结果**：只总结 high / medium / untriaged 的文件

### 行为分析 — Analyzer

```bash
agent-sys analyze                   # 全面分析（默认最近 7 天）
agent-sys analyze --hours 24        # 最近 24 小时
```

跨三个维度分析：**工作**（代码项目、语言偏好）、**学习**（文档、教程）、**个人生活**（下载、图片）

### 报告 — Reporter

```bash
agent-sys report daily              # 生成日报（工作/学习/生活全覆盖）
agent-sys report brief              # 上下文简报（给新 agent 会话用）
agent-sys report project            # 项目画像
```

### 个人画像 — Profile

```bash
agent-sys profile                   # 查看 LLM 构建的完整个人画像
```

### 优先级分类 — Prioritizer

```bash
agent-sys classify                  # P0 热 / P1 温 / P2 冷，按 code/doc/image 分组
```

### 查看历史报告

```bash
agent-sys query --category daily_report        # 日报
agent-sys query --category quick_report        # 快速报告
agent-sys query --category context_brief       # 上下文简报
agent-sys query --category behavior_insight    # 行为分析结果
agent-sys query --category scheduling_decision # 调度决策
agent-sys query --category project_summary     # 项目级摘要
```

## 系统状态

```bash
agent-sys ping                      # 检查 daemon 是否存活
agent-sys status                    # 查看详细状态（扫描进度、已索引文件数等）
agent-sys stop                      # 停止 daemon
```

## 在外部 Agent 中调用

> 对外的 import 前缀统一是 `agent_sys`（源码树在 `src/` 下，通过顶层的
> `agent_sys` shim 包暴露，`from agent_sys.syscall.client import ...` 与
> `from src.syscall.client import ...` 指向同一份代码）。

### Python SDK（异步）

```python
from agent_sys.syscall.client import SysAgentClient

async with SysAgentClient(caller="cursor") as client:
    results = await client.file_search("database migration")
    analysis = await client.analyze_behavior(hours=24)
    profile = await client.profile_get()
    brief = await client.report_brief()
    triage = await client.triage_files(batch_size=500)

    await client.context_save("session-abc", {"topic": "refactoring auth"})
    ctx = await client.context_load("session-abc")
```

### Python SDK（同步）

```python
from agent_sys.syscall.client import SyncSysAgentClient

with SyncSysAgentClient(caller="my_plugin") as client:
    results = client.file_search("config parser")
    profile = client.profile_get()
```

### HTTP API

```bash
curl http://127.0.0.1:7437/health
curl http://127.0.0.1:7437/status
curl -X POST http://127.0.0.1:7437/syscall \
  -H "Content-Type: application/json" \
  -d '{"call_type": "triage.files", "params": {"batch_size": 500}, "caller": "curl"}'
```

## LLM 配置

支持 4 种 provider 类型：

| Provider | 说明 | 配置 |
|---|---|---|
| `openai` | OpenAI 官方 API | `OPENAI_API_KEY` |
| `claude` | Anthropic Claude API | `ANTHROPIC_API_KEY` |
| `anthropic_compatible` | Anthropic 兼容 API（MiniMax 等） | `ANTHROPIC_API_KEY` + `base_url` |
| `compatible` | OpenAI 兼容 API（DeepSeek、Ollama、通义千问等） | `COMPATIBLE_API_KEY` + `base_url` |

配置方式（任选其一）：
1. **首次启动**：`agent-sys start` 交互式引导
2. **Web 仪表盘**：`agent-sys dashboard` → LLM Config 标签页
3. **手动编辑**：`~/.agent_sys/llm_config.yaml`

## 自定义 Agent

```python
from agent_sys.agents.base import BaseAgent, AgentTask

class MyCustomAgent(BaseAgent):
    name = "my_custom_task"

    async def execute(self, task: AgentTask, context: dict) -> dict:
        memory = context["memory"]
        llm = context["llm"]
        # your logic here
        return {"result": "done"}

# 注册到调度器
scheduler.register_agent(MyCustomAgent())
```

## 项目结构

```
agent_sys/
├── config/
│   └── default.yaml              # 系统配置（LLM + Cron + Adaptive + Triage）
├── src/
│   ├── kernel/                   # 内核层
│   │   ├── daemon.py             # 守护进程主循环
│   │   ├── config.py             # 配置加载（含 AdaptiveConfig）
│   │   └── cron.py               # LLM 自适应调度器（含 triage 管道）
│   ├── llm/                      # LLM 统一抽象层
│   │   ├── base.py               # LLMProvider 接口（支持多模态）
│   │   ├── openai_adapter.py     # OpenAI
│   │   ├── claude_adapter.py     # Claude + AnthropicCompatibleAdapter
│   │   ├── compatible_adapter.py # OpenAI 兼容 API
│   │   └── router.py             # 任务→模型路由
│   ├── filesystem/
│   │   └── watcher.py            # os.walk 全量扫描 + watchdog 实时
│   ├── memory/
│   │   └── store.py              # LRU + SQLite + 多维查询 + triage 操作
│   ├── scheduler/
│   │   └── pool.py               # 优先级队列 + 并发池
│   ├── syscall/
│   │   ├── protocol.py           # 19 个 Syscall 类型（RPC endpoint）
│   │   ├── server.py             # Socket/HTTP 服务端
│   │   └── client.py             # Async + Sync 客户端 SDK
│   ├── agents/
│   │   ├── base.py               # AgentTask + BaseAgent
│   │   ├── builtin.py            # 注册全部 15 个 Agent
│   │   ├── triage.py             # LLM 智能分诊（规则 + LLM）
│   │   ├── summarizer.py         # 多模态文件摘要（尊重 triage）
│   │   ├── analyzer.py           # 全人行为分析
│   │   ├── prioritizer.py        # 文件优先级分类
│   │   ├── reporter.py           # 多维度报告
│   │   └── profile_builder.py    # 完整个人画像
│   ├── web/                      # Web 仪表盘
│   │   ├── dashboard.py          # aiohttp 后端（10 个 API）
│   │   └── dashboard.html        # 暗色主题 SPA（7 个标签页）
│   ├── extractors.py             # PDF/DOCX/图片内容提取
│   └── cli.py                    # 命令行入口（15 个子命令）
├── pyproject.toml
├── requirements.txt
└── README.md
```

## 设计哲学

传统计算机中，CPU 通过调度器分配线程来处理计算任务。在 AgentOS 中，**LLM 通过调度器分配 Agent 线程来处理智能任务**。

关键差异：
- OS 线程处理的是确定性的计算 → Agent 线程处理的是模糊的、需要理解的任务
- OS 用文件系统存储数据 → AgentOS 用知识库存储 **被理解的** 数据
- OS 的 syscall 是同步的函数调用 → AgentOS 的 syscall 是异步的消息传递
- OS 的内存是字节寻址 → AgentOS 的内存是语义寻址（按意义检索）
- OS 的 cron 是固定时间表 → AgentOS 的 cron 由 **LLM 自适应决定**
- OS 对所有文件一视同仁 → AgentOS 先 **triage 分诊**，只深入理解有价值的文件

这不是要替代操作系统，而是在操作系统之上构建一个 **Agent 原生的运行时层**。
