import { useEffect, useMemo, useRef, useState } from 'react';
import type { ModelDraft, ModelMode, ModelScope } from '../../types';
import { normalizeApiBaseUrl } from '../../lib/modelSettings';

type ModelPreset = {
  id: string;
  display: string;
  model: string;
  type: string;
  providerIds: string[];
  capabilities: { vision: boolean; reasoning: boolean; tools: boolean };
  context: string;
  output: string;
};

const modelPresets: ModelPreset[] = [
  {
    id: 'grok',
    display: 'grok',
    model: 'x-ai/grok-4',
    type: 'chat',
    providerIds: ['openrouter'],
    capabilities: { vision: true, reasoning: true, tools: true },
    context: '128000',
    output: '4096',
  },
  {
    id: 'gemini-flash',
    display: 'gemini-flash',
    model: 'google/gemini-2.5-flash',
    type: 'chat',
    providerIds: ['openrouter'],
    capabilities: { vision: true, reasoning: false, tools: true },
    context: '1000000',
    output: '8192',
  },
  {
    id: 'claude-sonnet-4',
    display: 'claude-sonnet-4',
    model: 'anthropic/claude-sonnet-4',
    type: 'chat',
    providerIds: ['openrouter'],
    capabilities: { vision: true, reasoning: true, tools: true },
    context: '200000',
    output: '8192',
  },
  {
    id: 'chatgpt',
    display: 'chatgpt',
    model: 'openai/gpt-4o-mini',
    type: 'chat',
    providerIds: ['openrouter'],
    capabilities: { vision: true, reasoning: false, tools: true },
    context: '128000',
    output: '4096',
  },
  {
    id: 'qwen-plus-latest',
    display: 'qwen-plus-latest',
    model: 'qwen-plus-latest',
    type: 'chat',
    providerIds: ['dashscope'],
    capabilities: { vision: false, reasoning: false, tools: true },
    context: '128000',
    output: '8192',
  },
  {
    id: 'qwen-max-latest',
    display: 'qwen-max-latest',
    model: 'qwen-max-latest',
    type: 'chat',
    providerIds: ['dashscope'],
    capabilities: { vision: false, reasoning: true, tools: true },
    context: '128000',
    output: '8192',
  },
  {
    id: 'qwen-turbo-latest',
    display: 'qwen-turbo-latest',
    model: 'qwen-turbo-latest',
    type: 'chat',
    providerIds: ['dashscope'],
    capabilities: { vision: false, reasoning: false, tools: true },
    context: '1000000',
    output: '8192',
  },
  {
    id: 'qwen3.6-plus',
    display: 'qwen3.6-plus',
    model: 'qwen3.6-plus',
    type: 'chat',
    providerIds: ['dashscope'],
    capabilities: { vision: false, reasoning: true, tools: true },
    context: '1000000',
    output: '8192',
  },
  {
    id: 'qwen3-max',
    display: 'qwen3-max',
    model: 'qwen3-max',
    type: 'chat',
    providerIds: ['dashscope'],
    capabilities: { vision: false, reasoning: true, tools: true },
    context: '1000000',
    output: '8192',
  },
  {
    id: 'qwen-flash',
    display: 'qwen-flash',
    model: 'qwen-flash',
    type: 'chat',
    providerIds: ['dashscope'],
    capabilities: { vision: false, reasoning: false, tools: true },
    context: '1000000',
    output: '8192',
  },
  {
    id: 'qwen3-coder-plus',
    display: 'qwen3-coder-plus',
    model: 'qwen3-coder-plus',
    type: 'chat',
    providerIds: ['dashscope'],
    capabilities: { vision: false, reasoning: true, tools: true },
    context: '1000000',
    output: '8192',
  },
  {
    id: 'gpt-4o-mini',
    display: 'gpt-4o-mini',
    model: 'gpt-4o-mini',
    type: 'chat',
    providerIds: ['openai'],
    capabilities: { vision: true, reasoning: false, tools: true },
    context: '128000',
    output: '4096',
  },
  {
    id: 'llama3.1',
    display: 'llama3.1',
    model: 'llama3.1',
    type: 'chat',
    providerIds: ['ollama'],
    capabilities: { vision: false, reasoning: false, tools: true },
    context: '',
    output: '',
  },
];

