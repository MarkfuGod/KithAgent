# AgentOS — 对话总结

## 项目是什么

AgentOS 是一个 **系统级 Agent 守护进程**，核心思想是把传统操作系统的架构映射到 LLM Agent 系统上。

它开机即运行、常驻后台，持续索引用户 **整个 home 目录** 的文件和数据，构建个人知识库。不只分析代码项目，而是 **全面理解这个人** — 工作、学习、个人生活。当用户使用 Cursor、Claude Code、OpenClaw 等 Agent 工具时，它们可以直接调用 SysAgent 的 API（Unix Socket / HTTP），无需每次重新扫描文件系统。

### 核心映射

| 传统 OS | AgentOS |
|---|---|
| 内核 (Kernel) | `SysAgentKernel` — 守护进程，管理所有子系统 |
| 线程 (Thread) | `AgentTask` — 最小执行单元 |
| 进程调度器 | `AgentScheduler` — 优先级队列 + 并发控制 |
| 文件系统 (VFS) | `FileSystemWatcher` — `os.walk` 全量扫描 + watchdog 实时监控 |
| 内存/缓存 | `MemoryStore` — LRU 热缓存 + SQLite 冷存储 |
| 系统调用 | `SyscallServer` — Unix Socket / HTTP API |
| Cron 定时器 | `CronScheduler` — LLM 自适应调度 + 固定触发器 |

---

## 版本演进

### v0.1（第一轮）— 从零搭建骨架

1. **设计了整体架构**，创建了项目目录结构
2. **实现了 6 个核心模块**：kernel、filesystem、memory、scheduler、syscall、agents
3. **成功启动**：索引了用户 **160,429 个文件**
4. **修复了 watchdog bug**：子线程无法获取 event loop 的问题

### v0.2（第二轮）— 从索引到理解

按照 [plan](/.cursor/plans/agentos_llm_evolution_fdac4bb4.plan.md) 实现了 3 个阶段：

- **Phase 1 — LLM 统一抽象层**：`src/llm/`（OpenAI、Claude、任意兼容 API），`ModelRouter` 按任务路由
- **Phase 2 — 智能 Agent + Cron**：summarizer、analyzer、prioritizer、CronScheduler
- **Phase 3 — 报告 + 画像**：reporter、profile_builder、7 个新 Syscall、CLI 5 个新命令
- **DB 迁移**：file_index 新增 priority / semantic_summary / last_accessed_at

### v0.2.1（第三轮）— LLM 启动引导

- 交互式 LLM 选择（Ollama / 远程 API / 跳过）

### v0.3（第四轮）— 系统级全面升级

这是最大的一次升级，涉及几乎所有模块：

#### 1. 系统级扫描（从项目级 → 全用户）

- `config/default.yaml`：`watch_paths` 改为 `["~"]`，扫描整个 home 目录
- `watcher.py`：从 `Path.rglob("*")` 重写为 `os.walk()` + 目录级剪枝，21 万文件 15 秒扫完
- 非阻塞启动：文件扫描在后台 `asyncio.create_task` 中运行，不阻塞 daemon 启动
- `agent-sys status` 显示扫描进度和已索引文件数

#### 2. 多模态支持

- 索引扩展到 **文档**（PDF、DOCX、PPTX、XLSX）和 **图片**（PNG、JPG、GIF、WEBP）
- `src/extractors.py`（新文件）：PDF 文本提取（PyMuPDF）、DOCX 文本提取（python-docx）、图片 base64 编码
- `llm/base.py`：`LLMMessage.content` 支持 `str | list[dict]`（OpenAI 多模态格式）
- `llm/router.py`：新增 `vision` 路由，指向 `qwen-vl-plus`
- `summarizer.py`：新增 `_summarize_image()`（视觉模型）和 `_summarize_document()`（文本/OCR）

#### 3. LLM 自适应调度（Adaptive Scheduling）

