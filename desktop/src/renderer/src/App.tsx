import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AppShell } from './components/AppShell';
import { ChatView, starterPrompts } from './features/chat/ChatView';
import { DiagnosticsView } from './features/diagnostics/DiagnosticsView';
import { MemoryView } from './features/memory/MemoryView';
import { FirstInsightModal } from './features/onboarding/FirstInsight';
import { PrivacyView } from './features/privacy/PrivacyView';
import { SettingsView } from './features/settings/SettingsView';
import { TodayView } from './features/today/TodayView';
import { listFromText } from './lib/format';
import { persistFirstInsightState, readFirstInsightState } from './lib/firstInsight';
import { createId } from './lib/id';
import { buildModelSettingsPayload, modelDraftFromSavedScope } from './lib/modelSettings';
import {
  defaultOnboardingAnswers,
  emptyInsights,
  initialLoadState,
  initialOnboardingDraft,
  type ChatMessageView,
  type ChatProgress,
  type DaemonStartProgress,
  type FirstInsightState,
  type LoadKey,
  type ModelDraft,
  type ModelMode,
  type ModelScope,
  type Notice,
  type SectionErrors,
  type TabId,
} from './types';

const initialModelDraft: ModelDraft = {
  name: 'ollama',
  api_mode: 'openai_compatible',
  provider: 'openai_compatible',
  base_url: 'http://localhost:11434/v1',
  api_host: 'http://localhost:11434/v1',
  api_path: '/chat/completions',
  model: 'llama3.1',
  model_display_name: 'llama3.1',
  model_type: 'chat',
  api_key: '',
  api_key_env: 'OPENAI_COMPATIBLE_API_KEY',
  has_key: false,
  improve_compatibility: true,
  vision: false,
  reasoning: false,
  tools: true,
  context_window: '',
  max_output_tokens: '',
};

const initialDesktopModelDraft: ModelDraft = {
  ...initialModelDraft,
  name: 'openrouter',
  api_host: 'https://openrouter.ai/api/v1/chat/completions',
  api_path: '/chat/completions',
  base_url: 'https://openrouter.ai/api/v1',
  api_key_env: 'OPENROUTER_API_KEY',
  model: 'x-ai/grok-4',
  model_display_name: 'grok',
  vision: true,
  reasoning: true,
  context_window: '128000',
  max_output_tokens: '4096',
};

const welcomeMessage: ChatMessageView = {
  id: 'assistant-welcome',
  role: 'assistant',
  content: '我会先理解你允许我读取的本地资料，再用人话回答关于你的问题。你可以从“你觉得我是个什么样的人？”开始。',
};

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function isHeaderSafeAscii(value: string) {
  return !/[\r\n]/.test(value) && Array.from(value).every((char) => char.charCodeAt(0) <= 255);
}

