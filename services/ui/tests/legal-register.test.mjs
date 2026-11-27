// The legal register page: the register with its filters, export and
// categories, and one item's details, assessments, related records and graph.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { page, settle } from './support/page-fixture.mjs';
import { SIGNED_IN } from './support/account-storage.mjs';

const d3src = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');
const HOSTILE = '<img src=x onerror=alert(1)>';
const BASE = '/api/v1/isms/legal-register';
const copy = (value) => JSON.parse(JSON.stringify(value));
const tick = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

const META = {
  categories: [
    { id: 'c1', name: 'Data protection', description: '', item_count: 1 },
    { id: 'c2', name: 'Labour <b>law</b>', description: '', item_count: 0 },
  ],
  statuses: [
    { code: 'not_assessed', label: 'Not assessed' },
    { code: 'compliant', label: 'Compliant' },
    { code: 'partially_compliant', label: 'Partially compliant' },
    { code: 'non_compliant', label: 'Non-compliant' },
  ],
  outcomes: [
    { code: 'compliant', label: 'Compliant' },
    { code: 'partially_compliant', label: 'Partially compliant' },
    { code: 'non_compliant', label: 'Non-compliant' },
  ],
  default_controls: [{ id: 'k31', ref: 'A.5.31', title: 'Legal requirements' }],
};
const ISMS = {
  users: [
    { id: SIGNED_IN, username: 'me' },
    { id: 'u2', username: 'robin' },
  ],
  controls: [
    { id: 'k31', ref: 'A.5.31', title: 'Legal requirements' },
    { id: 'k1', ref: 'A.5.1' },
  ],
  clauses: [{ id: 'q42', ref: '4.2', title: 'Interested parties' }],
};
const ROWS = [
  {
    id: 'l1',
    title: HOSTILE,
    url: 'https://www.legislation.gov.uk/ukpga/2018/12/contents',
    category: { id: 'c1', name: 'Data protection' },
    jurisdiction: 'United Kingdom',
    authority: 'ICO',
    requirement: 'r'.repeat(300),
    applicability: 'We hold customer data.',
    in_scope: true,
    compliance_status: 'partially_compliant',
    last_assessed_date: '2026-06-01',
    next_assessment_date: '2026-11-01',
    assessment_due: true,
    owner: { id: 'u2', username: 'robin' },
  },
  {
    id: 'l2',
    title: 'Bribery Act 2010',
    url: 'javascript:alert(1)',
    category: null,
    jurisdiction: '',
    authority: '',
    requirement: 'Prevent bribery.',
    applicability: '',
    in_scope: false,
    compliance_status: 'not_assessed',
    last_assessed_date: null,
    next_assessment_date: null,
    assessment_due: false,
    owner: null,
  },
];
const ITEM = {
  ...ROWS[0],
  title: 'Data Protection Act 2018',
  category_id: 'c1',
  owner_user_id: 'u2',
  in_scope: false,
  version: 4,
  controls: [{ id: 'k31', ref: 'A.5.31', title: 'Legal requirements' }],
  clauses: [{ id: 'q42', ref: '4.2' }],
  links: [
    { target_type: 'document', target_id: 'd1', title: HOSTILE, url: '/isms-document.html?id=d1' },
    { target_type: 'risk', target_id: 'r1', title: 'Fine', url: 'https://evil.example/' },
  ],
  assessments: [
    {
      id: 'a1',
      assessed_on: '2026-06-01',
      outcome: 'partially_compliant',
      notes: HOSTILE,
      assessed_by: { id: 'u2', username: 'robin' },
    },
    { id: 'a2', assessed_on: '2026-01-01', outcome: 'compliant', notes: '', assessed_by: null },
  ],
};

// Answers the page's reads from `state`, which a test changes as it goes.
function reads(state) {
  return async (url) => {
    const path = url.pathname;
    if (path === BASE + '/meta') return state.meta;
    if (path === '/api/v1/isms/meta') return state.isms;
    if (path === BASE) {
      state.lists.push(url.searchParams);
      return state.list(url.searchParams);
    }
    if (path === `${BASE}/l1`) {
      if (state.itemError) throw new Error(state.itemError);
      return state.item;
    }
    if (path.startsWith(BASE + '/choices/')) {
      state.choices.push([path.split('/').pop(), url.searchParams.get('q')]);
      return state.choice(path.split('/').pop(), url.searchParams.get('q'));
    }
    if (path === '/api/v1/questions/entities/isms_legal_item/l1') return { threads: [] };
    if (path === '/api/v1/isms/legal-item/l1/changelog') return { items: [] };
    if (path === '/api/v1/stories/related/legal_item/l1') return { items: state.stories || [] };
    if (path === '/api/v1/stories') return { items: [] };
    throw new Error('Unexpected GET ' + url);
  };
}

function state(overrides = {}) {
  return {
    meta: copy(META),
    isms: copy(ISMS),
    item: copy(ITEM),
    lists: [],
    choices: [],
    list: () => ({ items: copy(ROWS), total: 2 }),
    choice: () => ({ items: [{ target_id: 'd2', title: 'Retention policy' }] }),
    ...overrides,
  };
}

async function register(t, { world = state(), ...options } = {}) {
  const ui = await page(t, 'legal-register', { get: reads(world), ...options });
  return Object.assign(ui, { world });
}

function detail(t, { world = state(), query = '', ...options } = {}) {
  return register(t, { world, query: '&id=l1' + query, ...options });
}

const lastList = (ui) => Object.fromEntries(ui.world.lists.at(-1));
const submit = (ui, id) =>
  ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true, bubbles: true }));
const changed = (ui, id, value) => {
  ui.el(id).value = value;
  ui.el(id).dispatchEvent(new ui.window.Event('change', { bubbles: true }));
};

// --- The register -----------------------------------------------------------------