- `config.py`：新增 `AdaptiveConfig` 数据类
- `cron.py` 重构：
  - 新增 `_adaptive_loop()`：采集活动快照 → LLM 决定运行哪些 agent、用什么 mode、多久下次运行
  - 白天活跃时：light 模式、10 分钟间隔、快速报告
  - 夜间空闲时：deep 模式、长间隔、全面分析
  - 决策持久化到 knowledge 表，供后续 LLM 参考
  - 自动去重：跳过已在运行的 agent，防止 after_scan 和 adaptive 冲突

#### 4. 全人分析（从"分析项目" → "分析这个人"）

**所有 5 个智能 Agent** 都已升级为跨 work / study / personal 三维度的全面分析：

- `analyzer.py`：重写 system prompt，分析工作项目 + 学习资料 + 个人文件，跨目录综合推断
- `reporter.py`：日报/快速报告/简报全面涵盖代码、文档、图片的分类活动
- `profile_builder.py`：构建"完整的人"画像，不只是编程习惯
- `prioritizer.py`：对所有文件类型（代码、文档、图片）分别报告 P0/P1/P2 分布
- `summarizer.py`：支持 deep/light 两种模式、时间预算控制、增量处理

#### 5. 增量式文件总结

- `summarizer.py`：时间预算机制 — 每批处理文件，超时自动停止，下次 cron 继续
- `_run_light()`：只读 metadata 让 LLM 批量总结（适合白天）
- `_run_deep()`：读文件内容/调视觉模型逐个总结（适合夜间）
- `_parse_json_lenient()`：容错解析 LLM 截断的 JSON
- `_build_hierarchical_summaries()`：文件摘要 → 项目级摘要

#### 6. 持久化与健壮性

- **API Key 持久化**：`~/.agent_sys/llm_config.yaml`，重启不用重新配置
- **进程管理**：`cmd_stop` 先 SIGTERM 后 SIGKILL，处理挂起/僵尸进程
- **文件选择多样化**：`get_files_needing_summary()` 按比例分配 code/doc/image 名额
- **全局 timeout 修复**：CLI 和 cron 的所有 agent 调用都传了足够长的 timeout（300s）
- **输出改进**：summarize 输出表格化显示每个文件，analyze/report/profile 完整 JSON 输出

#### 7. MemoryStore 新增查询

- `get_modification_rate(minutes)`：最近 N 分钟的文件修改数
- `get_project_directories(min_files)`：发现项目根目录
- `get_files_by_directory(directory)`：某目录下所有文件 + 摘要
- `get_directory_breakdown(depth)`：按目录 + 文件类型分组统计
- `get_files_by_category(category, limit)`：按类别采样文件
- `get_recent_scheduling_decisions(limit)`：最近的调度决策

### v0.4（第五轮）— 架构审计 + 可视化 + 智能分诊 + 多模型

这一轮做了四件大事：

#### 1. OS→AgentOS 映射审计

对整个代码库做了全面审计，确认 6 个核心映射的完成度：

| OS 概念 | 完成度 | 关键发现 |
|---|---|---|
| 文件系统 (VFS) | **Strong** | os.walk + watchdog + SQLite 索引，不是真 VFS 但够用 |
| 进程调度器 | **Strong** | 优先级队列 + 并发 + 自适应 cron |
| 内存管理 | **Partial** | LRU + SQLite 工作正常，但 **向量/embedding 搜索声称有实际没实现**（搜索仍是 SQL LIKE） |
| Shell/CLI | **Strong** | 15 个子命令，功能完整 |
| 系统调用 | **Mostly** | 19 个 syscall 类型，3 个仍未映射（file.list, agent.submit, agent.task_status），auth 未执行 |
| IPC | **Weak** | 外部 RPC 有，内部 agent 间只是共享 context dict，没有消息总线 |

#### 2. Web UI 仪表盘（新模块 `src/web/`）

从零构建了完整的 Web 调试仪表盘：

