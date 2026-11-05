// People and vendor records: moving between tabs by keyboard, cancelling and
// failing edits, assurance records edited, discarded and deleted, and the
// People & vendors register's tabs, paging and legacy links.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));
const pause = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));
const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';
const manager = {
  can_manage_isms: true,
  features: { enabled: { evidence: false, swot: false, stories: false } },
};
const meta = () => ({
  users: [{ id: 'u1', name: 'dana' }],
  org_nodes: [{ id: 'o1', name: 'Security team' }],
  assets: [{ id: 'as1', name: 'Payroll DB' }],
});
const person = () => ({
  id: 'pe1',
  name: 'Alex Example',
  email: 'alex@example.test',
  position: 'Engineer',
  notes: '',
  username: 'dana',
  user_id: 'u1',
  org_node_id: 'o1',
  org_node: 'Security team',
  asset_ids: ['as1'],
  assets: [{ id: 'as1', name: 'Payroll DB' }],
  assurances: [
    {
      id: 'as-1',
      category: 'Screening',
      name: 'Police check',
      status: 'met',
      source_system: 'HRIS',
      evidence_url: 'https://hr.example.test/check/1',
      completed_at: '2026-01-01',
      expires_at: '2027-01-01',
      notes: 'Renew yearly',
    },
    {
      id: 'as-2',
      category: 'Training',
      name: HOSTILE + 'Induction',
      status: 'pending',
      source_system: null,
      evidence_url: null,
      completed_at: null,
      expires_at: null,
      notes: '',
    },
  ],
});

// A person or vendor page. Each write handler may be replaced to answer slowly
// or fail; every request is recorded in `sent`.
async function record(t, kind = 'person', options = {}) {
  const current = { value: options.record ?? (kind === 'person' ? person() : null) };
  const sent = [];
  const navigated = [];
  const id = current.value?.id;
  const collection = kind === 'person' ? 'people' : 'vendors';
  const search = options.search ?? `?id=${id}`;
  const handle = (method) => async (u, body) => {
    sent.push([method, u.pathname, body === undefined ? undefined : plain(body)]);
    const custom = options[method]?.(u, body, current);
    if (custom !== undefined) return custom;
    if (method === 'DELETE' && u.pathname.includes('/assurances/')) {
      const gone = decodeURIComponent(u.pathname.split('/').pop());
      current.value.assurances = current.value.assurances.filter((a) => a.id !== gone);
    }
    return {};
  };
  const ui = await page(t, kind, {
    me: options.me ?? manager,
    query: search ? '&' + search.slice(1) : '',
    confirm: options.confirm ?? true,
    globals: { location: { search, assign: (url) => navigated.push(url) } },
    get: async (u) => {
      if (u.pathname === `/api/v1/isms/${collection}/${id}`) return structuredClone(current.value);
      if (u.pathname === '/api/v1/isms/assurance/meta') {
        if (options.metaFails) throw new Error('Metadata offline');
        return meta();
      }
      if (u.pathname === `/api/v1/isms/vendors/${id}/cloud-posture`)
        return { providers: [], can_refresh: false, interval_seconds: 3600 };
      throw new Error('Unexpected GET ' + u);
    },
    post: handle('POST'),
    patch: handle('PATCH'),
    remove: handle('DELETE'),
  });
  await settle(ui.idle);
  await pause(5);
  return { ui, sent, navigated, current };
}
const submit = (ui, id) =>
  ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true, bubbles: true }));
const type = (ui, id, value) => {
  ui.el(id).value = value;
  ui.el(id).dispatchEvent(new ui.window.Event('input', { bubbles: true }));
};
// API requests other than the shared navbar's framework catalogue.
const records = (ui) => ui.calls.filter((c) => c.url !== '/api/v1/frameworks');
const activeTab = (ui) => ui.document.querySelector('[data-record-tab].active')?.id;
const key = (ui, target, name) => {
  const event = new ui.window.KeyboardEvent('keydown', { key: name, cancelable: true });
  target.dispatchEvent(event);
  return event;
};
// Resolves once `release()` is called, so a write can be held in flight.
function held() {
  let release;
  const promise = new Promise((resolve) => (release = resolve));
  return { promise, release: (value = {}) => release(value) };
}

