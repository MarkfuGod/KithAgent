# AgentOS — SysAgent

**把传统操作系统的架构映射到 LLM Agent 系统上。**

SysAgent 是一个常驻后台的系统级 Agent 守护进程。它开机即运行，持续索引你 **整个 home 目录** 的文件，用 LLM 构建语义知识库。不只分析代码 — 它理解你的文档、图片、工作模式、学习轨迹和生活习惯。

当你使用 Cursor、Claude Code 等 Agent 工具时，它们可以直接调用 SysAgent，获得关于你这个人的全面上下文。

## 核心思想：OS → AgentOS 映射

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

| 传统 OS 概念 | AgentOS 对应 | 说明 |
|---|---|---|
| **内核 (Kernel)** | `SysAgentKernel` | 守护进程，管理所有子系统生命周期 |
| **线程 (Thread)** | `AgentTask` | 被调度器分配的最小执行单元 |
| **进程调度器** | `AgentScheduler` | 优先级队列 + 并发控制 + per-task timeout |
| **文件系统 (VFS)** | `FileSystemWatcher` | `os.walk` 高效全量扫描 + watchdog 实时监控 |
| **内存/缓存** | `MemoryStore` | LRU 热缓存 + SQLite 冷存储 + 多维查询 |
| **系统调用 (Syscall)** | `SyscallServer` | Unix Socket / HTTP API，19 种 syscall |
| **Cron** | `CronScheduler` | LLM 自适应调度 — 白天轻量高频，夜间深度全面 |

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

### 智能分诊 — Triage（v0.4 新增）

```bash
agent-sys triage                    # 运行 LLM 分诊
agent-sys triage --batch-size 1000  # 大批量
```

解决核心问题：21 万文件中大量第三方库源码不值得浪费 LLM token 总结。

- **Phase 1 — 规则快跳**：`site-packages/`、`node_modules/` 等自动标 `skip`，零 LLM 成本
- **Phase 2 — LLM 分诊**：按目录分组 → LLM 批量决策 → 四级分类：
  - `high` — 用户原创代码、个人文档、学习笔记、个性化配置
  - `medium` — 依赖配置、数据文件、项目脚手架
  - `low` — 通用库代码、标准模板
  - `skip` — 第三方源码、生成文件、原始数据集
- Summarizer 自动优先处理 `high` → `medium`，完全跳过 `skip` / `low`

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

### Python SDK（异步）

```python
from src.syscall.client import SysAgentClient

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
from src.syscall.client import SyncSysAgentClient

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
from src.agents.base import BaseAgent, AgentTask

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
│   │   ├── protocol.py           # 19 个 Syscall 类型
│   │   ├── server.py             # Socket/HTTP 服务端
│   │   └── client.py             # Async + Sync 客户端 SDK
│   ├── agents/
│   │   ├── base.py               # AgentTask + BaseAgent
│   │   ├── builtin.py            # 注册全部 12 个 Agent
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
