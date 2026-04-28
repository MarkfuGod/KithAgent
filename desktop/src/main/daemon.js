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
  const statusTimeoutMs = options.statusTimeoutMs || 2500;
  let ensurePromise = null;

  function describeError(error) {
    return error instanceof Error ? error.message : String(error);
  }

  function readAuthToken() {
    try {
      return existsSync(tokenPath) ? readFileSync(tokenPath, 'utf8').trim() : '';
    } catch {
      return '';
    }
  }

  function pythonCommand() {
    if (process.env.AGENT_SYS_PYTHON) {
      return process.env.AGENT_SYS_PYTHON;
    }

    const candidates = [
      process.env.VIRTUAL_ENV ? join(process.env.VIRTUAL_ENV, 'bin/python') : '',
      process.env.CONDA_PREFIX && ['kithagent', 'kithagnt'].includes(process.env.CONDA_DEFAULT_ENV) ? join(process.env.CONDA_PREFIX, 'bin/python') : '',
      process.env.CONDA_PREFIX ? join(process.env.CONDA_PREFIX, 'envs/kithagent/bin/python') : '',
      process.env.CONDA_PREFIX ? join(process.env.CONDA_PREFIX, '../kithagent/bin/python') : '',
      process.env.CONDA_PREFIX ? join(process.env.CONDA_PREFIX, 'envs/kithagnt/bin/python') : '',
      process.env.CONDA_PREFIX ? join(process.env.CONDA_PREFIX, '../kithagnt/bin/python') : '',
      process.env.CONDA_PREFIX ? join(process.env.CONDA_PREFIX, 'bin/python') : '',
      join(homedir(), 'miniconda3/envs/kithagent/bin/python'),
      join(homedir(), 'miniconda3/envs/kithagnt/bin/python'),
      join(homedir(), 'anaconda3/envs/kithagent/bin/python'),
      join(homedir(), 'anaconda3/envs/kithagnt/bin/python'),
      join(homedir(), 'mambaforge/envs/kithagent/bin/python'),
      join(homedir(), 'mambaforge/envs/kithagnt/bin/python'),
      join(repoRoot, '.venv/bin/python'),
      join(repoRoot, 'venv/bin/python'),
    ].filter(Boolean);

    const projectPython = candidates.find((candidate) => existsSync(candidate));
    return projectPython || 'python';
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

  function startDetachedCli(args) {
    if (options.startDetachedCliImpl) {
      return options.startDetachedCliImpl(args);
    }
    const child = spawn(pythonCommand(), ['-m', 'src.cli', ...args], {
      cwd: repoRoot,
      detached: true,
      env: { ...process.env, PYTHONPATH: repoRoot },
      stdio: 'ignore',
    });
    child.unref();
    return { pid: child.pid };
  }

  function runPythonJson(script, args = []) {
    if (options.runPythonJsonImpl) {
      return options.runPythonJsonImpl(script, args);
    }
    return new Promise((resolvePromise, rejectPromise) => {
      const child = spawn(pythonCommand(), ['-c', script, ...args.map(String)], {
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
        if (code !== 0) {
          rejectPromise(new Error(stderr || stdout || `python exited with ${code}`));
          return;
        }
        try {
          resolvePromise(JSON.parse(stdout || '{}'));
        } catch (error) {
          rejectPromise(new Error(`Invalid Python JSON output: ${describeError(error)}`));
        }
      });
    });
  }

  async function fetchUrl(url, requestOptions = {}) {
    if (!fetchImpl) {
      throw new Error('fetch is not available');
    }
    const timeoutMs = requestOptions.timeoutMs || options.httpTimeoutMs || 0;
    const controller = timeoutMs > 0 ? new AbortController() : null;
    const timeout = controller
      ? setTimeout(() => controller.abort(new Error(`HTTP request timed out after ${timeoutMs}ms`)), timeoutMs)
      : null;
    try {
      return await fetchImpl(url, {
        signal: controller ? controller.signal : undefined,
      });
    } finally {
      if (timeout) {
        clearTimeout(timeout);
      }
    }
  }

  async function fetchJson(path, init = {}, requestOptions = {}) {
    if (!fetchImpl) {
      throw new Error('fetch is not available');
    }
    const timeoutMs = requestOptions.timeoutMs || options.httpTimeoutMs || 0;
    const controller = timeoutMs > 0 ? new AbortController() : null;
    const timeout = controller
      ? setTimeout(() => controller.abort(new Error(`HTTP request timed out after ${timeoutMs}ms`)), timeoutMs)
      : null;
    let response;
    try {
      response = await fetchImpl(`${daemonBaseUrl}${path}`, {
        ...init,
        signal: controller ? controller.signal : init.signal,
      });
    } finally {
      if (timeout) {
        clearTimeout(timeout);
      }
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  async function unixSyscall(callType, params = {}, priority = 1, requestOptions = {}) {
    if (options.unixCallImpl) {
      return options.unixCallImpl(callType, params, priority, requestOptions);
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
      const timeoutMs = requestOptions.unixTimeoutMs || requestOptions.timeoutMs || options.unixTimeoutMs || 30000;
      client.setTimeout(timeoutMs, () => {
        client.destroy(new Error('Unix syscall timed out'));
      });
    });
  }

  async function syscall(callType, params = {}, priority = 1, requestOptions = {}) {
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
      }, { timeoutMs: requestOptions.httpTimeoutMs || requestOptions.timeoutMs });
      if (!response.success) {
        throw new Error(response.error || 'Kith request failed');
      }
      return response.data;
    } catch (error) {
      if (requestOptions.fallbackToUnix === false) {
        throw error;
      }
      return unixSyscall(callType, params, priority, requestOptions);
    }
  }

  async function daemonStatus() {
    try {
      const status = await fetchJson('/status', {}, { timeoutMs: statusTimeoutMs });
      return { running: true, status, transport: 'http' };
    } catch (httpError) {
      try {
        const status = await unixSyscall('sys.status');
        return { running: true, status, transport: 'unix_socket' };
      } catch (unixError) {
        return {
          running: false,
          error: `HTTP: ${describeError(httpError)}; Unix socket: ${describeError(unixError)}`,
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

      let lastStatus = before;
      for (let i = 0; i < 30; i += 1) {
        await sleep(500);
        const current = await daemonStatus();
        lastStatus = current;
        if (current.running) {
          return current;
        }
      }
      throw new Error(`agent-sys daemon did not become ready${lastStatus.error ? `: ${lastStatus.error}` : ''}`);
    })();

    try {
      return await ensurePromise;
    } finally {
      ensurePromise = null;
    }
  }

  async function ensureDashboard(port = 7438) {
    const url = `http://127.0.0.1:${port}`;
    const isReady = async () => {
      try {
        const response = await fetchUrl(url, { timeoutMs: 1500 });
        return response.ok;
      } catch {
        return false;
      }
    };

    if (await isReady()) {
      return { running: true, url };
    }

    const child = startDetachedCli(['dashboard', '--port', String(port)]);
    let lastStatus = 'waiting for dashboard HTTP server';
    for (let i = 0; i < 40; i += 1) {
      await sleep(500);
      if (await isReady()) {
        return { running: true, url, pid: child.pid };
      }
      lastStatus = `not ready after ${Math.round((i + 1) * 0.5)}s`;
    }

    throw new Error(`Dashboard did not become ready on ${url}: ${lastStatus}`);
  }

  return {
    readAuthToken,
    runCli,
    runPythonJson,
    syscall,
    daemonStatus,
    ensureDaemon,
    ensureDashboard,
    unixSyscall,
  };
}