test('arrow, Home and End keys move between the visible record tabs', async (t) => {
  const { ui } = await record(t);
  // Without evidence access the posture tab is hidden and assurance opens first.
  assert.equal(ui.el('tab-posture').parentElement.hidden, true);
  assert.equal(activeTab(ui), 'tab-assurance');
  const press = (name) => key(ui, ui.document.activeElement, name);
  ui.el('tab-assurance').focus();

  assert.equal(press('ArrowRight').defaultPrevented, true);
  assert.equal(activeTab(ui), 'tab-assets');
  assert.equal(ui.document.activeElement, ui.el('tab-assets'));
  assert.equal(ui.el('tab-assets').getAttribute('aria-selected'), 'true');
  assert.equal(ui.el('tab-assets').tabIndex, 0);
  assert.equal(ui.el('tab-assurance').tabIndex, -1);
  assert.ok(ui.el('pane-assets').classList.contains('active'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'assets');

  press('End');
  assert.equal(activeTab(ui), 'tab-related');
  // From the last tab, the next one is the first.
  press('ArrowRight');
  assert.equal(activeTab(ui), 'tab-details');
  // The hidden posture tab is skipped.
  press('ArrowRight');
  assert.equal(activeTab(ui), 'tab-assurance');
  press('Home');
  assert.equal(activeTab(ui), 'tab-details');
  press('ArrowLeft');
  assert.equal(activeTab(ui), 'tab-related');
  assert.equal(ui.document.activeElement, ui.el('tab-related'));
  // Other keys are left to the browser.
  assert.equal(press('Enter').defaultPrevented, false);
  assert.equal(activeTab(ui), 'tab-related');
  // Opening Related items without any related section says so.
  assert.equal(ui.el('noRelated').hidden, false);
});

test('a tab named in the address opens when it is visible, and the default opens otherwise', async (t) => {
  const assets = await record(t, 'person', { search: '?id=pe1&tab=assets' });
  assert.equal(activeTab(assets.ui), 'tab-assets');
  assert.equal(assets.ui.el('recordAssets').textContent, 'Payroll DB');
  // Posture is hidden without evidence access, so assurance opens instead.
  const posture = await record(t, 'person', { search: '?id=pe1&tab=posture' });
  assert.equal(activeTab(posture.ui), 'tab-assurance');
});

test('a vendor whose cloud provider reports posture opens on the cloud tab', async (t) => {
  const vendor = {
    id: 've1',
    name: 'Cloud Co',
    description: '',
    website: '',
    contact: '',
    asset_ids: [],
    assets: [],
    has_cloud_posture: true,
  };
  const { ui } = await record(t, 'vendor', {
    record: vendor,
    me: { can_view_isms: true, can_view_events: true },
  });
  assert.equal(ui.el('tab-cloud').parentElement.hidden, false);
  assert.equal(activeTab(ui), 'tab-cloud');
  await settle(() => /No cloud provider/.test(ui.el('cloudPosture').textContent));
  assert.ok(ui.calls.some((c) => c.url === '/api/v1/isms/vendors/ve1/cloud-posture'));
  assert.match(ui.el('cloudPosture').textContent, /Cloud posture · Cloud Co/);
});

test('an edit link opens the editor for managers and is ignored for readers', async (t) => {
  const editing = await record(t, 'person', { search: '?id=pe1&edit=1' });
  assert.equal(editing.ui.el('recordEditor').hidden, false);
  assert.equal(activeTab(editing.ui), 'tab-details');
  assert.equal(editing.ui.document.activeElement, editing.ui.el('personName'));
  assert.equal(editing.ui.el('personName').value, 'Alex Example');

  const reading = await record(t, 'person', {
    search: '?id=pe1&edit=1',
    me: { can_view_isms: true, features: manager.features },
  });
  assert.equal(reading.ui.el('recordEditor').hidden, true);
  assert.ok(!reading.ui.calls.some((c) => c.url === '/api/v1/isms/assurance/meta'));
});

test('readers without manage access cannot start a new record', async (t) => {
  const { ui } = await record(t, 'person', {
    record: null,
    search: '',
    me: { can_view_isms: true },
  });
  assert.equal(ui.el('recordSubtitle').textContent, 'You do not have permission to add records.');
  assert.equal(ui.el('recordEditor').hidden, true);
  assert.equal(ui.el('recordTabs').hidden, true);
  assert.deepEqual(records(ui), []);
});

test('an editor that cannot load its choices says so and stays closed', async (t) => {
  const { ui } = await record(t, 'person', { metaFails: true });
  ui.el('editRecord').click();
  await settle(() => ui.el('status').textContent !== '');
  assert.equal(ui.el('status').textContent, 'Could not open the editor: Metadata offline');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('recordEditor').hidden, true);
  assert.equal(ui.el('recordFacts').hidden, false);
});