- **`src/web/dashboard.py`**：aiohttp 后端，直接读 SQLite（daemon 不运行也能用），10 个 API endpoint
- **`src/web/dashboard.html`**：暗色主题 SPA，Chart.js 可视化，7 个标签页：
  - **Overview** — 文件总量/总结进度/知识条目/daemon 状态 + 类型分布饼图 + 优先级柱图 + 最近修改表
  - **Files & Directories** — 文件搜索 + 目录组成堆叠条形图（code/doc/image/data/config/other）
  - **Knowledge Base** — 按 category 浏览所有 knowledge 条目（报告、分析、调度决策等）
  - **LLM Config** — 可视化编辑 provider、模型、API key，保存到 `llm_config.yaml`
  - **Triage** — 分诊分布饼图 + pipeline 流程示意 + 各状态说明
  - **Scheduling** — 自适应调度决策历史
  - **Summary Progress** — 按文件类型的总结进度堆叠柱图 + 逐类型进度条
- **CLI**：`agent-sys dashboard [--port 7438]`

#### 3. LLM 智能分诊系统（新 Agent: `TriageAgent`）

解决了核心问题：21 万文件中大量是第三方库源码（PyTorch-YOLOv3 占 12 万），浪费 LLM token。

- **新文件** `src/agents/triage.py`：
  - **Phase 1 — 规则跳过**：`site-packages/`、`node_modules/`、`__pycache__/` 等模式直接标 `skip`，零 LLM 成本
  - **Phase 2 — LLM 分诊**：按目录分组 → LLM 批量决策（支持 `bulk` 前缀规则 + `individual` 覆盖）
  - 四级分类：`high`（用户原创）→ `medium`（有用上下文）→ `low`（通用代码）→ `skip`（噪音）
- **DB 迁移**：`file_index` 新增 `triage_status` 列 + 索引
- **Summarizer 联动**：`get_files_needing_summary()` 现在按 high → medium → untriaged 排序，skip/low 完全排除
- **Cron 管道**：`after_scan → triage → (after:triage) → summarizer`
- **Syscall**：新增 `triage.files` syscall
- **CLI**：`agent-sys triage [--batch-size 500]`

#### 4. Anthropic Compatible 适配器 + MiniMax-M2.7

- **`ClaudeAdapter`** 新增 `base_url` 参数（SDK + HTTP 双路径都支持自定义 endpoint）
- **新增 `AnthropicCompatibleAdapter`**：专用于 MiniMax 等第三方 Anthropic API 兼容服务
- **Router** 注册 `anthropic_compatible` provider 类型
- **已配置** MiniMax-M2.7（base_url: `https://api.minimaxi.com/anthropic`）
- **CLI** 交互式配置新增选项 `[3] Anthropic-Compat`
- **Dashboard** LLM Config 面板支持可视化增删改所有 provider

---

## 当前项目结构

