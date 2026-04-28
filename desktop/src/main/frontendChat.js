const DEFAULT_CHAT_PATH = '/chat/completions';
const SAVED_DESKTOP_MODEL_SCRIPT = `
import json
from src.kernel.user_settings import load_desktop_runtime_model_settings
print(json.dumps(load_desktop_runtime_model_settings(), ensure_ascii=False))
`;

function normalizeApiBaseUrl(rawHost, rawPath = DEFAULT_CHAT_PATH) {
  let host = String(rawHost || '').trim().replace(/\/+$/, '');
  const path = String(rawPath || '').trim();
  if (!host) return '';
  if (path && path !== DEFAULT_CHAT_PATH && !host.endsWith(path.replace(/\/+$/, ''))) {
    host = `${host}/${path.replace(/^\/+/, '')}`.replace(/\/+$/, '');
  }
  for (const suffix of ['/chat/completions', '/responses', '/completions']) {
    if (host.endsWith(suffix)) {
      return host.slice(0, -suffix.length).replace(/\/+$/, '');
    }
  }
  return host;
}

function completionEndpoint(settings) {
  const baseUrl = normalizeApiBaseUrl(settings.base_url || settings.api_host, settings.api_path);
  if (!baseUrl) {
    throw new Error('前端模型 API 主机未配置。请先在设置里配置 Desktop 对话模型。');
  }
  return `${baseUrl}${settings.api_path || DEFAULT_CHAT_PATH}`;
}

function stringValue(value) {
  if (value === undefined || value === null) return '';
  return String(value);
}

function assertHeaderSafe(value, label) {
  if (/[\r\n]/.test(value)) {
    throw new Error(`${label} 不能包含换行符。请检查 Desktop 对话模型配置。`);
  }
  for (let index = 0; index < value.length; index += 1) {
    if (value.charCodeAt(index) > 255) {
      throw new Error(`${label} 包含非 ASCII 字符。请检查是否把中文说明或别的文本填进了 API Key。`);
    }
  }
}

function emitProgress(sendEvent, requestId, stage, message, progress) {
  sendEvent({
    type: 'assistant.progress',
    data: {
      request_id: requestId,
      origin: 'frontend',
      stage,
      message,
      progress,
    },
  });
}

async function readSavedDesktopSettings(daemon) {
  if (!daemon || typeof daemon.runPythonJson !== 'function') {
    return {};
  }
  try {
    return await daemon.runPythonJson(SAVED_DESKTOP_MODEL_SCRIPT);
  } catch {
    return {};
  }
}

function mergeDesktopSettings(savedSettings, payloadSettings) {
  const saved = savedSettings && typeof savedSettings === 'object' ? savedSettings : {};
  const payload = payloadSettings && typeof payloadSettings === 'object' ? payloadSettings : {};
  if (!Object.keys(saved).length) {
    return payload;
  }

  const payloadHasRuntimeKey = Boolean(stringValue(payload.api_key).trim());
  const payloadLooksHydrated = Object.prototype.hasOwnProperty.call(payload, 'has_key')
    || Object.prototype.hasOwnProperty.call(payload, 'api_key_env');
  if (!payloadHasRuntimeKey && !payloadLooksHydrated) {
    return saved;
  }

  return {
    ...saved,
    ...payload,
    api_key: payloadHasRuntimeKey ? payload.api_key : saved.api_key,
    api_key_env: payload.api_key_env || saved.api_key_env,
  };
}

function emitTool(sendEvent, requestId, name, label, status, detail, startedAt) {
  sendEvent({
    type: 'assistant.tool',
    data: {
      request_id: requestId,
      origin: 'frontend',
      name,
      label,
      status,
      detail,
      elapsed_ms: startedAt ? Math.round((Date.now() - startedAt) * 10) / 10 : undefined,
    },
  });
}