test('a link to a deactivated account is kept and saved unchanged', async (t) => {
  const former = { ...person(), user_id: 'u9', username: 'former.staff' };
  const { ui, sent } = await record(t, 'person', { record: former });
  ui.el('editRecord').click();
  await settle(() => !ui.el('recordEditor').hidden);
  assert.deepEqual(
    [...ui.el('personUser').options].map((o) => [o.value, o.textContent, o.selected]),
    [
      ['', 'None', false],
      ['u1', 'dana', false],
      ['u9', 'former.staff', true],
    ],
  );
  submit(ui, 'personForm');
  await settle(() => sent.length === 1);
  assert.equal(sent[0][2].user_id, 'u9');
});

test('without the assets feature a save keeps the linked assets', async (t) => {
  const { ui, sent } = await record(t, 'person', {
    me: { ...manager, features: { enabled: { ...manager.features.enabled, assets: false } } },
  });
  assert.equal(ui.el('tab-assets').parentElement.hidden, true);
  ui.el('editRecord').click();
  await settle(() => !ui.el('recordEditor').hidden);
  assert.equal(ui.el('personAssets').parentElement.hidden, true);
  submit(ui, 'personForm');
  await settle(() => sent.length === 1);
  assert.deepEqual(sent[0][2].asset_ids, ['as1']);
});

test('a new person saved without the assets feature links no assets', async (t) => {
  const { ui, sent, navigated } = await record(t, 'person', {
    record: null,
    search: '',
    me: { ...manager, features: { enabled: { ...manager.features.enabled, assets: false } } },
  });
  assert.equal(ui.el('recordEditor').hidden, false);
  type(ui, 'personName', 'New starter');
  submit(ui, 'personForm');
  await settle(() => navigated.length === 1);
  assert.equal(sent[0][0], 'POST');
  assert.equal(sent[0][1], '/api/v1/isms/people');
  assert.equal(sent[0][2].name, 'New starter');
  assert.deepEqual(sent[0][2].asset_ids, []);
});

test('cancelling an edit asks before discarding typed details', async (t) => {
  let answer = false;
  const { ui, navigated } = await record(t, 'person', { confirm: () => answer });
  ui.el('editRecord').click();
  await settle(() => !ui.el('recordEditor').hidden);
  // Unchanged details close without a question.
  ui.el('newPerson').click();
  await settle(() => ui.el('recordEditor').hidden);
  assert.equal(ui.el('recordFacts').hidden, false);
  assert.equal(ui.confirmations.length, 0);

  ui.el('editRecord').click();
  await settle(() => !ui.el('recordEditor').hidden);
  type(ui, 'personName', 'Typed name');
  assert.equal(ui.el('recordDirty').hidden, false);
  ui.el('newPerson').click();
  await settle(() => ui.confirmations.length === 1);
  assert.equal(ui.confirmations[0].title, 'Discard changes?');
  assert.equal(ui.el('recordEditor').hidden, false);
  assert.equal(ui.el('personName').value, 'Typed name');

  answer = true;
  ui.el('newPerson').click();
  await settle(() => ui.el('recordEditor').hidden);
  assert.equal(ui.el('personName').value, 'Alex Example');
  assert.equal(ui.el('recordDirty').hidden, true);
  assert.deepEqual(navigated, []);
});