```
agent_sys/
├── config/default.yaml              # 系统配置（扫描 ~/，含 LLM + Cron + Adaptive + Triage）
├── src/
│   ├── kernel/
│   │   ├── config.py                # 配置加载（含 AdaptiveConfig）
│   │   ├── daemon.py                # 守护进程主循环
│   │   └── cron.py                  # LLM 自适应调度 + 固定触发器（含 triage 管道）
│   ├── llm/                         # LLM 统一抽象层
│   │   ├── base.py                  # LLMProvider 接口（支持多模态 content）
│   │   ├── openai_adapter.py        # OpenAI / GPT
│   │   ├── claude_adapter.py        # Anthropic Claude + AnthropicCompatibleAdapter
│   │   ├── compatible_adapter.py    # 任何 OpenAI 兼容 API
│   │   └── router.py               # 任务类型→模型路由（含 vision + anthropic_compatible）
│   ├── filesystem/watcher.py        # os.walk 全量扫描 + watchdog 实时监控
│   ├── memory/store.py              # LRU + SQLite（多维度查询 + triage 操作）
│   ├── scheduler/pool.py            # 优先级队列 + 并发池 + per-task timeout
│   ├── syscall/
│   │   ├── protocol.py              # 19 个 Syscall 类型（含 triage.files）
│   │   ├── server.py                # Socket/HTTP 服务端
│   │   └── client.py                # Async + Sync 客户端 SDK（含 triage）
│   ├── agents/
│   │   ├── base.py                  # AgentTask + BaseAgent
│   │   ├── builtin.py               # 注册全部 15 个 Agent
│   │   ├── triage.py                # LLM 智能分诊（规则 + LLM 两阶段）
│   │   ├── summarizer.py            # 多模态文件摘要（尊重 triage 结果）
│   │   ├── analyzer.py              # 全人行为分析（work / study / personal）
│   │   ├── prioritizer.py           # 文件优先级分类（按类型分组统计）
│   │   ├── reporter.py              # 全维度报告（daily / quick / brief）
│   │   └── profile_builder.py       # 完整个人画像
│   ├── web/                         # Web 仪表盘（新模块）
│   │   ├── dashboard.py             # aiohttp 后端（10 个 API endpoint）
│   │   └── dashboard.html           # 暗色主题 SPA（7 个标签页，Chart.js）
│   ├── extractors.py                # PDF/DOCX 文本提取 + 图片 base64 编码
│   └── cli.py                       # 命令行入口（15 个子命令，格式化输出）
├── pyproject.toml
├── requirements.txt                 # 含 PyMuPDF、python-docx
└── README.md
```

## 数据存储

所有数据都在 `~/.agent_sys/` 下：

| 文件 | 内容 |
|---|---|
| `memory.db` | SQLite 主数据库：`file_index`（21万+文件元数据、摘要、triage 状态）、`knowledge`（报告、分析、调度决策）|
| `llm_config.yaml` | 持久化的 LLM API Key 和 provider 配置（权限 0o600） |
| `logs/sysagent.log` | daemon 运行日志 |

### 查看存储的报告和分析结果

```bash
agent-sys query --category daily_report        # 日报
agent-sys query --category quick_report        # 快速报告
agent-sys query --category context_brief       # 上下文简报
agent-sys query --category behavior_insight    # 行为分析结果
agent-sys query --category scheduling_decision # 自适应调度决策
agent-sys query --category project_summary     # 项目级摘要
```

## 如何运行

```bash
# 安装
pip install -e ".[full]"

# 启动（首次会提示配置 LLM，配置后自动保存到 ~/.agent_sys/llm_config.yaml）
agent-sys start

# 常用命令
agent-sys status                    # 查看系统状态（含扫描进度）
agent-sys triage                    # 运行 LLM 分诊（决定哪些文件值得总结）
agent-sys triage --batch-size 1000  # 大批量分诊
agent-sys summarize                 # 手动触发文件总结（尊重 triage 结果）
agent-sys summarize --batch-size 50 # 指定批次大小
agent-sys analyze                   # 运行全人行为分析
agent-sys analyze --hours 24        # 分析最近 24 小时
agent-sys report daily              # 生成日报
agent-sys report brief              # 生成上下文简报
agent-sys profile                   # 查看个人画像
agent-sys classify                  # 运行文件优先级分类
agent-sys query --category daily_report  # 查看历史报告
agent-sys dashboard                 # 启动 Web 仪表盘（http://127.0.0.1:7438）
agent-sys dashboard --port 9000     # 指定端口
agent-sys stop                      # 停止 daemon
```

### v0.5（第六轮）— 架构大升级

#### 1. Per-Function LLM 路由

- `LLMConfig` 新增 `defaults`（全局 text/vision provider+tier）和 `functions`（每功能独立覆写）
- `ModelRouter.complete()` 新增 `is_vision` 参数，独立解析 text vs vision 的 provider+model
- 可以 Anthropic 处理文字、OpenAI 处理视觉，triage/analyze 可选 reasoning 模型
- `default.yaml` 新增 `defaults:` 和 `functions:` 配置块

#### 2. EventBus + SSE 实时仪表盘

