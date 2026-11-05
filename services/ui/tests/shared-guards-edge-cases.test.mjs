// Shared guards at their edges: post-sign-in navigation from a page without
// an origin, notification lists answered out of order, a stopped unsaved
// guard and a repeated click in the confirmation dialog.
import test from 'node:test';
import assert from 'node:assert/strict';
import { safeNext } from '../public/pages/navigation-security.js';
import { loadUi } from './support/load-ui.mjs';
import { page, settle } from './support/page-fixture.mjs';

test('post-sign-in navigation from a page without an origin goes home', () => {
  // A sandboxed frame or a file has the opaque origin "null", against which
  // no destination can be resolved.
  for (const next of ['/events.html', '/account.html#security', '/'])
    assert.equal(safeNext(next, 'null'), '/', next);
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const ITEMS = [
  {
    id: 'n1',
    kind: 'audit_started',
    summary: 'New audit',
    title: 'Access review',
    href: '/audit.html?id=a1',
    date_label: 'Starts',
    date: null,
    actor: 'dana',
    created_at: '2026-11-02T01:00:00',
    read_at: null,
  },
  {
    id: 'n2',
    kind: 'quiz_round',
    summary: 'Quiz to take',
    title: 'Security basics',
    href: '/quiz-take.html?round=r1',
    date_label: 'Due',
    date: null,
    actor: null,
    created_at: '2026-11-01T01:00:00',
    read_at: '2026-11-01T02:00:00',
  },
];

async function inbox(t) {
  const slow = deferred();
  const requests = [];
  const ui = await page(t, 'notifications', {
    me: {},
    get: async (url) => {
      requests.push(url.search);
      // The unread-only list is slow to answer; the full list is not.
      if (url.searchParams.get('unread') === 'true') return slow.promise;
      return { items: structuredClone(ITEMS), unread_count: 1 };
    },
  });
  await settle(() => ui.el('inbox-list').querySelectorAll('a[data-item]').length === 2);
  const unread = ui.el('inbox-unread');
  // Ask for unread only, then change back to everything before that answers.
  unread.checked = true;
  unread.dispatchEvent(new ui.window.Event('change'));
  unread.checked = false;
  unread.dispatchEvent(new ui.window.Event('change'));
  await settle(() => requests.length === 3 && ui.idle() === false);
  return { ui, slow, requests };
}

const listed = (ui) =>
  [...ui.el('inbox-list').querySelectorAll('a[data-item]')].map((link) => link.dataset.item);

test('a notification list that answers after a newer request is ignored', async (t) => {
  const { ui, slow, requests } = await inbox(t);
  assert.deepEqual(requests, ['', '?unread=true', '']);
  slow.resolve({ items: [structuredClone(ITEMS[0])], unread_count: 7 });
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(listed(ui), ['n1', 'n2']);
  assert.equal(ui.el('inbox-meta').textContent, '1 unread notification');
});

test('a notification list that fails after a newer request leaves the newer list in place', async (t) => {
  const { ui, slow } = await inbox(t);
  slow.reject(new Error('Inbox offline'));
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(listed(ui), ['n1', 'n2']);
  assert.equal(ui.el('inbox-meta').textContent, '1 unread notification');
  assert.equal(ui.el('inbox-status').hidden, true);
  assert.equal(ui.el('inbox-status').textContent, '');
});

test('a stopped unsaved guard no longer watches its form', async (t) => {
  const ui = await loadUi('pages/unsaved-guard.js', {
    html: '<form id="form"><input id="field"></form><span id="hint" hidden></span>',
  });
  t.after(ui.close);
  const hint = ui.document.getElementById('hint');
  const guard = ui.api.unsavedGuard({ root: ui.document.getElementById('form'), hint });
  guard.stop();
  const field = ui.document.getElementById('field');
  field.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  field.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  assert.equal(guard.isDirty(), false);
  assert.equal(hint.hidden, true);
});

test('a repeated Confirm click answers only the dialog it was given for', async (t) => {
  const ui = await loadUi('pages/confirm-modal.js', {
    setup(window) {
      window.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      window.HTMLDialogElement.prototype.close = function () {
        this.open = false;
      };
    },
  });
  t.after(ui.close);
  const first = ui.api.confirmAction({ title: 'Delete the first record' });
  const second = ui.api.confirmAction({ title: 'Delete the second record' });
  await new Promise((r) => setTimeout(r, 0));
  const dialog = ui.document.querySelector('dialog.keen-confirm');
  const confirm = dialog.querySelector('[data-confirm]');
  // Both clicks arrive before the next dialog is shown.
  confirm.click();
  confirm.click();
  assert.equal(await first, true);
  let answered = false;
  second.then(() => (answered = true));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(answered, false);
  assert.equal(dialog.open, true);
  assert.equal(dialog.querySelector('[data-title]').textContent, 'Delete the second record');
  dialog.querySelector('[data-cancel]').click();
  assert.equal(await second, false);
});
