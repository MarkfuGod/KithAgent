import { app, BrowserWindow, ipcMain, shell } from 'electron';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { DEFAULT_CHAT_PATH, normalizeApiBaseUrl } from './apiBaseUrl.js';
import { createDaemonBridge, DEFAULT_DAEMON_BASE_URL } from './daemon.js';
import { runFrontendChat } from './frontendChat.js';
import { parseSseEvent } from './sse.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '../../..');
const daemon = createDaemonBridge({ repoRoot });
const SHORT_SYSCALL_TIMEOUT_MS = 30000;
const INSIGHTS_HTTP_TIMEOUT_MS = 45000;
const INSIGHTS_UNIX_TIMEOUT_MS = 60000;
const LONG_SYSCALL_TIMEOUT_MS = 180000;
const ONBOARDING_SYSCALL_TIMEOUT_MS = 600000;
const DASHBOARD_FETCH_TIMEOUT_MS = 15000;
const DASHBOARD_BASE_URL = 'http://127.0.0.1:7438';
const DEV_SERVER_URL = 'http://127.0.0.1:5173';
const RENDERER_INDEX_PATH = join(repoRoot, 'desktop/dist/renderer/index.html');
const MEMORY_FACT_STATUSES = new Set(['inferred', 'confirmed', 'rejected', 'hidden']);
const MODEL_SETTINGS_GET_SCRIPT = `
import json
from src.kernel.user_settings import load_model_settings
print(json.dumps(load_model_settings(), ensure_ascii=False))
`;
const MODEL_SETTINGS_SAVE_SCRIPT = `
import json
import sys
from src.kernel.user_settings import save_model_settings
payload = json.loads(sys.argv[1] or "{}")
print(json.dumps(save_model_settings(payload), ensure_ascii=False))
`;
const DESKTOP_RUNTIME_MODEL_SCRIPT = `
import json
from src.kernel.user_settings import load_desktop_runtime_model_settings
print(json.dumps(load_desktop_runtime_model_settings(), ensure_ascii=False))
`;
const TRIAGE_FILES_SCRIPT = `
import json
import sqlite3
import sys
from pathlib import Path

prefix = str(sys.argv[1] or '').strip()
limit = max(1, min(int(sys.argv[2] or '200'), 300))
home = str(Path.home())
if prefix == '~':
    normalized_prefix = home
elif prefix.startswith('~/'):
    normalized_prefix = str(Path(home) / prefix[2:])
else:
    normalized_prefix = prefix

db_path = Path.home() / '.agent_sys' / 'memory.db'
db = sqlite3.connect(str(db_path), check_same_thread=False)
try:
    total = db.execute(
        'SELECT COUNT(*) FROM file_index WHERE path = ? OR path LIKE ?',
        (normalized_prefix, f'{normalized_prefix}/%'),
    ).fetchone()[0]
    rows = db.execute(
        """SELECT path, file_type, size_bytes,
                  CASE WHEN triage_status = '' OR triage_status IS NULL THEN 'untriaged'
                       ELSE triage_status END as status
           FROM file_index
           WHERE path = ? OR path LIKE ?
           ORDER BY
             CASE
               WHEN triage_status = '' OR triage_status IS NULL THEN 0
               WHEN triage_status = 'untriaged' THEN 0
               WHEN triage_status = 'unknown' THEN 1
               WHEN triage_status = 'skip' THEN 2
               WHEN triage_status = 'low' THEN 3
               WHEN triage_status = 'medium' THEN 4
               WHEN triage_status = 'high' THEN 5
               ELSE 6
             END,
             size_bytes DESC,
             path ASC
           LIMIT ?""",
        (normalized_prefix, f'{normalized_prefix}/%', limit),
    ).fetchall()
finally:
    db.close()

def display_path(path):
    if path.startswith(home):
        rel = path[len(home):].strip('/')
        return f'~/{rel}' if rel else '~'
    return path

print(json.dumps({
    'prefix': display_path(normalized_prefix),
    'total_files': total,
    'limit': limit,
    'files': [
        {
            'path': display_path(path),
            'file_type': (file_type or '').lower() or '(no suffix)',
            'size_bytes': size or 0,
            'status': status,
        }
        for path, file_type, size, status in rows
    ],
}))
`;

let mainWindow = null;
let eventAbortController = null;