- **`src/kernel/event_bus.py`**（新文件）：轻量 pub/sub + ring buffer，支持 SSE 流推送
- 事件类型：`task.started/completed/failed`、`llm.request/response`、`triage.batch_progress`、`summarize.file_progress`
- Scheduler 发射任务生命周期事件，Router 发射 LLM 调用事件，Triage/Summarizer 发射进度事件
- Dashboard 新增 **Live Activity** 标签页：活跃任务、LLM 调用日志（model/tokens/latency/preview）、triage/summarize 实时进度条
- Dashboard 新增 SSE endpoint `GET /api/events`
- Triage 标签页现在渲染 `by_directory` 数据（之前后端返回但前端没显示）

#### 3. Triage 调度修复

- `_default_decision` 所有路径都包含 triage（不只深夜），因为规则 pass 零 LLM 成本
- `_after_scan_loop` 修复首次扫描不触发问题（去掉 `last_scan_time > 0` guard）

#### 4. 向量语义搜索

- **`src/memory/embeddings.py`**（新文件）：sentence-transformers 封装（all-MiniLM-L6-v2, 384维）
- DB 迁移新增 `embedding BLOB` + `embedding_model TEXT` 列
- `search_files()` 自动判断：3+ 词自然语言查询走向量搜索，短关键词走 SQL LIKE
- Summarizer 完成后自动批量计算 embedding
- 只嵌入 triage_status 为 high/medium 的文件（~1万-3万 vs 20万+）

#### 5. 补齐 3 个悬空 Syscall

- `file.list` → `FileListAgent`：按目录/类型/triage 状态过滤文件元数据列表
- `agent.submit` → `AgentSubmitAgent`：外部 caller 可提交任意 agent 任务
- `agent.task_status` → `AgentTaskStatusAgent`：查询任务状态/进度
- 19 个 syscall 现在全部有对应 agent 映射

#### 6. SubAgent + fan_out 并行执行

- `AgentTask` 新增 `parent_task_id` 和 `children_ids`（SubAgent 就是有 parent 的 task）
- `AgentScheduler.fan_out()`：并行提交多个 task 并等待全部完成（像 Promise.all）
- Adaptive cron 支持 `stages` 格式：阶段间串行，阶段内 agent 并行（DAG 调度）
- LLM 调度器 prompt 更新：可输出 stages 表达依赖（triage → [summarizer + analyzer]）

### v0.6（第七轮）— 使命驱动的分诊 + 配置化过滤

核心洞察：**过滤和优先级是两件事**。过滤决定"哪些根本不看"，优先级决定"先看谁"。前者靠规则，后者必须结合系统使命 + 用户意愿 + LLM 语义判断。之前的 triage 把这两层混在一起，导致 `.cursor/extensions/` 下的插件源码依然被 LLM 分析浪费 token，而且所有文件按 `modified_at DESC` 一视同仁，完全没有"txt 可能不重要"这种用户偏好的表达入口。

#### 1. `ignore_subpaths` — 路径级黑名单

- `FilesystemConfig` 新增 `ignore_subpaths` 字段：支持 `.cursor/extensions` 这类带斜杠的路径模式
- `_should_ignore_dir` / `_should_ignore` 同时检查 `ignore_patterns`（单目录名）和 `ignore_subpaths`（子路径）
- 默认配置加入：`.cursor/extensions`、`.vscode/extensions`、`.cursor-server`、`go/pkg`、`.gradle/caches`、`.m2/repository`
- 修好了 `.cursor` 目录白名单过宽的 bug（之前为了保留 rules 放行了整个 `.cursor`，导致 `extensions` 下几千个第三方文件也被索引）

#### 2. `TriageConfig` — 使命感 + 用户意愿可配置

- 新增 `config/default.yaml` 的 `triage:` 块，三类配置：
  - `skip_path_patterns` — 硬过滤路径模式（rule-based，零 LLM 成本），替换之前硬编码在 `triage.py` 里的列表
  - `file_type_priority` — 文件扩展名 → 1~10 优先级。`.md=9 .docx=9 .py=7 .txt=3 .csv=2` 等，用户可随意调
  - `hints` — 自然语言偏好列表，注入到 LLM triage prompt