test('the register lists items as text with their scope, status and dates', async (t) => {
  const ui = await register(t);
  assert.equal(ui.el('workspace').hidden, false);
  assert.equal(ui.el('detail').hidden, true);
  assert.equal(ui.el('relatedNotice').hidden, true);
  const rows = [...ui.el('registerRows').rows];
  assert.equal(rows.length, 2);
  // Stored text is shown as text, never parsed.
  assert.equal(ui.el('registerRows').querySelector('img'), null);
  const [first, second] = rows.map((row) => [...row.cells].map((cell) => cell.textContent));
  assert.deepEqual(first, [
    HOSTILE + 'United Kingdom · ICO',
    'Data protection',
    'r'.repeat(239) + '…',
    'We hold customer data.',
    'In scope',
    'Partially compliant',
    '2026-06-01',
    '2026-11-01Due',
    'robin',
  ]);
  assert.deepEqual(second, [
    'Bribery Act 2010',
    '',
    'Prevent bribery.',
    '',
    'Out of scope',
    'Not assessed',
    '',
    '',
    '',
  ]);
  const links = [...rows[0].cells[0].querySelectorAll('a')];
  assert.equal(links[0].getAttribute('href'), '/legal-register.html?id=l1&framework=A');
  // The title links to the item, and a second link opens the legislation.
  assert.equal(links[1].href, 'https://www.legislation.gov.uk/ukpga/2018/12/contents');
  assert.equal(links[1].rel, 'noopener noreferrer');
  assert.equal(links[1].getAttribute('aria-label'), `Open the text of ${HOSTILE}`);
  // A link that is not a web address is not offered.
  assert.equal(rows[1].cells[0].querySelectorAll('a').length, 1);
  assert.match(rows[0].cells[5].querySelector('.badge').className, /text-bg-warning/);
  assert.match(rows[1].cells[4].querySelector('.badge').className, /text-bg-light/);
  assert.equal(ui.el('resultCount').textContent, 'Showing 1-2 of 2 items.');
  assert.equal(ui.el('previous').disabled, true);
  assert.equal(ui.el('next').disabled, true);
  assert.deepEqual(lastList(ui), { framework: 'A', limit: '100', offset: '0' });
  assert.equal(ui.el('exportCsv').getAttribute('href'), BASE + '/export.csv?framework=A');
  // An administrator may add items and manage categories.
  assert.equal(ui.el('newItem').hidden, false);
  assert.equal(ui.el('manageCategories').hidden, false);
  assert.deepEqual(
    [...ui.el('categoryFilter').options].map((option) => [option.value, option.text]),
    [
      ['', 'All categories'],
      ['c1', 'Data protection'],
      ['c2', 'Labour <b>law</b>'],
    ],
  );
  assert.deepEqual(
    [...ui.el('statusFilter').options].map((option) => option.value),
    ['', 'not_assessed', 'compliant', 'partially_compliant', 'non_compliant'],
  );
});

test('filters narrow the register and its export, and pages follow the total', async (t) => {
  const world = state({ list: () => ({ items: copy(ROWS), total: 250 }) });
  const ui = await register(t, { world, me: { can_view_isms: true } });
  // A reader sees the register without the actions that change it.
  assert.equal(ui.el('newItem').hidden, true);
  assert.equal(ui.el('manageCategories').hidden, true);
  assert.equal(ui.el('next').disabled, false);

  ui.el('next').click();
  await settle(() => ui.el('resultCount').textContent === 'Showing 101-102 of 250 items.');
  assert.equal(lastList(ui).offset, '100');
  assert.equal(ui.el('previous').disabled, false);
  ui.el('previous').click();
  await settle(() => ui.el('resultCount').textContent === 'Showing 1-2 of 250 items.');
  assert.equal(lastList(ui).offset, '0');
  assert.equal(ui.el('previous').disabled, true);

  ui.el('next').click();
  await settle(() => ui.el('previous').disabled === false);
  changed(ui, 'categoryFilter', 'c1');
  // A new filter starts again from the first page.
  await settle(() => lastList(ui).category_id === 'c1');
  assert.equal(lastList(ui).offset, '0');
  changed(ui, 'scopeFilter', 'false');
  changed(ui, 'statusFilter', 'non_compliant');
  ui.el('dueFilter').checked = true;
  ui.el('dueFilter').dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  await settle(() => lastList(ui).due === 'true');
  assert.deepEqual(lastList(ui), {
    framework: 'A',
    category_id: 'c1',
    in_scope: 'false',
    status: 'non_compliant',
    due: 'true',
    limit: '100',
    offset: '0',
  });
  assert.equal(
    ui.el('exportCsv').getAttribute('href'),
    BASE + '/export.csv?framework=A&category_id=c1&in_scope=false&status=non_compliant&due=true',
  );

  // Typing searches after a pause, and a submit searches at once.
  const before = world.lists.length;
  ui.el('search').value = 'data';
  ui.el('search').dispatchEvent(new ui.window.Event('input'));
  ui.el('search').value = 'data protection';
  ui.el('search').dispatchEvent(new ui.window.Event('input'));
  await settle(() => lastList(ui).q === 'data protection');
  assert.equal(world.lists.length, before + 1);
  ui.el('search').value = 'bribery';
  ui.el('search').dispatchEvent(new ui.window.Event('input'));
  submit(ui, 'filters');
  await settle(() => lastList(ui).q === 'bribery');
  await tick(300);
  assert.equal(world.lists.length, before + 2);

  world.list = () => ({ items: [], total: 0 });
  submit(ui, 'filters');
  await settle(() => ui.el('resultCount').textContent === 'No items match.');
  assert.equal(ui.el('registerRows').textContent, 'No items match.');
  assert.equal(ui.el('registerRows').querySelector('td').getAttribute('colspan'), '9');
});

