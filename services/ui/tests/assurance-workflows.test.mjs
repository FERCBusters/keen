import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));
const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';
const meta = {
  users: [{ id: 'u1', name: 'dana' }],
  org_nodes: [{ id: 'o1', name: 'Security team' }],
  assets: [
    { id: 'as1', name: 'Payroll DB' },
    { id: 'as2', name: HOSTILE + 'Laptop fleet' },
  ],
};
const people = () => [
  {
    id: 'pe1',
    name: HOSTILE + 'Alex Example',
    email: 'alex@example.test',
    position: 'Engineer',
    notes: 'Contractor',
    username: 'alex',
    user_id: 'u1',
    org_node_id: 'o1',
    asset_ids: ['as1', 'as2'],
    assurances: [
      {
        id: 'as-1',
        category: 'Screening',
        name: 'Police check',
        status: 'not_applicable',
        source_system: 'HRIS',
        evidence_url: 'javascript:alert(1)',
        completed_at: '2026-01-01',
        expires_at: '2027-01-01',
        notes: 'Renew yearly',
      },
      {
        id: 'as-2',
        category: 'Training',
        name: 'Security awareness',
        status: 'met',
        evidence_url: 'https://lms.example.test/cert/1',
      },
    ],
  },
  {
    id: 'pe2',
    name: 'Sam Sample',
    email: 'sam@example.test',
    position: '',
    org_node: 'Finance',
    asset_ids: [],
    assurances: [],
  },
];
const vendors = () => [
  {
    id: 've1',
    name: 'Hosting Co',
    description: 'Primary host',
    website: 'https://host.example.test',
    contact: 'ops@host.example.test',
    asset_ids: ['as1'],
  },
];

async function assurancePage(t, options = {}) {
  const state = {
    people: options.people ?? people(),
    vendors: options.vendors ?? vendors(),
    posted: [],
    patched: [],
    removed: [],
    prompts: [],
  };
  const fail = options.fail || {};
  const ui = await page(t, 'assurance', {
    me: options.me || { is_admin: true },
    query: options.query || '',
    globals: { confirm: (m) => (state.prompts.push(m), options.confirm ?? true) },
    setup(window) {
      if (options.tabs) window.bootstrap = options.tabs;
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/assurance/meta') {
        if (fail.load) throw new Error('Register offline');
        return structuredClone(meta);
      }
      if (u.pathname === '/api/v1/isms/people') return { items: structuredClone(state.people) };
      if (u.pathname === '/api/v1/isms/vendors') return { items: structuredClone(state.vendors) };
      if (u.pathname === '/api/v1/isms/swot') {
        if (fail.swot) throw new Error('Forbidden');
        return {
          items: [{ id: 'sw1', title: HOSTILE + 'Key supplier', kind: 'threat', significance: 4 }],
          total: 1,
        };
      }
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, body) => {
      state.posted.push({ path: u.pathname, body: plain(body) });
      if (fail.save) throw new Error('Validation failed');
      if (u.pathname === '/api/v1/isms/people') {
        const created = { ...plain(body), id: 'pe9', assurances: [] };
        state.people.push(created);
        return created;
      }
      if (u.pathname === '/api/v1/isms/vendors') {
        const created = { ...plain(body), id: 've9' };
        state.vendors.push(created);
        return created;
      }
      const person = state.people.find((p) => u.pathname.includes(`/people/${p.id}/`));
      person.assurances.push({ ...plain(body), id: 'as-9' });
      return {};
    },
    patch: async (u, body) => {
      state.patched.push({ path: u.pathname, body: plain(body) });
      if (fail.save) throw new Error('Validation failed');
      const person = state.people.find((p) => u.pathname === `/api/v1/isms/people/${p.id}`);
      if (person) Object.assign(person, plain(body));
      const vendor = state.vendors.find((v) => u.pathname === `/api/v1/isms/vendors/${v.id}`);
      if (vendor) Object.assign(vendor, plain(body));
      return person || vendor || {};
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (fail.remove) throw new Error('Locked');
      const id = u.pathname.split('/').pop();
      state.people = state.people.filter((p) => p.id !== id);
      state.vendors = state.vendors.filter((v) => v.id !== id);
      for (const p of state.people) p.assurances = p.assurances.filter((a) => a.id !== id);
      return {};
    },
  });
  await settle(() => ui.el('peopleList').children.length || ui.el('status').textContent);
  return { ui, state };
}
const submit = (ui, id) =>
  ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