test('cancelling a new record returns to the register', async (t) => {
  const { ui, navigated } = await record(t, 'person', { record: null, search: '' });
  assert.equal(ui.el('recordEditor').hidden, false);
  ui.el('newPerson').click();
  await settle(() => navigated.length === 1);
  assert.deepEqual(navigated, ['/assurance.html?tab=people&framework=A']);
});

test('a save in flight is not repeated, and edit and delete wait for it', async (t) => {
  const save = held();
  const { ui, sent, navigated } = await record(t, 'person', {
    PATCH: () => save.promise,
  });
  ui.el('editRecord').click();
  await settle(() => !ui.el('recordEditor').hidden);
  type(ui, 'personName', 'Renamed');
  submit(ui, 'personForm');
  submit(ui, 'personForm');
  ui.el('deleteRecord').click();
  // Edit would reload the saved details over the typing.
  ui.el('editRecord').click();
  await pause();
  assert.equal(ui.el('personName').value, 'Renamed');
  assert.deepEqual(
    sent.map(([method]) => method),
    ['PATCH'],
  );
  assert.equal(ui.confirmations.length, 0);
  assert.equal(ui.document.querySelector('#personForm [type="submit"]').disabled, true);
  save.release();
  await settle(() => navigated.length === 1);
  assert.deepEqual(navigated, ['/assurance.html?tab=people&framework=A']);
});

test('deleting a record can be declined, and a refused deletion stays on the page', async (t) => {
  let answer = false;
  const { ui, sent, navigated } = await record(t, 'person', {
    confirm: () => answer,
    DELETE: () => {
      throw new Error('In use by an audit');
    },
  });
  ui.el('deleteRecord').click();
  await settle(() => ui.confirmations.length === 1);
  await pause();
  assert.deepEqual(sent, []);
  answer = true;
  ui.el('deleteRecord').click();
  await settle(() => ui.el('status').textContent !== '');
  assert.equal(ui.el('status').textContent, 'Could not delete person: In use by an audit');
  assert.deepEqual(sent, [['DELETE', '/api/v1/isms/people/pe1', undefined]]);
  assert.deepEqual(navigated, []);
  // A later attempt is not blocked by the failed one.
  ui.el('deleteRecord').click();
  await settle(() => sent.length === 2);
});

test('assurance records are edited in place and saved with a PATCH', async (t) => {
  const { ui, sent, current } = await record(t, 'person', {
    PATCH: (u, body, store) => {
      store.value.assurances[0] = { ...store.value.assurances[0], ...plain(body) };
      return {};
    },
  });
  ui.el('assuranceRows').querySelector('[data-edit-assurance="as-1"]').click();
  assert.equal(ui.el('assuranceFormTitle').textContent, 'Edit assurance record');
  assert.equal(ui.el('assuranceStatus').value, 'met');
  assert.equal(ui.el('assuranceCompleted').value, '2026-01-01');
  type(ui, 'assuranceName', 'Police check (renewed)');
  ui.el('assuranceCompleted').value = '';
  submit(ui, 'assuranceForm');
  await settle(() => ui.el('status').textContent === 'Assurance record saved.');
  assert.deepEqual(sent[0], [
    'PATCH',
    '/api/v1/isms/people/pe1/assurances/as-1',
    {
      category: 'Screening',
      name: 'Police check (renewed)',
      status: 'met',
      source_system: 'HRIS',
      evidence_url: 'https://hr.example.test/check/1',
      completed_at: null,
      expires_at: '2027-01-01',
      notes: 'Renew yearly',
    },
  ]);
  assert.equal(current.value.assurances[0].name, 'Police check (renewed)');
  assert.match(ui.el('assuranceRows').textContent, /Police check \(renewed\)/);
  assert.equal(ui.el('assuranceEditor').hidden, true);
  // A new record after an edit is posted, not patched.
  ui.el('addAssurance').click();
  assert.equal(ui.el('assuranceFormTitle').textContent, 'Add assurance record');
  assert.equal(ui.el('assuranceStatus').value, 'pending');
  assert.equal(ui.el('assuranceName').value, '');
  type(ui, 'assuranceCategory', 'MFA');
  type(ui, 'assuranceName', 'Hardware key');
  submit(ui, 'assuranceForm');
  await settle(() => sent.length === 2);
  assert.deepEqual(sent[1].slice(0, 2), ['POST', '/api/v1/isms/people/pe1/assurances']);
});