function ensurePlainObject(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} 格式不正确。`);
  }
  return value;
}

function boundedString(value, label, maxLength = 2000, required = false) {
  const text = String(value ?? '').trim();
  if (required && !text) {
    throw new Error(`${label} 不能为空。`);
  }
  if (text.length > maxLength) {
    throw new Error(`${label} 过长。`);
  }
  return text;
}

function boundedNumber(value, fallback, min, max, label) {
  const number = Number(value ?? fallback);
  if (!Number.isFinite(number)) {
    throw new Error(`${label} 必须是数字。`);
  }
  return Math.max(min, Math.min(Math.round(number), max));
}

function assertHeaderSafe(value, label) {
  if (/[\r\n]/.test(value) || Array.from(value).some((char) => char.charCodeAt(0) > 255)) {
    throw new Error(`${label} 不能包含换行或非 ASCII 字符。`);
  }
}

function assertHttpUrl(value, label) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${label} 不是有效 URL。`);
  }
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error(`${label} 只支持 http 或 https。`);
  }
}

function sanitizeSourcesPayload(payload) {
  const input = ensurePlainObject(payload, '资料范围');
  if (!Array.isArray(input.watch_paths)) {
    throw new Error('资料范围必须是路径列表。');
  }
  const watchPaths = [...new Set(input.watch_paths.map((item) => boundedString(item, '资料路径', 2000)).filter(Boolean))];
  if (!watchPaths.length) {
    throw new Error('请至少保留一个资料范围。');
  }
  return { watch_paths: watchPaths };
}

function sanitizeTriageDecisionPayload(payload) {
  const input = ensurePlainObject(payload, '目录决策');
  const status = boundedString(input.status, '目录决策状态', 20, true);
  if (!['high', 'medium', 'low', 'skip'].includes(status)) {
    throw new Error('目录决策状态无效。');
  }
  return {
    prefix: boundedString(input.prefix, '目录前缀', 2000, true),
    status,
  };
}

function sanitizeAgentBriefPayload(payload) {
  const input = ensurePlainObject(payload, 'Agent handoff');
  return {
    caller: boundedString(input.caller || 'desktop', '调用方', 80) || 'desktop',
    session_id: boundedString(input.session_id, '会话 ID', 160),
    workspace: boundedString(input.workspace, 'Workspace', 2000),
    task: boundedString(input.task, '任务描述', 4000),
    surface: boundedString(input.surface || 'desktop', 'Surface', 80) || 'desktop',
  };
}

function sanitizeModelListPayload(payload) {
  const input = ensurePlainObject(payload, '模型列表请求');
  const sanitized = {
    mode: boundedString(input.mode || 'api', '模型模式', 20),
    scope: boundedString(input.scope, '模型范围', 20),
    provider: boundedString(input.provider, '模型 provider', 100),
    base_url: boundedString(input.base_url, '模型 API base URL', 2000),
    api_host: boundedString(input.api_host, '模型 API 主机', 2000),
    api_path: boundedString(input.api_path || DEFAULT_CHAT_PATH, '模型 API 路径', 500) || DEFAULT_CHAT_PATH,
    api_key: boundedString(input.api_key, '模型 API Key', 4000),
    api_key_env: boundedString(input.api_key_env, '模型 API Key 环境变量', 160),
  };
  if (sanitized.api_key) {
    assertHeaderSafe(sanitized.api_key, '模型 API Key');
  }
  return sanitized;
}

function sanitizeMemoryFeedbackPayload(payload) {
  const input = ensurePlainObject(payload, '记忆反馈');
  const factId = boundedString(input.fact_id, '记忆 ID', 200, true);
  const status = boundedString(input.status, '记忆状态', 40, true);
  if (!MEMORY_FACT_STATUSES.has(status)) {
    throw new Error('记忆状态不支持。');
  }
  return { fact_id: factId, status };
}

function sendDaemonEvent(event) {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.isDestroyed()) {
    return;
  }
  mainWindow.webContents.send('daemon:event', event);
}

function fallbackRendererHtml(message) {
  return `data:text/html;charset=utf-8,${encodeURIComponent(`
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8" />
        <title>Kith</title>
        <style>
          body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            background: #080b10;
            color: #f5f7fb;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          }
          main {
            max-width: 560px;
            padding: 32px;
            border: 1px solid rgba(255,255,255,0.14);
            border-radius: 24px;
            background: rgba(255,255,255,0.06);
          }
          code {
            color: #9bdcff;
          }
        </style>
      </head>
      <body>
        <main>
          <h1>Kith renderer is not available</h1>
          <p>${message}</p>
          <p>Use <code>npm run dev</code> for Vite development, or <code>npm run build && npm start</code> for the built renderer.</p>
        </main>
      </body>
    </html>
  `)}`;
}

