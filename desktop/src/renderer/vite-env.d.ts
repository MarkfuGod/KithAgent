/// <reference types="vite/client" />

declare global {
  type DaemonStatus = {
    running: boolean;
    status?: Record<string, unknown>;
    transport?: 'http' | 'unix_socket' | string;
    error?: string;
  };

  type DaemonEvent = {
    type: string;
    data?: unknown;
    error?: string;
  };

  type ChatMessage = {
    role: 'user' | 'assistant';
    content: string;
  };

  type ChatSource = {
    id?: string;
    title?: string;
    kind: 'backend_skill' | 'backend_skill_error' | string;
    result?: unknown;
    error?: string;
  };

  type ProfileFact = {
    id: string;
    category: string;
    statement: string;
    source_type: string;
    source_ref: string;
    confidence: number;
    status: 'inferred' | 'confirmed' | 'rejected' | 'hidden';
    created_at?: number;
    updated_at?: number;
    metadata?: Record<string, unknown>;
  };

  type ProfileSummary = {
    profile?: Record<string, unknown>;
    facts: ProfileFact[];
    stats?: Record<string, unknown>;
  };

  type MemoryReview = {
    facts: ProfileFact[];
    knowledge?: Array<Record<string, unknown>>;
  };

  type InsightSuggestion = {
    kind: string;
    title: string;
    detail: string;
    action: string;
    priority: 'low' | 'medium' | 'high' | string;
  };

  type FileOrganizationInsight = {
    directory: string;
    prefix: string;
    total: number;
    total_size: number;
    statuses: Record<string, number>;
    recommendation: 'include' | 'exclude' | 'review' | string;
    reason: string;
    score: number;
    last_modified: number;
  };

  type CleanupCandidate = {
    path: string;
    full_path: string;
    file_type: string;
    size_bytes: number;
    modified_at: number;
    age_days: number;
    triage_status: string;
    priority?: number;
    summary?: string;
    risk: 'low' | 'medium' | 'high' | string;
    reason: string;
    action: string;
    missing_on_disk: boolean;
  };

  type VideoInterest = {
    domain: string;
    title: string;
    count: number;
    last_seen: number;
    topics: string[];
    source_type: string;
  };

  type WebInterests = {
    top_domains: Array<{ domain: string; count: number; last_seen: number; kind: string }>;
    topics: Array<{ topic: string; confidence: number; source_type: string; updated_at: number }>;
    bookmarks: Array<Record<string, unknown>>;
    downloads: Array<Record<string, unknown>>;
    has_browser_signal: boolean;
  };

  type KithInsights = {
    generated_at: number;
    overview: {
      total_files: number;
      summarized_files: number;
      knowledge_entries: number;
      source_records: number;
      insight_items: number;
      total_size_bytes: number;
      recent_7d_modified: number;
      latest_indexed_at: number;
      inferred_facts: number;
      confirmed_facts: number;
      rag_pending: number;
      confidence: number;
    };
    file_organization: FileOrganizationInsight[];
    cleanup_candidates: CleanupCandidate[];
    video_interests: VideoInterest[];
    web_interests: WebInterests;
    suggestions: InsightSuggestion[];
  };

  type OnboardingAnswers = {
    roles: string[];
    goals: string[];
    interests: string[];
    current_focus: string[];
    planning_style: string;
    suggestion_cadence: string;
  };

  type OnboardingBootstrapResult = {
    ready: boolean;
    phase: string;
    run_id: string;
    elapsed_seconds: number;
    profile: Record<string, unknown>;
    topics: Array<Record<string, unknown>>;
    suggestions: Array<Record<string, unknown>>;
    sources: Array<Record<string, unknown>>;
    profile_facts: ProfileFact[];
    browser_history: {
      enabled: boolean;
      sources_count: number;
      entries_count: number;
      bookmarks_count: number;
      downloads_count: number;
      top_domains: Array<{ domain: string; count: number }>;
    };
    next_actions: string[];
  };

  type SourceSettings = {
    watch_paths: string[];
    model_mode?: string;
    llm_configured?: boolean;
    pruned_files?: number;
    scan_triggered?: boolean;
    restart_recommended?: boolean;
  };

  type TriageCluster = {
    directory: string;
    prefix: string;
    total: number;
    total_size: number;
    statuses: Record<string, number>;
    recommendation: 'include' | 'exclude' | 'review' | string;
    reason: string;
    config: number;
    data: number;
    generated: number;
    noise_parent?: boolean;
    samples?: Array<{
      path: string;
      file_type: string;
      size_bytes: number;
      status: string;
    }>;
  };

  type TriageFile = {
    path: string;
    file_type: string;
    size_bytes: number;
    status: string;
  };

  type KithApi = {
    daemon: {
      status: () => Promise<DaemonStatus>;
      start: () => Promise<DaemonStatus>;
      stop: () => Promise<{ stopped: boolean }>;
      openDashboard: () => Promise<{ running: boolean; url: string; pid?: number; opened?: unknown }>;
      events: {
        start: () => Promise<{ started: boolean }>;
        stop: () => Promise<{ stopped: boolean }>;
        onEvent: (callback: (event: DaemonEvent) => void) => () => void;
      };
    };
    assistant: {
      chat: (payload: {
        message: string;
        history: ChatMessage[];
        request_id?: string;
        model_settings?: Record<string, unknown>;
      }) => Promise<{ answer: string; context?: unknown; sources?: ChatSource[] }>;
    };
    insights: {
      get: (payload?: { limit?: number }) => Promise<KithInsights>;
    };
    onboarding: {
      bootstrap: (payload: {
        answers: OnboardingAnswers;
        include_browser_history: boolean;
        history_days?: number;
        history_limit?: number;
      }) => Promise<OnboardingBootstrapResult>;
    };
    profile: {
      summary: (payload?: { rebuild?: boolean }) => Promise<ProfileSummary>;
    };
    memory: {
      review: (payload?: { status?: string; limit?: number }) => Promise<MemoryReview>;
      feedback: (payload: { fact_id: string; status: ProfileFact['status'] }) => Promise<{ updated: boolean }>;
    };
    capabilities: {
      list: () => Promise<{
        contract_version?: string;
        version: string;
        generated_at?: number;
        capabilities: Array<{
          id: string;
          status: string;
          sensitivity: string;
          commands: string[];
          permission: string;
        }>;
      }>;
    };
    context: {
      agentBrief: (payload?: {
        caller?: string;
        workspace?: string;
        task?: string;
        session_id?: string;
        surface?: string;
      }) => Promise<{
        contract_version?: string;
        session_key: string;
        session?: {
          key: string;
          source_type: string;
          platform: string;
          caller: string;
          workspace_hash: string;
          session_id: string;
        };
        caller: string;
        surface: string;
        workspace: string;
        task: string;
        generated_at: number;
        evidence_policy?: Record<string, unknown>;
        evidence?: {
          status: string;
          workspace_file_count: number;
          recent_file_count: number;
          profile_fact_count: number;
          warnings: string[];
        };
        context_apis?: Array<{ name: string; syscall: string; status: string }>;
        profile: Record<string, unknown>;
        recent_files: Array<Record<string, unknown>>;
        workspace_files: Array<Record<string, unknown>>;
        context_briefs: unknown[];
        behavior_insights: unknown[];
        handoff_prompt: string;
      }>;
    };
    sources: {
      get: () => Promise<SourceSettings>;
      configure: (payload: { watch_paths: string[] }) => Promise<SourceSettings>;
    };
    settings: {
      modelGet: () => Promise<{
        exists: boolean;
        mode?: string;
        desktop_mode?: string;
        default_provider?: string;
        scopes?: Record<string, {
          scope?: string;
          mode?: 'api' | 'ollama' | 'local' | string;
          provider?: string;
          base_url?: string;
          api_key_env?: string;
          has_key?: boolean;
          ui?: {
            name?: string;
            api_mode?: string;
            api_host?: string;
            api_path?: string;
            model?: string;
            model_display_name?: string;
            model_type?: string;
            capabilities?: {
              vision?: boolean;
              reasoning?: boolean;
              tools?: boolean;
            };
            context_window?: string;
            max_output_tokens?: string;
            improve_compatibility?: boolean;
          };
        }>;
      }>;
      modelList: (payload: {
        mode: 'api' | 'ollama' | 'local';
        scope?: 'desktop' | 'backend';
        provider?: string;
        base_url?: string;
        api_host?: string;
        api_path?: string;
        api_key?: string;
        api_key_env?: string;
      }) => Promise<{ models: string[] }>;
      model: (payload: {
        mode: 'api' | 'ollama' | 'local';
        scope?: 'desktop' | 'backend';
        name?: string;
        api_mode?: string;
        provider?: string;
        base_url?: string;
        api_host?: string;
        api_path?: string;
        api_key?: string;
        api_key_env?: string;
        model?: string;
        model_display_name?: string;
        model_type?: string;
        improve_compatibility?: boolean;
        vision?: boolean;
        reasoning?: boolean;
        tools?: boolean;
        context_window?: string;
        max_output_tokens?: string;
      }) => Promise<{ saved: boolean; mode: string }>;
    };
    triage: {
      clusters: (payload?: { depth?: number; limit?: number }) => Promise<{ clusters: TriageCluster[]; total_clusters: number }>;
      files: (payload: { prefix: string; limit?: number }) => Promise<{ prefix: string; total_files: number; files: TriageFile[]; limit: number }>;
      clusterDecision: (payload: { prefix: string; status: 'high' | 'medium' | 'low' | 'skip' }) => Promise<{ success: boolean; status: string; updated: number; error?: string }>;
    };
  };

  interface Window {
    kith: KithApi;
  }
}

export {};
