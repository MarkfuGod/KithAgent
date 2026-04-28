import { compactJson, describeFactStatus } from '../../lib/format';

export function MemoryView({
  confirmedFacts,
  isLoading,
  memory,
  onGenerateProfile,
  onUpdateFact,
  profile,
}: {
  confirmedFacts: number;
  isLoading: boolean;
  memory: MemoryReview;
  onGenerateProfile: () => Promise<void>;
  onUpdateFact: (factId: string, status: ProfileFact['status']) => Promise<void>;
  profile: ProfileSummary;
}) {
  const facts = memory.facts.length ? memory.facts : profile.facts;
  const profileOverview = buildProfileOverview(profile.profile);

  return (
    <section className="memory-page">
      <article className="panel profile-panel">
        <div>
          <p className="eyebrow">关于我</p>
          <h3>Kith 当前对你的理解</h3>
          <p>画像不是永久标签。你可以随时重新生成，也可以逐条确认、纠正或隐藏记忆。</p>
        </div>
        <button className="primary" onClick={onGenerateProfile} disabled={isLoading} type="button">
          {isLoading ? '更新中...' : '生成 / 更新我的画像'}
        </button>
        {profileOverview ? (
          <ProfileOverview overview={profileOverview} raw={profile.profile} />
        ) : (
          <p className="empty">还没有画像。完成 First Insight 后，我会基于已授权资料建立第一版理解。</p>
        )}
      </article>

      <article className="panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">可校正记忆</p>
            <h3>{facts.length} 条画像记忆，{confirmedFacts} 条已确认</h3>
          </div>
        </div>
        <FactList facts={facts} onUpdate={onUpdateFact} />
      </article>
    </section>
  );
}

export function FactList({
  facts,
  onUpdate,
}: {
  facts: ProfileFact[];
  onUpdate: (factId: string, status: ProfileFact['status']) => Promise<void>;
}) {
  if (!facts.length) {
    return <p className="empty">还没有可审阅的记忆。先生成画像，或让 Kith 多理解一些资料。</p>;
  }
  const visibleFacts = facts
    .filter((fact) => !isLowSignalBrowserFact(fact))
    .sort((a, b) => factSortWeight(a) - factSortWeight(b));
  const hiddenLowSignalCount = facts.length - visibleFacts.length;

  if (!visibleFacts.length) {
    return <p className="empty">这批记忆只有低信息浏览器结构词，已先收起。重新生成画像后会优先保留更具体的网页主题和域名。</p>;
  }

  return (
    <>
      {hiddenLowSignalCount > 0 && (
        <p className="fact-note">已收起 {hiddenLowSignalCount} 条低信息浏览器信号，例如“书签栏 / 其他书签”。</p>
      )}
      <div className="facts">
        {visibleFacts.map((fact) => (
        <article className={`fact ${fact.status}`} key={fact.id}>
          <div>
            <strong>{fact.statement}</strong>
            <small>
              {describeFactSource(fact)} · {describeFactStatus(fact.status)} · 置信度 {Math.round((fact.confidence || 0) * 100)}%
            </small>
          </div>
          <div className="fact-actions" aria-label="记忆反馈">
            <button className={fact.status === 'confirmed' ? 'selected' : ''} disabled={fact.status === 'confirmed'} onClick={() => onUpdate(fact.id, 'confirmed')} type="button">准确</button>
            <button className={fact.status === 'rejected' ? 'selected' : ''} disabled={fact.status === 'rejected'} onClick={() => onUpdate(fact.id, 'rejected')} type="button">不准</button>
            <button className={fact.status === 'hidden' ? 'selected' : ''} disabled={fact.status === 'hidden'} onClick={() => onUpdate(fact.id, 'hidden')} type="button">隐藏</button>
          </div>
        </article>
        ))}
      </div>
    </>
  );
}

type ProfileOverviewData = {
  summary: string;
  roles: string[];
  goals: string[];
  focus: string[];
  explicitInterests: string[];
  browserInterests: string[];
  evidence: Array<{ label: string; value: string }>;
};

