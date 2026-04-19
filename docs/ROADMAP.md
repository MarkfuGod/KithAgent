# AgentOS Roadmap

Parked ideas and in-progress thinking that aren't yet design decisions.
Short notes live here so they don't pollute source files as top-of-module
comment graveyards.

## Triage

- Detect "this is a code project" early (by marker files) and summarize the
  README first, then defer the rest of the tree to a later pass. Treat code
  files as supporting context rather than the primary signal.
- Surface the triage priority table (`triage.file_type_priority`) in the web
  dashboard so users can adjust without editing YAML by hand.
- Per-extension summarization preferences: let the user mark "I don't want
  .log summarized ever" from the UI.

## Summarizer

- Parallel sub-summarizer pattern: when a triage run flags many high-priority
  files, let the parent task fan out N sub-agents that each handle a slice,
  then merge results. Strategy (aggressive / balanced / conservative) and
  concurrency count decided by the orchestrator, not hardcoded.
- Incremental re-summarization: a small byte-level diff can still be a large
  semantic diff and vice-versa. Ask the LLM to judge "is this change big
  enough to re-summarize?" rather than always re-summarizing on any change.

## Adaptive scheduling

- Bigger-picture CPU-LLM loop: on first boot the LLM should reason about the
  onboarding stages in order (index → triage → summarize → profile) and pick
  what to run next based on what's already done, instead of always running
  the full cycle every tick.
- The "OS mapping" is a narrative tool, not a constraint. If multi-host or
  Kubernetes-style deployment is ever needed, rewrite the scheduling layer
  rather than forcing the cron analogy.

## Platform

- Auth: token-based caller verification (partially done — see
  `src/syscall/server.py`). Still missing per-caller rate limits.
- Chart.js and dashboard assets bundled locally so the dashboard works
  offline.
- MLX adapter for Apple-silicon local inference.
- Plugin interface for third-party agents registered at runtime.

## Known bugs / annoyances

- Some summarizer entries still use `[light]` / `[doc]` / `[vision]` prefixes
  baked into the stored text — consider moving that into a structured
  metadata column instead of the summary body.
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

