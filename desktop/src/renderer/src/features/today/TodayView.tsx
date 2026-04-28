import { formatBytes, formatRelativeTime } from '../../lib/format';
import type { FirstInsightState, SectionErrors } from '../../types';

export function TodayView({
  daemon,
  errors,
  firstInsightState,
  insights,
  memory,
  onboardingResult,
  onAskKith,
  onOpenFirstInsight,
  onOpenDashboard,
  onRefresh,
  onReviewMemory,
  onSuggestionAction,
  profile,
  sources,
}: {
  daemon: DaemonStatus;
  errors: SectionErrors;
  firstInsightState: FirstInsightState;
  insights: KithInsights;
  memory: MemoryReview;
  onboardingResult: OnboardingBootstrapResult | null;
  onAskKith: (prompt: string) => void;
  onOpenFirstInsight: () => void;
  onOpenDashboard: () => Promise<void>;
  onRefresh: () => void;
  onReviewMemory: () => void;
  onSuggestionAction: (suggestion: InsightSuggestion) => void;
  profile: ProfileSummary;
  sources: SourceSettings;
}) {
  const confidence = Math.round((insights.overview.confidence || 0) * 100);
  const missingBrowserSignal = !insights.web_interests.has_browser_signal;
  const watchPathCount = sources.watch_paths?.length || 0;
  const memoryCount = insights.overview.confirmed_facts + insights.overview.inferred_facts;
  const visibleSuggestions = insights.suggestions.filter((suggestion) => !isLowSignalSuggestion(suggestion));
  const coreState = daemon.running ? '在线观察' : '等待启动';
  const primaryVisibleSuggestion = visibleSuggestions[0];
  const coreLine = primaryVisibleSuggestion
    ? primaryVisibleSuggestion.detail
    : daemon.running
      ? '我正在从你的授权资料里建立稳定理解。完成 First Insight 后，我会更快给出行动建议。'
      : '启动本地大脑后，我才会读取授权目录、画像记忆和今日信号。';
  const firstInsightComplete = firstInsightState === 'completed' || Boolean(onboardingResult);
  const firstInsightNeedsAttention = !firstInsightComplete;

  return (
    <section className="today-page">
      <section className="command-stage">
        <div className="core-visual" aria-label={`Kith 核心状态：${coreState}`}>
          <div className="orbital-ring ring-one" />
          <div className="orbital-ring ring-two" />
          <div className="neural-core">
            <span className={daemon.running ? 'core-pulse online' : 'core-pulse'} />
            <strong>KITH</strong>
            <small>{coreState}</small>
          </div>
          <div className="scan-line" />
        </div>

        <div className="command-brief">
          <p className="eyebrow">Kith today</p>
          <h3>{primaryVisibleSuggestion ? primaryVisibleSuggestion.title : daemon.running ? '我在理解你的本地世界。' : '先唤醒 Kith，本地智能才会开始工作。'}</h3>
          <p>{coreLine}</p>
          <div className="command-actions">
            {primaryVisibleSuggestion && (
              <button className="primary" onClick={() => onSuggestionAction(primaryVisibleSuggestion)} type="button">
                {primaryVisibleSuggestion.action || '执行建议'}
              </button>
            )}
            <button className="ghost" onClick={() => onAskKith('基于我当前的本地信号，告诉我现在最值得做什么。')} type="button">
              询问当前判断
            </button>
            {!firstInsightComplete && (
              <button className="ghost first-insight-nudge" onClick={onOpenFirstInsight} type="button">
                启动 First Insight
              </button>
            )}
            <button className="ghost" onClick={onRefresh} type="button">重新扫描状态</button>
          </div>
        </div>

        <div className="command-telemetry">
          <article>
            <span>理解可信度</span>
            <strong>{confidence}%</strong>
            <small>最近同步 {formatRelativeTime(insights.generated_at)}</small>
          </article>
          <article>
            <span>观察范围</span>
            <strong>{watchPathCount || '未设定'}</strong>
            <small>{watchPathCount ? '个授权目录' : '需要配置资料权限'}</small>
          </article>
          <article>
            <span>记忆网络</span>
            <strong>{memoryCount.toLocaleString()}</strong>
            <small>{insights.overview.confirmed_facts} 条已确认</small>
          </article>
          <article className={firstInsightNeedsAttention ? 'first-insight-attention' : ''}>
            <span>首次画像</span>
            <strong>{firstInsightComplete ? '已完成' : '待启动'}</strong>
            <small>{firstInsightComplete ? `${onboardingResult?.topics.length || 0} 个主题已建立` : '点击主操作进入弹窗'}</small>
          </article>
        </div>
      </section>

      {Object.values(errors).some(Boolean) && (
        <div className="inline-errors">
          {Object.entries(errors).map(([key, message]) => (
            message ? <span key={key}>{message}</span> : null
          ))}
        </div>
      )}

      <section className="cockpit-grid">
        <article>
          <span>正在观察</span>
          <strong>{insights.overview.total_files.toLocaleString()} 个文件</strong>
          <small>{formatBytes(insights.overview.total_size_bytes)} · {insights.overview.recent_7d_modified.toLocaleString()} 个最近变化</small>
        </article>
        <article>
          <span>我知道什么</span>
          <strong>{memoryCount.toLocaleString()} 条画像记忆</strong>
          <small>{profile.facts.length || memory.facts.length} 条可被你校正</small>
        </article>
        <article>
          <span>我还缺什么</span>
          <strong>{missingBrowserSignal ? '浏览兴趣' : '更强反馈'}</strong>
          <small>{missingBrowserSignal ? '可在 First Insight 授权聚合' : '确认或隐藏不准的记忆'}</small>
        </article>
        <article>
          <span>系统状态</span>
          <strong>{daemon.running ? '守护中' : '离线'}</strong>
          <small>{daemon.running ? '本地 daemon 可用' : '需要启动 Kith'}</small>
        </article>
      </section>

      <section className="panel action-panel primary-actions command-panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Kith 建议</p>
            <h3>现在最值得交给它处理的事</h3>
          </div>
          <button className="ghost" onClick={() => onAskKith('基于今日建议，帮我排一个 30 分钟行动顺序。')} type="button">
            生成行动顺序
          </button>
        </div>
        {visibleSuggestions.length ? (
          <div className="action-grid">
            {visibleSuggestions.map((suggestion) => (
              <article className={`action-card ${suggestion.priority}`} key={`${suggestion.kind}-${suggestion.title}`}>
                <span>{suggestion.kind}</span>
                <strong>{suggestion.title}</strong>
                <p>{suggestion.detail}</p>
                <div className="action-card-buttons">
                  <button className="primary" onClick={() => onSuggestionAction(suggestion)} type="button">
                    {suggestion.action || '去处理'}
                  </button>
                  <button className="ghost" onClick={() => onAskKith(`解释这个建议的依据：${suggestion.title}\n${suggestion.detail}`)} type="button">
                    查看原因
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="empty">还没有行动建议。启动 Kith、配置资料范围或生成画像后会逐步出现。</p>
        )}
      </section>

      <section className="signal-grid">
        <details className="panel signal-panel" open>
          <summary>
            <span>文件组织</span>
            <strong>推荐整理的文件夹</strong>
          </summary>
          <div className="insight-list">
            {insights.file_organization.slice(0, 5).map((item) => (
              <div className="insight-row" key={item.prefix}>
                <div>
                  <strong>{item.directory}</strong>
                  <small>{item.total.toLocaleString()} files · {formatBytes(item.total_size)} · {item.reason}</small>
                </div>
                <span className={`pill ${item.recommendation}`}>{item.recommendation}</span>
              </div>
            ))}
            {!insights.file_organization.length && <p className="empty">暂无文件夹建议。完成扫描和 triage 后会出现。</p>}
          </div>
        </details>

        <details className="panel signal-panel">
          <summary>
            <span>网页兴趣</span>
            <strong>主题和域名</strong>
          </summary>
          <div className="topic-strip">
            {insights.web_interests.topics.slice(0, 8).map((item) => (
              <span key={item.topic}>{item.topic}</span>
            ))}
          </div>
          <div className="insight-list compact">
            {insights.web_interests.top_domains.slice(0, 6).map((item) => (
              <div className="insight-row" key={item.domain}>
                <div>
                  <strong>{item.domain}</strong>
                  <small>{item.kind} · {item.count.toLocaleString()} signals · {formatRelativeTime(item.last_seen)}</small>
                </div>
              </div>
            ))}
            {!insights.web_interests.has_browser_signal && <p className="empty">暂无网页兴趣数据。Kith 不会未经授权读取浏览历史。</p>}
          </div>
        </details>

        <details className="panel signal-panel">
          <summary>
            <span>清理候选</span>
            <strong>保守建议，不会自动删除</strong>
          </summary>
          <div className="insight-list">
            {insights.cleanup_candidates.slice(0, 5).map((item) => (
              <div className="insight-row" key={item.full_path}>
                <div>
                  <strong>{item.path}</strong>
                  <small>{formatBytes(item.size_bytes)} · {item.reason} · {item.action}</small>
                </div>
                <span className={`pill risk-${item.risk}`}>{item.risk}</span>
              </div>
            ))}
            {!insights.cleanup_candidates.length && <p className="empty">没有明显清理候选。Kith 会优先保守地给出建议。</p>}
          </div>
        </details>

        <details className="panel signal-panel">
          <summary>
            <span>视频兴趣</span>
            <strong>可能在看的内容</strong>
          </summary>
          <div className="domain-cloud">
            {insights.video_interests.map((item) => (
              <div className="domain-card" key={item.domain}>
                <strong>{item.domain}</strong>
                <small>{item.count.toLocaleString()} signals · {formatRelativeTime(item.last_seen)}</small>
                {item.topics.length > 0 && <p>{item.topics.slice(0, 2).join(' / ')}</p>}
              </div>
            ))}
            {!insights.video_interests.length && <p className="empty">还没有视频兴趣信号。只有在你授权浏览历史聚合后才会显示。</p>}
          </div>
        </details>
      </section>

      <div className="secondary-actions">
        <button className="ghost" onClick={() => onAskKith('结合我的画像和最近活动，今天我最应该关注什么？')} type="button">
          问 Kith：今天关注什么
        </button>
        <button className="ghost" onClick={onReviewMemory} type="button">
          校正 {memory.facts.length || profile.facts.length} 条记忆
        </button>
        <button className="ghost" onClick={() => onOpenDashboard()} type="button">
          打开高级 Dashboard
        </button>
      </div>
    </section>
  );
}

function isLowSignalSuggestion(suggestion: InsightSuggestion) {
  const text = `${suggestion.title} ${suggestion.detail}`.toLowerCase();
  return [
    '/.cursor/extensions/',
    '~/ .cursor/extensions/',
    '~/.cursor/extensions/',
    '/node_modules/',
    '/.cache/',
    '/.venv/',
    '/venv/',
    '/dist/',
    '/build/',
    '/target/',
    '/__pycache__/',
  ].some((marker) => text.includes(marker));
}