function ProfileOverview({ overview, raw }: { overview: ProfileOverviewData; raw?: Record<string, unknown> }) {
  return (
    <div className="profile-overview">
      <div className="profile-summary-card">
        <span>画像摘要</span>
        <strong>{overview.summary || 'Kith 还在等待更多确认。'}</strong>
      </div>
      <div className="profile-kv-grid">
        <ProfileFacet label="角色" values={overview.roles} />
        <ProfileFacet label="近期目标" values={overview.goals} />
        <ProfileFacet label="当前关注" values={overview.focus} />
        <ProfileFacet label="明确兴趣" values={overview.explicitInterests} />
        <ProfileFacet label="浏览推断" values={overview.browserInterests} empty="暂无足够具体的浏览主题" />
      </div>
      {overview.evidence.length > 0 && (
        <div className="profile-evidence">
          {overview.evidence.map((item) => (
            <small key={item.label}>
              <span>{item.label}</span>
              {item.value}
            </small>
          ))}
        </div>
      )}
      <details className="raw-profile-details">
        <summary>查看原始画像 JSON</summary>
        <pre className="profile-json">{compactJson(raw)}</pre>
      </details>
    </div>
  );
}

function ProfileFacet({ label, values, empty = '待确认' }: { label: string; values: string[]; empty?: string }) {
  return (
    <div className="profile-facet">
      <span>{label}</span>
      {values.length ? (
        <div>
          {values.slice(0, 6).map((value) => (
            <strong key={value}>{value}</strong>
          ))}
        </div>
      ) : (
        <em>{empty}</em>
      )}
    </div>
  );
}

function buildProfileOverview(profile?: Record<string, unknown>): ProfileOverviewData | null {
  if (!profile) {
    return null;
  }
  const identity = objectValue(profile.identity);
  const interests = objectValue(profile.interests);
  const evidence = objectValue(profile.evidence);
  const confidence = objectValue(profile.confidence);
  const browserInterests = listValue(interests.inferred_from_browser).filter((value) => !isLowSignalBrowserTopic(value));

  return {
    summary: textValue(identity.summary),
    roles: listValue(identity.roles),
    goals: listValue(profile.goals),
    focus: listValue(interests.current_focus),
    explicitInterests: listValue(interests.explicit),
    browserInterests,
    evidence: [
      ['浏览记录', textValue(evidence.browser_entries)],
      ['书签', textValue(evidence.bookmarks)],
      ['下载', textValue(evidence.downloads)],
      ['浏览可信度', confidence.browser_history ? String(confidence.browser_history) : ''],
    ]
      .filter(([, value]) => value)
      .map(([label, value]) => ({ label, value })),
  };
}

function factSortWeight(fact: ProfileFact) {
  if (fact.status === 'inferred') return 0;
  if (fact.status === 'rejected') return 1;
  if (fact.status === 'confirmed') return 2;
  return 3;
}

function describeFactSource(fact: ProfileFact) {
  if (fact.category === 'interest.browser') return '浏览器主题推断';
  if (fact.category === 'interest.explicit') return '你填写的兴趣';
  if (fact.category === 'current_focus') return '你填写的当前关注';
  if (fact.category === 'goal') return '你填写的目标';
  if (fact.category === 'role') return '你填写的角色';
  if (fact.category === 'identity.quick') return '快速画像推断';
  return fact.category;
}

function isLowSignalBrowserFact(fact: ProfileFact) {
  if (fact.category !== 'interest.browser') {
    return false;
  }
  return isLowSignalBrowserTopic(fact.statement.replace(/^你最近可能在关注\s*/, ''));
}

function isLowSignalBrowserTopic(value: string) {
  return [
    '书签栏',
    '其他书签',
    '书签',
    '收藏夹',
    '收藏',
    '阅读列表',
    'bookmarks',
    'bookmark',
    'favorites',
    'reading list',
  ].includes(value.trim().toLowerCase());
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function listValue(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(textValue).filter(Boolean);
  }
  const text = textValue(value);
  return text ? [text] : [];
}

function textValue(value: unknown) {
  if (value === undefined || value === null || value === '') {
    return '';
  }
  return String(value);
}
