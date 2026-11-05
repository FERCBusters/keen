import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const node = (ref, extra = {}) => ({
  kind: 'clause',
  ref,
  title: `Clause ${ref}`,
  description: '',
  ...extra,
});
const catalog = [
  { slug: 'old', name: 'Earlier <b>edition</b>', version: '2015' },
  { slug: 'new', name: 'Later', version: '2026' },
];
async function compare(t, get = async () => ({ items: [] })) {
  const ui = await loadUi('pages/framework-compare.js', {
    html: '<section id="compare"></section>',
    vendor: { apiGet: get },
    setup: (window) => {
      window.HTMLElement.prototype.scrollIntoView = () => {};
    },
  });
  t.after(() => ui.close());
  const host = ui.document.getElementById('compare');
  const control = ui.api.frameworkComparison(host);
  control.open('old', catalog);
  const $ = (selector) => host.querySelector(selector);
  const run = () =>
    $('[data-compare-form]').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  return { ...ui, host, control, $, run };
}

test('comparison pairs exact references and types, detecting text and hierarchy changes', async (t) => {
  const ui = await compare(t);
  const rows = ui.api.compareNodes(
    [
      node('4'),
      node('5', { description: 'old', parent_ref: '4' }),
      node('6'),
      node('7', { clause_refs: ['b', 'a'] }),
    ],
    [
      node('4'),
      node('5', { description: 'new', parent_ref: '3' }),
      node('6', { kind: 'control' }),
      node('7', { clause_refs: ['a', 'b', 'a'] }),
      node('10'),
    ],
  );
  assert.deepEqual(
    Array.from(rows, (r) => [r.kind, r.ref, r.status]),
    [
      ['clause', '4', 'unchanged'],
      ['clause', '5', 'changed'],
      ['clause', '6', 'removed'],
      ['clause', '7', 'unchanged'],
      ['clause', '10', 'added'],
      ['control', '6', 'added'],
    ],
  );
  assert.deepEqual(
    Array.from(rows[1].fields, (f) => f[0]),
    ['description', 'parent_ref'],
  );
});

test('comparison renders safe before/after changes, metadata, additions and removals', async (t) => {
  const ui = await compare(t, async (url) => ({
    items: url.includes('/old/')
      ? [node('4', { description: 'Keep old words' }), node('5')]
      : [node('4', { description: 'Keep <img src=x> words' }), node('6')],
  }));
  ui.run();
  await settle(() => ui.$('[data-diff-status="changed"]'));
  assert.match(ui.host.textContent, /1 added · − 1 removed · ~ 1 changed/);
  assert.equal(ui.host.querySelector('img, b'), null);
  assert.match(ui.$('[data-diff-status="changed"] del').textContent, /old/);
  assert.match(ui.host.textContent, /<img src=x>/);
  assert.ok(ui.$('[data-diff-status="added"] ins'));
  assert.ok(ui.$('[data-diff-status="removed"] del'));
  ui.$('[data-swap]').click();
  assert.equal(ui.$('[data-before]').value, 'new');
  assert.equal(ui.$('[data-results]').textContent, '');
  ui.run();
  await settle(() => ui.$('[data-diff-status="changed"]'));
  assert.match(ui.$('[data-diff-status="changed"] del').textContent, /<img src=x>/);
});

test('large comparisons paginate and filtering includes unchanged entries only on request', async (t) => {
  const ui = await compare(t, async (url) => ({
    items: url.includes('/old/')
      ? [node('same')]
      : [node('same'), ...Array.from({ length: 105 }, (_, i) => node(String(i)))],
  }));
  ui.run();
  await settle(() => ui.$('[data-next]'));
  assert.equal(ui.host.querySelectorAll('article').length, 50);
  ui.$('[data-next]').click();
  assert.match(ui.host.textContent, /51–100 of 105/);
  ui.$('[data-next]').click();
  assert.equal(ui.host.querySelectorAll('article').length, 5);
  const search = ui.$('[data-search]');
  search.value = 'same';
  search.dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.host.querySelectorAll('article').length, 0);
  ui.$('[data-unchanged]').checked = true;
  ui.$('[data-unchanged]').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.host.querySelectorAll('article').length, 1);
  assert.equal(ui.$('article').dataset.diffStatus, 'unchanged');
});

test('identical selections are refused and failed comparisons can retry', async (t) => {
  let fail = true;
  const ui = await compare(t, async () => {
    if (fail) throw new Error('Offline');
    return { items: [] };
  });
  ui.$('[data-after]').value = 'old';
  ui.run();
  assert.match(ui.host.textContent, /Choose two different frameworks/);
  ui.$('[data-after]').value = 'new';
  ui.run();
  await settle(() => ui.host.textContent.includes('Comparison failed'));
  fail = false;
  ui.run();
  await settle(() => ui.$('[data-next]'));
  assert.match(ui.host.textContent, /No clause or control differences/);
});

test('late responses cannot fill a closed or reselected comparison', async (t) => {
  const pending = [];
  const ui = await compare(t, () => new Promise((resolve) => pending.push(resolve)));
  ui.run();
  ui.$('[data-after]').dispatchEvent(new ui.window.Event('change'));
  for (const resolve of pending.splice(0)) resolve({ items: [node('stale')] });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(ui.$('[data-results]').textContent, '');
  ui.run();
  ui.$('[data-close]').click();
  for (const resolve of pending) resolve({ items: [node('late')] });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(ui.host.hidden, true);
  assert.equal(ui.host.textContent, '');
});

