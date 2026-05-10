import { useMemo, useState } from 'react';
import { formatBytes, formatRelativeTime } from '../../lib/format';
import type { FirstInsightState, SectionErrors } from '../../types';

type BriefId = 'today-brief' | 'project-map' | 'privacy-ledger';

type FeedCard = {
  id: string;
  kind: 'continue' | 'ask' | 'organize' | 'brief' | 'memory' | 'export';
  index: string;
  eyebrow: string;
  title: string;
  detail: string;
  meta: string;
  action: string;
  tone: 'violet' | 'blue' | 'mint' | 'amber' | 'rose' | 'slate';
  onAction?: () => void | Promise<void>;
};

type ContextBrief = {
  id: BriefId;
  title: string;
  kind: string;
  updated: string;
  description: string;
  stats: Array<{ label: string; value: string }>;
  sections: Array<{ title: string; body: string }>;
};

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
  onCreateAgentHandoff,
  onOpenPrivacy,
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
  onCreateAgentHandoff: () => Promise<void>;
  onOpenPrivacy: () => void;
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
  const primaryVisibleSuggestion = visibleSuggestions[0];
  const firstInsightComplete = firstInsightState === 'completed' || Boolean(onboardingResult);
  const firstInsightNeedsAttention = !firstInsightComplete;
  const [activeBriefId, setActiveBriefId] = useState<BriefId>('today-brief');
  const sourceCount = watchPathCount || sources.watch_paths?.length || 0;
  const recentFileCount = insights.overview.recent_7d_modified || 0;
  const evidenceCount = insights.overview.source_records + insights.overview.knowledge_entries + insights.overview.summarized_files;
  const cleanupCount = insights.cleanup_candidates.length;
  const domains = useMemo(() => insights.web_interests.top_domains.slice(0, 3).map((item) => item.domain), [insights.web_interests.top_domains]);
  const topFiles = useMemo(() => insights.file_organization.slice(0, 4), [insights.file_organization]);
  const activeSuggestion = primaryVisibleSuggestion || {
    action: 'Build context',
    detail: daemon.running
      ? 'Kith is building useful local context from approved sources. Ask what changed, what matters, or what to continue.'
      : 'Start the local daemon before Kith can read approved folders, memory, and Today signals.',
    kind: 'system',
    priority: 'medium',
    title: daemon.running ? 'Turn recent local signals into a next step' : 'Start Kith to activate local memory',
  };

  const briefs = useMemo<ContextBrief[]>(() => ([
    {
      id: 'today-brief',
      title: 'Today Brief',
      kind: 'what matters now',
      updated: formatRelativeTime(insights.generated_at),
      description: 'A calm daily surface for what changed, what matters, and what Kith can help you continue.',
      stats: [
        { label: 'Files', value: insights.overview.total_files.toLocaleString() },
        { label: 'Changed', value: recentFileCount.toLocaleString() },
        { label: 'Confidence', value: `${confidence || 0}%` },
      ],
      sections: [
        { title: 'Continue', body: firstInsightNeedsAttention ? 'Finish First Insight so Kith has a first correctable understanding of you.' : activeSuggestion.title },
        { title: 'Evidence', body: topFiles.length ? topFiles.map((item) => item.directory).join(' / ') : 'Add approved folders to give Kith local evidence.' },
        { title: 'Next', body: daemon.running ? 'Ask Kith to brief the current project or prepare your next 30 minutes.' : 'Start Kith to generate the first useful local brief.' },
      ],
    },
    {
      id: 'project-map',
      title: 'Project Context Map',
      kind: 'local evidence',
      updated: formatRelativeTime(insights.generated_at),
      description: 'A project-like view of approved folders, high-signal files, recent activity, and memories that can be handed to another agent.',
      stats: [
        { label: 'Sources', value: sourceCount.toLocaleString() },
        { label: 'Evidence', value: evidenceCount.toLocaleString() },
        { label: 'Memory', value: memoryCount.toLocaleString() },
      ],
      sections: [
        { title: 'Focus', body: activeSuggestion.title },
        { title: 'Local files', body: topFiles.length ? topFiles.map((item) => item.directory).join(' / ') : 'No high-signal folders yet.' },
        { title: 'Agent handoff', body: 'Use Ask to create a context pack for Cursor, Claude Code, Codex, or another MCP-compatible tool.' },
      ],
    },
    {
      id: 'privacy-ledger',
      title: 'Privacy Ledger',
      kind: 'control room',
      updated: `${memoryCount.toLocaleString()} memories`,
      description: 'A user-visible record of what Kith can see, what it skipped, what stayed local, and which memories are correctable.',
      stats: [
        { label: 'Watch Paths', value: String(sourceCount) },
        { label: 'To Review', value: String(memory.facts.length || profile.facts.length) },
        { label: 'Cleanup', value: cleanupCount.toLocaleString() },
      ],
      sections: [
        { title: 'Control', body: sourceCount ? `${sourceCount} approved source${sourceCount === 1 ? '' : 's'} configured.` : 'No approved source folders yet.' },
        { title: 'Skipped', body: cleanupCount ? `${cleanupCount} cleanup candidate${cleanupCount === 1 ? '' : 's'} can be reviewed before indexing.` : 'Kith will surface noisy folders here before they become memory.' },
        { title: 'Cloud use', body: 'Model settings stay explicit so private/local, balanced, and high-quality modes remain understandable.' },
      ],
    },
  ]), [
    activeSuggestion.title,
    confidence,
    daemon.running,
    cleanupCount,
    evidenceCount,
    firstInsightNeedsAttention,
    insights.generated_at,
    insights.overview.total_files,
    memoryCount,
    memory.facts.length,
    profile.facts.length,
    recentFileCount,
    sourceCount,
    topFiles,
  ]);

  const activeBrief = briefs.find((brief) => brief.id === activeBriefId) || briefs[0];
  const feedCards: FeedCard[] = [
    {
      id: 'continue-design',
      kind: 'continue',
      index: '1',
      eyebrow: 'Continue where you left off',
      title: firstInsightNeedsAttention ? 'Finish your First Insight' : activeSuggestion.title,
      detail: firstInsightNeedsAttention
        ? 'Give Kith a first, correctable understanding of your roles, goals, interests, and current focus.'
        : activeSuggestion.detail,
      meta: firstInsightNeedsAttention ? 'Needs 3 min · Personal context' : `${activeSuggestion.kind} · ${activeSuggestion.priority}`,
      action: firstInsightNeedsAttention ? 'Continue' : activeSuggestion.action || 'Review',
      tone: 'violet',
      onAction: firstInsightNeedsAttention ? onOpenFirstInsight : () => onSuggestionAction(activeSuggestion),
    },
    {
      id: 'ask-my-computer',
      kind: 'ask',
      index: '2',
      eyebrow: 'Ask my computer',
      title: 'Ask source-backed questions about your local world',
      detail: domains.length
        ? `Recent browser/source signals include ${domains.slice(0, 2).join(', ')}. Kith can connect them to approved files.`
        : 'Ask about files, notes, projects, and recent work. Kith will show the evidence it used.',
      meta: `${formatRelativeTime(insights.generated_at)} · sources visible`,
      action: 'Ask',
      tone: 'blue',
      onAction: () => onAskKith('What was I working on recently? Show the files or evidence you used.'),
    },
    {
      id: 'organize-mess',
      kind: 'organize',
      index: '3',
      eyebrow: 'Organize my mess',
      title: cleanupCount ? `${cleanupCount} cleanup candidates need review` : 'Downloads, cache, and noisy folders stay reviewable',
      detail: cleanupCount
        ? 'Kith can group obvious noise before it enters summaries, but destructive actions still require your approval.'
        : 'Kith should recommend, group, and explain. It should not delete or hide anything important by default.',
      meta: `${watchPathCount || 0} approved roots · no destructive defaults`,
      action: 'Review privacy',
      tone: 'mint',
      onAction: onOpenPrivacy,
    },
    {
      id: 'brief-me',
      kind: 'brief',
      index: '4',
      eyebrow: 'Brief me',
      title: topFiles.length ? 'Your first local brief has enough evidence' : 'A useful brief appears once sources are approved',
      detail: topFiles.length
        ? topFiles.map((item) => `${item.directory} (${item.total.toLocaleString()})`).join(' · ')
        : 'Choose Documents, Desktop, Downloads, or a project folder. Kith will show what it used and what it skipped.',
      meta: `${watchPathCount || 0} watch paths · ${formatBytes(insights.overview.total_size_bytes)}`,
      action: 'Open brief',
      tone: 'slate',
      onAction: () => setActiveBriefId('today-brief'),
    },
    {
      id: 'remember-this',
      kind: 'memory',
      index: '5',
      eyebrow: 'Remember this',
      title: `${memory.facts.length || profile.facts.length} memories are available for review`,
      detail: 'Memory should feel correctable: confirm, reject, hide, and regenerate when Kith gets something wrong.',
      meta: `${insights.overview.confirmed_facts} confirmed · ${insights.overview.inferred_facts} inferred`,
      action: 'Review memory',
      tone: 'amber',
      onAction: onReviewMemory,
    },
    {
      id: 'agent-context-export',
      kind: 'export',
      index: '6',
      eyebrow: 'Agent context export',
      title: 'Give Cursor, Claude Code, or Codex a better starting point',
      detail: 'Kith can turn local evidence into a project brief, context pack, or handoff prompt so other agents stop starting cold.',
      meta: 'MCP-ready direction · local context layer',
      action: 'Create handoff',
      tone: 'rose',
      onAction: onCreateAgentHandoff,
    },
  ];

  return (
    <section className="flow-page">
      <div className="flow-status-strip" aria-label="Kith Today status overview">
        <article>
          <span>Today</span>
          <strong>{daemon.running ? 'Ready' : 'Offline'}</strong>
          <small>{confidence || 0}% confidence</small>
        </article>
        <article>
          <span>Approved Sources</span>
          <strong>{sourceCount}</strong>
          <small>{sourceCount ? 'under your control' : 'choose folders first'}</small>
        </article>
        <article>
          <span>Local Evidence</span>
          <strong>{evidenceCount.toLocaleString()}</strong>
          <small>summaries / records / knowledge</small>
        </article>
        <article>
          <span>Memory</span>
          <strong>{memoryCount.toLocaleString()}</strong>
          <small>{insights.overview.confirmed_facts} confirmed facts</small>
        </article>
      </div>

      {Object.values(errors).some(Boolean) && (
        <div className="inline-errors">
          {Object.entries(errors).map(([key, message]) => (
            message ? <span key={key}>{message}</span> : null
          ))}
        </div>
      )}

      <section className="flow-workspace">
        <div className="flow-feed-wrap">
          <div className="flow-feed-heading">
            <div>
              <p className="eyebrow">Private AI home screen</p>
              <h3>Continue where you left off.</h3>
              <p>Kith turns approved local context into a daily brief, source-backed answers, and next actions you can trust.</p>
            </div>
            <button className="ghost" onClick={() => onAskKith('Generate a Today brief from my approved local context. Include what changed, what matters, and what I should continue.')} type="button">
              Draft Today Brief
            </button>
          </div>

          <div className="flow-feed" aria-label="Kith Today cards">
            {feedCards.map((card) => (
              <article className={`flow-card ${card.kind} ${card.tone}`} key={card.id}>
                <div className="flow-card-index">{card.index}</div>
                <div className="flow-card-icon" aria-hidden="true">{cardIcon(card.kind)}</div>
                <div className="flow-card-main">
                  <div className="flow-card-meta">
                    <span>{card.eyebrow}</span>
                    <small>{card.meta}</small>
                  </div>
                  <h4>{card.title}</h4>
                  <p>{card.detail}</p>
                  {card.kind === 'brief' && topFiles.length > 0 && (
                    <div className="flow-file-row" aria-label="最近文件">
                      {topFiles.map((item) => (
                        <button key={item.prefix} onClick={() => onAskKith(`总结这个文件夹为什么重要：${item.directory}\n${item.reason}`)} type="button">
                          <b>{fileBadge(item.directory)}</b>
                          <span>{item.directory}</span>
                        </button>
                      ))}
                      <button onClick={() => setActiveBriefId('project-map')} type="button">+{Math.max(1, insights.file_organization.length - topFiles.length)} more</button>
                    </div>
                  )}
                </div>
                <button className={card.kind === 'continue' ? 'primary' : 'ghost'} onClick={card.onAction} type="button">
                  {card.action} →
                </button>
              </article>
            ))}
          </div>
        </div>

        <aside className="artifact-panel" aria-label="Kith context preview">
          <div className="artifact-alert">
            <span>Private by default · sources visible</span>
            <button aria-label="Show privacy ledger" onClick={() => setActiveBriefId('privacy-ledger')} type="button">×</button>
          </div>

          <div className="artifact-switcher" role="tablist" aria-label="Context briefs">
            {briefs.map((brief) => (
              <button
                aria-selected={brief.id === activeBriefId}
                className={brief.id === activeBriefId ? 'active' : ''}
                key={brief.id}
                onClick={() => setActiveBriefId(brief.id)}
                role="tab"
                type="button"
              >
                {brief.kind}
              </button>
            ))}
          </div>

          <section className="artifact-preview">
            <div className="artifact-hero">
              <div className="artifact-wave" />
              <p>{activeBrief.kind}</p>
              <h3>{activeBrief.title}</h3>
              <small>{activeBrief.updated}</small>
            </div>

            <div className="artifact-mini-grid">
              {activeBrief.sections.map((section) => (
                <article key={section.title}>
                  <strong>{section.title}</strong>
                  <p>{section.body}</p>
                </article>
              ))}
            </div>

            <div className="artifact-stats">
              {activeBrief.stats.map((stat) => (
                <div key={stat.label}>
                  <strong>{stat.value}</strong>
                  <span>{stat.label}</span>
                </div>
              ))}
            </div>

            <button className="primary wide" onClick={() => onAskKith(`Use this Kith context brief: ${activeBrief.title}\n${activeBrief.description}`)} type="button">
              Ask about this brief
            </button>
            <div className="artifact-actions">
              <button className="ghost" onClick={() => onAskKith(`What should I continue next based on ${activeBrief.title}?`)} type="button">Continue</button>
              <button className="ghost" onClick={onReviewMemory} type="button">Memory</button>
              <button className="ghost" onClick={onOpenPrivacy} type="button">Privacy</button>
            </div>
          </section>

          <details className="artifact-about" open>
            <summary>Why Kith suggested this</summary>
            <p>{activeBrief.description}</p>
            <div>
              {[
                `${insights.overview.total_files.toLocaleString()} files`,
                `${memoryCount.toLocaleString()} memories`,
                missingBrowserSignal ? 'browser off' : 'browser signal',
                `${visibleSuggestions.length} suggestions`,
              ].map((item) => <span key={item}>{item}</span>)}
            </div>
          </details>
        </aside>
      </section>

      <div className="secondary-actions">
        <button className="ghost" onClick={() => onAskKith('结合我的画像和最近活动，今天我最应该关注什么？')} type="button">
          Ask Kith: what matters today?
        </button>
        <button className="ghost" onClick={onReviewMemory} type="button">
          Review {memory.facts.length || profile.facts.length} memories
        </button>
        <button className="ghost" onClick={() => onOpenDashboard()} type="button">
          Open Advanced Dashboard
        </button>
      </div>
    </section>
  );
}

function cardIcon(kind: FeedCard['kind']) {
  const icons: Record<FeedCard['kind'], string> = {
    continue: '✎',
    ask: '?',
    organize: '▣',
    brief: '§',
    memory: '✓',
    export: '↗',
  };
  return icons[kind];
}

function fileBadge(directory: string) {
  const extension = directory.split('.').pop()?.slice(0, 4).toUpperCase();
  if (extension && extension !== directory.toUpperCase()) {
    return extension;
  }
  return 'DOC';
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