- `TriageConfig` dataclass 新增到 `config.py`，随 kernel config 一起加载

#### 3. 三层防线重构

过去的数据流是两层混乱：
```
config.ignore_patterns → watcher（放行 .cursor） → triage._rule_based_pass（硬编码 skip）→ LLM
```

现在是三层职责清晰：
```
config.ignore_patterns + ignore_subpaths → watcher（精确过滤）
                 ↓
           file_index（干净的）
                 ↓
config.triage.skip_path_patterns → triage.rule_based_pass（配置化 skip）
                 ↓
config.triage.file_type_priority → store.get_untriaged_files（加权排序）
                 ↓
config.triage.hints + MISSION → LLM triage（使命感驱动判断）
```

#### 4. `get_untriaged_files` 支持加权排序

- `store.py` 的 `get_untriaged_files(type_priority=...)` 接受扩展名优先级字典
- 动态构建 SQL `CASE file_type WHEN '.md' THEN 9 ... ELSE 5 END` 作为主排序键，`modified_at DESC` 作为次排序键
- 扩展名校验防注入（只允许 `. + alnum/._-`，长度 ≤ 12）
- 未传 `type_priority` 时回退到纯 recency 行为，保持向后兼容
- 验证：3 个文件（.txt 最新、.md 最老）在加权下 .md 排第一，在不加权下 .txt 排第一

#### 5. Triage prompt 重构为使命感 + 用户意愿组合

- `_TRIAGE_SYSTEM` 常量改名 `_TRIAGE_MISSION` 并重写，明确声明 AgentOS 的使命："理解用户作为一个人"
- 新函数 `_build_triage_prompt(hints, type_priority)` 动态组合：
  - 固定的使命说明 + 分类规则
  - **USER PREFERENCES** 块注入自然语言 hints
  - **File-type priority hints** 块注入扩展名偏好
- prompt 明确告诉 LLM："把用户偏好作为 HINTS 不是 RULES，`.txt` 命名为 `journal.txt` 仍然可以是 high"
- 每次 triage run 组合一次 prompt（而非每批 LLM 调用），放在 hot loop 外

#### 6. 多 terminal 启动保护

- 旧行为：第二个 terminal 跑 `agent-sys start` 会 SIGTERM 掉第一个 daemon，用户完全不知情
- 新行为：检测到现有 daemon 存活 → 打印清晰提示（status / stop / start --force 三种选项）→ 退出 1
- 新增 `-f/--force` 显式覆盖语义，保留强制重启能力
- stale PID（进程已死但文件还在）仍然自动清理，不影响正常重启

---

## 已知问题和待完善

### 已知问题

1. **Auth 未执行**：`allowed_callers` 和 `auth_token_path` 配了但 server.py 没做校验
2. **Cron 语义**：adaptive mode 开启后，YAML 里的 interval/weekly 触发器被 LLM 决策接管，不独立运行
3. **Dashboard 离线**：Chart.js 从 CDN 加载，无网络时图表不可用
4. **Triage 偏好目前只在 CLI/YAML 可调**：Web Dashboard 的 Triage 标签还没提供偏好编辑 UI（占位 TODO）

### 下一步可做的

- 主动推送通知（异常检测 → 桌面通知）
- 对话记忆 Agent（跨会话记住历史）
- 自动化 Agent（检测重复操作，生成脚本）
- Ollama 深度适配（自动检测可用模型、health check）
- MLX 本地推理适配
- 插件系统（第三方注册自定义 Agent）
- Auth 校验（syscall server 验证 caller token）
- Chart.js 本地化（打包到项目内，离线可用）
- Dashboard 支持从 daemon EventBus 连接（当 dashboard 作为独立进程启动时通过 HTTP 转发事件）