async function backendSkill(sendEvent, requestId, name, label, fn) {
  const startedAt = Date.now();
  emitTool(sendEvent, requestId, name, label, 'running', '已向后端发送请求。', startedAt);
  try {
    const result = await fn();
    emitTool(sendEvent, requestId, name, label, 'completed', '后端已返回结果。', startedAt);
    return { ok: true, result };
  } catch (error) {
    emitTool(
      sendEvent,
      requestId,
      name,
      label,
      'warning',
      error instanceof Error ? error.message : String(error),
      startedAt,
    );
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function compactForPrompt(value, limit = 12000) {
  const text = JSON.stringify(value, null, 2);
  return text.length <= limit ? text : `${text.slice(0, limit)}\n...<truncated>`;
}

function shouldRequestBackendSkills(message) {
  const text = String(message || '').trim().toLowerCase();
  if (!text) return false;
  if (/^\/(profile|focus|brief|plan)\b/.test(text)) return true;
  const backendMarkers = [
    '我最近', '我的', '我现在', '我今天', '关于我', '你怎么看我',
    '画像', '记忆', '资料', '文件', '目录', '下载', '桌面', '文档',
    '总结', '建议', '计划', '安排', '洞察', '最近', '今天', '工作',
    '学习', '生活', 'focus', 'profile', 'brief', 'plan',
  ];
  return backendMarkers.some((marker) => text.includes(marker));
}

function buildMessages({ message, history, backendContext }) {
  const messages = [
    {
      role: 'system',
      content: [
        '你是 Kith Desktop 的前端对话 LLM。',
        '架构边界：前端 LLM 只负责和用户实时对话、决定是否需要后端信息、整合后端返回结果；后端 LLM 只在后端任务中运行。',
        '你不能声称自己直接读取文件系统、数据库或执行后台队列。你看到的 backend_context 是通过 KithAgent backend skills 请求返回的内容。',
        '如果需要更多后端能力，请明确告诉用户需要触发哪个后端 skill，而不是编造结果。',
        '回答要具体、可行动、中文优先。涉及用户画像时，区分确认事实、推断和证据不足。',
        '可用 backend skills 包括：profile.summary（用户画像）、memory.review（记忆/知识）、assistant.insights（今日洞察）、sources.get（资料范围）。',
      ].join('\n'),
    },
    {
      role: 'user',
      content: `本轮已经请求并返回的 backend_context：\n${compactForPrompt(backendContext)}`,
    },
  ];

  for (const item of Array.isArray(history) ? history.slice(-6) : []) {
    const role = item && item.role;
    const content = item && item.content;
    if ((role === 'user' || role === 'assistant') && content) {
      messages.push({ role, content: String(content) });
    }
  }
  messages.push({ role: 'user', content: message });
  return messages;
}

function parseSseLines(buffer, onLine) {
  const lines = buffer.split(/\r?\n/);
  const rest = lines.pop() || '';
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('data:')) {
      onLine(trimmed.slice(5).trim());
    }
  }
  return rest;
}

