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

async function register(t, options = {}) {
  const navigated = [];
  const ui = await page(t, 'assurance', {
    me: options.me || { is_admin: true },
    query: options.query || '',
    globals: { location: { search: options.query || '', replace: (url) => navigated.push(url) } },
    get: async (u) => {
      if (options.fail) throw new Error('Offline');
      if (u.pathname === '/api/v1/isms/people') return { items: options.people || people() };
      if (u.pathname === '/api/v1/isms/vendors') return { items: vendors() };
      throw new Error('Unexpected GET ' + u);
    },
  });
  return { ui, navigated };
}
async function detail(t, kind = 'person', options = {}) {
  const record = structuredClone(kind === 'person' ? people()[0] : vendors()[0]);
  record.assets = meta.assets;
  const sent = [],
    navigated = [];
  const id = kind === 'person' ? 'pe1' : 've1',
    collection = kind === 'person' ? 'people' : 'vendors';
  const search = options.create ? '' : `?id=${id}${options.tab ? '&tab=' + options.tab : ''}`;
  const ui = await page(t, kind, {
    me: options.me || { is_admin: true },
    query: search ? '&' + search.slice(1) : '',
    confirm: options.confirm ?? true,
    globals: { location: { search, assign: (url) => navigated.push(url) } },
    get: async (u) => {
      if (u.pathname === `/api/v1/isms/${collection}/${id}`) {
        if (options.failLoad) throw new Error('Unavailable');
        return structuredClone(record);
      }
      if (u.pathname === '/api/v1/isms/assurance/meta') return structuredClone(meta);
      if (u.pathname.endsWith('/workstation-posture'))
        return { devices: [], total: 0, next_offset: null, freshness_hours: 24 };
      if (u.pathname.endsWith('/quizzes'))
        return {
          eligible: true,
          assignments: [],
          assignments_total: 0,
          items: [],
          total: 0,
          next_offset: null,
          assignments_next_offset: null,
        };
      if (u.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (u.pathname.startsWith('/api/v1/stories')) return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, body) => {
      sent.push(['POST', u.pathname, plain(body)]);
      if (options.failSave) throw new Error('Rejected');
      if (u.pathname.endsWith('/assurances'))
        record.assurances.push({ ...plain(body), id: 'new-assurance' });
      return { ...record, id };
    },
    patch: async (u, body) => {
      sent.push(['PATCH', u.pathname, plain(body)]);
      if (options.failSave) throw new Error('Rejected');
      return record;
    },
    remove: async (u) => {
      sent.push(['DELETE', u.pathname]);
      if (options.failDelete) throw new Error('In use');
      record.assurances = record.assurances?.filter((a) => !u.pathname.endsWith(a.id));
      return {};
    },
  });
  await settle(ui.idle);
  return { ui, record, sent, navigated };
}
const submit = (ui, id) =>
  ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true, bubbles: true }));

test('the register contains safe links and searchable rows without detail editors', async (t) => {
  const { ui } = await register(t);
  assert.equal(ui.document.querySelector('form'), null);
  assert.equal(ui.el('peopleList').querySelector('img'), null);
  assert.match(ui.el('peopleList').textContent, /Alex Example/);
  assert.match(ui.el('peopleList').querySelector('a').href, /person.html\?id=pe1/);
  assert.match(ui.el('vendorsList').querySelector('a').href, /vendor.html\?id=ve1/);
  ui.el('personSearch').value = 'FINANCE';
  ui.el('personSearch').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('peopleList').textContent, /Sam/);
  assert.doesNotMatch(ui.el('peopleList').textContent, /Alex/);
  assert.equal(ui.el('newPerson').hidden, false);
});

test('register feature gates, vendor tab, legacy links and read-only actions', async (t) => {
  const { ui } = await register(t, {
    me: { can_view_isms: true, features: { enabled: { people: false } } },
    query: '&tab=vendors',
  });
  assert.equal(ui.el('peopleTab').parentElement.hidden, true);
  assert.ok(ui.el('vendorsPane').classList.contains('active'));
  assert.equal(ui.el('newVendor').hidden, true);
  assert.equal(
    ui.calls.some((c) => c.url === '/api/v1/isms/people'),
    false,
  );
  const legacy = await register(t, { query: '&edit=pe1' });
  assert.match(legacy.navigated[0], /person.html\?id=pe1/);
});

test('register paging is bounded and load failures offer retry', async (t) => {
  const { ui } = await register(t, {
    people: Array.from({ length: 52 }, (_, i) => ({
      ...people()[0],
      id: 'p' + i,
      name: 'Person ' + i,
    })),
  });
  assert.equal(ui.el('peopleList').children.length, 50);
  ui.document.querySelector('[data-page-next="people"]').click();
  assert.equal(ui.el('peopleList').children.length, 2);
  const failed = await register(t, { fail: true });
  assert.equal(failed.ui.el('retryRegister').hidden, false);
});

