import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// The shared "Sample into audit" dialog used by event, ISMS and risk pages.
const AUDITS = [
  {
    id: 'a1',
    title: 'Q1 review',
    status: 'open',
    start_date: '2026-01-01',
    end_date: '2026-03-31',
    created_by_user_id: 'u1',
  },
  { id: 'a2', title: 'Closed review', status: 'completed', created_by_user_id: 'u1' },
  { id: 'a3', title: 'Colleague review', status: 'in_progress', created_by_user_id: 'u2' },
  { id: 'a4', title: '', status: 'in_progress', created_by_user_id: 'u1' },
];
const AUDITOR = { can_manage_audits: true, can_view_events: true, user_id: 'u1' };

async function sampling(t, { audits = AUDITS, auditsError, post, url } = {}) {
  const calls = [];
  const toasts = [];
  const modal = { shown: 0, hidden: 0 };
  const ui = await loadUi('app.js', {
    html: '<div id="pageStatus"></div>',
    url: url ?? 'https://keen.example/event.html?id=e1&framework=A',
    setup(window) {
      window.bootstrap = {
        Modal: {
          getOrCreateInstance: () => ({
            show: () => modal.shown++,
            hide: () => modal.hidden++,
          }),
        },
      };
    },
    vendor: {
      toast: (el, message, type) => toasts.push({ el: el?.id, message, type }),
      apiGet: async (path) => {
        calls.push({ method: 'GET', path });
        if (auditsError) throw new Error(auditsError);
        return { items: audits };
      },
      apiPost: async (path, body) => {
        calls.push({ method: 'POST', path, body: JSON.parse(JSON.stringify(body)) });
        return post ? post(path, body) : { ok: true };
      },
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  const open = (options = {}) =>
    ui.api.openAuditSampleModal({
      me: AUDITOR,
      entityType: 'event',
      entityId: 'e1',
      title: 'Deploy',
      framework: 'A',
      statusEl: el('pageStatus'),
      ...options,
    });
  const submit = async () => {
    el('entityAuditSampleSubmit').click();
    await new Promise((resolve) => setTimeout(resolve, 5));
  };
  return { ...ui, calls, toasts, modal, el, open, submit };
}

test('the dialog offers only active audits the user may add to', async (t) => {
  const ui = await sampling(t);

  await ui.open();

  assert.deepEqual(ui.calls, [{ method: 'GET', path: '/api/v1/audits?framework=A&limit=500' }]);
  assert.deepEqual(
    [...ui.el('entityAuditSampleSelect').options].map((o) => [o.value, o.textContent]),
    [
      ['a1', 'Q1 review · 2026-01-01 → 2026-03-31 · Open'],
      ['a4', 'Audit · In progress'],
    ],
  );
  assert.equal(ui.el('entityAuditSampleTitle').textContent, 'Sample “Deploy” into audit');
  assert.equal(
    ui.el('entityAuditSampleCreate').getAttribute('href'),
    '/audits.html?new=1&framework=A',
  );
  assert.equal(ui.el('entityAuditSampleSubmit').disabled, false);
  assert.equal(ui.el('entityAuditSampleUseCurrentUrl').checked, true);
  assert.equal(ui.modal.shown, 1);
});

test('sampling records the item, its notes and its page in the chosen audit', async (t) => {
  const sampled = [];
  const ui = await sampling(t);
  await ui.open({
    evidenceUrl: '/event.html?id=e1&framework=A',
    onSampled: (auditId) => sampled.push(auditId),
  });
  ui.el('entityAuditSampleSelect').value = 'a4';
  ui.el('entityAuditSampleNotes').value = 'Approvals checked';

  await ui.submit();

  assert.deepEqual(ui.calls.at(-1), {
    method: 'POST',
    path: '/api/v1/audits/a4/evidence',
    body: {
      entity_type: 'event',
      entity_id: 'e1',
      evidence_url: '/event.html?id=e1&framework=A',
      title: 'Deploy',
      notes: 'Approvals checked',
    },
  });
  assert.deepEqual(ui.toasts.at(-1), {
    el: 'pageStatus',
    message: 'Item sampled into audit',
    type: 'success',
  });
  assert.equal(ui.modal.hidden, 1);
  assert.deepEqual(sampled, ['a4']);
  assert.equal(ui.el('entityAuditSampleSubmit').textContent, 'Sample into audit');
  assert.equal(ui.el('entityAuditSampleSubmit').disabled, false);
});

test('the page reference is the current page unless the auditor leaves it out', async (t) => {
  const ui = await sampling(t, { url: 'https://keen.example/risk.html?id=r1&framework=A#detail' });
  await ui.open({ entityType: 'risk', entityId: 'r1', title: '' });
  assert.equal(ui.el('entityAuditSampleTitle').textContent, 'Sample item into audit');
  await ui.submit();
  assert.equal(ui.calls.at(-1).body.evidence_url, '/risk.html?id=r1&framework=A');
  assert.equal(ui.calls.at(-1).body.title, 'Keen item');

  await ui.open({ entityType: 'risk', entityId: 'r1' });
  ui.el('entityAuditSampleUseCurrentUrl').checked = false;
  await ui.submit();
  assert.equal(ui.calls.at(-1).body.evidence_url, null);
  // Reopening the dialog leaves one submit action, so each click samples once.
  assert.equal(ui.calls.filter((c) => c.method === 'POST').length, 2);
});

test('without an active audit the dialog explains how to start one', async (t) => {
  const ui = await sampling(t, { audits: [AUDITS[1], AUDITS[2]] });
  await ui.open();
  assert.deepEqual(
    [...ui.el('entityAuditSampleSelect').options].map((o) => o.textContent),
    ['No open or in-progress audits — start one first'],
  );
  for (const id of [
    'entityAuditSampleSubmit',
    'entityAuditSampleNotes',
    'entityAuditSampleUseCurrentUrl',
  ])
    assert.equal(ui.el(id).disabled, true, id);
  assert.deepEqual(ui.toasts.at(-1), {
    el: 'entityAuditSampleStatus',
    message:
      'No open or in-progress audits are available. Start an Audit first, then sample this item into it.',
    type: 'warning',
  });
});

test('audits that cannot be listed are reported in the dialog', async (t) => {
  const ui = await sampling(t, { auditsError: 'Service unavailable' });
  await ui.open();
  assert.deepEqual(
    [...ui.el('entityAuditSampleSelect').options].map((o) => o.textContent),
    ['Failed to load audits'],
  );
  assert.equal(ui.el('entityAuditSampleSubmit').disabled, true);
  assert.equal(ui.el('entityAuditSampleNotes').disabled, true);
  assert.deepEqual(ui.toasts.at(-1), {
    el: 'entityAuditSampleStatus',
    message: 'Failed to load audits: Error: Service unavailable',
    type: 'danger',
  });
});

test('a refused sample is reported and the dialog stays open', async (t) => {
  const ui = await sampling(t, {
    post: async () => {
      throw new Error('Audit is locked');
    },
  });
  await ui.open();

  await ui.submit();

  assert.deepEqual(ui.toasts.at(-1), {
    el: 'entityAuditSampleStatus',
    message: 'Failed: Error: Audit is locked',
    type: 'danger',
  });
  assert.equal(ui.modal.hidden, 0);
  assert.equal(ui.el('entityAuditSampleSubmit').disabled, false);
  assert.equal(ui.el('entityAuditSampleSubmit').textContent, 'Sample into audit');
});

test('sampling needs a chosen audit', async (t) => {
  const ui = await sampling(t);
  await ui.open();
  ui.el('entityAuditSampleSelect').add(new ui.window.Option('Choose', ''));
  ui.el('entityAuditSampleSelect').value = '';
  await ui.submit();
  assert.equal(ui.calls.filter((c) => c.method === 'POST').length, 0);
  assert.deepEqual(ui.toasts.at(-1), {
    el: 'entityAuditSampleStatus',
    message: 'Start an Audit first, then sample this item into it.',
    type: 'warning',
  });
});

test('the dialog opens only for saved items and users who may sample them', async (t) => {
  const ui = await sampling(t);
  await ui.open({ me: { can_view_events: true } });
  assert.deepEqual(ui.toasts.at(-1), {
    el: 'pageStatus',
    message: 'audits.manage permission required.',
    type: 'danger',
  });
  await ui.open({ entityId: '  ' });
  assert.deepEqual(ui.toasts.at(-1), {
    el: 'pageStatus',
    message: 'Save this item before sampling it into an audit.',
    type: 'warning',
  });
  assert.deepEqual(ui.calls, []);
  assert.equal(ui.modal.shown, 0);
});