const providerPresets = [
  {
    id: 'openrouter',
    label: 'OpenRouter',
    mode: 'api',
    api_mode: 'openai_compatible',
    provider: 'openai_compatible',
    api_host: 'https://openrouter.ai/api/v1/chat/completions',
    api_path: '/chat/completions',
    api_key_env: 'OPENROUTER_API_KEY',
    model: 'x-ai/grok-4',
    model_display_name: 'grok',
    api_key: '',
    hint: 'OpenAI API 兼容；保存时会自动转成 https://openrouter.ai/api/v1。',
  },
  {
    id: 'ollama',
    label: 'Ollama',
    mode: 'ollama',
    api_mode: 'openai_compatible',
    provider: 'openai_compatible',
    api_host: 'http://localhost:11434/v1',
    api_path: '/chat/completions',
    api_key_env: 'OPENAI_COMPATIBLE_API_KEY',
    model: 'llama3.1',
    model_display_name: 'llama3.1',
    api_key: '',
    hint: '本地 Ollama 兼容 OpenAI Chat Completions。',
  },
  {
    id: 'dashscope',
    label: 'DashScope 兼容 API',
    mode: 'api',
    api_mode: 'openai_compatible',
    provider: 'openai_compatible',
    api_host: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    api_path: '/chat/completions',
    api_key_env: 'DASHSCOPE_API_KEY',
    model: 'qwen-plus-latest',
    model_display_name: 'qwen-plus-latest',
    api_key: '',
    hint: '阿里云 OpenAI 兼容模式。',
  },
  {
    id: 'openai',
    label: 'OpenAI API',
    mode: 'api',
    api_mode: 'openai',
    provider: 'openai',
    api_host: 'https://api.openai.com/v1',
    api_path: '/chat/completions',
    api_key_env: 'OPENAI_API_KEY',
    model: 'gpt-4o-mini',
    model_display_name: 'gpt-4o-mini',
    api_key: '',
    hint: '官方 OpenAI API。',
  },
] as const;

const apiModeOptions = [
  { value: 'openai_compatible', label: 'OpenAI API 兼容' },
  { value: 'openai', label: 'OpenAI 官方' },
  { value: 'anthropic_compatible', label: 'Anthropic API 兼容' },
  { value: 'anthropic', label: 'Anthropic 官方' },
] as const;

export type SettingsViewProps = {
  backendModelDraft: ModelDraft;
  backendModelMode: ModelMode;
  daemon: DaemonStatus;
  desktopModelDraft: ModelDraft;
  desktopModelMode: ModelMode;
  isModelSaving: boolean;
  onBackendModelDraftChange: (draft: ModelDraft) => void;
  onBackendModelModeChange: (mode: ModelMode) => void;
  onDesktopModelDraftChange: (draft: ModelDraft) => void;
  onDesktopModelModeChange: (mode: ModelMode) => void;
  onFetchModels: (scope: ModelScope, mode: ModelMode, draft: ModelDraft) => Promise<string[]>;
  onSaveModel: (scope: ModelScope, mode: ModelMode, draft: ModelDraft) => Promise<void>;
};