const selectOptions = (ui, id, values) => {
  for (const option of ui.el(id).options) option.selected = values.includes(option.value);
};

test('people and vendors are listed as text with linked assets and search', async (t) => {
  const { ui } = await assurancePage(t);
  const list = ui.el('peopleList');
  assert.equal(list.querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
  const [alex, sam] = list.querySelectorAll('.list-group-item');
  assert.match(
    alex.textContent,
    /Engineer · Payroll DB, <img src=x onerror="globalThis\.pwned=1">Laptop fleet · 2 assurance records/,
  );
  assert.match(alex.querySelector('.badge').textContent, /alex/);
  assert.match(sam.textContent, /Finance · No assets linked · 0 assurance records/);
  assert.equal(
    alex.querySelector('a').getAttribute('href'),
    '/swot.html?target_type=person&target_id=pe1',
  );
  assert.match(ui.el('vendorsList').textContent, /Hosting Co\s+Primary host\s*Payroll DB/);
  assert.equal(ui.el('assuranceCard').style.display, 'none');
  // Select lists hold names as text.
  assert.equal(ui.el('personAssets').options[1].textContent, HOSTILE + 'Laptop fleet');
  assert.equal(ui.el('personUser').options[0].textContent, '— None —');
  assert.equal(ui.el('personAssets').options.length, 2);

  ui.el('personSearch').value = 'FINANCE';
  ui.el('personSearch').dispatchEvent(new ui.window.Event('input'));
  assert.equal(list.querySelectorAll('.list-group-item').length, 0);
  ui.el('personSearch').value = 'sam@';
  ui.el('personSearch').dispatchEvent(new ui.window.Event('input'));
  assert.equal(list.querySelectorAll('.list-group-item').length, 1);
  ui.el('personSearch').value = 'nobody';
  ui.el('personSearch').dispatchEvent(new ui.window.Event('input'));
  assert.match(list.textContent, /No matching people yet/);
});

test('viewing a person shows assurance records with safe evidence links and related SWOT items', async (t) => {
  const { ui } = await assurancePage(t);
  ui.el('peopleList').querySelector('[data-view-person="pe1"]').click();
  assert.equal(ui.el('assuranceCard').style.display, '');
  assert.equal(ui.el('assuranceTitle').textContent, `Personnel assurance · ${HOSTILE}Alex Example`);
  const rows = [...ui.el('assuranceRows').querySelectorAll('tr')];
  assert.match(
    rows[0].textContent,
    /Screening\s*Police check\s*not applicable\s*HRIS\s*2026-01-01\s*2027-01-01/,
  );
  // Non-HTTP evidence URLs are neutralised.
  assert.equal(rows[0].querySelector('a').getAttribute('href'), '#');
  assert.equal(rows[1].querySelector('a').getAttribute('href'), 'https://lms.example.test/cert/1');
  assert.equal(rows[1].querySelector('a').getAttribute('rel'), 'noopener noreferrer');
  assert.match(rows[1].textContent, /—\s*—\s*—/);
  const swot = ui.el('assuranceSwot');
  await settle(() => /Related SWOT items/.test(swot.textContent));
  assert.equal(swot.querySelector('img'), null);
  assert.match(swot.textContent, /Key supplier threat · 4\/5/);
  assert.match(swot.textContent, /Explore in SWOT analysis \(1\)/);
  assert.ok(
    ui.calls.some((c) => c.url === '/api/v1/isms/swot?target_type=person&target_id=pe1&limit=100'),
  );

  ui.el('peopleList').querySelector('[data-view-person="pe2"]').click();
  assert.match(ui.el('assuranceRows').textContent, /No assurance records for this person/);
});

test('editing a person fills the form and saves changes', async (t) => {
  const { ui, state } = await assurancePage(t);
  ui.el('peopleList').querySelector('[data-edit-person="pe1"]').click();
  assert.equal(ui.el('personFormTitle').textContent, `Edit ${HOSTILE}Alex Example`);
  assert.equal(ui.el('personEmail').value, 'alex@example.test');
  assert.equal(ui.el('personUser').value, 'u1');
  assert.equal(ui.el('personOrg').value, 'o1');
  assert.deepEqual(
    [...ui.el('personAssets').selectedOptions].map((o) => o.value),
    ['as1', 'as2'],
  );
  ui.el('personPosition').value = 'Lead engineer';
  ui.el('personOrg').value = '';
  selectOptions(ui, 'personAssets', ['as2']);
  submit(ui, 'personForm');
  await settle(() => ui.el('status').textContent === 'Person saved.');
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/isms/people/pe1',
    body: {
      name: HOSTILE + 'Alex Example',
      email: 'alex@example.test',
      position: 'Lead engineer',
      notes: 'Contractor',
      user_id: 'u1',
      org_node_id: null,
      asset_ids: ['as2'],
    },
  });
  // The reloaded record stays selected.
  assert.equal(ui.el('personPosition').value, 'Lead engineer');
  assert.equal(ui.el('assuranceCard').style.display, '');

  ui.el('newPerson').click();
  assert.equal(ui.el('personFormTitle').textContent, 'Add a person');
  assert.equal(ui.el('personName').value, '');
  assert.equal(ui.el('personAssets').selectedOptions.length, 0);
  assert.equal(ui.el('assuranceCard').style.display, 'none');
});

