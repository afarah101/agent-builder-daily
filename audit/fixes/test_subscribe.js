// Behaviour test for site/js/subscribe.js with a fake DOM and fake fetch. Run: node audit/fixes/test_subscribe.js
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '../../site/js/subscribe.js'), 'utf8');

function setup(fetchImpl) {
  const input = { value: ' me@example.com ', disabled: false };
  const btn = { textContent: 'Subscribe', disabled: false };
  const confirm = { textContent: '' };
  const container = { querySelector: (s) => (s === '.email-input' ? input : btn) };
  const document = { querySelector: () => container, getElementById: () => confirm };
  const calls = [];
  const fetch = (url, opts) => { calls.push({ url, opts }); return fetchImpl(opts); };
  const fn = new Function('document', 'fetch', 'AbortController', 'setTimeout', 'clearTimeout', src + '\nreturn submitEmail;')(
    document, fetch, AbortController, setTimeout, clearTimeout);
  return { fn, input, btn, confirm, calls };
}

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // 1. Resolved request: "Submitting…" while pending, "Subscribed!" only after resolve.
  let release;
  const t1 = setup(() => new Promise((r) => { release = r; }));
  t1.fn({ preventDefault() {} });
  assert.strictEqual(t1.btn.textContent, 'Submitting…');
  assert.strictEqual(t1.btn.disabled, true);
  assert.ok(!t1.btn.textContent.includes('Subscribed'));
  assert.strictEqual(t1.calls[0].opts.mode, 'no-cors');
  release({ type: 'opaque' }); await tick();
  assert.ok(t1.btn.textContent.startsWith('Subscribed!'));
  assert.strictEqual(t1.input.value, '');
  console.log('PASS resolved -> Subscribed! only after resolve');

  // 2. Rejected request: error shown, email kept, controls re-enabled.
  const t2 = setup(() => Promise.reject(new TypeError('Failed to fetch')));
  t2.fn({ preventDefault() {} }); await tick();
  assert.ok(!t2.btn.textContent.includes('Subscribed'));
  assert.strictEqual(t2.input.value, ' me@example.com ');
  assert.strictEqual(t2.btn.disabled, false);
  assert.strictEqual(t2.input.disabled, false);
  assert.ok(t2.confirm.textContent.length > 0);
  console.log('PASS rejected -> error shown, email kept');

  // 3. Hung request: aborted at 10 s, error shown, email kept.
  const t3 = setup((opts) => new Promise((_, rej) => opts.signal.addEventListener('abort', () => rej(new Error('aborted')))));
  const start = Date.now();
  t3.fn({ preventDefault() {} });
  await tick(9500);
  assert.strictEqual(t3.btn.textContent, 'Submitting…');
  await tick(700);
  assert.ok(!t3.btn.textContent.includes('Subscribed'));
  assert.strictEqual(t3.input.value, ' me@example.com ');
  assert.ok(t3.confirm.textContent.length > 0);
  console.log(`PASS hung -> aborted after ${((Date.now() - start) / 1000).toFixed(1)}s, error shown, email kept`);
})().catch((e) => { console.error('FAIL', e.message); process.exit(1); });