export function SettingsView({
  backendModelDraft,
  backendModelMode,
  daemon,
  desktopModelDraft,
  desktopModelMode,
  isModelSaving,
  onBackendModelDraftChange,
  onBackendModelModeChange,
  onDesktopModelDraftChange,
  onDesktopModelModeChange,
  onFetchModels,
  onSaveModel,
}: SettingsViewProps) {
  const backendStatus = describeModelStatus(backendModelMode, backendModelDraft, daemon);
  const [activeScope, setActiveScope] = useState<ModelScope>('desktop');
  const [activeSettingsNav, setActiveSettingsNav] = useState<'providers' | 'model'>('providers');
  const [providerFocusToken, setProviderFocusToken] = useState(0);
  const activeDraft = activeScope === 'desktop' ? desktopModelDraft : backendModelDraft;
  const activeMode = activeScope === 'desktop' ? desktopModelMode : backendModelMode;
  const activeStatus = activeScope === 'desktop'
    ? {
      title: '前端 Desktop LLM：只服务前端对话',
      detail: `当前使用 ${desktopModelDraft.model || '未填写模型'}。保存这里不会影响后端任务模型。`,
    }
    : backendStatus;

  function updateActiveMode(mode: ModelMode) {
    if (activeScope === 'desktop') {
      onDesktopModelModeChange(mode);
    } else {
      onBackendModelModeChange(mode);
    }
  }

  function updateActiveDraft(draft: ModelDraft) {
    if (activeScope === 'desktop') {
      onDesktopModelDraftChange(draft);
    } else {
      onBackendModelDraftChange(draft);
    }
  }

  function applyProviderPreset(preset: (typeof providerPresets)[number]) {
    updateActiveMode(preset.mode);
    updateActiveDraft({
      ...activeDraft,
      name: preset.id,
      api_mode: preset.api_mode,
      provider: preset.provider,
      base_url: normalizeApiBaseUrl(preset.api_host, preset.api_path),
      api_host: preset.api_host,
      api_path: preset.api_path,
      api_key_env: preset.api_key_env,
      has_key: false,
      model: preset.model,
      model_display_name: preset.model_display_name,
      api_key: preset.api_key,
      improve_compatibility: true,
    });
  }

  function addCustomProvider() {
    updateActiveMode('api');
    updateActiveDraft({
      ...activeDraft,
      name: 'custom',
      api_mode: 'openai_compatible',
      provider: 'openai_compatible',
      base_url: '',
      api_host: '',
      api_path: '/chat/completions',
      api_key: '',
      api_key_env: 'OPENAI_COMPATIBLE_API_KEY',
      has_key: false,
      model: '',
      model_display_name: '',
      model_type: 'chat',
      improve_compatibility: true,
    });
    setProviderFocusToken((value) => value + 1);
  }

  function scrollToSettingsSection(section: 'providers' | 'model') {
    setActiveSettingsNav(section);
    document.getElementById(`settings-${section}-section`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    });
  }

  return (
    <section className="settings-page settings-workbench">
      <aside className="settings-nav-rail" aria-label="设置分类">
        <button className={activeSettingsNav === 'providers' ? 'active' : ''} onClick={() => scrollToSettingsSection('providers')} type="button">
          <span>模型提供方</span>
          <small>连接谁的 API</small>
        </button>
        <button className={activeSettingsNav === 'model' ? 'active' : ''} onClick={() => scrollToSettingsSection('model')} type="button">
          <span>模型</span>
          <small>当前 provider 的模型</small>
        </button>
        <button disabled title="MCP 接入会在 Agent Context Provider 阶段启用" type="button">
          <span>MCP</span>
          <small>下一阶段启用</small>
        </button>
        <button disabled title="资料范围请到隐私页管理" type="button">
          <span>知识库</span>
          <small>资料范围在隐私页管理</small>
        </button>
      </aside>

      <aside className="provider-column" id="settings-providers-section" aria-label="模型提供方列表">
        <div className="provider-column-heading">
          <strong>模型提供方</strong>
          <div className="mini-segmented" role="tablist" aria-label="模型作用域">
            <button className={activeScope === 'desktop' ? 'active' : ''} onClick={() => setActiveScope('desktop')} type="button">前端</button>
            <button className={activeScope === 'backend' ? 'active' : ''} onClick={() => setActiveScope('backend')} type="button">后端</button>
          </div>
        </div>
        <div className="provider-list">
          {providerPresets.map((preset) => (
            <button
              className={activeDraft.name === preset.id ? 'selected' : ''}
              key={preset.id}
              onClick={() => applyProviderPreset(preset)}
              type="button"
            >
              <b>{preset.label.slice(0, 1)}</b>
              <span>
                <strong>{preset.label}</strong>
                <small>{preset.api_mode} · {normalizeApiBaseUrl(preset.api_host, preset.api_path)}</small>
              </span>
              <i aria-hidden="true" />
            </button>
          ))}
          <button
            className={!providerPresets.some((preset) => preset.id === activeDraft.name) ? 'selected' : ''}
            onClick={addCustomProvider}
            type="button"
          >
            <b>{(activeDraft.name || 'C').slice(0, 1).toUpperCase()}</b>
            <span>
              <strong>{activeDraft.name || 'custom'}</strong>
              <small>{activeDraft.model || '手动配置'}</small>
            </span>
            <i aria-hidden="true" />
          </button>
        </div>
        <button className="provider-add" onClick={addCustomProvider} type="button">+ 添加</button>
      </aside>

      <ModelConfigSection
        description={activeScope === 'desktop'
          ? '用于和用户对话；遇到 /plan、/profile 等 skill command 或相关意图时，才请求后端 skills。'
          : '用于后端 triage、summarizer、profile、今日建议等任务。只有保存这里才会热重载 daemon router。'}
        draft={activeDraft}
        isModelSaving={isModelSaving}
        mode={activeMode}
        onDraftChange={updateActiveDraft}
        onFetchModels={() => onFetchModels(activeScope, activeMode, activeDraft)}
        onModeChange={updateActiveMode}
        onSave={() => onSaveModel(activeScope, activeMode, activeDraft)}
        onToggle={() => setActiveScope(activeScope === 'desktop' ? 'backend' : 'desktop')}
        providerFocusToken={providerFocusToken}
        sectionId="settings-model-section"
        scope={activeScope}
        showProviderPresets={false}
        status={activeStatus}
        title={activeScope === 'desktop' ? '前端 Desktop 对话模型' : '后端任务模型'}
      />
    </section>
  );
}