test('a refused assurance save keeps the editor and what was typed', async (t) => {
  const { ui, sent } = await record(t, 'person', {
    POST: () => {
      throw new Error('Category is required');
    },
  });
  ui.el('addAssurance').click();
  type(ui, 'assuranceName', 'Unsaved requirement');
  submit(ui, 'assuranceForm');
  await settle(() => ui.el('status').textContent !== '');
  assert.equal(ui.el('status').textContent, 'Could not save assurance: Category is required');
  assert.equal(sent.length, 1);
  assert.equal(ui.el('assuranceEditor').hidden, false);
  assert.equal(ui.el('assuranceName').value, 'Unsaved requirement');
  assert.equal(ui.el('assuranceDirty').hidden, false);
  assert.equal(ui.document.querySelector('#assuranceForm [type="submit"]').disabled, false);
});

test('an assurance save in flight is sent once and holds other assurance changes', async (t) => {
  const save = held();
  const { ui, sent } = await record(t, 'person', { POST: () => save.promise });
  ui.el('addAssurance').click();
  type(ui, 'assuranceCategory', 'Training');
  type(ui, 'assuranceName', 'Phishing drill');
  submit(ui, 'assuranceForm');
  submit(ui, 'assuranceForm');
  // Neither editing nor deleting another record starts meanwhile.
  ui.el('assuranceRows').querySelector('[data-edit-assurance="as-1"]').click();
  ui.el('assuranceRows').querySelector('[data-delete-assurance="as-1"]').click();
  await pause();
  assert.deepEqual(
    sent.map(([method]) => method),
    ['POST'],
  );
  assert.equal(ui.confirmations.length, 0);
  assert.equal(ui.el('assuranceName').value, 'Phishing drill');
  save.release();
  await settle(() => ui.el('status').textContent === 'Assurance record saved.');
});

test('switching or cancelling an assurance record asks before discarding typing', async (t) => {
  let answer = false;
  const { ui } = await record(t, 'person', { confirm: () => answer });
  ui.el('assuranceRows').querySelector('[data-edit-assurance="as-1"]').click();
  type(ui, 'assuranceNotes', 'Half-written note');

  ui.el('assuranceRows').querySelector('[data-edit-assurance="as-2"]').click();
  await settle(() => ui.confirmations.length === 1);
  assert.deepEqual(plain(ui.confirmations[0]), {
    title: 'Discard changes?',
    message: 'Discard the unsaved assurance record?',
    confirmLabel: 'Discard',
  });
  assert.equal(ui.el('assuranceName').value, 'Police check');
  assert.equal(ui.el('assuranceNotes').value, 'Half-written note');

  ui.el('newAssurance').click();
  await settle(() => ui.confirmations.length === 2);
  assert.equal(ui.el('assuranceEditor').hidden, false);

  answer = true;
  ui.el('assuranceRows').querySelector('[data-edit-assurance="as-2"]').click();
  await settle(() => ui.el('assuranceName').value === HOSTILE + 'Induction');
  assert.equal(ui.el('assuranceNotes').value, '');
  type(ui, 'assuranceNotes', 'Another note');
  ui.el('newAssurance').click();
  await settle(() => ui.el('assuranceEditor').hidden);
  assert.equal(ui.confirmations.length, 4);
  assert.equal(ui.el('assuranceDirty').hidden, true);
  // A fresh record afterwards is added, not edited.
  ui.el('addAssurance').click();
  await settle(() => ui.el('assuranceFormTitle').textContent === 'Add assurance record');
});