test('a new person is created and selected for assurance entry', async (t) => {
  const { ui, state } = await assurancePage(t, { people: [] });
  assert.match(ui.el('peopleList').textContent, /No matching people yet/);
  ui.el('personName').value = 'Jordan New';
  ui.el('personEmail').value = 'jordan@example.test';
  selectOptions(ui, 'personAssets', ['as1']);
  submit(ui, 'personForm');
  await settle(() => ui.el('status').textContent === 'Person saved.');
  assert.equal(state.posted[0].path, '/api/v1/isms/people');
  assert.deepEqual(state.posted[0].body.asset_ids, ['as1']);
  assert.equal(state.posted[0].body.user_id, null);
  assert.equal(ui.el('personFormTitle').textContent, 'Edit Jordan New');
  assert.equal(ui.el('assuranceTitle').textContent, 'Personnel assurance · Jordan New');
});

test('assurance records are added, edited and deleted for the selected person', async (t) => {
  const { ui, state } = await assurancePage(t);
  ui.el('peopleList').querySelector('[data-view-person="pe1"]').click();
  ui.el('assuranceRows').querySelector('[data-edit-assurance="as-1"]').click();
  assert.equal(ui.el('assuranceStatus').value, 'not_applicable');
  assert.equal(ui.el('assuranceNotes').value, 'Renew yearly');
  ui.el('assuranceEvidence').value = 'https://hr.example.test/check/1';
  ui.el('assuranceCompleted').value = '';
  submit(ui, 'assuranceForm');
  await settle(() => ui.el('status').textContent === 'Assurance record saved.');
  assert.equal(state.patched[0].path, '/api/v1/isms/people/pe1/assurances/as-1');
  assert.equal(state.patched[0].body.evidence_url, 'https://hr.example.test/check/1');
  assert.equal(state.patched[0].body.completed_at, null);
  assert.equal(state.patched[0].body.expires_at, '2027-01-01');
  assert.equal(ui.el('assuranceName').value, '');

  ui.el('newAssurance').click();
  ui.el('assuranceCategory').value = 'Training';
  ui.el('assuranceName').value = 'Phishing drill';
  ui.el('assuranceStatus').value = 'pending';
  submit(ui, 'assuranceForm');
  await settle(
    () => state.posted.length === 1 && /Phishing drill/.test(ui.el('assuranceRows').textContent),
  );
  assert.equal(state.posted[0].path, '/api/v1/isms/people/pe1/assurances');
  assert.deepEqual(state.posted[0].body, {
    category: 'Training',
    name: 'Phishing drill',
    status: 'pending',
    source_system: '',
    evidence_url: '',
    completed_at: null,
    expires_at: null,
    notes: '',
  });

  ui.el('assuranceRows').querySelector('[data-delete-assurance="as-2"]').click();
  await settle(() => ui.el('status').textContent === 'Assurance record deleted.');
  assert.deepEqual(state.removed, ['/api/v1/isms/people/pe1/assurances/as-2']);
  assert.equal(state.prompts.at(-1), 'Delete this assurance record?');
  assert.doesNotMatch(ui.el('assuranceRows').textContent, /Security awareness/);
});