function ModelSummaryCard({
  detail,
  onClick,
  title,
}: {
  detail: string;
  onClick: () => void;
  title: string;
}) {
  return (
    <button className="model-summary-card" onClick={onClick} type="button">
      <span>
        <small>模型设置</small>
        <strong>{title}</strong>
        <em>{detail}</em>
      </span>
      <b>展开</b>
    </button>
  );
}

function ModelConfigSection({
  description,
  draft: modelDraft,
  isModelSaving,
  mode: modelMode,
  onDraftChange: onModelDraftChange,
  onFetchModels,
  onModeChange: onModelModeChange,
  onSave,
  onToggle,
  providerFocusToken,
  sectionId,
  scope,
  showProviderPresets = true,
  status: modelStatus,
  title,
}: {
  description: string;
  draft: ModelDraft;
  isModelSaving: boolean;
  mode: ModelMode;
  onDraftChange: (draft: ModelDraft) => void;
  onFetchModels: () => Promise<string[]>;
  onModeChange: (mode: ModelMode) => void;
  onSave: () => Promise<void>;
  onToggle: () => void;
  providerFocusToken: number;
  sectionId?: string;
  scope: ModelScope;
  showProviderPresets?: boolean;
  status: { title: string; detail: string };
  title: string;
}) {
  const providerNameInputRef = useRef<HTMLInputElement>(null);
  const [remoteModels, setRemoteModels] = useState<ModelPreset[]>([]);
  const [modelFetchStatus, setModelFetchStatus] = useState('');
  const providerKey = providerKeyFromDraft(modelDraft);
  const visibleModelPresets = useMemo(() => {
    const localPresets = modelPresets.filter((preset) => preset.providerIds.includes(providerKey));
    return remoteModels.length ? remoteModels : localPresets;
  }, [providerKey, remoteModels]);
  const hasCustomModel = Boolean(modelDraft.model)
    && !visibleModelPresets.some((preset) => preset.model === modelDraft.model);

  useEffect(() => {
    if (!providerFocusToken) return;
    providerNameInputRef.current?.focus();
  }, [providerFocusToken]);

  useEffect(() => {
    setRemoteModels([]);
    setModelFetchStatus('');
  }, [modelDraft.name, modelDraft.api_host, modelDraft.base_url]);

  function selectModelMode(mode: ModelMode) {
    onModelModeChange(mode);
    if (mode === 'ollama') {
      onModelDraftChange({
        ...modelDraft,
        name: 'ollama',
        api_mode: 'openai_compatible',
        provider: 'openai_compatible',
        base_url: 'http://localhost:11434/v1',
        api_host: 'http://localhost:11434/v1',
        api_path: '/chat/completions',
        api_key_env: 'OPENAI_COMPATIBLE_API_KEY',
        has_key: false,
        api_key: '',
      });
    } else if (mode === 'local') {
      onModelDraftChange({ ...modelDraft, api_key: '' });
    } else {
      onModelDraftChange({ ...modelDraft, name: modelDraft.name || 'openrouter' });
    }
  }

  function applyProviderPreset(preset: (typeof providerPresets)[number]) {
    onModelModeChange(preset.mode);
    onModelDraftChange({
      ...modelDraft,
      name: preset.id,
      api_mode: preset.api_mode,
      provider: preset.provider,
      base_url: normalizeApiBaseUrl(preset.api_host, preset.api_path),
      api_host: preset.api_host,
      api_path: preset.api_path,
      api_key_env: preset.api_key_env,
      has_key: false,
      model: preset.model,
      model_display_name: preset.model_display_name,
      api_key: preset.api_key,
      improve_compatibility: true,
    });
  }

  function applyModelPreset(preset: (typeof modelPresets)[number]) {
    onModelDraftChange({
      ...modelDraft,
      model: preset.model,
      model_display_name: preset.display,
      model_type: preset.type,
      vision: preset.capabilities.vision,
      reasoning: preset.capabilities.reasoning,
      tools: preset.capabilities.tools,
      context_window: preset.context,
      max_output_tokens: preset.output,
    });
  }

  async function fetchProviderModels() {
    setModelFetchStatus('正在从 provider 获取模型列表...');
    try {
      const models = await onFetchModels();
      const nextModels = models.map((model) => ({
        id: `remote-${model}`,
        display: model,
        model,
        type: 'chat',
        providerIds: [providerKey],
        capabilities: { vision: false, reasoning: false, tools: true },
        context: '',
        output: '',
      }));
      setRemoteModels(nextModels);
      setModelFetchStatus(nextModels.length ? `已获取 ${nextModels.length} 个模型。` : 'provider 没有返回模型列表。');
    } catch (error) {
      setModelFetchStatus(`获取失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function updateApiMode(apiMode: string) {
    const provider = apiMode || 'openai_compatible';
    onModelDraftChange({
      ...modelDraft,
      api_mode: provider,
      provider,
      api_host: provider === 'openai' ? 'https://api.openai.com/v1' : modelDraft.api_host,
      base_url: provider === 'openai' ? 'https://api.openai.com/v1' : normalizeApiBaseUrl(modelDraft.api_host, modelDraft.api_path),
    });
  }

  return (
      <article className="panel settings-model-panel" id={sectionId}>
        <div className="model-section-heading">
          <div>
            <p className="eyebrow">模型设置</p>
            <h3>{title}</h3>
          </div>
          <button className="ghost" onClick={onToggle} type="button">
            切换
          </button>
        </div>
        <p>{description}</p>
        <div className="model-status-card">
          <strong>{modelStatus.title}</strong>
          <small>{modelStatus.detail}</small>
        </div>
        <div className="segmented" role="radiogroup" aria-label="Kith 模型来源">
          {(['ollama', 'api', 'local'] as const).map((mode) => (
            <button
              aria-checked={modelMode === mode}
              className={modelMode === mode ? 'active' : ''}
              key={mode}
              onClick={() => selectModelMode(mode)}
              role="radio"
              type="button"
            >
              {mode === 'ollama' ? '本机 Ollama' : mode === 'api' ? '在线 API' : '不用 LLM'}
            </button>
          ))}
        </div>
        {modelMode !== 'local' && (
          <>
            {showProviderPresets && (
              <div className="provider-presets" aria-label="常用 API 配置">
                {providerPresets.map((preset) => (
                  <button className="model-preset" key={preset.id} onClick={() => applyProviderPreset(preset)} type="button">
                    <strong>{preset.label}</strong>
                    <small>{preset.api_host}</small>
                    <em>{preset.hint}</em>
                  </button>
                ))}
              </div>
            )}
            <div className="settings-layer-card">
              <div className="settings-layer-heading">
                <div>
                  <p className="eyebrow">模型提供方</p>
                  <h4>连接 {modelDraft.name || 'provider'} API</h4>
                </div>
                <small>Provider 负责 API Key、Host、Path 和兼容模式；这些错了，该 provider 下所有模型都不可用。</small>
              </div>
              <div className="provider-config-grid">
                <label>
                  <span>名称</span>
                  <input ref={providerNameInputRef} value={modelDraft.name} onChange={(event) => onModelDraftChange({ ...modelDraft, name: event.target.value })} placeholder="例如 openrouter" />
                </label>
                <label>
                  <span>API 模式</span>
                  <select value={modelDraft.api_mode} onChange={(event) => updateApiMode(event.target.value)}>
                    {apiModeOptions.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
                <label className="model-api-key">
                  <span>API 密钥</span>
                  <div>
                    <input value={modelDraft.api_key} onChange={(event) => onModelDraftChange({ ...modelDraft, api_key: event.target.value })} placeholder="sk-... 或 provider token" type="password" />
                    <button disabled={isModelSaving} onClick={onSave} type="button">检查</button>
                  </div>
                  <small>{modelDraft.has_key && !modelDraft.api_key ? `已保存 ${modelDraft.api_key_env || 'API Key'}，留空会继续使用旧 Key。` : '填入新 Key 后保存会覆盖本机保存的 Key。'}</small>
                </label>
                <label>
                  <span>API 主机</span>
                  <input
                    value={modelDraft.api_host}
                    onChange={(event) => onModelDraftChange({ ...modelDraft, api_host: event.target.value, base_url: normalizeApiBaseUrl(event.target.value, modelDraft.api_path) })}
                    placeholder="https://openrouter.ai/api/v1/chat/completions"
                  />
                  <small>{normalizeApiBaseUrl(modelDraft.api_host, modelDraft.api_path) || '保存时会写入 API root'}</small>
                </label>
                <label>
                  <span>API 路径</span>
                  <input
                    value={modelDraft.api_path}
                    onChange={(event) => onModelDraftChange({ ...modelDraft, api_path: event.target.value, base_url: normalizeApiBaseUrl(modelDraft.api_host, event.target.value) })}
                    placeholder="/chat/completions"
                  />
                </label>
                <label className="model-toggle-row">
                  <input
                    checked={modelDraft.improve_compatibility}
                    onChange={(event) => onModelDraftChange({ ...modelDraft, improve_compatibility: event.target.checked })}
                    type="checkbox"
                  />
                  <span>改善网络兼容性</span>
                </label>
              </div>
            </div>

            <div className="settings-layer-card model-layer-card">
              <div className="settings-layer-heading">
                <div>
                  <p className="eyebrow">模型</p>
                  <h4>选择当前调用的 model id</h4>
                </div>
                <small>Model 负责真实模型 ID、显示名、类型、能力标签、上下文窗口和最大输出 Token；模型 ID 错了只影响当前模型。</small>
              </div>
              <div className="model-editor-grid">
                <div className="model-list-panel">
                  <div className="model-list-toolbar">
                    <strong>模型列表</strong>
                    <div>
                      <button onClick={() => onModelDraftChange({ ...modelDraft, model: '', model_display_name: '', model_type: 'chat' })} type="button">+ 新建</button>
                      <button disabled={!visibleModelPresets.length} onClick={() => visibleModelPresets[0] && applyModelPreset(visibleModelPresets[0])} type="button">重置</button>
                      <button onClick={fetchProviderModels} type="button">获取</button>
                    </div>
                  </div>
                  {modelFetchStatus && <small className="model-fetch-status">{modelFetchStatus}</small>}
                  <div className="model-list">
                    {hasCustomModel && (
                      <button className="selected" type="button">
                        <span>
                          <strong>{modelDraft.model_display_name || modelDraft.model}</strong>
                          <small>{modelDraft.model}</small>
                        </span>
                        <em>自定义</em>
                      </button>
                    )}
                    {visibleModelPresets.map((preset) => (
                      <button
                        className={modelDraft.model === preset.model ? 'selected' : ''}
                        key={preset.id}
                        onClick={() => applyModelPreset(preset)}
                        type="button"
                      >
                        <span>
                          <strong>{preset.display}</strong>
                          <small>{preset.model}</small>
                        </span>
                        <em>{capabilityLabel(preset.capabilities)}</em>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="model-advanced-card">
                  <p className="eyebrow">编辑模型</p>
                  <div className="model-config-form compact">
                    <label>
                      <span>模型 ID</span>
                      <input value={modelDraft.model} onChange={(event) => onModelDraftChange({ ...modelDraft, model: event.target.value })} placeholder="x-ai/grok-4" />
                    </label>
                    <label>
                      <span>显示名称</span>
                      <input value={modelDraft.model_display_name} onChange={(event) => onModelDraftChange({ ...modelDraft, model_display_name: event.target.value })} placeholder="grok" />
                    </label>
                    <label>
                      <span>模型类型</span>
                      <select value={modelDraft.model_type} onChange={(event) => onModelDraftChange({ ...modelDraft, model_type: event.target.value })}>
                        <option value="chat">聊天</option>
                        <option value="embedding">嵌入</option>
                        <option value="rerank">重排</option>
                      </select>
                    </label>
                    <div className="model-capabilities">
                      <label><input checked={modelDraft.vision} onChange={(event) => onModelDraftChange({ ...modelDraft, vision: event.target.checked })} type="checkbox" />视觉</label>
                      <label><input checked={modelDraft.reasoning} onChange={(event) => onModelDraftChange({ ...modelDraft, reasoning: event.target.checked })} type="checkbox" />推理</label>
                      <label><input checked={modelDraft.tools} onChange={(event) => onModelDraftChange({ ...modelDraft, tools: event.target.checked })} type="checkbox" />工具使用</label>
                    </div>
                    <label>
                      <span>上下文窗口</span>
                      <input value={modelDraft.context_window} onChange={(event) => onModelDraftChange({ ...modelDraft, context_window: event.target.value })} placeholder="例如 128000" type="number" />
                    </label>
                    <label>
                      <span>最大输出 Token 数</span>
                      <input value={modelDraft.max_output_tokens} onChange={(event) => onModelDraftChange({ ...modelDraft, max_output_tokens: event.target.value })} placeholder="例如 4096" type="number" />
                    </label>
                  </div>
                </div>
              </div>
            </div>
          </>
        )}
        <button className="primary" disabled={isModelSaving} onClick={onSave} type="button">
          {isModelSaving ? '保存中...' : scope === 'backend' ? '保存后端模型并热重载' : '保存 Desktop 模型'}
        </button>
      </article>
  );
}

function describeModelStatus(mode: ModelMode, draft: ModelDraft, daemon: DaemonStatus) {
  const status = daemon.status || {};
  const providers = Array.isArray(status.llm_providers) ? status.llm_providers.map(String) : [];
  const providerText = providers.length ? `后端当前可用：${providers.join(', ')}` : '后端当前还没有可用 LLM provider。';

  if (mode === 'local') {
    return {
      title: '不用 LLM：只保留本地索引和规则兜底',
      detail: `这种模式更保守，但 Desktop 对话、画像生成和复杂建议会明显变弱。${providerText}`,
    };
  }
  if (mode === 'ollama') {
    return {
      title: '本机 Ollama：需要你已启动 Ollama',
      detail: `将请求 ${normalizeApiBaseUrl(draft.api_host || draft.base_url, draft.api_path) || 'http://localhost:11434/v1'} 的 ${draft.model || '未填写模型'}。如果没有 ollama serve 或没有 pull 该模型，聊天和后端 LLM 任务都会失败。${providerText}`,
    };
  }
  return {
    title: draft.api_key ? '在线 API：Key 将保存到本机配置' : '在线 API：还需要 API Key',
    detail: `将使用 ${draft.api_mode || draft.provider || 'openai_compatible'} / ${draft.model || '未填写模型'}。API root: ${normalizeApiBaseUrl(draft.api_host || draft.base_url, draft.api_path) || '未填写'}。${providerText}`,
  };
}

function providerKeyFromDraft(draft: ModelDraft) {
  const name = (draft.name || '').toLowerCase();
  const host = (draft.api_host || draft.base_url || '').toLowerCase();
  if (name.includes('dashscope') || host.includes('dashscope') || host.includes('aliyuncs.com')) return 'dashscope';
  if (name.includes('openrouter') || host.includes('openrouter.ai')) return 'openrouter';
  if (name.includes('openai') || host.includes('api.openai.com')) return 'openai';
  if (name.includes('ollama') || host.includes('localhost:11434') || host.includes('127.0.0.1:11434')) return 'ollama';
  return name || 'custom';
}

function capabilityLabel(capabilities: { vision: boolean; reasoning: boolean; tools: boolean }) {
  const labels = [];
  if (capabilities.vision) labels.push('视觉');
  if (capabilities.reasoning) labels.push('推理');
  if (capabilities.tools) labels.push('工具');
  return labels.length ? labels.join(' · ') : '基础聊天';
}

type EventSummary = {
  title: string;
  description: string;
  meta: string[];
  tone: 'neutral' | 'active' | 'success' | 'warning';
};

function summarizeDaemonEvent(event: DaemonEvent): EventSummary {
  const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {};

  if (event.type === 'assistant.progress') {
    return {
      title: 'Kith 正在回答',
      description: text(data.message) || '正在组织本地上下文。',
      meta: [percent(data.progress), text(data.stage)].filter(Boolean),
      tone: 'active',
    };
  }

  if (event.type === 'llm.request') {
    return {
      title: '模型请求已发出',
      description: `${text(data.task_type) || '任务'} · ${text(data.provider) || 'provider'} / ${text(data.model) || 'model'}`,
      meta: [`max tokens ${text(data.max_tokens) || '-'}`, bool(data.is_vision) ? 'vision' : 'text'],
      tone: 'active',
    };
  }

  if (event.type === 'llm.response') {
    const usage = data.usage && typeof data.usage === 'object' ? data.usage as Record<string, unknown> : {};
    return {
      title: '模型已返回',
      description: `${text(data.task_type) || '任务'} · ${text(data.model) || 'model'}`,
      meta: [
        `prompt ${text(usage.prompt_tokens) || '-'}`,
        `completion ${text(usage.completion_tokens) || '-'}`,
      ],
      tone: 'success',
    };
  }

  if (event.type === 'triage.batch_progress') {
    return {
      title: '文件分诊进行中',
      description: shortenPath(text(data.directory) || '正在处理目录'),
      meta: [`classified ${text(data.classified) || 0}`, `batch ${text(data.batch_files) || 0}`, `${text(data.elapsed_s) || 0}s`],
      tone: 'active',
    };
  }

  if (event.type.includes('error') || event.error) {
    return {
      title: '事件异常',
      description: event.error || text(data.error) || text(data.msg) || '发生了一个后台事件错误。',
      meta: [],
      tone: 'warning',
    };
  }

  return {
    title: readableEventType(event.type),
    description: text(data.message) || text(data.msg) || compactJson(data),
    meta: [],
    tone: 'neutral',
  };
}

function readableEventType(type: string) {
  return type
    .split(/[._-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function text(value: unknown) {
  if (value === undefined || value === null || value === '') return '';
  return String(value);
}

function bool(value: unknown) {
  return value === true || value === 'true';
}

function percent(value: unknown) {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '';
}

function compactJson(value: Record<string, unknown>) {
  const raw = JSON.stringify(value);
  return raw.length > 120 ? `${raw.slice(0, 120)}...` : raw;
}

function shortenPath(path: string) {
  return path.replace(/^\/Users\/[^/]+\//, '~/');
}
