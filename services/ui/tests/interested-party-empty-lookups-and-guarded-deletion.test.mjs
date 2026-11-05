import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));
// The lookups services/api/app/services/interested_parties.py offers.
const meta = {
  framework: 'A',
  can_view: true,
  can_manage: true,
  events: ['Business as Usual', 'Incident', 'Alert', 'Notifiable Event'],
  when: ['As Required', 'Upon Identification', 'Routine'],
  with_whom: ['Individual member', 'NCSC', 'ICO'],
  methods: ['Email', 'Telephone', 'ICO Portal'],
};
const party = {
  id: 'ip1',
  framework: 'A',
  name: { id: 'n1', name: 'Regulator' },
  name_id: 'n1',
  nature: { id: 't1', name: 'Statutory' },
  nature_id: 't1',
  note: '',
  label: 'Regulator - Statutory',
  controls: [],
  communications: [],
  control_count: 0,
  communication_count: 0,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
};

async function partyPage(
  t,
  { query = '&id=ip1', me = { is_admin: true }, lookups, metaFail, confirm = true } = {},
) {
  const requests = [];
  const ui = await page(t, 'interested_party', {
    query,
    me,
    confirm,
    get: async (u) => {
      switch (u.pathname) {
        case '/api/v1/interested-parties/meta':
          if (metaFail) throw new Error('Meta offline');
          return structuredClone(meta);
        case '/api/v1/interested-parties/names':
          return { items: lookups ? [] : [{ id: 'n1', name: 'Regulator' }] };
        case '/api/v1/interested-parties/natures':
          return { items: lookups ? [] : [{ id: 't1', name: 'Statutory' }] };
        case '/api/v1/controls':
          return { total: 0, limit: 5000, offset: 0, items: [] };
        case '/api/v1/interested-parties/ip1':
          return structuredClone(party);
        default:
          throw new Error('Unexpected GET ' + u);
      }
    },
    post: async (u, body) => {
      requests.push(['POST', u.pathname, plain(body)]);
      // The API refuses a party without a name or nature of interest.
      throw new Error('name is required');
    },
    remove: async (u) => {
      requests.push(['DELETE', u.pathname]);
      return { ok: true };
    },
  });
  await settle(
    () => ui.el('partyTitle').textContent !== 'Interested party' || ui.el('status').textContent,
  );
  return { ui, requests };
}

test('leaving a tab of a new party opened without a query string leaves a bare address', async (t) => {
  const { ui } = await partyPage(t, { query: '' });
  ui.window.history.replaceState({}, '', '/interested_party.html?tab=communications');
  ui.el('partyDetailsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.window.location.href, 'https://keen.example/interested_party.html');
});

test('saving a new party before any names or natures exist sends none and shows the refusal', async (t) => {
  const { ui, requests } = await partyPage(t, { query: '', lookups: 'empty' });
  assert.equal(ui.el('partyName').options.length, 0);
  ui.el('partyNote').value = 'Annual review';
  ui.el('partyForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'name is required');
  assert.deepEqual(requests, [
    [
      'POST',
      '/api/v1/interested-parties',
      { framework: 'A', name_id: null, nature_id: null, note: 'Annual review', controls: [] },
    ],
  ]);
  assert.equal(ui.el('status').dataset.alert, 'danger');
});

test('the delete button left in view by a failed load deletes nothing', async (t) => {
  // A reader keeps the button the page never got to hide.
  const reader = await partyPage(t, { me: { can_view_interested_parties: true }, metaFail: true });
  assert.match(reader.ui.el('status').textContent, /Meta offline/);
  assert.equal(reader.ui.el('deleteParty').style.display, '');
  reader.ui.el('deleteParty').click();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(reader.ui.confirmations, []);
  assert.deepEqual(reader.requests, []);

  // So does a manager on a party that has not been saved yet.
  const unsaved = await partyPage(t, { query: '', metaFail: true });
  unsaved.ui.el('deleteParty').click();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(unsaved.ui.confirmations, []);
  assert.deepEqual(unsaved.requests, []);
});

test('cancelling the delete confirmation keeps the party', async (t) => {
  const { ui, requests } = await partyPage(t, { confirm: false });
  ui.el('deleteParty').click();
  await settle(() => ui.confirmations.length === 1);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(ui.confirmations[0].title, 'Delete interested party');
  assert.deepEqual(requests, []);
  assert.equal(ui.window.location.pathname, '/interested_party.html');
  assert.equal(ui.el('status').textContent, '');
});
