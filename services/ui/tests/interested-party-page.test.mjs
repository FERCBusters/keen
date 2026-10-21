import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));
const meta = {
  events: ['Business as Usual', 'Incident'],
  when: ['As Required', 'Annually'],
  with_whom: ['Individual member', 'Board'],
  methods: ['Email', 'Phone', '<b>Post</b>'],
};

function party(overrides = {}) {
  return {
    id: 'ip1',
    framework: 'A',
    name: { id: 'n2', name: 'Regulator <script>' },
    nature: { id: 't2', name: 'Legal' },
    note: 'Quarterly',
    updated_at: '2026-06-01T00:00:00Z',
    controls: [{ id: 'c2' }],
    communications: [
      { event: 'Incident', when: 'Annually', with_whom: 'Board', methods: ['Phone'] },
    ],
    ...overrides,
  };
}

async function partyPage(t, options = {}) {
  const state = { party: options.party || party(), posted: [], patched: [], removed: [] };
  const ui = await page(t, 'interested_party', {
    query: options.query ?? '&id=ip1',
    me: options.me || { is_admin: true },
    globals: options.globals,
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/interested-parties/meta') {
        if (options.fail) throw new Error('Meta offline');
        return meta;
      }
      if (p === '/api/v1/interested-parties/names')
        return {
          items: [
            { id: 'n1', name: 'Customers' },
            { id: 'n2', name: 'Regulator <script>' },
          ],
        };
      if (p === '/api/v1/interested-parties/natures')
        return {
          items: [
            { id: 't1', name: 'Commercial' },
            { id: 't2', name: 'Legal' },
          ],
        };
      if (p === '/api/v1/controls')
        return {
          items: [
            { id: 'c1', ref: 'A1', title: 'Access' },
            { id: 'c2', ref: 'A2', title: '<i>Comms</i>' },
          ],
        };
      if (p === '/api/v1/interested-parties/ip1') {
        // The party is organisation-wide; the page asks for this framework's links.
        assert.equal(u.searchParams.get('framework'), 'A');
        return structuredClone(state.party);
      }
      if (p === '/api/v1/interested-parties/ip1/changelog')
        return { items: [{ summary: 'Party created', changes: [] }] };
      if (p === '/api/v1/audits') return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (u.pathname === '/api/v1/interested-parties') {
        state.party = { ...party(), ...b, id: 'ip9', communications: [] };
        return structuredClone(state.party);
      }
      return { ok: true };
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (options.patchFail) throw new Error('Rejected');
      if (u.pathname.endsWith('/communications'))
        return { ...state.party, communications: b.items };
      return { ...state.party, note: b.note };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      return { ok: true };
    },
  });
  await settle(
    () => ui.el('partyTitle').textContent !== 'Interested Party' || ui.el('status').textContent,
  );
  return { ui, state };
}

test('interested party details render selected lookups, controls and communications safely', async (t) => {
  const { ui } = await partyPage(t);
  assert.equal(ui.el('partyTitle').textContent, 'Regulator <script> — Legal');
  assert.equal(
    ui.el('partyMeta').textContent,
    'Organisation-wide • controls shown for A • Updated 2026-06-01T00:00:00Z',
  );
  assert.equal(ui.el('partyName').value, 'n2');
  assert.equal(ui.el('partyNature').value, 't2');
  assert.equal(ui.el('partyNote').value, 'Quarterly');
  assert.equal(ui.el('controlsChecklist').querySelector('i'), null);
  assert.deepEqual(
    [...ui.document.querySelectorAll('[data-control-check]')].map((c) => c.checked),
    [false, true],
  );
  assert.match(
    ui.el('controlsChecklist').querySelector('a').getAttribute('href'),
    /control\.html\?id=c1&tab=interested-parties&framework=A/,
  );
  const row = ui.document.querySelector('[data-comm-row="0"]');
  assert.equal(row.querySelector('[data-comm-event]').value, 'Incident');
  assert.equal(row.querySelector('[data-comm-with-whom]').value, 'Board');
  assert.deepEqual(
    [...row.querySelectorAll('[data-comm-methods] input:checked')].map((x) => x.value),
    ['Phone'],
  );
  assert.equal(row.querySelector('b'), null);
  assert.equal(ui.el('deleteParty').style.display, '');
  assert.equal(ui.el('samplePartyAudit').style.display, '');
});

test('controls can be filtered and the edited party is saved with its selections', async (t) => {
  const { ui, state } = await partyPage(t);
  ui.el('controlSearch').value = 'access';
  ui.el('controlSearch').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.document.querySelectorAll('[data-control-check]').length, 1);
  ui.document.querySelector('[data-control-check][value="c1"]').checked = true;
  ui.el('partyName').value = 'n1';
  ui.el('partyNote').value = 'Monthly';
  ui.el('partyForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Interested party saved.');
  // Only visible checked controls are submitted.
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/interested-parties/ip1',
    body: { framework: 'A', name_id: 'n1', nature_id: 't2', note: 'Monthly', controls: ['c1'] },
  });
  ui.el('controlSearch').value = 'zzz';
  ui.el('controlSearch').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('controlsChecklist').textContent, /No controls match/);
});