test('opening comparison finishes scrolling before focusing the native selector', async (t) => {
  const calls = [];
  const ui = await loadUi('pages/framework-compare.js', {
    html: '<section id="compare"></section>',
    setup: (window) => {
      window.HTMLElement.prototype.scrollIntoView = function (options) {
        calls.push(['scroll', options.block, options.behavior]);
      };
      const focus = window.HTMLElement.prototype.focus;
      window.HTMLElement.prototype.focus = function (options) {
        calls.push(['focus', this.id, options?.preventScroll]);
        focus.call(this, options);
      };
    },
  });
  t.after(() => ui.close());
  const host = ui.document.getElementById('compare');
  ui.api.frameworkComparison(host).open('old', [
    { slug: 'old', name: 'Earlier' },
    { slug: 'new', name: 'Later' },
  ]);
  assert.deepEqual(calls, [
    ['scroll', 'start', 'instant'],
    ['focus', 'fc-after', true],
  ]);
  const after = host.querySelector('[data-after]');
  assert.equal(ui.document.activeElement, after);
  after.dispatchEvent(new ui.window.MouseEvent('mousedown', { bubbles: true }));
  after.dispatchEvent(new ui.window.MouseEvent('mouseup', { bubbles: true }));
  after.click();
  assert.equal(host.querySelector('[data-after]'), after);
  assert.equal(calls.length, 2);
});

test('scope changes and inserted words are marked on the side that has them', async (t) => {
  const ui = await compare(t, async (url) => ({
    items: url.includes('/old/')
      ? [node('4', { description: 'Keep records', in_scope: true })]
      : [node('4', { description: 'Keep signed records', in_scope: false })],
  }));
  ui.run();
  await settle(() => ui.$('[data-diff-status="changed"]'));
  const rows = Object.fromEntries(
    [...ui.$('[data-diff-status="changed"] tbody').rows].map((row) => [
      row.cells[0].textContent,
      [row.cells[1], row.cells[2]],
    ]),
  );
  assert.deepEqual(
    rows.Scope.map((cell) => cell.textContent),
    ['In scope', 'Out of scope'],
  );
  // Only the later text gains a passage; the earlier text is shown unmarked.
  const [before, after] = rows.Description;
  assert.equal(before.textContent, 'Keep records');
  assert.equal(before.querySelector('del'), null);
  assert.equal(after.querySelector('ins').textContent, 'signed ');
  assert.equal(after.textContent, 'Keep signed records');
});

test('frameworks with the same details compare without a details section, and Previous pages back', async (t) => {
  const ui = await loadUi('pages/framework-compare.js', {
    html: '<section id="compare"></section>',
    vendor: {
      apiGet: async (url) => ({
        items: url.includes('/copy/') ? [] : Array.from({ length: 51 }, (_, i) => node(String(i))),
      }),
    },
    setup: (window) => {
      window.HTMLElement.prototype.scrollIntoView = () => {};
    },
  });
  t.after(() => ui.close());
  const host = ui.document.getElementById('compare');
  ui.api.frameworkComparison(host).open('copy', [
    { slug: 'copy', name: 'Same', version: '1' },
    { slug: 'twin', name: 'Same', version: '1' },
  ]);
  host
    .querySelector('[data-compare-form]')
    .dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => host.querySelector('[data-next]'));
  assert.equal(host.querySelector('details'), null);
  assert.equal(host.querySelector('[data-prev]').disabled, true);
  host.querySelector('[data-next]').click();
  assert.equal(host.querySelector('[data-prev]').disabled, false);
  assert.match(host.textContent, /51–51 of 51/);
  host.querySelector('[data-prev]').click();
  assert.match(host.textContent, /1–50 of 51/);
  assert.equal(host.querySelectorAll('article').length, 50);
  assert.equal(host.querySelector('[data-prev]').disabled, true);
});

test('a comparison that fails after the selection changed reports nothing', async (t) => {
  const pending = [];
  const ui = await compare(t, () => new Promise((resolve, reject) => pending.push(reject)));
  ui.run();
  assert.equal(ui.$('[data-feedback]').textContent, 'Loading framework contents…');
  ui.$('[data-after]').dispatchEvent(new ui.window.Event('change'));
  for (const reject of pending) reject(new Error('Offline'));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(ui.$('[data-feedback]').textContent, '');
  assert.equal(ui.$('[data-run]').disabled, false);
});

test('a catalogue with one framework offers it on both sides and asks for two', async (t) => {
  const ui = await loadUi('pages/framework-compare.js', {
    html: '<section id="compare"></section>',
    setup: (window) => {
      window.HTMLElement.prototype.scrollIntoView = () => {};
    },
  });
  t.after(() => ui.close());
  const host = ui.document.getElementById('compare');
  ui.api.frameworkComparison(host).open('only', [{ slug: 'only', name: 'Only one' }]);
  assert.equal(host.querySelector('[data-before]').value, 'only');
  assert.equal(host.querySelector('[data-after]').value, 'only');
  host
    .querySelector('[data-compare-form]')
    .dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  assert.equal(
    host.querySelector('[data-feedback]').textContent,
    'Choose two different frameworks.',
  );
});
