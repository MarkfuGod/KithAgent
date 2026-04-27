import { FormEvent, useEffect, useMemo, useState } from 'react';

const tabs = [
  { id: 'chat', label: 'Ask Jarvis' },
  { id: 'profile', label: 'About Me' },
  { id: 'memories', label: 'Memories' },
  { id: 'privacy', label: 'Sources & Privacy' },
  { id: 'advanced', label: 'Advanced' },
] as const;

type TabId = (typeof tabs)[number]['id'];

const starterPrompts = [
  '你觉得我是个什么样的人？',
  '我最近的注意力放在哪里？',
  '帮我总结我的工作、学习和生活线索。',
];

function compactJson(value: unknown) {
  if (!value || typeof value !== 'object') {
    return '';
  }
  return JSON.stringify(value, null, 2);
}

function describeFactStatus(status: ProfileFact['status']) {
  if (status === 'confirmed') return '已确认';
  if (status === 'rejected') return '不准确';
  if (status === 'hidden') return '已隐藏';
  return '推断';
}

export function App() {
  const [activeTab, setActiveTab] = useState<TabId>('chat');
  const [daemon, setDaemon] = useState<DaemonStatus>({ running: false });
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: 'assistant',
      content: '我会先理解你允许我读取的本地资料，再用人话回答关于你的问题。你可以从“你觉得我是个什么样的人？”开始。',
    },
  ]);
  const [draft, setDraft] = useState(starterPrompts[0]);
  const [profile, setProfile] = useState<ProfileSummary>({ facts: [] });
  const [memory, setMemory] = useState<MemoryReview>({ facts: [] });
  const [sources, setSources] = useState<SourceSettings>({ watch_paths: [] });
  const [sourceDraft, setSourceDraft] = useState('~/Documents\n~/Desktop');
  const [modelMode, setModelMode] = useState<'api' | 'ollama' | 'local'>('ollama');
  const [modelDraft, setModelDraft] = useState({
    provider: 'openai_compatible',
    base_url: 'http://localhost:11434/v1',
    model: 'llama3.1',
    api_key: '',
  });
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');

  const confirmedFacts = useMemo(
    () => profile.facts.filter((fact) => fact.status === 'confirmed').length,
    [profile.facts],
  );

  async function refreshAll(rebuild = false) {
    setBusy('同步本地大脑');
    try {
      const [daemonStatus, sourceState, profileState, memoryState] = await Promise.all([
        window.kith.daemon.status(),
        window.kith.sources.get().catch(() => sources),
        window.kith.profile.summary({ rebuild }).catch(() => profile),
        window.kith.memory.review({ limit: 40 }).catch(() => memory),
      ]);
      setDaemon(daemonStatus);
      setSources(sourceState);
      setSourceDraft((sourceState.watch_paths || []).join('\n') || sourceDraft);
      setProfile(profileState);
      setMemory(memoryState);
    } finally {
      setBusy('');
    }
  }

  useEffect(() => {
    refreshAll(false).catch((error) => {
      setNotice(error instanceof Error ? error.message : String(error));
    });
  }, []);

  async function startDaemon() {
    setBusy('启动本地大脑');
    try {
      const status = await window.kith.daemon.start();
      setDaemon(status);
      setNotice('本地大脑已启动。');
    } finally {
      setBusy('');
    }
  }

  async function submitChat(event?: FormEvent) {
    event?.preventDefault();
    const message = draft.trim();
    if (!message) return;
    const nextMessages: ChatMessage[] = [...messages, { role: 'user', content: message }];
    setMessages(nextMessages);
    setDraft('');
    setBusy('Jarvis 正在组织答案');
    try {
      const response = await window.kith.jarvis.chat({ message, history: nextMessages.slice(-8) });
      setMessages([...nextMessages, { role: 'assistant', content: response.answer }]);
    } catch (error) {
      setMessages([
        ...nextMessages,
        { role: 'assistant', content: `我现在没法回答：${error instanceof Error ? error.message : String(error)}` },
      ]);
    } finally {
      setBusy('');
    }
  }

  async function generateProfile() {
    await refreshAll(true);
    setNotice('画像已更新。你可以在 About Me 里校正它。');
  }

  async function updateFact(factId: string, status: ProfileFact['status']) {
    await window.kith.memory.feedback({ fact_id: factId, status });
    await refreshAll(false);
  }

  async function saveSources() {
    const watch_paths = sourceDraft
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    const updated = await window.kith.sources.configure({ watch_paths });
    setSources(updated);
    setNotice('资料范围已保存。重启或重新扫描后会按新范围理解你。');
  }

  async function saveModel() {
    const result = await window.kith.settings.model({ mode: modelMode, ...modelDraft });
    setNotice(`模型设置已保存：${result.mode}`);
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="orb" />
          <div>
            <p className="eyebrow">Kith</p>
            <h1>Personal Jarvis</h1>
          </div>
        </div>
        <p className="mission">一款先征得同意，再理解你的 Mac 个人助理。</p>
        <nav>
          {tabs.map((tab) => (
            <button
              className={activeTab === tab.id ? 'nav active' : 'nav'}
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <div className="daemon-card">
          <span className={daemon.running ? 'dot ok' : 'dot'} />
          <div>
            <strong>{daemon.running ? '本地大脑在线' : '本地大脑未启动'}</strong>
            <small>{busy || (daemon.running ? '索引、画像和记忆服务可用' : '点击启动后开始工作')}</small>
          </div>
        </div>
        {!daemon.running && (
          <button className="primary wide" onClick={startDaemon} type="button">
            启动 Kith
          </button>
        )}
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">Local-first companion</p>
            <h2>{tabs.find((tab) => tab.id === activeTab)?.label}</h2>
          </div>
          <button className="ghost" onClick={() => refreshAll(false)} type="button">
            刷新
          </button>
        </header>

        {notice && <div className="notice">{notice}</div>}

        {activeTab === 'chat' && (
          <section className="panel chat-panel">
            <div className="hero-card">
              <p className="eyebrow">Ask from your real context</p>
              <h3>问关于“你”的问题，而不是重新解释背景。</h3>
              <div className="prompt-row">
                {starterPrompts.map((prompt) => (
                  <button key={prompt} onClick={() => setDraft(prompt)} type="button">
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
            <div className="messages">
              {messages.map((message, index) => (
                <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
                  {message.content}
                </article>
              ))}
            </div>
            <form className="composer" onSubmit={submitChat}>
              <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="问 Jarvis 一句..." />
              <button className="primary" disabled={Boolean(busy)} type="submit">
                发送
              </button>
            </form>
          </section>
        )}

        {activeTab === 'profile' && (
          <section className="grid two">
            <article className="panel">
              <p className="eyebrow">About Me</p>
              <h3>Jarvis 当前对你的理解</h3>
              <pre className="profile-json">{compactJson(profile.profile) || '还没有画像。点击生成后，我会基于已索引资料建立第一版理解。'}</pre>
              <button className="primary" onClick={generateProfile} disabled={Boolean(busy)} type="button">
                生成 / 更新我的画像
              </button>
            </article>
            <article className="panel">
              <p className="eyebrow">Correctable facts</p>
              <h3>{profile.facts.length} 条画像记忆，{confirmedFacts} 条已确认</h3>
              <FactList facts={profile.facts.slice(0, 12)} onUpdate={updateFact} />
            </article>
          </section>
        )}

        {activeTab === 'memories' && (
          <section className="panel">
            <p className="eyebrow">Memory Review</p>
            <h3>Jarvis 记住的事实</h3>
            <FactList facts={memory.facts} onUpdate={updateFact} />
          </section>
        )}

        {activeTab === 'privacy' && (
          <section className="grid two">
            <article className="panel">
              <p className="eyebrow">Sources</p>
              <h3>允许 Kith 理解哪些资料？</h3>
              <textarea value={sourceDraft} onChange={(event) => setSourceDraft(event.target.value)} rows={8} />
              <button className="primary" onClick={saveSources} type="button">
                保存资料范围
              </button>
              <small>每行一个目录。建议从 Documents、Desktop、学习资料目录开始。</small>
            </article>
            <article className="panel">
              <p className="eyebrow">Model</p>
              <h3>选择理解能力来源</h3>
              <div className="segmented">
                {(['ollama', 'api', 'local'] as const).map((mode) => (
                  <button className={modelMode === mode ? 'active' : ''} key={mode} onClick={() => setModelMode(mode)} type="button">
                    {mode === 'ollama' ? '本机 Ollama' : mode === 'api' ? '在线 API' : '本地轻量'}
                  </button>
                ))}
              </div>
              <input value={modelDraft.base_url} onChange={(event) => setModelDraft({ ...modelDraft, base_url: event.target.value })} placeholder="Base URL" />
              <input value={modelDraft.model} onChange={(event) => setModelDraft({ ...modelDraft, model: event.target.value })} placeholder="Model" />
              <input value={modelDraft.api_key} onChange={(event) => setModelDraft({ ...modelDraft, api_key: event.target.value })} placeholder="API Key（Ollama 可留空）" type="password" />
              <button className="primary" onClick={saveModel} type="button">
                保存模型设置
              </button>
            </article>
          </section>
        )}

        {activeTab === 'advanced' && (
          <section className="grid two">
            <article className="panel">
              <p className="eyebrow">Diagnostics</p>
              <h3>Daemon 状态</h3>
              <pre className="profile-json">{JSON.stringify(daemon.status || daemon, null, 2)}</pre>
            </article>
            <article className="panel stack">
              <p className="eyebrow">Developer tools</p>
              <h3>高级工具</h3>
              <button className="ghost" onClick={() => window.kith.daemon.openDashboard()} type="button">
                打开原始 Dashboard
              </button>
              <button className="ghost danger" onClick={() => window.kith.daemon.stop().then(() => setDaemon({ running: false }))} type="button">
                停止本地大脑
              </button>
            </article>
          </section>
        )}
      </section>
    </main>
  );
}

function FactList({
  facts,
  onUpdate,
}: {
  facts: ProfileFact[];
  onUpdate: (factId: string, status: ProfileFact['status']) => Promise<void>;
}) {
  if (!facts.length) {
    return <p className="empty">还没有可审阅的记忆。先生成画像，或让 Jarvis 多理解一些资料。</p>;
  }
  return (
    <div className="facts">
      {facts.map((fact) => (
        <article className="fact" key={fact.id}>
          <div>
            <strong>{fact.statement}</strong>
            <small>
              {fact.category} · {describeFactStatus(fact.status)} · 置信度 {Math.round((fact.confidence || 0) * 100)}%
            </small>
          </div>
          <div className="fact-actions">
            <button onClick={() => onUpdate(fact.id, 'confirmed')} type="button">准确</button>
            <button onClick={() => onUpdate(fact.id, 'rejected')} type="button">不准</button>
            <button onClick={() => onUpdate(fact.id, 'hidden')} type="button">隐藏</button>
          </div>
        </article>
      ))}
    </div>
  );
}