test('a slower answer to an earlier filter never replaces a newer one', async (t) => {
  let release;
  const world = state();
  const ui = await register(t, { world });
  world.list = (query) =>
    query.get('q') === 'slow'
      ? new Promise((resolve) => {
          release = () => resolve({ items: [], total: 0 });
        })
      : { items: [copy(ROWS[1])], total: 1 };
  ui.el('search').value = 'slow';
  submit(ui, 'filters');
  await settle(() => release);
  ui.el('search').value = 'fast';
  submit(ui, 'filters');
  await settle(() => ui.el('registerRows').rows.length === 1);
  release();
  await tick();
  assert.equal(ui.el('registerRows').rows[0].cells[0].textContent, 'Bribery Act 2010');
  assert.equal(ui.el('resultCount').textContent, 'Showing 1-1 of 1 items.');
});

test('the register opens on the items related to one record', async (t) => {
  const ui = await register(t, { query: '&target_type=asset&target_id=a1' });
  assert.equal(ui.el('relatedNotice').hidden, false);
  assert.deepEqual(lastList(ui), {
    framework: 'A',
    target_type: 'asset',
    target_id: 'a1',
    limit: '100',
    offset: '0',
  });
  assert.match(ui.el('exportCsv').getAttribute('href'), /target_type=asset&target_id=a1$/);

  // A kind of record the register does not relate to, a kind whose feature is
  // off, and a kind without a record are each ignored.
  for (const [query, me] of [
    ['&target_type=person&target_id=p1', undefined],
    ['&target_type=asset', undefined],
    [
      '&target_type=risk&target_id=r1',
      { is_admin: true, features: { enabled: { risk_cia: false } } },
    ],
  ]) {
    const other = await register(t, { query, ...(me ? { me } : {}) });
    assert.equal(other.el('relatedNotice').hidden, true, query);
    assert.deepEqual(lastList(other), { framework: 'A', limit: '100', offset: '0' }, query);
  }
});

test('a register that cannot be loaded says why', async (t) => {
  const world = state({
    list: () => {
      throw new Error('Register offline');
    },
  });
  const ui = await register(t, { world });
  assert.equal(ui.el('status').textContent, 'Register offline');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  world.list = () => ({ items: copy(ROWS), total: 150 });
  submit(ui, 'filters');
  await settle(() => ui.el('registerRows').rows.length === 2);
  // A failed page turn is reported, and leaves the pager on the page in view.
  world.list = () => {
    throw new Error('Page offline');
  };
  ui.el('next').click();
  await settle(() => ui.el('status').textContent === 'Page offline');
  assert.equal(ui.el('previous').disabled, true);
  world.list = () => ({ items: copy(ROWS), total: 150 });
  ui.el('next').click();
  await settle(() => ui.el('previous').disabled === false);
  assert.equal(lastList(ui).offset, '100');
  world.list = () => {
    throw new Error('Still offline');
  };
  ui.el('previous').click();
  await settle(() => ui.el('status').textContent === 'Still offline');
  assert.equal(ui.el('resultCount').textContent, 'Showing 101-102 of 150 items.');
});

// --- Categories -------------------------------------------------------------------

test('categories are added, renamed and deleted from the register', async (t) => {
  let answer = false;
  const world = state();
  const ui = await register(t, {
    world,
    confirm: () => answer,
    post: async (url, body) => {
      if (body.name === 'Refused') throw new Error('A category with this name already exists');
      world.meta.categories.push({ id: 'c3', name: body.name, description: '', item_count: 0 });
      return {};
    },
    patch: async () => ({}),
    remove: async () => ({}),
  });
  const toggle = ui.el('manageCategories');
  assert.equal(ui.el('categoryPanel').hidden, true);
  toggle.click();
  assert.equal(ui.el('categoryPanel').hidden, false);
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  toggle.click();
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  toggle.click();

  const entries = () => [...ui.el('categoryList').children];
  assert.deepEqual(
    entries().map((entry) => [
      entry.querySelector('input').value,
      entry.querySelector('.small-muted').textContent,
      entry.querySelector('[data-remove]').disabled,
    ]),
    [
      ['Data protection', '1 item', true],
      ['Labour <b>law</b>', '0 items', false],
    ],
  );
  assert.equal(ui.el('categoryList').querySelector('b'), null);

  ui.el('categoryName').value = 'Export control';
  ui.el('categoryDescription').value = 'Sanctions';
  submit(ui, 'categoryForm');
  await settle(() => ui.el('status').textContent === 'Category added.');
  assert.deepEqual(copy(ui.calls.find((call) => call.method === 'POST')), {
    method: 'POST',
    url: BASE + '/categories',
    body: { name: 'Export control', description: 'Sanctions' },
  });
  assert.equal(ui.el('categoryName').value, '');
  assert.equal(entries().length, 3);
  // The register and its filter are read again with the new category.
  assert.ok([...ui.el('categoryFilter').options].some((option) => option.value === 'c3'));
  assert.equal(world.lists.length, 2);

  ui.el('categoryName').value = 'Refused';
  submit(ui, 'categoryForm');
  await settle(() => ui.el('status').dataset.alert === 'danger');
  assert.equal(ui.el('status').textContent, 'A category with this name already exists');
  assert.equal(ui.el('categoryName').value, 'Refused');

  // A click that is not on a button changes nothing.
  entries()[1].querySelector('input').click();
  entries()[1].querySelector('input').value = 'Employment law';
  entries()[1].querySelector('button').click();
  await settle(() => ui.el('status').textContent === 'Category renamed.');
  assert.deepEqual(copy(ui.calls.find((call) => call.method === 'PATCH')), {
    method: 'PATCH',
    url: BASE + '/categories/c2',
    body: { name: 'Employment law' },
  });

  entries()[1].querySelector('[data-remove]').click();
  await tick();
  assert.equal(ui.confirmations.at(-1).title, 'Delete category');
  assert.equal(ui.calls.filter((call) => call.method === 'DELETE').length, 0);
  answer = true;
  entries()[1].querySelector('[data-remove]').click();
  await settle(() => ui.el('status').textContent === 'Category deleted.');
  assert.equal(ui.calls.find((call) => call.method === 'DELETE').url, BASE + '/categories/c2');
});