function loadBuiltRendererOrError(message) {
  if (existsSync(RENDERER_INDEX_PATH)) {
    return mainWindow.loadFile(RENDERER_INDEX_PATH);
  }
  return mainWindow.loadURL(fallbackRendererHtml(message));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1024,
    height: 560,
    minWidth: 980,
    minHeight: 560,
    title: 'Kith',
    backgroundColor: '#080b10',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 18, y: 18 },
    webPreferences: {
      preload: join(__dirname, '../preload/preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  const devServerUrl = process.env.VITE_DEV_SERVER_URL || (!app.isPackaged ? DEV_SERVER_URL : '');
  if (devServerUrl) {
    mainWindow.webContents.once('did-fail-load', (_event, _code, _description, validatedURL, isMainFrame) => {
      if (isMainFrame === false || !String(validatedURL || '').startsWith(devServerUrl)) {
        return;
      }
      loadBuiltRendererOrError(`Could not reach the Vite dev server at ${devServerUrl}.`);
    });
    mainWindow.loadURL(devServerUrl);
  } else {
    loadBuiltRendererOrError('The built renderer file is missing.');
  }
}

ipcMain.handle('daemon:status', daemon.daemonStatus);
ipcMain.handle('daemon:start', daemon.ensureDaemon);
ipcMain.handle('daemon:stop', () => daemon.runCli(['stop']).then(() => ({ stopped: true })));
ipcMain.handle('daemon:openDashboard', async () => {
  const dashboard = await daemon.ensureDashboard();
  const opened = await shell.openExternal(dashboard.url);
  return { ...dashboard, opened };
});

async function fetchDashboardJson(path, init = {}) {
  await daemon.ensureDashboard();
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(new Error(`Dashboard request timed out after ${DASHBOARD_FETCH_TIMEOUT_MS}ms`)),
    DASHBOARD_FETCH_TIMEOUT_MS,
  );
  try {
    const response = await fetch(`${DASHBOARD_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `Dashboard HTTP ${response.status}`);
    }
    return payload;
  } finally {
    clearTimeout(timeout);
  }
}

ipcMain.handle('triage:clusters', async (_event, payload = {}) => {
  const input = ensurePlainObject(payload, '目录聚类请求');
  const depth = boundedNumber(input.depth, 3, 1, 8, '目录聚类深度');
  const limit = boundedNumber(input.limit, 80, 1, 300, '目录聚类数量');
  return fetchDashboardJson(`/api/file-clusters?depth=${encodeURIComponent(depth)}&limit=${encodeURIComponent(limit)}`);
});

ipcMain.handle('triage:files', async (_event, payload = {}) => {
  const input = ensurePlainObject(payload, '目录文件请求');
  const prefix = boundedString(input.prefix, '目录前缀', 2000, true);
  const limit = boundedNumber(input.limit, 200, 1, 300, '目录文件数量');
  return daemon.runPythonJson(TRIAGE_FILES_SCRIPT, [prefix, limit], { timeoutMs: SHORT_SYSCALL_TIMEOUT_MS });
});

ipcMain.handle('triage:clusterDecision', async (_event, payload = {}) => {
  const decision = sanitizeTriageDecisionPayload(payload);
  return fetchDashboardJson('/api/file-clusters/decision', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Kith-Dashboard': '1',
    },
    body: JSON.stringify(decision),
  });
});
ipcMain.handle('daemon:events:start', async () => {
  await daemon.ensureDaemon();
  if (eventAbortController) {
    return { started: true };
  }

  eventAbortController = new AbortController();
  const token = daemon.readAuthToken();
  const headers = token ? { 'X-Agent-Token': token } : {};

  fetch(`${DEFAULT_DAEMON_BASE_URL}/events`, {
    headers,
    signal: eventAbortController.signal,
  }).then(async (response) => {
    if (!response.ok || !response.body) {
      throw new Error(`events stream failed: HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';
      for (const chunk of chunks) {
        const event = parseSseEvent(chunk);
        if (event) {
          sendDaemonEvent(event);
        }
      }
    }
  }).catch((error) => {
    if (error.name !== 'AbortError') {
      sendDaemonEvent({
        type: 'desktop.events.error',
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }).finally(() => {
    eventAbortController = null;
  });

  return { started: true };
});
ipcMain.handle('daemon:events:stop', async () => {
  eventAbortController?.abort();
  eventAbortController = null;
  return { stopped: true };
});

ipcMain.handle('kith:chat', async (_event, payload) => {
  return runFrontendChat({ daemon, payload, sendEvent: sendDaemonEvent });
});

ipcMain.handle('insights:get', async (_event, payload = {}) => {
  await daemon.ensureDaemon();
  return daemon.syscall('assistant.insights', payload, 1, {
    httpTimeoutMs: INSIGHTS_HTTP_TIMEOUT_MS,
    unixTimeoutMs: INSIGHTS_UNIX_TIMEOUT_MS,
  });
});

ipcMain.handle('onboarding:bootstrap', async (_event, payload = {}) => {
  const started = Date.now();
  console.info('[desktop] onboarding:bootstrap received', {
    includeBrowserHistory: Boolean(payload.include_browser_history),
    historyLimit: payload.history_limit,
  });
  await daemon.ensureDaemon();
  console.info('[desktop] onboarding:bootstrap daemon ready');
  try {
    const result = await daemon.syscall('onboarding.bootstrap', payload, 1, {
      timeoutMs: ONBOARDING_SYSCALL_TIMEOUT_MS,
      unixTimeoutMs: ONBOARDING_SYSCALL_TIMEOUT_MS,
    });
    console.info('[desktop] onboarding:bootstrap completed', {
      elapsedMs: Date.now() - started,
      browserEntries: result?.browser_history?.entries_count,
      facts: result?.profile_facts?.length,
    });
    return result;
  } catch (error) {
    console.error('[desktop] onboarding:bootstrap failed', error);
    throw error;
  }
});

ipcMain.handle('profile:summary', async (_event, payload = {}) => {
  await daemon.ensureDaemon();
  return daemon.syscall('profile.summary', payload, 1, { timeoutMs: LONG_SYSCALL_TIMEOUT_MS });
});

ipcMain.handle('memory:review', async (_event, payload = {}) => {
  await daemon.ensureDaemon();
  return daemon.syscall('memory.review', payload, 1, { timeoutMs: SHORT_SYSCALL_TIMEOUT_MS });
});

ipcMain.handle('memory:feedback', async (_event, payload = {}) => {
  await daemon.ensureDaemon();
  return daemon.syscall('memory.feedback', sanitizeMemoryFeedbackPayload(payload), 1, { timeoutMs: SHORT_SYSCALL_TIMEOUT_MS });
});

ipcMain.handle('capabilities:list', async () => {
  await daemon.ensureDaemon();
  return daemon.syscall('capabilities.list', {}, 1, { timeoutMs: SHORT_SYSCALL_TIMEOUT_MS });
});

ipcMain.handle('context:agentBrief', async (_event, payload = {}) => {
  await daemon.ensureDaemon();
  return daemon.syscall('context.agent_brief', sanitizeAgentBriefPayload(payload), 1, { timeoutMs: LONG_SYSCALL_TIMEOUT_MS });
});

ipcMain.handle('sources:get', async () => {
  await daemon.ensureDaemon();
  return daemon.syscall('sources.get', {}, 1, { httpTimeoutMs: 8000, unixTimeoutMs: 12000 });
});

ipcMain.handle('sources:configure', async (_event, payload) => {
  await daemon.ensureDaemon();
  return daemon.syscall('sources.configure', sanitizeSourcesPayload(payload), 0, { httpTimeoutMs: 10000, unixTimeoutMs: 20000 });
});

ipcMain.handle('settings:model', async (_event, payload) => {
  if (payload?.scope === 'desktop') {
    return daemon.runPythonJson(MODEL_SETTINGS_SAVE_SCRIPT, [JSON.stringify(payload)], { timeoutMs: SHORT_SYSCALL_TIMEOUT_MS });
  }
  await daemon.ensureDaemon();
  return daemon.syscall('settings.model', payload, 0, { httpTimeoutMs: 10000, unixTimeoutMs: 20000 });
});

ipcMain.handle('settings:model:get', async () => {
  return daemon.runPythonJson(MODEL_SETTINGS_GET_SCRIPT, [], { timeoutMs: SHORT_SYSCALL_TIMEOUT_MS });
});

ipcMain.handle('settings:model:list', async (_event, payload = {}) => {
  const modelPayload = sanitizeModelListPayload(payload);
  const baseUrl = normalizeApiBaseUrl(modelPayload.base_url || modelPayload.api_host, modelPayload.api_path);
  if (!baseUrl) {
    throw new Error('请先填写 provider API 主机。');
  }
  assertHttpUrl(baseUrl, '模型 API 主机');

  const apiKeyEnv = modelPayload.api_key_env;
  let apiKey = String(modelPayload.api_key || (apiKeyEnv ? process.env[apiKeyEnv] : '') || '').trim();
  if (!apiKey && modelPayload.scope === 'desktop') {
    const savedDesktop = await daemon.runPythonJson(DESKTOP_RUNTIME_MODEL_SCRIPT, [], { timeoutMs: SHORT_SYSCALL_TIMEOUT_MS }).catch(() => ({}));
    apiKey = String(savedDesktop.api_key || '').trim();
  }
  if (apiKey) {
    assertHeaderSafe(apiKey, '模型 API Key');
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error('model list request timed out')), 15000);
  try {
    const response = await fetch(`${baseUrl}/models`, {
      headers: {
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error?.message || data.error || `HTTP ${response.status}`);
    }
    const rawModels = Array.isArray(data.data) ? data.data : Array.isArray(data.models) ? data.models : [];
    const models = rawModels
      .map((item) => (typeof item === 'string' ? item : item?.id || item?.name))
      .filter(Boolean)
      .map(String);
    return { models };
  } finally {
    clearTimeout(timeout);
  }
});

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
