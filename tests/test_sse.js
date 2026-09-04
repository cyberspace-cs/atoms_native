// Exercise the actual browser consumer with byte streams; no network or DOM.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
const start = source.indexOf('  async function streamPost(');
const end = source.indexOf('  // ---------- agent stream rendering', start);

async function run(chunks, handler = () => {}) {
  let canceled = false;
  const reader = {
    async read() { return chunks.length ? { value: chunks.shift(), done: false } : { done: true }; },
    async cancel() { canceled = true; },
    releaseLock() {},
  };
  const ctx = vm.createContext({ TextDecoder, authHeader: () => ({}),
    fetch: async () => ({ ok: true, body: { getReader: () => reader } }) });
  vm.runInContext(source.slice(start, end), ctx);
  await ctx.streamPost('/unused', {}, handler);
  return canceled;
}
const bytes = s => new TextEncoder().encode(s);
test('UTF-8 split bytes and CRLF reach terminal done', async () => {
  const data = bytes('data: {"type":"system","message":"你好"}\r\n\r\ndata: {"type":"done"}\r\n\r\n');
  const seen = [];
  await run(Array.from(data, byte => new Uint8Array([byte])), e => seen.push(e));
  assert.equal(seen[0].message, '你好');
  assert.equal(seen[1].type, 'done');
});
test('EOF without terminal event is failure, not silent success', async () => {
  await assert.rejects(run([bytes('data: {"type":"system"}\n\n')]), /中断|终态/);
});
test('malformed event is not swallowed', async () => {
  await assert.rejects(run([bytes('data: broken\n\n')]), /JSON|Unexpected/);
});
test('async rendering failures propagate to caller', async () => {
  await assert.rejects(run([bytes('data: {"type":"done"}\n\n')], async () => {
    throw new Error('render failed');
  }), /render failed/);
});
test('terminal error stops further events and releases reader', async () => {
  const seen = [];
  const canceled = await run([bytes('data: {"type":"error"}\n\ndata: {"type":"done"}\n\n')], e => seen.push(e.type));
  assert.deepEqual(seen, ['error']);
  assert.equal(canceled, true);
});
