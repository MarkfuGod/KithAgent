import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import net from 'node:net';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';

export const DEFAULT_DAEMON_BASE_URL = 'http://127.0.0.1:7437';
export const DEFAULT_SOCKET_PATH = '/tmp/agent_sys.sock';

function defaultTokenPath() {
  return join(homedir(), '.agent_sys', 'auth_token');
}

function encodeFrame(payload) {
  const body = Buffer.from(JSON.stringify(payload));
  const header = Buffer.alloc(4);
  header.writeUInt32BE(body.length, 0);
  return Buffer.concat([header, body]);
}

function decodeFrame(buffer) {
  if (buffer.length < 4) {
    return null;
  }
  const length = buffer.readUInt32BE(0);
  if (buffer.length < length + 4) {
    return null;
  }
  return {
    payload: JSON.parse(buffer.subarray(4, length + 4).toString('utf8')),
    remaining: buffer.subarray(length + 4),
  };
}

export function createDaemonBridge(options = {}) {
  const repoRoot = options.repoRoot || resolve('.');
  const daemonBaseUrl = options.daemonBaseUrl || DEFAULT_DAEMON_BASE_URL;
  const socketPath = options.socketPath || DEFAULT_SOCKET_PATH;
  const tokenPath = options.tokenPath || defaultTokenPath();
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  const sleep = options.sleep || ((ms) => new Promise((resolvePromise) => setTimeout(resolvePromise, ms)));
  const caller = options.caller || 'electron';
  let ensurePromise = null;

  function readAuthToken() {
    try {
      return existsSync(tokenPath) ? readFileSync(tokenPath, 'utf8').trim() : '';
    } catch {
      return '';
    }
  }

  function pythonCommand() {
    return process.env.AGENT_SYS_PYTHON || 'python3';
  }

  function runCli(args) {
    if (options.runCliImpl) {
      return options.runCliImpl(args);
    }
    return new Promise((resolvePromise, rejectPromise) => {
      const child = spawn(pythonCommand(), ['-m', 'src.cli', ...args], {
        cwd: repoRoot,
        env: { ...process.env, PYTHONPATH: repoRoot },
        stdio: ['ignore', 'pipe', 'pipe'],
      });

      let stdout = '';
      let stderr = '';
      child.stdout.on('data', (chunk) => {
        stdout += chunk.toString();
      });
      child.stderr.on('data', (chunk) => {
        stderr += chunk.toString();
      });
      child.on('error', rejectPromise);
      child.on('close', (code) => {
        if (code === 0) {
          resolvePromise({ stdout, stderr });
          return;
        }
        rejectPromise(new Error(stderr || stdout || `agent-sys exited with ${code}`));
      });
    });
  }

  async function fetchJson(path, init = {}) {
    if (!fetchImpl) {
      throw new Error('fetch is not available');
    }
    const response = await fetchImpl(`${daemonBaseUrl}${path}`, init);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  async function unixSyscall(callType, params = {}, priority = 1) {
    if (options.unixCallImpl) {
      return options.unixCallImpl(callType, params, priority);
    }

    const token = readAuthToken();
    const requestParams = token ? { ...params, _auth_token: token } : params;
    const request = {
      call_type: callType,
      params: requestParams,
      caller,
      priority,
    };

    return new Promise((resolvePromise, rejectPromise) => {
      const client = net.createConnection(socketPath);
      let buffer = Buffer.alloc(0);

      client.on('connect', () => {
        client.write(encodeFrame(request));
      });
      client.on('data', (chunk) => {
        buffer = Buffer.concat([buffer, chunk]);
        const decoded = decodeFrame(buffer);
        if (!decoded) {
          return;
        }
        client.end();
        if (!decoded.payload.success) {
          rejectPromise(new Error(decoded.payload.error || 'Unix syscall failed'));
          return;
        }
        resolvePromise(decoded.payload.data);
      });
      client.on('error', rejectPromise);
      client.setTimeout(5000, () => {
        client.destroy(new Error('Unix syscall timed out'));
      });
    });
  }

  async function syscall(callType, params = {}, priority = 1) {
    const token = readAuthToken();
    const body = {
      call_type: callType,
      params,
      caller,
      priority,
    };
    const headers = {
      'Content-Type': 'application/json',
    };
    if (token) {
      headers['X-Agent-Token'] = token;
    }

    try {
      const response = await fetchJson('/syscall', {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      });
      if (!response.success) {
        throw new Error(response.error || 'Jarvis request failed');
      }
      return response.data;
    } catch (error) {
      return unixSyscall(callType, params, priority);
    }
  }

  async function daemonStatus() {
    try {
      const status = await fetchJson('/status');
      return { running: true, status, transport: 'http' };
    } catch (httpError) {
      try {
        const status = await unixSyscall('sys.status');
        return { running: true, status, transport: 'unix_socket' };
      } catch (unixError) {
        return {
          running: false,
          error: httpError instanceof Error ? httpError.message : String(httpError),
        };
      }
    }
  }

  async function ensureDaemon() {
    if (ensurePromise) {
      return ensurePromise;
    }

    ensurePromise = (async () => {
      const before = await daemonStatus();
      if (before.running) {
        return before;
      }
      await runCli(['start', '-d']);

      for (let i = 0; i < 30; i += 1) {
        await sleep(500);
        const current = await daemonStatus();
        if (current.running) {
          return current;
        }
      }
      throw new Error('agent-sys daemon did not become ready');
    })();

    try {
      return await ensurePromise;
    } finally {
      ensurePromise = null;
    }
  }

  return {
    readAuthToken,
    runCli,
    syscall,
    daemonStatus,
    ensureDaemon,
    unixSyscall,
  };
}