test('deleting an assurance record removes it, and closes its editor when it was open', async (t) => {
  let answer = false;
  const { ui, sent } = await record(t, 'person', { confirm: () => answer });
  const remove = (id) =>
    ui.el('assuranceRows').querySelector(`[data-delete-assurance="${id}"]`).click();
  remove('as-1');
  await settle(() => ui.confirmations.length === 1);
  assert.deepEqual(plain(ui.confirmations[0]), {
    title: 'Delete assurance record',
    message: 'Delete this assurance record?',
    confirmLabel: 'Delete',
    danger: true,
  });
  await pause();
  assert.deepEqual(sent, []);

  answer = true;
  // Deleting another record leaves the open editor alone.
  ui.el('assuranceRows').querySelector('[data-edit-assurance="as-1"]').click();
  remove('as-2');
  await settle(() => !/Induction/.test(ui.el('assuranceRows').textContent));
  assert.deepEqual(sent, [['DELETE', '/api/v1/isms/people/pe1/assurances/as-2', undefined]]);
  assert.equal(ui.el('assuranceEditor').hidden, false);
  assert.equal(ui.el('assuranceName').value, 'Police check');
  assert.equal(ui.window.pwned, undefined);

  // Deleting the record being edited closes its editor.
  remove('as-1');
  await settle(() => ui.el('assuranceEditor').hidden);
  assert.equal(ui.el('assuranceRows').textContent, 'No assurance records for this person.');
  assert.ok(ui.calls.filter((c) => c.url === '/api/v1/isms/people/pe1').length >= 3);
  // The next save adds a record instead of patching the deleted one.
  ui.el('addAssurance').click();
  type(ui, 'assuranceCategory', 'MFA');
  type(ui, 'assuranceName', 'Key');
  submit(ui, 'assuranceForm');
  await settle(() => sent.length === 3);
  assert.deepEqual(sent[2].slice(0, 2), ['POST', '/api/v1/isms/people/pe1/assurances']);
});

test('a refused assurance deletion is reported and the record stays', async (t) => {
  const { ui, sent } = await record(t, 'person', {
    DELETE: () => {
      throw new Error('Record is locked');
    },
  });
  ui.el('assuranceRows').querySelector('[data-delete-assurance="as-1"]').click();
  await settle(() => ui.el('status').textContent !== '');
  assert.equal(ui.el('status').textContent, 'Record is locked');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(sent.length, 1);
  assert.match(ui.el('assuranceRows').textContent, /Police check/);
  // The failure releases the page for the next change.
  ui.el('assuranceRows').querySelector('[data-delete-assurance="as-1"]').click();
  await settle(() => sent.length === 2);
});

const registerPeople = () => [
  {
    id: 'pe1',
    name: 'Alex',
    email: '',
    position: '',
    org_node: null,
    username: null,
    assurances: [],
  },
];
const registerVendors = () => [
  { id: 've1', name: 'Hosting Co', contact: '', description: '', asset_ids: [] },
];
async function register(t, { me = { is_admin: true }, query = '', people, vendors } = {}) {
  const navigated = [];
  const ui = await page(t, 'assurance', {
    me,
    query,
    globals: { location: { search: query, replace: (url) => navigated.push(url) } },
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/people') return { items: people ?? registerPeople() };
      if (u.pathname === '/api/v1/isms/vendors') return { items: vendors ?? registerVendors() };
      throw new Error('Unexpected GET ' + u);
    },
  });
  return { ui, navigated };
}
const pane = (ui) =>
  ['people', 'vendors'].filter((kind) => ui.el(kind + 'Pane').classList.contains('active'));
const tabParam = (ui) => new URL(ui.window.location.href).searchParams.get('tab');

test('register rows show a dash for missing roles, accounts, contacts and descriptions', async (t) => {
  const { ui } = await register(t);
  assert.deepEqual(
    [...ui.el('peopleList').querySelector('tr').cells].map((c) => c.textContent),
    ['Alex', '—', '—', '0'],
  );
  assert.deepEqual(
    [...ui.el('vendorsList').querySelector('tr').cells].map((c) => c.textContent),
    ['Hosting Co', '—', '—', '0'],
  );
});