test('person details separate posture, assurances and related records into tabs', async (t) => {
  const { ui } = await detail(t);
  assert.ok(ui.el('pane-posture').classList.contains('active'));
  assert.equal(ui.el('recordEditor').hidden, true);
  assert.equal(ui.document.querySelector('#recordFacts img'), null);
  assert.equal(
    ui.calls.some((c) => c.url.includes('/quizzes')),
    false,
  );
  ui.el('tab-assurance').click();
  assert.ok(ui.el('pane-assurance').classList.contains('active'));
  assert.equal(ui.el('assuranceEditor').hidden, true);
  assert.match(ui.el('assuranceRows').textContent, /Police check/);
  assert.equal(ui.el('assuranceRows').querySelector('a[href^="javascript:"]'), null);
  assert.ok(ui.el('assuranceRows').querySelector('a[href="https://lms.example.test/cert/1"]'));
  ui.el('tab-related').click();
  await settle(ui.idle);
  assert.ok(ui.calls.some((c) => c.url.includes('/people/pe1/quizzes')));
  assert.equal(ui.el('recordQuizzes').hidden, false);
});

test('read-only person viewers cannot edit or fetch named quizzes and unavailable features stay hidden', async (t) => {
  const { ui } = await detail(t, 'person', {
    me: {
      can_view_isms: true,
      can_view_quizzes: true,
      features: { enabled: { evidence: false, stories: false, swot: false } },
    },
  });
  assert.equal(ui.el('editRecord').hidden, true);
  assert.equal(ui.el('deleteRecord').hidden, true);
  assert.equal(ui.el('tab-posture').parentElement.hidden, true);
  assert.equal(ui.el('addAssurance').hidden, true);
  ui.el('tab-related').click();
  await settle(ui.idle);
  assert.equal(ui.el('recordQuizzes').hidden, true);
  assert.equal(
    ui.calls.some(
      (c) => c.url.includes('/quizzes') || c.url.includes('/stories') || c.url.includes('/swot'),
    ),
    false,
  );
  assert.equal(ui.el('noRelated').hidden, false);
});

for (const kind of ['person', 'vendor']) {
  test(`${kind} editing saves details and returns to the matching register tab`, async (t) => {
    const { ui, sent, navigated } = await detail(t, kind);
    ui.el('editRecord').click();
    await settle(() => !ui.el('recordEditor').hidden);
    ui.el(kind + 'Name').value = 'Updated';
    submit(ui, kind + 'Form');
    await settle(() => navigated.length === 1);
    assert.equal(sent[0][0], 'PATCH');
    assert.equal(sent[0][2].name, 'Updated');
    assert.match(
      navigated[0],
      new RegExp('assurance.html\\?tab=' + (kind === 'person' ? 'people' : 'vendors')),
    );
  });
  test(`${kind} creation starts in the editor and failed saves preserve input`, async (t) => {
    const { ui, sent, navigated } = await detail(t, kind, { create: true, failSave: true });
    assert.equal(ui.el('recordTabs').hidden, true);
    assert.equal(ui.el('recordEditor').hidden, false);
    ui.el(kind + 'Name').value = 'Keep this';
    submit(ui, kind + 'Form');
    await settle(ui.idle);
    assert.equal(sent[0][0], 'POST');
    assert.equal(navigated.length, 0);
    assert.equal(ui.el(kind + 'Name').value, 'Keep this');
    assert.match(ui.el('status').textContent, /Could not save/);
  });
  test(`${kind} deletion is confirmed and returns to the register`, async (t) => {
    const { ui, sent, navigated } = await detail(t, kind);
    ui.el('deleteRecord').click();
    await settle(() => navigated.length === 1);
    assert.equal(sent[0][0], 'DELETE');
  });
}

test('person account and asset selection preserve existing links when editing', async (t) => {
  const { ui, sent } = await detail(t);
  ui.el('editRecord').click();
  await settle(() => !ui.el('recordEditor').hidden);
  assert.equal(ui.el('personUser').value, 'u1');
  assert.equal(ui.el('personOrg').value, 'o1');
  assert.equal(ui.el('personAssets').selectedOptions.length, 2);
  submit(ui, 'personForm');
  await settle(ui.idle);
  assert.deepEqual(sent[0][2].asset_ids, ['as1', 'as2']);
});

test('person assurance creation refreshes the assurance tab and preserves date semantics', async (t) => {
  const { ui, sent } = await detail(t);
  ui.el('tab-assurance').click();
  ui.el('addAssurance').click();
  ui.el('assuranceCategory').value = 'Training';
  ui.el('assuranceName').value = 'Induction';
  submit(ui, 'assuranceForm');
  await settle(() => ui.el('assuranceRows').textContent.includes('Induction'));
  assert.equal(sent[0][2].completed_at, null);
  assert.equal(sent[0][2].expires_at, null);
  assert.equal(ui.el('assuranceEditor').hidden, true);
  ui.el('assuranceRows').querySelector('[data-edit-assurance="as-1"]').click();
  assert.equal(ui.el('assuranceName').value, 'Police check');
  ui.el('newAssurance').click();
  assert.equal(ui.el('assuranceEditor').hidden, true);
});

test('detail failures do not reveal secondary panels, and a disabled page makes no record requests', async (t) => {
  const failed = await detail(t, 'person', { failLoad: true });
  assert.equal(failed.ui.el('retryRecord').hidden, false);
  assert.equal(failed.ui.el('recordTabs').hidden, true);
  await assert.rejects(
    detail(t, 'vendor', { me: { is_admin: true, features: { enabled: { vendors: false } } } }),
    /Feature disabled/,
  );
});