test('a second change waits for the one in flight', async (t) => {
  let release;
  const ui = await register(t, {
    patch: () =>
      new Promise((resolve) => {
        release = () => resolve({});
      }),
  });
  const rename = () => ui.el('categoryList').querySelector('button');
  rename().click();
  rename().click();
  await settle(() => release);
  assert.equal(ui.calls.filter((call) => call.method === 'PATCH').length, 1);
  release();
  await settle(() => ui.el('status').textContent === 'Category renamed.');
  // Once it has finished, the next change goes ahead.
  rename().click();
  await settle(() => ui.calls.filter((call) => call.method === 'PATCH').length === 2);
  release();
  await settle(() => ui.idle());
});

// --- One item ---------------------------------------------------------------------

test('an item shows its details, assessments, related records and history', async (t) => {
  const ui = await detail(t, {
    scripts: [d3src, 'window.d3 = d3;'],
    vendor: {
      // As the design system does: only a path on this site takes the framework.
      withFramework: (path, framework) =>
        path.startsWith('/')
          ? `${path}${path.includes('?') ? '&' : '?'}framework=${framework}`
          : path,
    },
  });
  assert.equal(ui.el('workspace').hidden, true);
  assert.equal(ui.el('detail').hidden, false);
  assert.equal(ui.el('newItem').hidden, true);
  assert.equal(ui.el('pageTitle').textContent, 'Data Protection Act 2018');
  assert.deepEqual(
    ['title', 'url', 'jurisdiction', 'authority', 'applicability', 'nextAssessment'].map(
      (id) => ui.el(id).value,
    ),
    [
      'Data Protection Act 2018',
      'https://www.legislation.gov.uk/ukpga/2018/12/contents',
      'United Kingdom',
      'ICO',
      'We hold customer data.',
      '2026-11-01',
    ],
  );
  assert.deepEqual(
    ['category', 'inScope', 'owner'].map((id) => ui.el(id).value),
    ['c1', 'false', 'u2'],
  );
  assert.deepEqual(
    [...ui.el('controls').options].map((option) => [option.text, option.selected]),
    [
      ['A.5.31 Legal requirements', true],
      ['A.5.1', false],
    ],
  );
  assert.deepEqual(
    [...ui.el('clauses').selectedOptions].map((option) => option.value),
    ['q42'],
  );
  const control = ui.el('controlLinks').querySelector('a');
  assert.equal(control.getAttribute('href'), '/control.html?id=k31&tab=governance&framework=A');
  assert.equal(control.title, 'Legal requirements');
  const clause = ui.el('clauseLinks').querySelector('a');
  assert.equal(clause.getAttribute('href'), '/clause.html?id=q42&tab=isms&framework=A');
  // A clause without a title is named by its reference.
  assert.equal(clause.title, '4.2');
  assert.equal(ui.el('editor').disabled, false);
  assert.equal(ui.el('draftNotice').hidden, true);
  assert.equal(ui.el('deleteItem').hidden, false);
  assert.equal(ui.el('sourceLink').hidden, false);
  assert.equal(ui.el('sourceLink').href, 'https://www.legislation.gov.uk/ukpga/2018/12/contents');
  for (const tab of ['assessments', 'related', 'graph', 'questions', 'changelog'])
    assert.equal(ui.el(tab + 'Tab').disabled, false, tab);

  for (const id of ['statusSummary', 'assessmentSummary'])
    assert.equal(
      ui.el(id).textContent,
      'Partially compliantDueLast assessed: 2026-06-01 · Next assessment: 2026-11-01',
      id,
    );
  const rows = [...ui.el('assessmentRows').rows].map((row) =>
    [...row.cells].map((cell) => cell.textContent),
  );
  assert.deepEqual(rows, [
    ['2026-06-01', 'Partially compliant', 'robin', HOSTILE, 'Remove'],
    ['2026-01-01', 'Compliant', '', '', 'Remove'],
  ]);
  assert.equal(ui.el('assessmentRows').querySelector('img'), null);
  assert.equal(ui.el('assessmentForm').hidden, false);
  assert.deepEqual(
    [...ui.el('outcome').options].map((option) => option.value),
    ['compliant', 'partially_compliant', 'non_compliant'],
  );
  // The person adding an assessment is offered as its assessor, today.
  assert.equal(ui.el('assessedBy').value, SIGNED_IN);
  assert.equal(ui.el('assessedOn').value, new Date().toISOString().slice(0, 10));

  const groups = [...ui.el('relatedList').children].map((group) => [
    group.querySelector('h3').textContent,
    [...group.querySelectorAll('a')].map((link) => [link.textContent, link.getAttribute('href')]),
  ]);
  assert.deepEqual(groups, [
    ['Documents1', [[HOSTILE, '/isms-document.html?id=d1&framework=A']]],
    ['Assets0', []],
    // An address that is not a path on Keen opens nothing.
    ['Risks1', [['Fine', '#']]],
    ['SWOT items0', []],
  ]);
  assert.equal(ui.el('relatedList').querySelector('img'), null);
  assert.equal(ui.el('relatedList').children[1].textContent, 'Assets0No linked records.');
  assert.equal(ui.el('relatedList').querySelectorAll('[data-unlink-id]').length, 2);
  assert.equal(ui.el('linkForm').hidden, false);
  assert.deepEqual(
    [...ui.el('linkType').options].map((option) => [option.value, option.text]),
    [
      ['document', 'Documents'],
      ['asset', 'Assets'],
      ['risk', 'Risks'],
      ['swot', 'SWOT items'],
    ],
  );
  assert.deepEqual(ui.world.choices, [['document', '']]);
  assert.deepEqual(
    [...ui.el('linkItem').options].map((option) => [option.value, option.text]),
    [
      ['', 'Choose a record'],
      ['d2', 'Retention policy'],
    ],
  );

  // The graph pictures the item with its controls, clauses and related records.
  const labels = [...ui.el('graph').querySelectorAll('text')].map((text) => text.textContent);
  for (const expected of ['Controls (1)', 'Clauses (1)', 'Documents (1)', 'Risks (1)', 'A.5.31'])
    assert.ok(labels.includes(expected), expected);
  assert.ok(!labels.includes('Assets (0)'));
  assert.ok(!labels.includes('SWOT items (0)'));
  const before = ui.el('graph').querySelector('svg');
  ui.el('graphTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.notEqual(ui.el('graph').querySelector('svg'), before);

  assert.equal(ui.el('questionPanel').textContent.includes('No questions yet.'), true);
  assert.match(ui.el('changelog').textContent, /No changes recorded\./);
  assert.ok(ui.calls.some((call) => call.url === '/api/v1/isms/legal-item/l1/changelog?limit=100'));
});

test('a reader sees an item without the actions that change it', async (t) => {
  const world = state();
  Object.assign(world.item, {
    url: null,
    category: null,
    category_id: null,
    owner_user_id: null,
    controls: [],
    clauses: [],
    links: [],
    assessments: [],
    last_assessed_date: null,
    next_assessment_date: null,
    assessment_due: false,
    compliance_status: 'not_assessed',
  });
  world.isms = {};
  const ui = await detail(t, { world, me: { can_view_isms: true } });
  assert.equal(ui.el('editor').disabled, true);
  for (const id of ['deleteItem', 'sample', 'assessmentForm', 'linkForm', 'sourceLink'])
    assert.equal(ui.el(id).hidden, true, id);
  assert.equal(ui.el('controlLinks').textContent, '-');
  assert.equal(ui.el('clauseLinks').textContent, '-');
  assert.equal(
    ui.el('assessmentSummary').textContent,
    'Not assessedLast assessed: never · Next assessment: not set',
  );
  assert.equal(ui.el('assessmentRows').textContent, 'No assessments recorded.');
  assert.equal(ui.el('relatedList').querySelectorAll('button').length, 0);
  // Without D3 the pane says so; nothing is searched for a reader.
  assert.equal(
    ui.el('graph').textContent,
    'This item is not linked to any control, clause or related record yet.',
  );
  assert.deepEqual(world.choices, []);
  assert.equal(ui.el('assessedBy').options.length, 0);
});

test('assessment rows keep their remove action from a reader', async (t) => {
  const ui = await detail(t, { me: { can_view_isms: true } });
  assert.equal(ui.el('assessmentRows').querySelectorAll('button').length, 0);
  assert.equal(ui.el('relatedList').querySelectorAll('button').length, 0);
  assert.equal(ui.el('assessmentRows').rows[0].cells[4].textContent, '');
});

test('related records follow the features that are switched on', async (t) => {
  const ui = await detail(t, {
    me: {
      is_admin: true,
      features: { enabled: { documents: false, assets: false, risk_cia: false, swot: false } },
    },
  });
  assert.equal(ui.el('relatedList').children.length, 0);
  assert.equal(ui.el('linkForm').hidden, true);
  assert.deepEqual(ui.world.choices, []);
});

test('a SWOT item is related like the other records, and left out when SWOT is off', async (t) => {
  const world = state();
  world.item.links.push({
    target_type: 'swot',
    target_id: 'w1',
    title: 'New <i>regulation</i>',
    url: '/swot.html?id=w1',
  });
  world.choice = (type) => ({
    items: type === 'swot' ? [{ target_id: 'w2', title: 'Skilled team' }] : [],
  });
  const ui = await detail(t, {
    world,
    post: async () => ({}),
    scripts: [d3src, 'window.d3 = d3;'],
  });
  const group = ui.el('relatedList').children[3];
  assert.equal(group.querySelector('h3').textContent, 'SWOT items1');
  assert.equal(group.querySelector('a').getAttribute('href'), '/swot.html?id=w1&framework=A');
  assert.equal(group.querySelector('a').textContent, 'New <i>regulation</i>');
  assert.equal(group.querySelector('i'), null);
  const labels = [...ui.el('graph').querySelectorAll('text')].map((text) => text.textContent);
  assert.match(labels.join(' '), /SWOT items \(1\)/, labels.join('|'));

  changed(ui, 'linkType', 'swot');
  await settle(() => ui.el('linkItem').options.length === 2);
  assert.deepEqual(world.choices.at(-1), ['swot', '']);
  ui.el('linkItem').value = 'w2';
  submit(ui, 'linkForm');
  await settle(() => ui.el('status').textContent === 'Record linked.');
  assert.deepEqual(copy(ui.calls.find((call) => call.method === 'POST')).body, {
    target_type: 'swot',
    target_id: 'w2',
    expected_version: 4,
  });

  const off = await detail(t, {
    world: state({ item: world.item }),
    me: { is_admin: true, features: { enabled: { swot: false } } },
  });
  assert.deepEqual(
    [...off.el('relatedList').querySelectorAll('h3')].map((h) => h.textContent),
    ['Documents1', 'Assets0', 'Risks1'],
  );
  assert.ok(![...off.el('linkType').options].some((option) => option.value === 'swot'));
});

test('saving an item sends its fields with the version it was read at', async (t) => {
  const world = state();
  const ui = await detail(t, {
    world,
    patch: async (url, body) => {
      if (body.title === 'Refused') throw new Error('title is required');
      world.item = { ...world.item, title: body.title, version: 5 };
      return world.item;
    },
  });
  ui.el('title').value = 'Data Protection Act 2018 (UK)';
  ui.el('url').value = '  ';
  ui.el('inScope').value = 'true';
  ui.el('owner').value = '';
  ui.el('nextAssessment').value = '';
  ui.el('category').value = '';
  ui.el('controls').options[1].selected = true;
  submit(ui, 'itemForm');
  await settle(() => ui.el('status').textContent === 'Legal register item saved.');
  const patch = ui.calls.find((call) => call.method === 'PATCH');
  assert.equal(patch.url, BASE + '/l1?framework=A');
  assert.deepEqual(copy(patch.body), {
    title: 'Data Protection Act 2018 (UK)',
    url: null,
    jurisdiction: 'United Kingdom',
    authority: 'ICO',
    requirement: 'r'.repeat(300),
    applicability: 'We hold customer data.',
    in_scope: true,
    owner_user_id: null,
    next_assessment_date: null,
    controls: ['k31', 'k1'],
    clauses: ['q42'],
    category_id: null,
    expected_version: 4,
  });
  assert.equal(ui.el('pageTitle').textContent, 'Data Protection Act 2018 (UK)');

  // A new category name takes the place of the chosen category.
  ui.el('category').value = 'c1';
  ui.el('newCategory').value = ' Privacy ';
  ui.el('owner').value = 'u2';
  ui.el('nextAssessment').value = '2027-01-01';
  ui.el('url').value = 'https://example.test/act';
  submit(ui, 'itemForm');
  await settle(() => ui.calls.filter((call) => call.method === 'PATCH').length === 2);
  const second = copy(ui.calls.filter((call) => call.method === 'PATCH')[1].body);
  assert.equal(second.category_name, 'Privacy');
  assert.equal('category_id' in second, false);
  assert.deepEqual(
    [second.owner_user_id, second.next_assessment_date, second.url, second.expected_version],
    ['u2', '2027-01-01', 'https://example.test/act', 5],
  );
  await settle(() => ui.el('newCategory').value === '');

  ui.el('title').value = 'Refused';
  submit(ui, 'itemForm');
  await settle(() => ui.el('status').dataset.alert === 'danger');
  assert.equal(ui.el('status').textContent, 'title is required');
});

test('an assessment is added with its outcome, assessor and next date', async (t) => {
  let answer = false;
  const world = state();
  world.isms.users = [{ id: 'u2', username: 'robin' }];
  const ui = await detail(t, {
    world,
    confirm: () => answer,
    post: async () => ({}),
    remove: async () => ({}),
  });
  // The signed-in person is not among the accounts offered, so none is chosen.
  assert.equal(ui.el('assessedBy').value, 'u2');
  ui.el('assessedBy').value = '';
  ui.el('assessedOn').value = '2026-11-20';
  ui.el('outcome').value = 'non_compliant';
  ui.el('assessmentNotes').value = 'No retention schedule.';
  submit(ui, 'assessmentForm');
  await settle(() => ui.el('status').textContent === 'Assessment added.');
  const [first] = ui.calls.filter((call) => call.method === 'POST');
  assert.equal(first.url, BASE + '/l1/assessments?framework=A');
  // With no next date entered, none is sent and the item keeps its own.
  assert.deepEqual(copy(first.body), {
    assessed_on: '2026-11-20',
    outcome: 'non_compliant',
    notes: 'No retention schedule.',
    assessed_by_user_id: null,
    expected_version: 4,
  });
  // The form is ready for the next assessment.
  assert.equal(ui.el('assessmentNotes').value, '');

  ui.el('assessmentNext').value = '2027-05-01';
  ui.el('assessedBy').value = 'u2';
  submit(ui, 'assessmentForm');
  await settle(() => ui.calls.filter((call) => call.method === 'POST').length === 2);
  const second = copy(ui.calls.filter((call) => call.method === 'POST')[1].body);
  assert.deepEqual([second.next_assessment_date, second.assessed_by_user_id], ['2027-05-01', 'u2']);
  await settle(() => ui.el('assessmentNext').value === '');

  // Removing an assessment asks first; a click elsewhere in the table does nothing.
  ui.el('assessmentRows').rows[0].cells[0].click();
  ui.el('assessmentRows').querySelector('[data-remove-assessment]').click();
  await tick();
  assert.equal(ui.confirmations.at(-1).title, 'Remove assessment');
  assert.equal(ui.calls.filter((call) => call.method === 'DELETE').length, 0);
  answer = true;
  ui.el('assessmentRows').querySelector('[data-remove-assessment]').click();
  await settle(() => ui.el('status').textContent === 'Assessment removed.');
  assert.equal(
    ui.calls.find((call) => call.method === 'DELETE').url,
    BASE + '/l1/assessments/a1?expected_version=4&framework=A',
  );
});

test('records are searched, linked and unlinked on the Related items tab', async (t) => {
  let release;
  const world = state();
  const ui = await detail(t, { world, post: async () => ({}), remove: async () => ({}) });
  changed(ui, 'linkType', 'asset');
  await settle(() => world.choices.length === 2);
  assert.deepEqual(world.choices[1], ['asset', '']);

  // A slower answer to an earlier search never replaces a newer one.
  world.choice = (type, q) =>
    q === 'slow'
      ? new Promise((resolve) => {
          release = () => resolve({ items: [{ target_id: 'old', title: 'Old' }] });
        })
      : { items: [] };
  ui.el('linkSearch').value = 'slow';
  ui.el('findLinks').click();
  await settle(() => release);
  ui.el('linkSearch').value = 'none';
  ui.el('findLinks').click();
  await settle(() => world.choices.length === 4);
  release();
  await tick();
  // With nothing found, there is nothing to choose.
  assert.equal(ui.el('linkItem').options.length, 1);
  assert.equal(ui.el('linkItem').disabled, true);

  world.choice = () => ({ items: [{ target_id: 'a9', title: 'Payroll' }] });
  ui.el('linkSearch').value = 'pay';
  ui.el('findLinks').click();
  await settle(() => ui.el('linkItem').options.length === 2);
  assert.equal(ui.el('linkItem').disabled, false);
  assert.deepEqual(world.choices.at(-1), ['asset', 'pay']);
  ui.el('linkItem').value = 'a9';
  submit(ui, 'linkForm');
  await settle(() => ui.el('status').textContent === 'Record linked.');
  assert.deepEqual(copy(ui.calls.find((call) => call.method === 'POST')), {
    method: 'POST',
    url: BASE + '/l1/links',
    body: { target_type: 'asset', target_id: 'a9', expected_version: 4 },
  });

  ui.el('relatedList').querySelector('h3').click();
  ui.el('relatedList').querySelector('[data-unlink-id]').click();
  await settle(() => ui.el('status').textContent === 'Record unlinked.');
  assert.equal(
    ui.calls.find((call) => call.method === 'DELETE').url,
    BASE + '/l1/links/document/d1?expected_version=4',
  );

  // A search that fails is reported.
  world.choice = () => {
    throw new Error('Search offline');
  };
  ui.el('findLinks').click();
  await settle(() => ui.el('status').textContent === 'Search offline');
  world.choice = () => {
    throw new Error('Types offline');
  };
  changed(ui, 'linkType', 'risk');
  await settle(() => ui.el('status').textContent === 'Types offline');
});

test('deleting an item asks first and returns to the register', async (t) => {
  const navigations = [];
  const location = {
    origin: 'https://keen.example',
    href: 'https://keen.example/legal-register.html?id=l1&framework=A',
    search: '?id=l1&framework=A',
    assign: (url) => navigations.push(url),
  };
  let answer = false;
  const ui = await detail(t, {
    globals: { location },
    confirm: () => answer,
    remove: async () => ({}),
  });
  ui.el('deleteItem').click();
  await tick();
  assert.equal(ui.confirmations.at(-1).title, 'Delete legal register item');
  assert.equal(ui.calls.filter((call) => call.method === 'DELETE').length, 0);
  answer = true;
  ui.el('deleteItem').click();
  await settle(() => navigations.length === 1);
  assert.equal(
    ui.calls.find((call) => call.method === 'DELETE').url,
    BASE + '/l1?expected_version=4&framework=A',
  );
  assert.deepEqual(navigations, ['/legal-register.html?framework=A']);
  // Leaving the page shows no message of its own.
  assert.equal(ui.el('status').textContent, '');
});

test('an item is sampled into an audit with a link back to it', async (t) => {
  const world = state();
  const read = reads(world);
  const ui = await detail(t, {
    world,
    me: { is_admin: true, features: { enabled: { sampling: true } } },
    get: (url) =>
      url.pathname === '/api/v1/audits'
        ? { items: [{ id: 'au1', title: 'Annual audit', status: 'open' }] }
        : read(url),
    post: async () => ({}),
  });
  assert.equal(ui.el('sample').hidden, false);
  ui.el('sample').click();
  await settle(() => ui.el('entityAuditSampleSelect')?.value === 'au1');
  ui.el('entityAuditSampleSubmit').click();
  await settle(() => ui.calls.some((call) => call.method === 'POST'));
  const post = ui.calls.find((call) => call.method === 'POST');
  assert.equal(post.url, '/api/v1/audits/au1/evidence');
  assert.deepEqual(copy(post.body), {
    entity_type: 'isms_legal_item',
    entity_id: 'l1',
    evidence_url: '/legal-register.html?id=l1&framework=A',
    title: 'Data Protection Act 2018',
    notes: '',
  });
});

test('a requested tab opens, and a refused field returns to Details', async (t) => {
  const shown = [];
  const setup = (window) => {
    window.bootstrap = {
      Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
    };
  };
  const ui = await detail(t, { query: '&tab=assessments', setup });
  assert.deepEqual(shown, ['assessmentsTab']);
  ui.el('title').dispatchEvent(new ui.window.Event('invalid'));
  assert.deepEqual(shown, ['assessmentsTab', 'detailsTab']);
  // A tab the page does not have opens Details.
  shown.length = 0;
  await detail(t, { query: '&tab=nowhere', setup });
  assert.deepEqual(shown, ['detailsTab']);
});

test('an item that cannot be loaded is reported', async (t) => {
  const ui = await detail(t, { world: state({ itemError: 'Legal register item not found' }) });
  assert.equal(ui.el('status').textContent, 'Legal register item not found');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  // Without the bootstrap bundle, a refused field leaves the tabs as they are.
  ui.el('title').dispatchEvent(new ui.window.Event('invalid'));
});

test('a new item starts with the control for legal requirements and opens once saved', async (t) => {
  const navigations = [];
  const shown = [];
  const location = {
    origin: 'https://keen.example',
    href: 'https://keen.example/legal-register.html?new=1&framework=A',
    search: '?new=1&tab=assessments&framework=A',
    assign: (url) => navigations.push(url),
  };
  const ui = await register(t, {
    globals: { location },
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
    post: async (url, body) => {
      if (!body.title) throw new Error('title is required');
      return { id: 'l9' };
    },
  });
  assert.equal(ui.el('detail').hidden, false);
  assert.equal(ui.el('pageTitle').textContent, 'New legal register item');
  assert.equal(ui.el('draftNotice').hidden, false);
  // A draft has only its Details tab, whatever tab the address names.
  assert.deepEqual(shown, ['detailsTab']);
  for (const tab of ['assessments', 'related', 'graph', 'questions', 'changelog'])
    assert.equal(ui.el(tab + 'Tab').disabled, true, tab);
  for (const id of ['deleteItem', 'sample', 'sourceLink']) assert.equal(ui.el(id).hidden, true, id);
  assert.equal(ui.el('inScope').value, 'true');
  assert.deepEqual(
    [...ui.el('controls').selectedOptions].map((option) => option.text),
    ['A.5.31 Legal requirements'],
  );
  assert.equal(ui.el('controlLinks').textContent, 'A.5.31');
  assert.equal(ui.el('clauseLinks').textContent, '-');
  assert.ok(!ui.calls.some((call) => call.url.startsWith(BASE + '/l1')));

  submit(ui, 'itemForm');
  await settle(() => ui.el('status').textContent === 'title is required');
  assert.deepEqual(navigations, []);

  ui.el('title').value = 'Modern Slavery Act 2015';
  ui.el('category').value = 'c2';
  submit(ui, 'itemForm');
  await settle(() => navigations.length === 1);
  const post = ui.calls.filter((call) => call.method === 'POST').at(-1);
  assert.equal(post.url, BASE + '?framework=A');
  assert.deepEqual(copy(post.body), {
    title: 'Modern Slavery Act 2015',
    url: null,
    jurisdiction: '',
    authority: '',
    requirement: '',
    applicability: '',
    in_scope: true,
    owner_user_id: null,
    next_assessment_date: null,
    controls: ['k31'],
    clauses: [],
    category_id: 'c2',
  });
  assert.deepEqual(navigations, ['/legal-register.html?id=l9&framework=A']);
});

// --- The Legal register tab of a related record ------------------------------------

const HOST =
  '<main><ul class="nav-tabs"></ul><div class="tab-content"></div><div id="status"></div></main>';

async function relatedTab(t, { me = { can_view_isms: true }, id = 'd1', get } = {}) {
  const calls = [];
  const ui = await loadUi('pages/legal-related.js', {
    html: HOST,
    vendor: {
      apiGet: async (url) => {
        calls.push(url);
        return get(url);
      },
      withFramework: (path, framework) => `${path}&framework=${framework}`,
    },
  });
  t.after(() => ui.close());
  ui.api.mountLegalRelated({ me, type: 'document', id });
  return Object.assign(ui, { calls, pane: () => ui.document.getElementById('legalRelatedPane') });
}

test('a related record lists its legal register items when its tab is opened', async (t) => {
  let answer = () => {
    throw new Error('Register offline');
  };
  const ui = await relatedTab(t, { get: (url) => answer(url) });
  const tab = ui.document.getElementById('legalRelatedTab');
  assert.equal(tab.textContent, 'Legal register');
  assert.equal(tab.dataset.bsTarget, '#legalRelatedPane');
  // Nothing is requested until the tab is opened.
  assert.deepEqual(ui.calls, []);

  tab.click();
  await settle(() => /could not be loaded/.test(ui.pane().textContent));
  assert.equal(
    ui.pane().textContent,
    'Related legal register items could not be loaded: Register offline',
  );
  // A later click tries again.
  answer = () => ({
    items: [
      { id: 'l1', title: HOSTILE, in_scope: true },
      { id: 'l2', title: 'Bribery Act 2010', in_scope: false },
    ],
    total: 2,
  });
  tab.click();
  await settle(() => ui.pane().querySelector('h2'));
  assert.deepEqual(ui.calls, [
    '/api/v1/isms/legal-register?target_type=document&target_id=d1&limit=100',
    '/api/v1/isms/legal-register?target_type=document&target_id=d1&limit=100',
  ]);
  assert.equal(ui.pane().querySelector('img'), null);
  assert.deepEqual(
    [...ui.pane().querySelectorAll('p')].map((entry) => entry.textContent),
    [HOSTILE + ' In scope', 'Bribery Act 2010 Out of scope'],
  );
  const links = [...ui.pane().querySelectorAll('a')].map((link) => [
    link.textContent,
    link.getAttribute('href'),
  ]);
  assert.deepEqual(links, [
    [HOSTILE, '/legal-register.html?id=l1&tab=related&framework=F1'],
    ['Bribery Act 2010', '/legal-register.html?id=l2&tab=related&framework=F1'],
    [
      'Open in the legal register (2)',
      '/legal-register.html?target_type=document&target_id=d1&framework=F1',
    ],
  ]);
  // Once loaded, opening the tab again asks nothing more.
  tab.click();
  await tick();
  assert.equal(ui.calls.length, 2);
});

test('a record related to no legal register item says so', async (t) => {
  const ui = await relatedTab(t, { get: () => ({ items: [], total: 0 }) });
  ui.document.getElementById('legalRelatedTab').click();
  await settle(() => ui.pane().querySelector('h2'));
  assert.match(ui.pane().textContent, /No legal register items are related\./);
});

test('the Legal register tab is left out without a record, access or the feature', async (t) => {
  for (const options of [
    { id: '' },
    { me: { can_view_isms: false } },
    { me: null },
    { me: { can_view_isms: true, features: { enabled: { legal_register: false } } } },
  ]) {
    const ui = await relatedTab(t, options);
    assert.equal(ui.document.getElementById('legalRelatedTab'), null);
    assert.equal(ui.pane(), null);
  }
});

// --- Stories ----------------------------------------------------------------------

test('a saved item has a Stories tab listing the stories that include it', async (t) => {
  const world = state({
    stories: [
      {
        id: 's1',
        title: 'Regulator <b>letter</b>',
        status: 'open',
        item_count: 2,
        updated_at: '2026-05-01',
        created_by: 'alice',
        can_edit: false,
        version: 1,
      },
    ],
  });
  const ui = await detail(t, { world });
  const tab = ui.document.getElementById('storiesRelatedTab');
  assert.equal(tab.textContent, 'Stories');
  const pane = ui.document.getElementById('storiesRelatedPane');
  assert.match(pane.textContent, /Stories that include this legal register item/);
  tab.dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => pane.querySelector('.list-group a'));
  assert.ok(ui.calls.some((c) => c.url === '/api/v1/stories/related/legal_item/l1'));
  assert.equal(tab.textContent, 'Stories (1)');
  const link = pane.querySelector('.list-group a');
  assert.equal(link.textContent, 'Regulator <b>letter</b>');
  assert.equal(link.getAttribute('href'), '/story.html?id=s1&framework=A');
  assert.equal(pane.querySelector('.list-group b'), null);
});

test('a new item and an account without story access have no Stories tab', async (t) => {
  const draft = await register(t, { query: '&new=1' });
  assert.equal(draft.document.getElementById('storiesRelatedTab'), null);
  const reader = await detail(t, { me: { can_view_isms: true } });
  assert.equal(reader.document.getElementById('storiesRelatedTab'), null);
});
