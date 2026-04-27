import assert from 'node:assert/strict';
import test from 'node:test';

import { createDaemonBridge } from '../src/main/daemon.js';

test('daemonStatus falls back to Unix socket when HTTP is unavailable', async () => {
  const bridge = createDaemonBridge({
    fetchImpl: async () => {
      throw new Error('connection refused');
    },
    unixCallImpl: async (callType) => {
      assert.equal(callType, 'sys.status');
      return { pid: 123, running: true };
    },
  });

  const status = await bridge.daemonStatus();
  assert.equal(status.running, true);
  assert.equal(status.transport, 'unix_socket');
  assert.equal(status.status.pid, 123);
});

test('syscall falls back to Unix socket when HTTP syscall fails', async () => {
  const calls = [];
  const bridge = createDaemonBridge({
    fetchImpl: async () => {
      throw new Error('no aiohttp');
    },
    unixCallImpl: async (callType, params, priority) => {
      calls.push({ callType, params, priority });
      return { answer: 'ok' };
    },
  });

  const result = await bridge.syscall('assistant.chat', { message: 'hi' }, 1);
  assert.deepEqual(result, { answer: 'ok' });
  assert.deepEqual(calls, [{ callType: 'assistant.chat', params: { message: 'hi' }, priority: 1 }]);
});

test('ensureDaemon coalesces concurrent starts', async () => {
  let runCliCount = 0;
  let statusChecks = 0;
  const bridge = createDaemonBridge({
    sleep: async () => {},
    runCliImpl: async () => {
      runCliCount += 1;
    },
    fetchImpl: async () => {
      statusChecks += 1;
      if (statusChecks <= 1) {
        throw new Error('not ready');
      }
      return {
        ok: true,
        json: async () => ({ pid: 456, running: true }),
      };
    },
    unixCallImpl: async () => {
      throw new Error('socket missing');
    },
  });

  const [a, b, c] = await Promise.all([
    bridge.ensureDaemon(),
    bridge.ensureDaemon(),
    bridge.ensureDaemon(),
  ]);

  assert.equal(runCliCount, 1);
  assert.equal(a.running, true);
  assert.deepEqual(a, b);
  assert.deepEqual(b, c);
});
