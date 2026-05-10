import { useState, type FormEvent, type ReactNode } from 'react';
import { tabs, type DaemonStartProgress, type LoadState, type TabId } from '../types';
import { ToastStack } from './ToastStack';
import type { Notice } from '../types';

type SpeechRecognitionConstructor = new () => {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  start: () => void;
};

export function AppShell({
  activeTab,
  children,
  commandDisabled,
  commandDraft,
  daemon,
  daemonStartProgress,
  firstInsightNeedsAttention,
  loadState,
  notices,
  onCommandDraftChange,
  onCommandSubmit,
  onDismissNotice,
  onRefresh,
  onStartDaemon,
  onTabChange,
}: {
  activeTab: TabId;
  children: ReactNode;
  commandDisabled: boolean;
  commandDraft: string;
  daemon: DaemonStatus;
  daemonStartProgress: DaemonStartProgress | null;
  firstInsightNeedsAttention: boolean;
  loadState: LoadState;
  notices: Notice[];
  onCommandDraftChange: (draft: string) => void;
  onCommandSubmit: (event?: FormEvent) => void;
  onDismissNotice: (id: string) => void;
  onRefresh: () => void;
  onStartDaemon: () => void;
  onTabChange: (tab: TabId) => void;
}) {
  const [isListening, setIsListening] = useState(false);
  const active = tabs.find((tab) => tab.id === activeTab) || tabs[0];
  const daemonBusy = loadState.daemon || loadState.refresh;
  const statusText = daemonBusy
    ? loadState.daemon
      ? '正在启动本地服务'
      : '正在同步本地理解'
    : daemon.running
      ? '索引、画像和记忆服务可用'
      : '启动后才会读取授权资料';

  function startVoiceInput() {
    const speechWindow = window as unknown as {
      SpeechRecognition?: SpeechRecognitionConstructor;
      webkitSpeechRecognition?: SpeechRecognitionConstructor;
    };
    const Recognition = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
    if (!Recognition) {
      onCommandDraftChange(commandDraft || '语音输入暂不可用，请直接输入：帮我把这个想法整理成一个可继续编辑的 Artifact。');
      return;
    }

    const recognition = new Recognition();
    recognition.lang = 'zh-CN';
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results)
        .flatMap((result) => Array.from(result))
        .map((item) => item.transcript)
        .join('')
        .trim();
      if (transcript) {
        onCommandDraftChange(commandDraft ? `${commandDraft} ${transcript}` : transcript);
      }
    };
    recognition.onerror = () => setIsListening(false);
    recognition.onend = () => setIsListening(false);
    setIsListening(true);
    recognition.start();
  }

  return (
    <>
      <main className="shell">
        <aside className="sidebar">
          <div className="sidebar-header">
            <div className="brand">
              <div className="orb" />
              <div>
                <p className="eyebrow">Private AI Memory</p>
                <h1>Kith</h1>
              </div>
            </div>
            <p className="mission">Kith remembers what matters on your computer, cites the sources it used, and helps you continue where you left off.</p>
          </div>

          <div className="sidebar-nav-scroll">
            <nav aria-label="主要导航">
              {tabs.map((tab) => {
                const className = [
                  'nav',
                  activeTab === tab.id ? 'active' : '',
                  firstInsightNeedsAttention && tab.id === 'today' ? 'attention' : '',
                ].filter(Boolean).join(' ');

                return (
                  <button
                    aria-current={activeTab === tab.id ? 'page' : undefined}
                    className={className}
                    key={tab.id}
                    onClick={() => onTabChange(tab.id)}
                    type="button"
                  >
                    <strong>{tab.label}</strong>
                    <small>{tab.hint}</small>
                  </button>
                );
              })}
            </nav>
          </div>

          <div className="sidebar-footer">
            <div className="daemon-card">
              <span className={daemon.running ? 'dot ok' : 'dot'} />
              <div>
                <strong>{daemon.running ? '本地大脑在线' : '本地大脑未启动'}</strong>
                <small>{statusText}</small>
              </div>
            </div>
            {daemonStartProgress && (
              <div className={`daemon-start-progress ${daemonStartProgress.tone || 'active'}`}>
                <div>
                  <strong>{daemonStartProgress.stage}</strong>
                  <span>{Math.round(daemonStartProgress.progress * 100)}%</span>
                </div>
                <small>{daemonStartProgress.message}</small>
                <div className="daemon-start-track" aria-hidden="true">
                  <i style={{ width: `${Math.max(6, Math.round(daemonStartProgress.progress * 100))}%` }} />
                </div>
              </div>
            )}
            {!daemon.running && (
              <button className="primary wide" disabled={daemonBusy} onClick={onStartDaemon} type="button">
                {loadState.daemon ? '启动中...' : '启动 Kith'}
              </button>
            )}
          </div>
        </aside>

        <section className="content">
          <header className="topbar">
            <div>
              <p className="eyebrow">Local memory · Under your control</p>
              <h2>{active.label}</h2>
              <small>{active.hint}</small>
            </div>
            <button className="ghost" disabled={loadState.refresh} onClick={onRefresh} type="button">
              {loadState.refresh ? '同步中...' : '刷新'}
            </button>
          </header>
          {children}
        </section>
      </main>
      <form className="global-command-bar" onSubmit={onCommandSubmit}>
        <div className="command-spark" aria-hidden="true">K</div>
        <textarea
          aria-label="Ask Kith about your approved local context"
          disabled={commandDisabled}
          onChange={(event) => onCommandDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              onCommandSubmit();
            }
          }}
          placeholder="Ask Kith... 例如：我最近在做什么？哪些文件能解释这个项目？"
          rows={1}
          value={commandDraft}
        />
        <button
          aria-label="语音输入入口"
          className={`voice-button ${isListening ? 'listening' : ''}`}
          onClick={startVoiceInput}
          type="button"
        >
          ◉
        </button>
        <button className="primary" disabled={commandDisabled || !commandDraft.trim()} type="submit">
          {commandDisabled ? 'Thinking' : 'Ask'}
        </button>
      </form>
      <ToastStack notices={notices} onDismiss={onDismissNotice} />
    </>
  );
}