export function App() {
  const [activeTab, setActiveTab] = useState<TabId>('today');
  const [daemon, setDaemon] = useState<DaemonStatus>({ running: false });
  const [insights, setInsights] = useState<KithInsights>(emptyInsights);
  const [messages, setMessages] = useState<ChatMessageView[]>([welcomeMessage]);
  const [draft, setDraft] = useState(starterPrompts[0]);
  const [profile, setProfile] = useState<ProfileSummary>({ facts: [] });
  const [memory, setMemory] = useState<MemoryReview>({ facts: [] });
  const [sources, setSources] = useState<SourceSettings>({ watch_paths: [] });
  const [sourceDraft, setSourceDraft] = useState('~/Documents\n~/Desktop');
  const [desktopModelMode, setDesktopModelMode] = useState<ModelMode>('api');
  const [desktopModelDraft, setDesktopModelDraft] = useState<ModelDraft>(initialDesktopModelDraft);
  const [backendModelMode, setBackendModelMode] = useState<ModelMode>('ollama');
  const [backendModelDraft, setBackendModelDraft] = useState<ModelDraft>(initialModelDraft);
  const [triageClusters, setTriageClusters] = useState<TriageCluster[]>([]);
  const [isTriageReviewLoading, setIsTriageReviewLoading] = useState(false);
  const [loadState, setLoadState] = useState(initialLoadState);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [sectionErrors, setSectionErrors] = useState<SectionErrors>({});
  const [onboardingDraft, setOnboardingDraft] = useState(initialOnboardingDraft);
  const [onboardingResult, setOnboardingResult] = useState<OnboardingBootstrapResult | null>(null);
  const [firstInsightState, setFirstInsightState] = useState<FirstInsightState>(() => readFirstInsightState());
  const [isFirstInsightOpen, setIsFirstInsightOpen] = useState(() => readFirstInsightState() === 'pending');
  const [lastUserPrompt, setLastUserPrompt] = useState('');
  const [daemonEvents, setDaemonEvents] = useState<DaemonEvent[]>([]);
  const [chatProgress, setChatProgress] = useState<ChatProgress | null>(null);
  const [streamedChatAnswer, setStreamedChatAnswer] = useState('');
  const [daemonStartProgress, setDaemonStartProgress] = useState<DaemonStartProgress | null>(null);
  const [sourceSaveStatus, setSourceSaveStatus] = useState('');
  const [triageSyncStatus, setTriageSyncStatus] = useState('');
  const activeChatRequestRef = useRef<string | null>(null);

  const confirmedFacts = useMemo(
    () => profile.facts.filter((fact) => fact.status === 'confirmed').length,
    [profile.facts],
  );
  const firstInsightNeedsAttention = firstInsightState !== 'completed' && !onboardingResult;

  const setLoading = useCallback((key: LoadKey, value: boolean) => {
    setLoadState((current) => ({ ...current, [key]: value }));
  }, []);

  const pushNotice = useCallback((message: string, tone: Notice['tone'] = 'info') => {
    const notice = { id: createId('notice'), message, tone };
    setNotices((current) => [notice, ...current].slice(0, 4));
  }, []);

  const dismissNotice = useCallback((id: string) => {
    setNotices((current) => current.filter((notice) => notice.id !== id));
  }, []);

  const applySavedModelSettings = useCallback((settings: Awaited<ReturnType<typeof window.kith.settings.modelGet>>) => {
    const desktop = modelDraftFromSavedScope(settings.scopes?.desktop, initialDesktopModelDraft);
    if (desktop) {
      setDesktopModelMode(desktop.mode);
      setDesktopModelDraft(desktop.draft);
    }

    const backend = modelDraftFromSavedScope(settings.scopes?.backend, initialModelDraft);
    if (backend) {
      setBackendModelMode(backend.mode);
      setBackendModelDraft(backend.draft);
    }
  }, []);

  const refreshAll = useCallback(async (rebuild = false) => {
    setLoading('refresh', true);
    try {
      const modelSettings = await window.kith.settings.modelGet();
      applySavedModelSettings(modelSettings);

      const daemonStatus = await window.kith.daemon.status();
      setDaemon(daemonStatus);
      if (!daemonStatus.running) {
        pushNotice(daemonStatus.error ? `本地大脑未启动：${daemonStatus.error}` : '本地大脑未启动。点击左侧按钮启动 Kith。', 'error');
        return;
      }

      const results = await Promise.allSettled([
        window.kith.sources.get(),
        window.kith.profile.summary({ rebuild }),
        window.kith.memory.review({ limit: 40 }),
        window.kith.insights.get({ limit: 12 }),
      ]);
      const nextErrors: SectionErrors = {};

      if (results[0].status === 'fulfilled') {
        setSources(results[0].value);
        setSourceDraft((results[0].value.watch_paths || []).join('\n') || sourceDraft);
      } else {
        nextErrors.sources = `资料范围同步失败：${String(results[0].reason)}`;
      }

      if (results[1].status === 'fulfilled') {
        setProfile(results[1].value);
      } else {
        nextErrors.profile = `画像同步失败：${String(results[1].reason)}`;
      }

      if (results[2].status === 'fulfilled') {
        setMemory(results[2].value);
      } else {
        nextErrors.memory = `记忆同步失败：${String(results[2].reason)}`;
      }

      if (results[3].status === 'fulfilled') {
        setInsights(results[3].value);
      } else {
        nextErrors.insights = `今日建议同步失败：${String(results[3].reason)}`;
      }

      setSectionErrors(nextErrors);
      if (Object.keys(nextErrors).length) {
        pushNotice('部分数据同步失败，页面保留了上一次可用内容。', 'error');
      }
    } catch (error) {
      pushNotice(error instanceof Error ? error.message : String(error), 'error');
    } finally {
      setLoading('refresh', false);
    }
  }, [applySavedModelSettings, pushNotice, setLoading, sourceDraft]);

  useEffect(() => {
    refreshAll(false).catch((error) => {
      pushNotice(error instanceof Error ? error.message : String(error), 'error');
    });
  }, []);

  useEffect(() => {
    if (!daemon.running) {
      return undefined;
    }

    let unsubscribe = () => {};
    window.kith.daemon.events.start().catch(() => undefined);
    unsubscribe = window.kith.daemon.events.onEvent((event) => {
      setDaemonEvents((current) => [event, ...current].slice(0, 120));
      if (event.type === 'assistant.progress' && event.data && typeof event.data === 'object') {
        const data = event.data as {
          request_id?: unknown;
          stage?: unknown;
          message?: unknown;
          progress?: unknown;
        };
        if (typeof data.request_id === 'string' && data.request_id === activeChatRequestRef.current) {
          setChatProgress({
            requestId: data.request_id,
            stage: typeof data.stage === 'string' ? data.stage : 'working',
            message: typeof data.message === 'string' ? data.message : 'Kith 正在处理请求...',
            progress: typeof data.progress === 'number' ? data.progress : 0.2,
          });
        }
      }
      if (event.type === 'llm.delta' && event.data && typeof event.data === 'object') {
        const data = event.data as { request_id?: unknown; content?: unknown };
        if (typeof data.request_id === 'string' && data.request_id === activeChatRequestRef.current && typeof data.content === 'string') {
          setStreamedChatAnswer((current) => `${current}${data.content}`);
        }
      }
      if (event.type === 'desktop.events.error') {
        pushNotice(`实时事件连接失败：${event.error || '未知错误'}`, 'error');
      }
    });

    return () => {
      unsubscribe();
      window.kith.daemon.events.stop().catch(() => undefined);
    };
  }, [daemon.running, pushNotice]);

  async function startDaemon() {
    setLoading('daemon', true);
    const timers: number[] = [];
    let startupSettled = false;
    const scheduleProgress = (delayMs: number, progress: DaemonStartProgress) => {
      timers.push(window.setTimeout(() => {
        if (!startupSettled) {
          setDaemonStartProgress(progress);
        }
      }, delayMs));
    };
    const waitForDaemonReady = async () => {
      let lastStatusError = '';
      const startedAt = Date.now();
      while (Date.now() - startedAt < 45_000) {
        await new Promise((resolve) => window.setTimeout(resolve, 900));
        const current = await window.kith.daemon.status();
        if (current.running) {
          return current;
        }
        lastStatusError = current.error || 'daemon 还没有响应';
        const elapsedRatio = Math.min(1, (Date.now() - startedAt) / 45_000);
        setDaemonStartProgress({
          stage: '等待 daemon 就绪',
          message: `状态探测：${lastStatusError}`,
          progress: Math.min(0.82, 0.54 + elapsedRatio * 0.28),
          tone: 'active',
        });
      }
      throw new Error(`daemon 未在 45 秒内就绪${lastStatusError ? `：${lastStatusError}` : ''}`);
    };
    setDaemonStartProgress({
      stage: '准备启动',
      message: '确认本地 Python 环境和 daemon 状态。',
      progress: 0.08,
      tone: 'active',
    });
    scheduleProgress(700, {
      stage: '启动后台服务',
      message: '正在唤醒本地 Kith daemon。',
      progress: 0.28,
      tone: 'active',
    });
    scheduleProgress(2200, {
      stage: '等待 RPC 就绪',
      message: '正在等待 syscall / HTTP API 响应。',
      progress: 0.54,
      tone: 'active',
    });
    scheduleProgress(5200, {
      stage: '初始化索引服务',
      message: 'daemon 已启动时会继续恢复索引、记忆和事件流。',
      progress: 0.72,
      tone: 'active',
    });
    pushNotice('正在启动 Kith，本地索引服务准备好后会自动刷新。');
    try {
      let startError = '';
      const status = await Promise.any([
        window.kith.daemon.start().catch((error) => {
          startError = errorMessage(error);
          throw error;
        }),
        waitForDaemonReady(),
      ]).catch((error) => {
        if (startError) {
          throw new Error(startError);
        }
        if (error instanceof AggregateError) {
          throw new Error(error.errors.map(errorMessage).join('；'));
        }
        throw error;
      });
      startupSettled = true;
      setDaemon(status);
      setDaemonStartProgress({
        stage: '后台同步今日建议',
        message: '本地大脑已在线；画像、记忆和今日建议会在后台继续刷新。',
        progress: 0.92,
        tone: 'active',
      });
      pushNotice('本地大脑已启动，今日建议会在后台同步。', 'success');
      refreshAll(false).catch((error) => {
        pushNotice(`后台同步今日建议失败：${errorMessage(error)}`, 'error');
      });
      setDaemonStartProgress({
        stage: '启动完成',
        message: '索引、画像和记忆服务已可用；慢查询不会阻塞启动。',
        progress: 1,
        tone: 'success',
      });
      window.setTimeout(() => setDaemonStartProgress(null), 1800);
    } catch (error) {
      setDaemonStartProgress({
        stage: '启动失败',
        message: errorMessage(error),
        progress: 1,
        tone: 'error',
      });
      pushNotice(`启动失败：${errorMessage(error)}`, 'error');
    } finally {
      startupSettled = true;
      timers.forEach((timer) => window.clearTimeout(timer));
      setLoading('daemon', false);
    }
  }

  async function submitChat(event?: FormEvent) {
    event?.preventDefault();
    const message = draft.trim();
    if (!message) return;

    const nextMessages: ChatMessageView[] = [...messages, { id: createId('user'), role: 'user', content: message }];
    const requestId = createId('chat-run');
    activeChatRequestRef.current = requestId;
    setStreamedChatAnswer('');
    setChatProgress({
      requestId,
      stage: 'start',
      message: '发送请求到前端 Desktop 对话模型。',
      progress: 0.03,
    });
    setMessages(nextMessages);
    setLastUserPrompt(message);
    setDraft('');
    setLoading('chat', true);

    try {
      const chatPayload = {
        message,
        history: nextMessages.slice(-8),
        request_id: requestId,
        model_settings: buildModelSettingsPayload(desktopModelMode, desktopModelDraft),
      };
      const response = await window.kith.assistant.chat(chatPayload);
      setMessages([
        ...nextMessages,
        {
          id: createId('assistant'),
          role: 'assistant',
          content: response.answer,
          sources: response.sources,
        },
      ]);
    } catch (error) {
      setMessages([
        ...nextMessages,
        {
          id: createId('assistant-error'),
          role: 'assistant',
          content: `我现在没法回答：${error instanceof Error ? error.message : String(error)}`,
          failed: true,
        },
      ]);
    } finally {
      activeChatRequestRef.current = null;
      setStreamedChatAnswer('');
      setChatProgress(null);
      setLoading('chat', false);
    }
  }

  function askKith(prompt: string) {
    setDraft(prompt);
    setActiveTab('chat');
  }

  function retryLastPrompt() {
    if (!lastUserPrompt) return;
    setDraft(lastUserPrompt);
    setActiveTab('chat');
  }

  async function generateProfile() {
    setLoading('memory', true);
    try {
      await refreshAll(true);
      pushNotice('画像已更新。你可以在记忆页校正它。', 'success');
    } finally {
      setLoading('memory', false);
    }
  }

  async function updateFact(factId: string, status: ProfileFact['status']) {
    setLoading('memory', true);
    try {
      await window.kith.memory.feedback({ fact_id: factId, status });
      await refreshAll(false);
      pushNotice('记忆反馈已保存。', 'success');
    } catch (error) {
      pushNotice(`保存记忆反馈失败：${error instanceof Error ? error.message : String(error)}`, 'error');
    } finally {
      setLoading('memory', false);
    }
  }

  async function saveSources() {
    setLoading('sources', true);
    setSourceSaveStatus('正在把资料范围发送到本地 daemon...');
    try {
      const watch_paths = sourceDraft
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
      const updated = await window.kith.sources.configure({ watch_paths });
      setSources(updated);
      setSourceSaveStatus(updated.scan_triggered ? '已保存，并已通知后端开始后台重扫。' : '已保存；后端已接收，必要时重启后继续扫描。');
      pushNotice(updated.scan_triggered ? '资料范围已保存，后台重扫已开始。' : '资料范围已保存。', 'success');
    } catch (error) {
      setSourceSaveStatus(`保存失败：${errorMessage(error)}`);
      pushNotice(`保存资料范围失败：${error instanceof Error ? error.message : String(error)}`, 'error');
    } finally {
      setLoading('sources', false);
    }
  }

  async function saveModel(scope: ModelScope, mode: ModelMode, draft: ModelDraft) {
    const apiKey = draft.api_key.trim();
    if (mode === 'ollama' && !draft.model.trim()) {
      pushNotice('请先填写 Ollama 模型名，例如 llama3.1。', 'error');
      return;
    }
    if (mode === 'api' && !draft.model.trim()) {
      pushNotice('请先填写在线 API 的模型名。', 'error');
      return;
    }
    if (mode === 'api' && !apiKey && !draft.has_key) {
      pushNotice('未填写 API Key：会保存配置；聊天时需要本机环境变量里已有对应 Key，或稍后补填 Key。', 'info');
    }
    if (mode === 'api' && apiKey && !isHeaderSafeAscii(apiKey)) {
      const clearedDraft = { ...draft, api_key: '' };
      if (scope === 'desktop') {
        setDesktopModelDraft(clearedDraft);
      } else {
        setBackendModelDraft(clearedDraft);
      }
      pushNotice('API Key 只能填 provider token，不能包含中文说明、模型回复或换行。', 'error');
      return;
    }
    setLoading('model', true);
    try {
      await window.kith.settings.model({ scope, ...buildModelSettingsPayload(mode, { ...draft, api_key: apiKey }) });
      const settings = await window.kith.settings.modelGet();
      applySavedModelSettings(settings);
      const modeLabel = mode === 'ollama' ? '本机 Ollama' : mode === 'api' ? '在线 API' : '不用 LLM';
      const scopeLabel = scope === 'desktop' ? 'Desktop 对话模型' : '后端任务模型';
      pushNotice(`${scopeLabel}已保存：${modeLabel}${scope === 'backend' ? '。后端配置已尝试热重载。' : '。'}`, 'success');
    } catch (error) {
      pushNotice(`保存模型设置失败：${error instanceof Error ? error.message : String(error)}`, 'error');
    } finally {
      setLoading('model', false);
    }
  }

  async function fetchModelList(scope: ModelScope, mode: ModelMode, draft: ModelDraft) {
    const result = await window.kith.settings.modelList({
      scope,
      ...buildModelSettingsPayload(mode, draft),
    });
    return result.models || [];
  }

  const refreshTriageReview = useCallback(async () => {
    setIsTriageReviewLoading(true);
    setTriageSyncStatus('正在向后端同步待确认目录...');
    try {
      const result = await window.kith.triage.clusters({ depth: 2, limit: 80 });
      setTriageClusters(result.clusters || []);
      setTriageSyncStatus(`已同步 ${result.clusters?.length || 0} 个待确认/建议排除目录。`);
      pushNotice(`待确认目录已同步：${result.clusters?.length || 0} 个。`, 'success');
    } catch (error) {
      setTriageSyncStatus(`同步失败：${errorMessage(error)}`);
      pushNotice(`同步待确认目录失败：${errorMessage(error)}`, 'error');
    } finally {
      setIsTriageReviewLoading(false);
    }
  }, [pushNotice]);

  const loadTriageFiles = useCallback(async (prefix: string) => {
    return window.kith.triage.files({ prefix, limit: 200 });
  }, []);

  async function applyTriageDecision(prefix: string | string[], status: 'high' | 'skip') {
    const prefixes = Array.isArray(prefix)
      ? [...new Set(prefix.map((item) => item.trim()).filter(Boolean))]
      : [prefix.trim()].filter(Boolean);
    if (!prefixes.length) {
      pushNotice('请先勾选要处理的目录。', 'error');
      return;
    }
    setIsTriageReviewLoading(true);
    try {
      const results = await Promise.all(
        prefixes.map((item) => window.kith.triage.clusterDecision({ prefix: item, status })),
      );
      const failed = results.find((result) => !result.success);
      if (failed) {
        throw new Error(failed.error || '目录决策失败');
      }
      const updated = results.reduce((sum, result) => sum + (result.updated || 0), 0);
      pushNotice(`${status === 'high' ? '已纳入总结' : '已排除噪音'}：${updated} 个文件。`, 'success');
      await refreshTriageReview();
      await refreshAll(false);
    } catch (error) {
      pushNotice(`更新目录决策失败：${errorMessage(error)}`, 'error');
    } finally {
      setIsTriageReviewLoading(false);
    }
  }

  useEffect(() => {
    if (activeTab !== 'privacy' || !daemon.running) {
      return;
    }
    refreshTriageReview().catch((error) => {
      pushNotice(`同步待确认目录失败：${errorMessage(error)}`, 'error');
    });
  }, [activeTab, daemon.running, pushNotice, refreshTriageReview]);

  async function runOnboardingBootstrap() {
    setLoading('firstInsight', true);
    pushNotice('Kith 正在生成第一版画像：会结合你的回答、已索引文件，以及你授权的浏览标题/域名聚合。');
    try {
      const result = await window.kith.onboarding.bootstrap({
        answers: {
          ...defaultOnboardingAnswers,
          roles: listFromText(onboardingDraft.roles),
          goals: listFromText(onboardingDraft.goals),
          interests: listFromText(onboardingDraft.interests),
          current_focus: listFromText(onboardingDraft.current_focus),
          planning_style: onboardingDraft.planning_style,
          suggestion_cadence: onboardingDraft.suggestion_cadence,
        },
        include_browser_history: onboardingDraft.include_browser_history,
        history_days: 30,
        history_limit: 500,
      });
      setOnboardingResult(result);
      persistFirstInsightState('completed');
      setFirstInsightState('completed');
      pushNotice(`第一版画像已完成：${result.topics.length} 个主题，${result.profile_facts.length} 条可校正记忆。`, 'success');
      await refreshAll(false);
    } catch (error) {
      pushNotice(`画像初始化失败：${error instanceof Error ? error.message : String(error)}`, 'error');
    } finally {
      setLoading('firstInsight', false);
    }
  }

  function dismissFirstInsight() {
    if (firstInsightState === 'completed' || onboardingResult) {
      setIsFirstInsightOpen(false);
      return;
    }
    persistFirstInsightState('dismissed');
    setFirstInsightState('dismissed');
    setIsFirstInsightOpen(false);
    pushNotice('本次先收起 First Insight；完成前下次启动还会自动提醒。');
  }

  function handleSuggestion(suggestion: InsightSuggestion) {
    if (suggestion.kind === 'memory') {
      setActiveTab('memory');
    } else if (suggestion.kind === 'privacy') {
      setActiveTab('privacy');
    } else {
      askKith(`${suggestion.title}\n\n${suggestion.detail}`);
    }
  }

  async function stopDaemon() {
    if (!window.confirm('确定要停止本地大脑吗？停止后聊天、画像和今日建议都会暂时不可用。')) {
      return;
    }
    try {
      await window.kith.daemon.stop();
      setDaemon({ running: false });
      pushNotice('本地大脑已停止。', 'success');
    } catch (error) {
      pushNotice(`停止失败：${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  }

  async function openDashboard() {
    try {
      await window.kith.daemon.openDashboard();
      pushNotice('原始 Dashboard 已打开。', 'success');
    } catch (error) {
      pushNotice(`打开原始 Dashboard 失败：${errorMessage(error)}`, 'error');
    }
  }

  return (
    <>
      <AppShell
        activeTab={activeTab}
        daemon={daemon}
        daemonStartProgress={daemonStartProgress}
        firstInsightNeedsAttention={firstInsightNeedsAttention}
        loadState={loadState}
        notices={notices}
        onDismissNotice={dismissNotice}
        onRefresh={() => refreshAll(false)}
        onStartDaemon={startDaemon}
        onTabChange={setActiveTab}
      >
        {activeTab === 'today' && (
          <TodayView
            daemon={daemon}
            errors={sectionErrors}
            firstInsightState={firstInsightState}
            insights={insights}
            memory={memory}
            onboardingResult={onboardingResult}
            onAskKith={askKith}
            onOpenFirstInsight={() => setIsFirstInsightOpen(true)}
            onOpenDashboard={openDashboard}
            onRefresh={() => refreshAll(false)}
            onReviewMemory={() => setActiveTab('memory')}
            onSuggestionAction={handleSuggestion}
            profile={profile}
            sources={sources}
          />
        )}

        {activeTab === 'chat' && (
          <ChatView
            draft={draft}
            isLoading={loadState.chat}
            messages={messages}
            onClear={() => setMessages([welcomeMessage])}
            onDraftChange={setDraft}
            onRetry={retryLastPrompt}
            onSubmit={submitChat}
            progress={chatProgress}
            streamedAnswer={streamedChatAnswer}
            traceEvents={daemonEvents}
          />
        )}

        {activeTab === 'memory' && (
          <MemoryView
            confirmedFacts={confirmedFacts}
            isLoading={loadState.memory || loadState.refresh}
            memory={memory}
            onGenerateProfile={generateProfile}
            onUpdateFact={updateFact}
            profile={profile}
          />
        )}

        {activeTab === 'privacy' && (
          <PrivacyView
            isSourcesSaving={loadState.sources}
            isTriageReviewLoading={isTriageReviewLoading}
            onSaveSources={saveSources}
            onSourceDraftChange={setSourceDraft}
            onLoadTriageFiles={loadTriageFiles}
            onTriageDecision={applyTriageDecision}
            onRefreshTriageReview={refreshTriageReview}
            sourceDraft={sourceDraft}
            sourceSaveStatus={sourceSaveStatus}
            sources={sources}
            triageSyncStatus={triageSyncStatus}
            triageClusters={triageClusters}
          />
        )}

        {activeTab === 'settings' && (
          <SettingsView
            backendModelDraft={backendModelDraft}
            backendModelMode={backendModelMode}
            desktopModelDraft={desktopModelDraft}
            desktopModelMode={desktopModelMode}
            daemon={daemon}
            isModelSaving={loadState.model}
            onBackendModelDraftChange={setBackendModelDraft}
            onBackendModelModeChange={setBackendModelMode}
            onDesktopModelDraftChange={setDesktopModelDraft}
            onDesktopModelModeChange={setDesktopModelMode}
            onFetchModels={fetchModelList}
            onSaveModel={saveModel}
          />
        )}

        {activeTab === 'diagnostics' && (
          <DiagnosticsView
            daemon={daemon}
            events={daemonEvents}
            onOpenDashboard={openDashboard}
            onStopDaemon={stopDaemon}
          />
        )}
      </AppShell>

      {isFirstInsightOpen && (
        <FirstInsightModal
          draft={onboardingDraft}
          firstInsightState={firstInsightState}
          isGenerating={loadState.firstInsight}
          onAskKith={(prompt) => {
            setIsFirstInsightOpen(false);
            askKith(prompt);
          }}
          onClose={() => setIsFirstInsightOpen(false)}
          onDismiss={dismissFirstInsight}
          onDraftChange={setOnboardingDraft}
          onReviewMemory={() => {
            setIsFirstInsightOpen(false);
            setActiveTab('memory');
          }}
          onRunOnboarding={runOnboardingBootstrap}
          result={onboardingResult}
        />
      )}
    </>
  );
}