async function streamDesktopCompletion({ settings, messages, requestId, sendEvent }) {
  if (!settings || settings.mode === 'local') {
    throw new Error('前端 Desktop 对话模型未启用。请在设置里选择在线 API 或 Ollama。');
  }
  const model = stringValue(settings.model).trim();
  if (!model) {
    throw new Error('前端模型 ID 未配置。');
  }
  const endpoint = completionEndpoint(settings);
  const apiKey = stringValue(settings.api_key).trim();
  if (settings.mode === 'api' && !apiKey) {
    throw new Error('前端在线 API 缺少 API Key。请在 Desktop 对话模型里填写并保存。');
  }
  if (apiKey) {
    assertHeaderSafe(apiKey, '前端模型 API Key');
  }

  sendEvent({
    type: 'llm.request',
    data: {
      request_id: requestId,
      origin: 'frontend',
      task_type: 'frontend_chat',
      provider: settings.provider || settings.api_mode || 'openai_compatible',
      model,
      message_count: messages.length,
      stream: true,
    },
  });

  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
    },
    body: JSON.stringify({
      model,
      messages,
      stream: true,
      temperature: 0.35,
      max_tokens: Number(settings.max_output_tokens || 1200) || 1200,
    }),
  });

  if (!response.ok || !response.body) {
    const body = await response.text().catch(() => '');
    throw new Error(`前端模型请求失败：HTTP ${response.status}${body ? ` ${body.slice(0, 500)}` : ''}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let content = '';
  let usage = null;

  const handleData = (data) => {
    if (!data || data === '[DONE]') return;
    try {
      const payload = JSON.parse(data);
      usage = payload.usage || usage;
      const delta = payload.choices?.[0]?.delta?.content || payload.choices?.[0]?.message?.content || '';
      if (delta) {
        content += delta;
        sendEvent({
          type: 'llm.delta',
          data: {
            request_id: requestId,
            origin: 'frontend',
            model,
            content: delta,
          },
        });
      }
    } catch {
      // Ignore provider heartbeat or non-JSON compatibility frames.
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = parseSseLines(buffer, handleData);
  }
  buffer = parseSseLines(`${buffer}\n`, handleData);

  sendEvent({
    type: 'llm.response',
    data: {
      request_id: requestId,
      origin: 'frontend',
      task_type: 'frontend_chat',
      provider: settings.provider || settings.api_mode || 'openai_compatible',
      model,
      usage: usage || {},
      content_preview: content.slice(0, 200),
    },
  });

  return content.trim();
}

export async function runFrontendChat({ daemon, payload, sendEvent }) {
  const requestId = stringValue(payload?.request_id) || `frontend-chat-${Date.now()}`;
  const message = stringValue(payload?.message).trim();
  if (!message) {
    return { answer: '你可以直接输入想让 Kith 帮你理解或推进的事情。', sources: [] };
  }

  const needsBackend = shouldRequestBackendSkills(message);
  emitProgress(
    sendEvent,
    requestId,
    'start',
    needsBackend ? '前端对话模型收到请求，准备调用后端 skills。' : '前端对话模型收到请求，这轮不需要启动后端。',
    0.05,
  );

  let backendContext = {
    skipped: true,
    reason: 'This turn looks like general chat, so no backend skills were requested.',
  };
  let skillResults = [];
  if (needsBackend) {
    const [profile, memory, insights, sources] = await Promise.all([
      backendSkill(sendEvent, requestId, 'profile.summary', '请求用户画像', () => (
        daemon.syscall('profile.summary', { rebuild: false }, 1, { timeoutMs: 45000, fallbackToUnix: false })
      )),
      backendSkill(sendEvent, requestId, 'memory.review', '请求记忆与知识', () => (
        daemon.syscall('memory.review', { limit: 30 }, 1, { timeoutMs: 45000, fallbackToUnix: false })
      )),
      backendSkill(sendEvent, requestId, 'assistant.insights', '请求今日洞察', () => (
        daemon.syscall('assistant.insights', { limit: 8 }, 1, { timeoutMs: 45000, fallbackToUnix: false })
      )),
      backendSkill(sendEvent, requestId, 'sources.get', '请求资料范围', () => (
        daemon.syscall('sources.get', {}, 1, { timeoutMs: 20000, fallbackToUnix: false })
      )),
    ]);
    backendContext = {
      skipped: false,
      profile,
      memory,
      insights,
      sources,
    };
    skillResults = [
      { id: 'profile.summary', title: '用户画像', result: profile },
      { id: 'memory.review', title: '记忆与知识', result: memory },
      { id: 'assistant.insights', title: '今日洞察', result: insights },
      { id: 'sources.get', title: '资料范围', result: sources },
    ];
    emitProgress(sendEvent, requestId, 'compose', '后端 skills 已返回，正在交给前端模型组织回答。', 0.42);
  } else {
    emitProgress(sendEvent, requestId, 'compose', '跳过后端 skills，直接交给前端模型回答。', 0.42);
  }

  const settings = mergeDesktopSettings(
    await readSavedDesktopSettings(daemon),
    payload?.model_settings || {},
  );
  const messages = buildMessages({
    message,
    history: payload?.history || [],
    backendContext,
  });

  try {
    emitProgress(sendEvent, requestId, 'llm', '前端 Desktop LLM 正在流式生成回答。', 0.62);
    const answer = await streamDesktopCompletion({
      settings,
      messages,
      requestId,
      sendEvent,
    });
    emitProgress(sendEvent, requestId, 'finalize', '前端回答已生成。', 0.96);
    return {
      answer: answer || '前端模型没有返回内容。',
      context: backendContext,
      sources: skillResults.map((item) => ({
        id: item.id,
        title: item.title,
        kind: item.result.ok ? 'backend_skill' : 'backend_skill_error',
      })),
    };
  } catch (error) {
    sendEvent({
      type: 'llm.error',
      data: {
        request_id: requestId,
        origin: 'frontend',
        task_type: 'frontend_chat',
        provider: settings.provider || settings.api_mode || 'openai_compatible',
        model: settings.model || 'model',
        error: error instanceof Error ? error.message : String(error),
      },
    });
    throw error;
  }
}