test('register tabs open by click and by arrow, Home and End keys', async (t) => {
  const { ui } = await register(t);
  assert.deepEqual(pane(ui), ['people']);
  ui.el('vendorsTab').click();
  assert.deepEqual(pane(ui), ['vendors']);
  assert.equal(tabParam(ui), 'vendors');
  assert.equal(ui.el('vendorsTab').getAttribute('aria-selected'), 'true');
  assert.equal(ui.el('peopleTab').tabIndex, -1);

  const press = (tab, name) => key(ui, ui.el(tab), name);
  assert.equal(press('vendorsTab', 'ArrowLeft').defaultPrevented, true);
  assert.deepEqual(pane(ui), ['people']);
  assert.equal(ui.document.activeElement, ui.el('peopleTab'));
  press('peopleTab', 'ArrowRight');
  assert.deepEqual(pane(ui), ['vendors']);
  press('vendorsTab', 'Home');
  assert.deepEqual(pane(ui), ['people']);
  press('peopleTab', 'End');
  assert.deepEqual(pane(ui), ['vendors']);
  assert.equal(ui.document.activeElement, ui.el('vendorsTab'));
  assert.equal(press('vendorsTab', 'a').defaultPrevented, false);
  assert.deepEqual(pane(ui), ['vendors']);
});

test('with one register switched off, keys stay on the other', async (t) => {
  const { ui } = await register(t, {
    me: { is_admin: true, features: { enabled: { people: false } } },
  });
  // Without a tab in the address the vendors register opens.
  assert.deepEqual(pane(ui), ['vendors']);
  assert.equal(ui.el('peopleTab').parentElement.hidden, true);
  assert.ok(!ui.calls.some((c) => c.url === '/api/v1/isms/people'));
  ui.el('vendorsTab').focus();
  key(ui, ui.el('vendorsTab'), 'ArrowRight');
  // Focus stays off the hidden people tab.
  assert.equal(ui.document.activeElement, ui.el('vendorsTab'));
  key(ui, ui.el('vendorsTab'), 'Home');
  assert.equal(ui.document.activeElement, ui.el('vendorsTab'));
  assert.deepEqual(pane(ui), ['vendors']);
  assert.equal(tabParam(ui), 'vendors');
});

test('with both registers switched off, neither tab opens', async (t) => {
  const { ui } = await register(t, {
    me: { is_admin: true, features: { enabled: { people: false, vendors: false } } },
  });
  assert.deepEqual(pane(ui), []);
  assert.equal(tabParam(ui), null);
  assert.equal(ui.el('vendorsTab').getAttribute('aria-selected'), 'false');
  assert.deepEqual(records(ui), []);
  assert.equal(ui.el('newPerson').hidden, true);
  assert.equal(ui.el('newVendor').hidden, true);
});

test('register pages move back as well as forward', async (t) => {
  const people = Array.from({ length: 51 }, (_, i) => ({
    ...registerPeople()[0],
    id: 'p' + i,
    name: 'Person ' + String(i).padStart(2, '0'),
  }));
  const { ui } = await register(t, { people });
  const prev = ui.document.querySelector('[data-page-prev="people"]');
  const next = ui.document.querySelector('[data-page-next="people"]');
  assert.equal(prev.disabled, true);
  next.click();
  assert.equal(ui.el('peopleCount').textContent, '51–51 of 51');
  assert.equal(prev.disabled, false);
  assert.equal(next.disabled, true);
  prev.click();
  assert.equal(ui.el('peopleCount').textContent, '1–50 of 51');
  assert.equal(ui.el('peopleList').children.length, 50);
  assert.equal(prev.disabled, true);
});

test('a legacy edit link opens the matching vendor, and an unknown one stays on the register', async (t) => {
  const vendor = await register(t, { query: '&edit=ve1' });
  await settle(() => vendor.navigated.length === 1);
  assert.deepEqual(vendor.navigated, ['/vendor.html?id=ve1&framework=A']);
  const unknown = await register(t, { query: '&edit=gone' });
  await settle(() => unknown.ui.el('peopleList').children.length === 1);
  await pause();
  assert.deepEqual(unknown.navigated, []);
});
