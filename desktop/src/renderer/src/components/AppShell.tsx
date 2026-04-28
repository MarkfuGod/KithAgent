import type { ReactNode } from 'react';
import { tabs, type DaemonStartProgress, type LoadState, type TabId } from '../types';
import { ToastStack } from './ToastStack';
import type { Notice } from '../types';

export function AppShell({
  activeTab,
  children,
  daemon,
  daemonStartProgress,
  firstInsightNeedsAttention,
  loadState,
  notices,
  onDismissNotice,
  onRefresh,
  onStartDaemon,
  onTabChange,
}: {
  activeTab: TabId;
  children: ReactNode;
  daemon: DaemonStatus;
  daemonStartProgress: DaemonStartProgress | null;
  firstInsightNeedsAttention: boolean;
  loadState: LoadState;
  notices: Notice[];
  onDismissNotice: (id: string) => void;
  onRefresh: () => void;
  onStartDaemon: () => void;
  onTabChange: (tab: TabId) => void;
}) {
  const active = tabs.find((tab) => tab.id === activeTab) || tabs[0];
  const daemonBusy = loadState.daemon || loadState.refresh;
  const statusText = daemonBusy
    ? loadState.daemon
      ? '正在启动本地服务'
      : '正在同步本地理解'
    : daemon.running
      ? '索引、画像和记忆服务可用'
      : '启动后才会读取授权资料';

  return (
    <>
      <main className="shell">
        <aside className="sidebar">
          <div className="sidebar-header">
            <div className="brand">
              <div className="orb" />
              <div>
                <p className="eyebrow">Kith</p>
                <h1>本地小助理</h1>
              </div>
            </div>
            <p className="mission">一个安静陪你整理本地线索的小助理。只在你授权的范围里理解、记住，并轻轻提醒下一步。</p>
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
              <p className="eyebrow">Kith personal space</p>
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
      <ToastStack notices={notices} onDismiss={onDismissNotice} />
    </>
  );
}