test('people and vendors are deleted only after confirmation', async (t) => {
  const declined = await assurancePage(t, { confirm: false });
  declined.ui.el('peopleList').querySelector('[data-delete-person="pe1"]').click();
  declined.ui.el('vendorsList').querySelector('[data-delete-vendor="ve1"]').click();
  declined.ui.el('peopleList').querySelector('[data-view-person="pe1"]').click();
  declined.ui.el('assuranceRows').querySelector('[data-delete-assurance="as-1"]').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(declined.state.removed, []);
  assert.deepEqual(declined.state.prompts, [
    'Delete this person and their assurance records?',
    'Delete this vendor? Assets will remain in the asset register.',
    'Delete this assurance record?',
  ]);

  const { ui, state } = await assurancePage(t);
  ui.el('peopleList').querySelector('[data-view-person="pe1"]').click();
  ui.el('peopleList').querySelector('[data-delete-person="pe1"]').click();
  await settle(() => ui.el('status').textContent === 'Person deleted.');
  assert.deepEqual(state.removed, ['/api/v1/isms/people/pe1']);
  assert.equal(ui.el('assuranceCard').style.display, 'none');
  assert.equal(ui.el('peopleList').querySelectorAll('.list-group-item').length, 1);
  ui.el('vendorsList').querySelector('[data-delete-vendor="ve1"]').click();
  await settle(() => ui.el('status').textContent === 'Vendor deleted.');
  assert.match(ui.el('vendorsList').textContent, /No vendors yet/);
});

test('vendors are edited, created and reset', async (t) => {
  const { ui, state } = await assurancePage(t);
  ui.el('vendorsList').querySelector('[data-edit-vendor="ve1"]').click();
  assert.equal(ui.el('vendorFormTitle').textContent, 'Edit Hosting Co');
  assert.equal(ui.el('vendorWebsite').value, 'https://host.example.test');
  assert.deepEqual(
    [...ui.el('vendorAssets').selectedOptions].map((o) => o.value),
    ['as1'],
  );
  await settle(() => /Related SWOT items/.test(ui.el('assuranceSwot').textContent));
  ui.el('vendorDescription').value = 'Primary and DR host';
  submit(ui, 'vendorForm');
  await settle(() => ui.el('status').textContent === 'Vendor saved.');
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/isms/vendors/ve1',
    body: {
      name: 'Hosting Co',
      description: 'Primary and DR host',
      website: 'https://host.example.test',
      contact: 'ops@host.example.test',
      asset_ids: ['as1'],
    },
  });
  assert.equal(ui.el('vendorDescription').value, 'Primary and DR host');

  ui.el('newVendor').click();
  assert.equal(ui.el('vendorFormTitle').textContent, 'Add a vendor');
  assert.equal(ui.el('vendorAssets').selectedOptions.length, 0);
  ui.el('vendorName').value = 'Payroll SaaS';
  submit(ui, 'vendorForm');
  await settle(
    () => state.posted.length === 1 && /Payroll SaaS/.test(ui.el('vendorsList').textContent),
  );
  assert.equal(state.posted[0].path, '/api/v1/isms/vendors');
  assert.equal(ui.el('vendorFormTitle').textContent, 'Edit Payroll SaaS');
});