test('communications can be added, removed and saved', async (t) => {
  const { ui, state } = await partyPage(t);
  ui.el('addCommunication').click();
  ui.el('addCommunication').click();
  assert.equal(ui.document.querySelectorAll('[data-comm-row]').length, 3);
  const second = ui.document.querySelector('[data-comm-row="1"]');
  assert.equal(second.querySelector('[data-comm-event]').value, 'Business as Usual');
  second.querySelector('[data-comm-when]').value = 'Annually';
  ui.document.querySelector('[data-remove-comm="2"]').click();
  assert.equal(ui.document.querySelectorAll('[data-comm-row]').length, 2);
  // Edits survive re-rendering after removal.
  assert.equal(ui.document.querySelector('[data-comm-row="1"] [data-comm-when]').value, 'Annually');
  ui.el('saveCommunications').click();
  await settle(() => ui.el('status').textContent === 'Communications saved.');
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/interested-parties/ip1/communications',
    body: {
      items: [
        { event: 'Incident', when: 'Annually', with_whom: 'Board', methods: ['Phone'] },
        {
          event: 'Business as Usual',
          when: 'Annually',
          with_whom: 'Individual member',
          methods: ['Email'],
        },
      ],
    },
  });
});

test('a new interested party is created before its communications are saved', async (t) => {
  const { ui, state } = await partyPage(t, { query: '' });
  assert.equal(ui.el('partyTitle').textContent, 'New interested party');
  assert.equal(ui.el('partyMeta').textContent, 'Organisation-wide • controls for A');
  assert.equal(ui.el('deleteParty').style.display, 'none');
  assert.match(ui.el('communicationsList').textContent, /No communications have been added yet/);
  ui.el('partyChangelogTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() =>
    /Save the interested party to start its changelog/.test(ui.el('partyChangelog').textContent),
  );
  ui.el('addCommunication').click();
  ui.el('saveCommunications').click();
  await settle(() => ui.el('status').textContent === 'Communications saved.');
  assert.equal(state.posted[0].path, '/api/v1/interested-parties');
  assert.equal(state.posted[0].body.name_id, 'n1');
  assert.equal(state.patched[0].path, '/api/v1/interested-parties/ip9/communications');
  assert.equal(state.patched[0].body.items.length, 1);
  assert.match(ui.window.location.search, /id=ip9/);
});

test('read-only users see disabled inputs and no mutation actions', async (t) => {
  const { ui, state } = await partyPage(t, { me: { can_view_interested_parties: true } });
  for (const id of ['partyName', 'partyNature', 'partyNote', 'controlSearch'])
    assert.equal(ui.el(id).disabled, true, id);
  for (const id of [
    'addCommunication',
    'saveCommunications',
    'saveParty',
    'deleteParty',
    'samplePartyAudit',
  ])
    assert.equal(ui.el(id).style.display, 'none', id);
  assert.ok(
    [...ui.document.querySelectorAll('[data-control-check], [data-comm-event]')].every(
      (x) => x.disabled,
    ),
  );
  assert.equal(ui.document.querySelector('[data-remove-comm]'), null);
  ui.el('partyForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.patched.length + state.posted.length, 0);
});

test('users without permission are warned', async (t) => {
  const { ui } = await partyPage(t, { me: {} });
  assert.match(ui.el('status').textContent, /do not have permission to view Interested Parties/);
});

test('changelog tab and deep link load entries', async (t) => {
  const { ui } = await partyPage(t, { query: '&id=ip1&tab=changelog' });
  assert.match(ui.el('partyChangelog').textContent, /Party created/);
  ui.el('partyCommunicationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=communications/);
  ui.el('partyDetailsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.doesNotMatch(ui.window.location.search, /tab=/);
});

test('deletion confirms first and save failures are reported', async (t) => {
  const cancelled = await partyPage(t, { globals: { confirm: () => false } });
  cancelled.ui.el('deleteParty').click();
  assert.deepEqual(cancelled.state.removed, []);
  const { ui, state } = await partyPage(t, { globals: { confirm: () => true } });
  ui.el('deleteParty').click();
  await settle(() => state.removed.length === 1);
  assert.deepEqual(state.removed, ['/api/v1/interested-parties/ip1']);
  const failing = await partyPage(t, { patchFail: true });
  failing.ui.el('partyForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => failing.ui.el('status').textContent === 'Error: Rejected');
  failing.ui.el('saveCommunications').click();
  await settle(() => failing.state.patched.length === 2);
});

test('lookup failures are reported and the party can be sampled into an audit', async (t) => {
  const failed = await partyPage(t, { fail: true });
  assert.match(
    failed.ui.el('status').textContent,
    /Failed to load Interested Party: Error: Meta offline/,
  );
  const { ui, state } = await partyPage(t);
  ui.el('samplePartyAudit').click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.entity_type, 'interested_party');
  assert.equal(state.posted[0].body.title, 'Regulator <script> — Legal');
});

test('disabled controls feature skips the control catalogue', async (t) => {
  const { ui } = await partyPage(t, {
    me: { is_admin: true, features: { enabled: { controls: false } } },
  });
  assert.match(ui.el('controlsChecklist').textContent, /No controls match/);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/controls')));
});

test('audit managers without event access can sample an Interested Party', async (t) => {
  const { ui } = await partyPage(t, {
    me: {
      can_view_interested_parties: true,
      can_manage_audits: true,
      features: { enabled: { event_sampling: false } },
    },
  });
  assert.equal(ui.el('samplePartyAudit').style.display, '');
});
