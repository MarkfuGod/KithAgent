export type DiagnosticsViewProps = {
  capabilities: Awaited<ReturnType<KithApi['capabilities']['list']>> | null;
  capabilitiesStatus: string;
  daemon: DaemonStatus;
  events: DaemonEvent[];
  isCapabilitiesLoading: boolean;
  onOpenDashboard: () => Promise<void>;
  onRefreshCapabilities: () => Promise<void>;
  onStopDaemon: () => Promise<void>;
};

export function DiagnosticsView({
  capabilities,
  capabilitiesStatus,
  daemon,
  events,
  isCapabilitiesLoading,
  onOpenDashboard,
  onRefreshCapabilities,
  onStopDaemon,
}: DiagnosticsViewProps) {
  return (
    <section className="diagnostics-page">
      <article className="panel">
        <p className="eyebrow">诊断</p>
        <h3>本地服务状态</h3>
        <pre className="profile-json">{JSON.stringify(daemon.status || daemon, null, 2)}</pre>
      </article>
      <article className="panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Capability nodes</p>
            <h3>后端明确暴露了哪些能力</h3>
          </div>
          <button className="ghost" disabled={!daemon.running || isCapabilitiesLoading} onClick={() => onRefreshCapabilities()} type="button">
            {isCapabilitiesLoading ? '读取中...' : '刷新能力'}
          </button>
        </div>
        {capabilitiesStatus ? <p className="empty">{capabilitiesStatus}</p> : null}
        {capabilities?.capabilities.length ? (
          <div className="event-list">
            {capabilities.capabilities.map((capability) => (
              <div className={`event-row ${capability.status.includes('planned') ? 'warning' : 'success'}`} key={capability.id}>
                <div className="event-row-header">
                  <strong>{capability.id}</strong>
                  <span>{capability.status}</span>
                </div>
                <p>{capability.permission}</p>
                <div className="event-meta">
                  <small>{capability.sensitivity}</small>
                  {capability.commands.slice(0, 4).map((command) => (
                    <small key={command}>{command}</small>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </article>
      <article className="panel">
        <p className="eyebrow">实时事件</p>
        <h3>Daemon 最近在做什么</h3>
        <div className="event-list">
          {events.map((event, index) => {
            const summary = summarizeDaemonEvent(event);
            return (
              <div className={`event-row ${summary.tone}`} key={`${event.type}-${index}`}>
                <div className="event-row-header">
                  <strong>{summary.title}</strong>
                  <span>{event.type}</span>
                </div>
                <p>{summary.description}</p>
                {summary.meta.length ? (
                  <div className="event-meta">
                    {summary.meta.map((item) => (
                      <small key={item}>{item}</small>
                    ))}
                  </div>
                ) : null}
                <details>
                  <summary>原始详情</summary>
                  <pre>{JSON.stringify(event.data || event.error || {}, null, 2)}</pre>
                </details>
              </div>
            );
          })}
          {!events.length && <p className="empty">启动本地大脑后，这里会显示索引、画像和建议相关的实时事件。</p>}
        </div>
      </article>
      <article className="panel stack">
        <p className="eyebrow">高级工具</p>
        <h3>少跳转，但保留调试入口</h3>
        <p className="empty">常用信息已经整合进 Desktop。原始 Dashboard 仍保留给调试和更深层数据查看。</p>
        <button className="ghost" onClick={() => onOpenDashboard()} type="button">
          打开原始 Dashboard
        </button>
        <button className="ghost danger" disabled={!daemon.running} onClick={() => onStopDaemon()} type="button">
          停止本地大脑
        </button>
      </article>
    </section>
  );
}

type EventSummary = {
  title: string;
  description: string;
  meta: string[];
  tone: 'neutral' | 'active' | 'success' | 'warning';
};

function summarizeDaemonEvent(event: DaemonEvent): EventSummary {
  const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {};

  if (event.type === 'assistant.progress') {
    return {
      title: 'Kith 正在回答',
      description: text(data.message) || '正在组织本地上下文。',
      meta: [percent(data.progress), text(data.stage)].filter(Boolean),
      tone: 'active',
    };
  }

  if (event.type === 'llm.request') {
    return {
      title: '模型请求已发出',
      description: `${text(data.task_type) || '任务'} · ${text(data.provider) || 'provider'} / ${text(data.model) || 'model'}`,
      meta: [`max tokens ${text(data.max_tokens) || '-'}`, bool(data.is_vision) ? 'vision' : 'text'],
      tone: 'active',
    };
  }

  if (event.type === 'llm.response') {
    const usage = data.usage && typeof data.usage === 'object' ? data.usage as Record<string, unknown> : {};
    return {
      title: '模型已返回',
      description: `${text(data.task_type) || '任务'} · ${text(data.model) || 'model'}`,
      meta: [`prompt ${text(usage.prompt_tokens) || '-'}`, `completion ${text(usage.completion_tokens) || '-'}`],
      tone: 'success',
    };
  }

  if (event.type === 'triage.batch_progress') {
    return {
      title: '文件分诊进行中',
      description: shortenPath(text(data.directory) || '正在处理目录'),
      meta: [`classified ${text(data.classified) || 0}`, `batch ${text(data.batch_files) || 0}`, `${text(data.elapsed_s) || 0}s`],
      tone: 'active',
    };
  }

  if (event.type.includes('error') || event.error) {
    return {
      title: '事件异常',
      description: event.error || text(data.error) || text(data.msg) || '发生了一个后台事件错误。',
      meta: [],
      tone: 'warning',
    };
  }

  return {
    title: readableEventType(event.type),
    description: text(data.message) || text(data.msg) || compactJson(data),
    meta: [],
    tone: 'neutral',
  };
}

function readableEventType(type: string) {
  return type
    .split(/[._-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function text(value: unknown) {
  if (value === undefined || value === null || value === '') return '';
  return String(value);
}

function bool(value: unknown) {
  return value === true || value === 'true';
}

function percent(value: unknown) {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '';
}

function compactJson(value: Record<string, unknown>) {
  const raw = JSON.stringify(value);
  return raw.length > 120 ? `${raw.slice(0, 120)}...` : raw;
}

function shortenPath(path: string) {
  return path.replace(/^\/Users\/[^/]+\//, '~/');
}
