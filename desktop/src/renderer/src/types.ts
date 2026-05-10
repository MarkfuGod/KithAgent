export const tabs = [
  { id: 'today', label: 'Today', hint: '继续、简报、下一步' },
  { id: 'chat', label: 'Ask', hint: '向授权资料提问' },
  { id: 'memory', label: 'Memory', hint: '确认、纠正、隐藏' },
  { id: 'privacy', label: 'Privacy', hint: '来源、跳过、可见范围' },
  { id: 'settings', label: 'Models', hint: '本地 / API / 路由' },
  { id: 'diagnostics', label: 'Advanced', hint: '状态、事件、开发工具' },
] as const;

export type TabId = (typeof tabs)[number]['id'];

export type FirstInsightState = 'pending' | 'dismissed' | 'completed';

export type OnboardingDraft = {
  roles: string;
  goals: string;
  interests: string;
  current_focus: string;
  planning_style: string;
  suggestion_cadence: string;
  include_browser_history: boolean;
};

export type LoadKey = 'daemon' | 'refresh' | 'chat' | 'firstInsight' | 'sources' | 'model' | 'memory';

export type LoadState = Record<LoadKey, boolean>;

export type Notice = {
  id: string;
  message: string;
  tone: 'info' | 'success' | 'error';
};

export type SectionErrors = Partial<Record<'sources' | 'profile' | 'memory' | 'insights', string>>;

export type ChatMessageView = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: Array<Record<string, unknown>>;
  failed?: boolean;
  retryable?: boolean;
};

export type ChatProgress = {
  requestId: string;
  stage: string;
  message: string;
  progress: number;
};

export type DaemonStartProgress = {
  stage: string;
  message: string;
  progress: number;
  tone?: 'active' | 'success' | 'error';
};

export type ModelMode = 'api' | 'ollama' | 'local';
export type ModelScope = 'desktop' | 'backend';

export type ModelDraft = {
  name: string;
  api_mode: string;
  provider: string;
  base_url: string;
  api_host: string;
  api_path: string;
  model: string;
  model_display_name: string;
  model_type: string;
  api_key: string;
  api_key_env?: string;
  has_key?: boolean;
  improve_compatibility: boolean;
  vision: boolean;
  reasoning: boolean;
  tools: boolean;
  context_window: string;
  max_output_tokens: string;
};

export const initialLoadState: LoadState = {
  daemon: false,
  refresh: false,
  chat: false,
  firstInsight: false,
  sources: false,
  model: false,
  memory: false,
};

export const initialOnboardingDraft: OnboardingDraft = {
  roles: '创作者 / 开发者 / 学习者',
  goals: '让 Kith 帮我整理文件、理解兴趣、提醒下一步',
  interests: 'AI 工具、本地自动化、产品设计',
  current_focus: '把 Kith 做成真正懂我的本地助理',
  planning_style: 'lightweight',
  suggestion_cadence: 'daily',
  include_browser_history: true,
};

export const defaultOnboardingAnswers: OnboardingAnswers = {
  roles: [],
  goals: [],
  interests: [],
  current_focus: [],
  planning_style: 'lightweight',
  suggestion_cadence: 'daily',
};

export const emptyInsights: KithInsights = {
  generated_at: 0,
  overview: {
    total_files: 0,
    summarized_files: 0,
    knowledge_entries: 0,
    source_records: 0,
    insight_items: 0,
    total_size_bytes: 0,
    recent_7d_modified: 0,
    latest_indexed_at: 0,
    inferred_facts: 0,
    confirmed_facts: 0,
    rag_pending: 0,
    confidence: 0,
  },
  file_organization: [],
  cleanup_candidates: [],
  video_interests: [],
  web_interests: {
    top_domains: [],
    topics: [],
    bookmarks: [],
    downloads: [],
    has_browser_signal: false,
  },
  suggestions: [],
};
