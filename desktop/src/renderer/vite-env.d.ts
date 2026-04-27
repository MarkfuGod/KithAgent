/// <reference types="vite/client" />

declare global {
  type DaemonStatus = {
    running: boolean;
    status?: Record<string, unknown>;
    error?: string;
  };

  type ChatMessage = {
    role: 'user' | 'assistant';
    content: string;
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

  type SourceSettings = {
    watch_paths: string[];
    model_mode?: string;
    llm_configured?: boolean;
  };

  type KithApi = {
    daemon: {
      status: () => Promise<DaemonStatus>;
      start: () => Promise<DaemonStatus>;
      stop: () => Promise<{ stopped: boolean }>;
      openDashboard: () => Promise<void>;
    };
    jarvis: {
      chat: (payload: { message: string; history: ChatMessage[] }) => Promise<{ answer: string; context?: unknown }>;
    };
    profile: {
      summary: (payload?: { rebuild?: boolean }) => Promise<ProfileSummary>;
    };
    memory: {
      review: (payload?: { status?: string; limit?: number }) => Promise<MemoryReview>;
      feedback: (payload: { fact_id: string; status: ProfileFact['status'] }) => Promise<{ updated: boolean }>;
    };
    sources: {
      get: () => Promise<SourceSettings>;
      configure: (payload: { watch_paths: string[] }) => Promise<SourceSettings>;
    };
    settings: {
      model: (payload: {
        mode: 'api' | 'ollama' | 'local';
        provider?: string;
        base_url?: string;
        api_key?: string;
        model?: string;
      }) => Promise<{ saved: boolean; mode: string }>;
    };
  };

  interface Window {
    kith: KithApi;
  }
}

export {};