test('save, delete and load failures are reported', async (t) => {
  const failing = await assurancePage(t, { fail: { save: true, remove: true, swot: true } });
  const { ui } = failing;
  submit(ui, 'personForm');
  await settle(() => ui.el('status').textContent === 'Could not save person: Validation failed');
  submit(ui, 'vendorForm');
  await settle(() => ui.el('status').textContent === 'Could not save vendor: Validation failed');
  ui.el('peopleList').querySelector('[data-view-person="pe1"]').click();
  await settle(() =>
    /unavailable with your current access/.test(ui.el('assuranceSwot').textContent),
  );
  submit(ui, 'assuranceForm');
  await settle(() => ui.el('status').textContent === 'Could not save assurance: Validation failed');
  ui.el('assuranceRows').querySelector('[data-delete-assurance="as-1"]').click();
  await settle(() => ui.el('status').textContent === 'Could not delete assurance: Locked');
  ui.el('peopleList').querySelector('[data-delete-person="pe2"]').click();
  await settle(() => ui.el('status').textContent === 'Could not delete person: Locked');
  ui.el('vendorsList').querySelector('[data-delete-vendor="ve1"]').click();
  await settle(() => ui.el('status').textContent === 'Could not delete vendor: Locked');
  assert.equal(ui.el('status').dataset.alert, 'danger');

  const offline = await assurancePage(t, { fail: { load: true } });
  assert.equal(
    offline.ui.el('status').textContent,
    'Could not load assurance register: Register offline',
  );
});

test('readers see the register without edit forms or actions', async (t) => {
  const { ui, state } = await assurancePage(t, { me: { can_view_isms: true } });
  for (const id of ['personForm', 'vendorForm', 'assuranceForm'])
    assert.equal(ui.el(id).style.display, 'none');
  assert.equal(
    ui.document.querySelector(
      '[data-edit-person], [data-delete-person], [data-edit-vendor], [data-delete-vendor]',
    ),
    null,
  );
  ui.el('peopleList').querySelector('[data-view-person="pe1"]').click();
  assert.equal(ui.el('assuranceRows').querySelector('[data-edit-assurance]'), null);
  submit(ui, 'personForm');
  submit(ui, 'assuranceForm');
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual([state.posted, state.patched], [[], []]);
});

test('feature flags limit the register to the enabled sections', async (t) => {
  const { ui } = await assurancePage(t, {
    me: { is_admin: true, features: { enabled: { people: false } } },
  });
  assert.equal(ui.el('vendorsPane').classList.contains('active'), true);
  assert.equal(ui.el('peoplePane').classList.contains('active'), false);
  assert.ok(!ui.calls.some((c) => c.url === '/api/v1/isms/people'));
  const noVendors = await assurancePage(t, {
    me: { is_admin: true, features: { enabled: { vendors: false } } },
  });
  assert.ok(!noVendors.ui.calls.some((c) => c.url === '/api/v1/isms/vendors'));
  assert.match(noVendors.ui.el('vendorsList').textContent, /No vendors yet/);
});

test('a SWOT edit link opens the linked vendor or person', async (t) => {
  const shown = [];
  const tabs = {
    Tab: {
      getOrCreateInstance: (el) => ({ show: () => shown.push(el.getAttribute('data-bs-target')) }),
    },
  };
  const vendor = await assurancePage(t, { query: '&edit=ve1', tabs });
  assert.equal(vendor.ui.el('vendorFormTitle').textContent, 'Edit Hosting Co');
  assert.deepEqual(shown, ['#vendorsPane']);
  const person = await assurancePage(t, { query: '&edit=pe2', tabs });
  assert.equal(person.ui.el('personFormTitle').textContent, 'Edit Sam Sample');
  assert.equal(person.ui.el('assuranceCard').style.display, '');
  const unknown = await assurancePage(t, { query: '&edit=missing' });
  assert.equal(unknown.ui.el('personFormTitle').textContent, 'Add a person');
});
