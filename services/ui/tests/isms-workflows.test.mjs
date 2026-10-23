// ISMS register workflows through the real page: documents with tags, folders and
// files; organisation chart; assets with licences and hosting accounts; the access
// control matrix; effectiveness metrics; meetings; deletion and refused saves.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import { page, settle } from './support/page-fixture.mjs';

const accounts = [
  { id: 'aws1', name: 'Production', account_id: '111122223333', notes: 'Main', entry_count: 1 },
  { id: 'aws2', name: 'Staging', account_id: '444455556666', entry_count: 1 },
  { id: 'aws3', name: 'Sandbox', entry_count: 1 },
];
const meta = {
  framework: 'A',
  users: [
    { id: 'u1', username: 'alice' },
    { id: 'u2', username: 'bob' },
  ],
  org_nodes: [
    { id: 'n1', name: 'Security team', node_type: 'role' },
    { id: 'n2', name: 'Platform operations', node_type: 'team', parent_id: 'n1' },
  ],
  documents: [{ id: 'd1', title: 'Security policy' }],
  document_types: ['policy', 'process'],
  effectiveness_metric_sources: [{ id: 'ci', source: 'ci', label: 'Build server' }],
  assets: [
    { id: 'a1', asset: 'Database', name: 'Database' },
    { id: 'a2', asset: 'Payroll', name: 'Payroll' },
  ],
  asset_categories: [
    {
      id: 'cat1',
      name: 'Data',
      subcategories: [
        { id: 'sub1', name: 'Databases' },
        { id: 'sub2', name: 'Spreadsheets' },
      ],
    },
    { id: 'cat2', name: 'Hardware', subcategories: [{ id: 'sub3', name: 'Laptops' }] },
  ],
  licenses: [{ id: 'l1', name: 'Enterprise', description: 'Per user', asset_count: 1 }],
  aws_accounts: accounts,
  business_processes: [],
  controls: [{ id: 'c1', ref: 'A.5.1', title: 'Policies' }],
  clauses: [{ id: 'k1', ref: '5.1', title: 'Leadership' }],
  access_statuses: ['Pending Approval', 'Approved'],
};
const records = () => ({
  objectives: [
    {
      id: 'o1',
      goal: 'Train operators',
      requirement: 'Awareness',
      status: 'defined',
      resource_users: [],
      controls: [],
      clauses: [],
    },
  ],
  documents: [
    {
      id: 'd1',
      title: 'Security policy',
      tags: ['Security'],
      document_type: 'policy',
      content_html: '<p>Policy</p>',
      content_version: 3,
      controls: [{ id: 'c1', ref: 'A.5.1' }],
      clauses: [],
    },
  ],
  org_nodes: [
    {
      id: 'n1',
      name: 'Security team',
      node_type: 'role',
      people: [{ user: { id: 'u1', username: 'alice' } }],
      controls: [],
      clauses: [],
    },
    {
      id: 'n2',
      name: 'Platform operations',
      node_type: 'team',
      parent_id: 'n1',
      people: [],
      controls: [],
      clauses: [],
    },
  ],
  assets: [
    {
      id: 'a1',
      asset: 'Database',
      name: 'Database',
      category_id: 'cat1',
      subcategory_id: 'sub2',
      license_id: 'l1',
      license_name: 'Enterprise',
      owner_org_node_id: 'n1',
      description: 'Primary database',
      controls: [],
      clauses: [],
    },
  ],
  access_control_matrix: [
    {
      id: 'ac1',
      task_action: 'Deploy',
      service_asset_id: 'a1',
      service: { id: 'a1', asset: 'Database' },
      aws_accounts: accounts,
      aws_account_ids: ['aws1', 'aws2', 'aws3'],
      status: 'Approved',
      approved_by: { username: 'alice' },
      approved_by_user_id: 'u1',
      roles: [{ id: 'n1', name: 'Security team' }],
      role_org_node_ids: ['n1'],
      notes: 'Quarterly review',
    },
  ],
  effectiveness_measures: [
    {
      id: 'e1',
      summary: 'Agent coverage',
      source_type: 'ci',
      metric_key: 'agent.coverage',
      target_value: 95,
      target_unit: '%',
      threshold_operator: 'gte',
      controls: [],
      metric_entries: [{ id: 'me1', metric_value: 90, metric_unit: '%', value_display: '90%' }],
    },
    {
      id: 'e2',
      summary: 'Restore tests',
      source_type: 'retired-source',
      target_unit: 'result',
      controls: [],
      metric_entries: [],
    },
  ],
  meetings: [
    {
      id: 'm1',
      title: 'Annual review',
      date: '2026-01-01',
      start_time: '09:00:00',
      end_time: '10:30:00',
      attendee_user_ids: ['u1'],
      apology_user_ids: ['u2'],
      attendee_person_ids: [],
      apology_person_ids: [],
      links: [
        { link_type: 'isms_document', document_id: 'd1' },
        { link_type: 'external_url', title: 'Minutes', url: 'https://docs.example/minutes' },
      ],
      agenda_minutes_notes: '<p>Agenda</p>',
      controls: [],
      clauses: [],
    },
  ],
});
const plain = (value) =>
  value instanceof FormData ? value : JSON.parse(JSON.stringify(value ?? null));

async function isms(t, tab, options = {}) {
  const sent = [];
  const prompts = [];
  const folders = options.folders || [];
  const people = options.people || [];
  const record = (method) => async (u, body) => {
    sent.push({ method, url: u.pathname + u.search, body: plain(body) });
    const answer = options.respond?.(method, u, body);
    if (answer instanceof Error) throw answer;
    return answer ?? { id: 'new1', ...(body instanceof FormData ? {} : body) };
  };
  const ui = await page(t, 'isms', {
    query: '&tab=' + tab,
    me: options.me || { is_admin: true },
    globals: {
      confirm: (message) => {
        prompts.push(message);
        return options.confirm ?? true;
      },
    },
    setup(window) {
      window.d3 = d3;
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/meta') return structuredClone(meta);
      if (u.pathname === '/api/v1/isms/summary')
        return { framework: 'A', counts: {}, ...(options.records || ((r) => r))(records()) };
      if (u.pathname === '/api/v1/isms/document-folders') return structuredClone(folders);
      if (u.pathname === '/api/v1/isms/people') return { items: structuredClone(people) };
      throw new Error('Unexpected GET ' + u);
    },
    post: record('POST'),
    patch: record('PATCH'),
    remove: record('DELETE'),
  });
  const writes = () => sent.filter((call) => call.method !== 'GET');
  const submit = (id) =>
    ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  const click = (selector) => ui.document.querySelector(selector).click();
  const select = (id, ...values) => {
    for (const option of ui.el(id).options) option.selected = values.includes(option.value);
    ui.el(id).dispatchEvent(new ui.window.Event('change'));
  };
  const statusIs = (text) => settle(() => ui.el('status').textContent === text);
  // Add opens the form once its section has loaded.
  const add = async (kind, card) => {
    click(`[data-isms-add="${kind}"]`);
    await settle(() => ui.el(card).style.display === '');
  };
  return { ...ui, sent, writes, prompts, folders, submit, click, select, statusIs, add };
}

const chips = (ui) =>
  [...ui.el('docSelectedTags').querySelectorAll('[data-remove-doc-tag]')].map(
    (b) => b.dataset.removeDocTag,
  );

// --- Documents ---------------------------------------------------------------------

test('a written document is saved with its tags, a new folder and its links', async (t) => {
  const ui = await isms(t, 'documents', {
    respond: (method, u, body) => {
      if (u.pathname === '/api/v1/isms/document-folders') {
        ui.folders.push({ id: 'f1', name: body.name, parent_id: null });
        return { id: 'f1', name: body.name };
      }
    },
  });
  await settle(() => ui.el('documentsRows').textContent.includes('Security policy'));
  await ui.add('document', 'documentFormCard');
  assert.equal(ui.el('docWritePane').hidden, false);

  ui.el('docTitle').value = 'Access policy';
  ui.el('docTags').value = 'Access';
  ui.el('docTags').dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Enter' }));
  ui.el('docTags').value = 'Review,';
  ui.el('docTags').dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: ',' }));
  // Repeats differing only in case are ignored; known tags are offered.
  ui.el('docTags').value = 'access';
  ui.el('docTags').dispatchEvent(new ui.window.Event('blur'));
  assert.deepEqual(chips(ui), ['Access', 'Review']);
  ui.click('#docTagChoices [data-add-doc-tag="Security"]');
  ui.click('#docSelectedTags [data-remove-doc-tag="Review"]');
  assert.deepEqual(chips(ui), ['Access', 'Security']);
  assert.equal(ui.el('docTagChoices').textContent.trim(), '');

  ui.el('docNewFolder').value = 'Policies';
  ui.el('docNewFolder').dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Enter' }));
  await ui.statusIs('Folder created and selected.');
  assert.equal(ui.el('docFolder').value, 'f1');
  assert.equal(ui.el('docNewFolder').value, '');

  ui.el('docContent').value = '<p>Who may access what</p>';
  ui.select('docControls', 'c1');
  ui.select('docClauses', 'k1');
  ui.submit('documentForm');
  await ui.statusIs('Document created');

  assert.deepEqual(ui.writes(), [
    {
      method: 'POST',
      url: '/api/v1/isms/document-folders',
      body: { name: 'Policies', parent_id: null },
    },
    {
      method: 'POST',
      url: '/api/v1/isms/documents?framework=A',
      body: {
        title: 'Access policy',
        document_type: 'policy',
        external_url: null,
        description: '',
        folder_id: 'f1',
        tags: ['Access', 'Security'],
        controls: ['c1'],
        clauses: ['k1'],
        content_html: '<p>Who may access what</p>',
      },
    },
  ]);
  // The form is cleared and closed for the next record.
  assert.equal(ui.el('docTitle').value, '');
  assert.deepEqual(chips(ui), []);
  assert.equal(ui.el('documentFormCard').style.display, 'none');
});

test('a linked or uploaded document needs its address or file, and the file is sent', async (t) => {
  const ui = await isms(t, 'documents', {
    respond: (method, u) => (u.pathname === '/api/v1/isms/documents' ? { id: 'd9' } : undefined),
  });
  await settle(() => ui.el('documentsRows').textContent.includes('Security policy'));
  await ui.add('document', 'documentFormCard');
  ui.el('docTitle').value = 'Supplier register';

  ui.el('docMediumLink').checked = true;
  ui.el('docMediumLink').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(
    ['docWritePane', 'docUploadPane', 'docLinkPane'].map((id) => ui.el(id).hidden),
    [true, true, false],
  );
  ui.submit('documentForm');
  await ui.statusIs('Enter the document URL.');

  ui.el('docMediumUpload').checked = true;
  ui.el('docMediumUpload').dispatchEvent(new ui.window.Event('change'));
  ui.submit('documentForm');
  await ui.statusIs('Select a document to upload.');
  assert.deepEqual(ui.writes(), []);

  const file = new File(['%PDF-1.7'], 'register.pdf', { type: 'application/pdf' });
  Object.defineProperty(ui.el('docFile'), 'files', { configurable: true, value: [file] });
  ui.submit('documentForm');
  await ui.statusIs('Document saved and file uploaded');
  const [created, upload] = ui.writes();
  assert.equal(created.url, '/api/v1/isms/documents?framework=A');
  assert.equal(created.body.external_url, null);
  assert.equal('content_html' in created.body, false);
  assert.equal(upload.url, '/api/v1/isms/documents/d9/file');
  assert.equal(upload.body.get('file').name, 'register.pdf');
});

test('editing a document keeps its content version, and Cancel leaves it unchanged', async (t) => {
  const ui = await isms(t, 'documents');
  await settle(() => ui.el('documentsRows').textContent.includes('Security policy'));
  ui.click('[data-isms-edit="document"][data-isms-id="d1"]');
  assert.equal(ui.el('docTitle').value, 'Security policy');
  assert.deepEqual(chips(ui), ['Security']);
  assert.equal(ui.el('docContent').value, '<p>Policy</p>');
  assert.equal(ui.el('docMediumWrite').checked, true);
  assert.deepEqual(
    [...ui.el('docControls').selectedOptions].map((o) => o.value),
    ['c1'],
  );
  assert.equal(
    ui.el('documentForm').querySelector('button[type="submit"]').textContent,
    'Save document record',
  );

  ui.el('docTitle').value = 'Security policy v2';
  ui.submit('documentForm');
  await ui.statusIs('Document updated');
  assert.deepEqual(ui.writes()[0], {
    method: 'PATCH',
    url: '/api/v1/isms/documents/d1?framework=A',
    body: {
      title: 'Security policy v2',
      document_type: 'policy',
      external_url: null,
      description: '',
      folder_id: null,
      tags: ['Security'],
      controls: ['c1'],
      clauses: [],
      content_html: '<p>Policy</p>',
      expected_content_version: 3,
    },
  });

  // Moving written content to a link clears it, under the same version check.
  ui.click('[data-isms-edit="document"][data-isms-id="d1"]');
  ui.el('docMediumLink').checked = true;
  ui.el('docMediumLink').dispatchEvent(new ui.window.Event('change'));
  ui.el('docUrl').value = ' https://docs.example/policy ';
  ui.submit('documentForm');
  await settle(() => ui.writes().length === 2);
  const moved = ui.writes()[1].body;
  assert.deepEqual(
    [moved.external_url, moved.content_html, moved.expected_content_version],
    ['https://docs.example/policy', '', 3],
  );

  ui.click('[data-isms-edit="document"][data-isms-id="d1"]');
  ui.click('[data-isms-cancel-edit="document"]');
  assert.equal(ui.el('documentForm').dataset.editingId, '');
  assert.equal(ui.el('documentFormCard').style.display, 'none');
  assert.equal(ui.writes().length, 2);
});

// --- Deleting and refused saves ------------------------------------------------------

test('deleting asks first, and declining sends nothing', async (t) => {
  const declined = await isms(t, 'objectives', { confirm: false });
  await settle(() => declined.el('objectivesRows').textContent.includes('Train operators'));
  declined.click('[data-isms-delete="objective"][data-isms-id="o1"]');
  assert.deepEqual(declined.prompts, [
    'Delete Train operators? This will remove the ISMS record and its ISMS links.',
  ]);
  assert.deepEqual(declined.writes(), []);

  const ui = await isms(t, 'objectives');
  await settle(() => ui.el('objectivesRows').textContent.includes('Train operators'));
  ui.click('[data-isms-delete="objective"][data-isms-id="o1"]');
  await ui.statusIs('ISMS item deleted');
  assert.deepEqual(
    ui.writes().map((w) => [w.method, w.url]),
    [['DELETE', '/api/v1/isms/objectives/o1?framework=A']],
  );
});

test('a refused save keeps the entered values and says why', async (t) => {
  const ui = await isms(t, 'objectives', {
    respond: () => new Error('Unknown organisation chart node'),
  });
  await settle(() => ui.el('objectivesRows').textContent.includes('Train operators'));
  await ui.add('objective', 'objectiveFormCard');
  ui.el('objGoal').value = 'Encrypt backups';
  ui.submit('objectiveForm');
  await ui.statusIs('Could not save: Unknown organisation chart node');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('objGoal').value, 'Encrypt backups');
  assert.equal(ui.el('objectiveFormCard').style.display, '');
});

// --- Organisation chart --------------------------------------------------------------

test('the organisation chart draws the hierarchy, and a node is edited from the table', async (t) => {
  const ui = await isms(t, 'org');
  await settle(() => ui.el('orgChartViz').querySelector('svg'));
  const chart = ui.el('orgChartViz');
  const titles = [...chart.querySelectorAll('.isms-org-node-title')].map((n) => n.textContent);
  assert.deepEqual(titles, ['ISMS organisation', 'Security team', 'Platform operations']);
  // Parents link to children: the root to the team lead, the lead to operations.
  assert.equal(chart.querySelectorAll('svg > g > g:first-child path').length, 2);
  assert.deepEqual(
    [...chart.querySelectorAll('.isms-org-node-subtitle')].map((n) => n.textContent),
    ['root', 'role · alice', 'team'],
  );
  assert.deepEqual(
    [...chart.querySelectorAll('svg > g > g:last-child > g > title')].map((n) => n.textContent),
    ['ISMS organisation', 'Security team (alice)', 'Platform operations'],
  );

  ui.click('[data-isms-edit="org"][data-isms-id="n2"]');
  assert.deepEqual(
    ['orgParent', 'orgName', 'orgType'].map((id) => ui.el(id).value),
    ['n1', 'Platform operations', 'team'],
  );
  ui.el('orgDescription').value = 'Runs production';
  ui.select('orgUsers', 'u2');
  ui.submit('orgForm');
  await ui.statusIs('Organisation chart node updated');
  assert.deepEqual(ui.writes(), [
    {
      method: 'PATCH',
      url: '/api/v1/isms/org-nodes/n2?framework=A',
      body: {
        parent_id: 'n1',
        name: 'Platform operations',
        node_type: 'team',
        description: 'Runs production',
        user_ids: ['u2'],
        controls: [],
        clauses: [],
      },
    },
  ]);
});

test('long organisation names wrap onto two lines and end with an ellipsis', async (t) => {
  const ui = await isms(t, 'org', {
    records: (r) => {
      r.org_nodes.push({
        id: 'n3',
        name: 'Information security governance and assurance working group',
        node_type: 'committee',
        parent_id: 'n1',
        people: [],
      });
      return r;
    },
  });
  await settle(() => ui.el('orgChartViz').querySelector('svg'));
  const node = [...ui.el('orgChartViz').querySelectorAll('svg > g > g:last-child > g')].find((g) =>
    g.querySelector('title').textContent.startsWith('Information security'),
  );
  assert.deepEqual(
    [...node.querySelectorAll('.isms-org-node-title')].map((n) => n.textContent),
    ['Information security', 'governance and assurance…'],
  );
  assert.equal(node.querySelector('.isms-org-node-subtitle').textContent, 'committee');
  // Siblings are ordered by name under their parent.
  const titles = [...ui.el('orgChartViz').querySelectorAll('svg > g > g:last-child > g > title')];
  assert.deepEqual(
    titles.map((n) => n.textContent),
    [
      'ISMS organisation',
      'Security team (alice)',
      'Information security governance and assurance working group',
      'Platform operations',
    ],
  );
});

// --- Assets, licences and hosting accounts ----------------------------------------------

test('an asset takes its category, subcategory, licence and owner', async (t) => {
  const ui = await isms(t, 'assets');
  await settle(() => ui.el('assetsRows').textContent.includes('Database'));
  assert.match(ui.el('assetsRows').textContent, /Enterprise/);
  await ui.add('asset', 'assetFormCard');
  const options = (id) => [...ui.el(id).options].map((o) => o.textContent);
  assert.deepEqual(options('assetCategory'), ['Data', 'Hardware']);
  assert.deepEqual(options('assetSubcategory'), ['Databases', 'Spreadsheets']);
  ui.select('assetCategory', 'cat2');
  assert.deepEqual(options('assetSubcategory'), ['Laptops']);

  ui.el('assetName').value = 'Laptop fleet';
  ui.select('assetLicense', 'l1');
  ui.select('assetOwner', 'n1');
  ui.submit('assetForm');
  await ui.statusIs('ISMS asset created');
  assert.deepEqual(ui.writes()[0], {
    method: 'POST',
    url: '/api/v1/isms/assets?framework=A',
    body: {
      asset: 'Laptop fleet',
      category_id: 'cat2',
      subcategory_id: 'sub3',
      license_id: 'l1',
      owner_org_node_id: 'n1',
      register_held_by_org_node_id: null,
      description: '',
      controls: [],
      clauses: [],
    },
  });

  // Editing restores the saved category and subcategory, not the first ones.
  ui.click('[data-isms-edit="asset"][data-isms-id="a1"]');
  assert.deepEqual(
    ['assetName', 'assetCategory', 'assetSubcategory', 'assetLicense', 'assetOwner'].map(
      (id) => ui.el(id).value,
    ),
    ['Database', 'cat1', 'sub2', 'l1', 'n1'],
  );
});

test('categories, licences and hosting accounts are managed in their own cards', async (t) => {
  const ui = await isms(t, 'assets');
  await settle(() => ui.el('assetsRows').textContent.includes('Database'));

  // One management card is open at a time.
  ui.click('[data-isms-toggle-card="licenseCard"]');
  assert.equal(ui.el('licenseCard').style.display, '');
  ui.click('[data-isms-toggle-card="assetCategoryCard"]');
  assert.equal(ui.el('assetCategoryCard').style.display, '');
  assert.equal(ui.el('licenseCard').style.display, 'none');
  assert.match(ui.el('assetCategoryList').textContent, /Data\s*Databases\s*Spreadsheets/);
  ui.el('assetCategoryName').value = 'Facilities';
  ui.click('#addAssetCategory');
  await ui.statusIs('Asset category added');
  ui.click('[data-isms-toggle-card="assetCategoryCard"]');
  ui.el('assetSubcategoryCategory').value = 'cat2';
  ui.el('assetSubcategoryName').value = 'Badges';
  ui.click('#addAssetSubcategory');
  await ui.statusIs('Asset subcategory added');

  // Opening the licence card closes the category card.
  ui.click('[data-isms-toggle-card="licenseCard"]');
  assert.equal(ui.el('licenseCard').style.display, '');
  assert.equal(ui.el('assetCategoryCard').style.display, 'none');
  assert.match(ui.el('licensesRows').textContent, /Enterprise\s*Per user\s*1/);
  ui.click('[data-license-edit="l1"]');
  assert.deepEqual(
    ['licenseId', 'licenseName', 'licenseDescription'].map((id) => ui.el(id).value),
    ['l1', 'Enterprise', 'Per user'],
  );
  assert.equal(ui.el('saveLicense').textContent, 'Save license');
  ui.el('licenseName').value = 'Enterprise seat';
  ui.submit('licenseForm');
  await ui.statusIs('License updated');
  ui.click('[data-license-delete="l1"]');
  await ui.statusIs('License deleted');
  ui.el('licenseName').value = 'Open source';
  ui.submit('licenseForm');
  await ui.statusIs('License added');

  assert.deepEqual(
    ui.writes().map((w) => [w.method, w.url, w.body]),
    [
      ['POST', '/api/v1/isms/asset-categories', { name: 'Facilities' }],
      ['POST', '/api/v1/isms/asset-subcategories', { name: 'Badges', category_id: 'cat2' }],
      [
        'PATCH',
        '/api/v1/isms/licenses/l1?framework=A',
        { name: 'Enterprise seat', description: 'Per user' },
      ],
      ['DELETE', '/api/v1/isms/licenses/l1?framework=A', null],
      ['POST', '/api/v1/isms/licenses?framework=A', { name: 'Open source', description: '' }],
    ],
  );
  assert.deepEqual(ui.prompts, [
    'Delete Enterprise? Licenses related to assets cannot be deleted.',
  ]);
});

// --- Access control matrix -----------------------------------------------------------

test('the access matrix groups by service with role columns and account pills', async (t) => {
  const ui = await isms(t, 'access');
  await settle(() => ui.el('accessMatrixRows').textContent.includes('Deploy'));
  const head = [...ui.el('accessMatrixHead').querySelectorAll('th')].map((th) => th.textContent);
  // Organisation roles become columns; teams do not.
  assert.deepEqual(head, [
    'Service',
    'Task/action',
    'Managed hosting accounts',
    'Status',
    'Approved by',
    'Security team',
    'Notes',
    'Actions',
  ]);
  const row = ui.el('accessMatrixRows').querySelector('tr[data-isms-row="access"]');
  assert.equal(
    row.dataset.ismsFilterText,
    'Deploy · Database · Approved · Production (111122223333), Staging (444455556666), ' +
      'Sandbox · Security team · alice · Quarterly review',
  );
  assert.equal(row.querySelector('a').getAttribute('href'), '/isms-asset.html?id=a1&framework=A');
  const pills = [...row.querySelectorAll('.isms-aws-account-pill')];
  assert.deepEqual(
    pills.map((p) => p.textContent),
    ['Production', 'Staging', '+1'],
  );
  assert.equal(pills[2].title, 'Production (111122223333)\nStaging (444455556666)\nSandbox');
  assert.match(row.querySelector('.isms-access-role-cell').textContent, /✓/);

  ui.click('[data-isms-edit="access"][data-isms-id="ac1"]');
  assert.deepEqual(
    [...ui.el('accessAwsAccounts').selectedOptions].map((o) => o.value),
    ['aws1', 'aws2', 'aws3'],
  );
  assert.deepEqual(
    [...ui.el('accessRole').selectedOptions].map((o) => o.value),
    ['n1'],
  );
  ui.el('accessTaskAction').value = 'Deploy and roll back';
  ui.select('accessAwsAccounts', 'aws1');
  ui.submit('accessControlForm');
  await ui.statusIs('Access control matrix row updated');
  assert.deepEqual(ui.writes()[0], {
    method: 'PATCH',
    url: '/api/v1/isms/access-control-matrix/ac1?framework=A',
    body: {
      task_action: 'Deploy and roll back',
      service_asset_id: 'a1',
      aws_account_ids: ['aws1'],
      status: 'Approved',
      approved_by_user_id: 'u1',
      role_org_node_ids: ['n1'],
      notes: 'Quarterly review',
    },
  });
});

test('hosting accounts are edited and removed from their card', async (t) => {
  const ui = await isms(t, 'access');
  await settle(() => ui.el('accessMatrixRows').textContent.includes('Deploy'));
  ui.click('[data-isms-toggle-card="awsAccountCard"]');
  assert.match(ui.el('awsAccountsRows').textContent, /Production\s*111122223333\s*Main\s*1/);
  ui.click('[data-aws-account-edit="aws1"]');
  assert.deepEqual(
    ['awsAccountName', 'awsAccountNumber', 'awsAccountNotes'].map((id) => ui.el(id).value),
    ['Production', '111122223333', 'Main'],
  );
  ui.el('awsAccountNotes').value = 'Main estate';
  ui.submit('awsAccountForm');
  await ui.statusIs('hosting account updated');
  ui.click('[data-aws-account-delete="aws2"]');
  await ui.statusIs('hosting account deleted');
  ui.el('awsAccountName').value = 'Disaster recovery';
  ui.submit('awsAccountForm');
  await ui.statusIs('hosting account added');
  assert.deepEqual(
    ui.writes().map((w) => [w.method, w.url, w.body]),
    [
      [
        'PATCH',
        '/api/v1/isms/aws-accounts/aws1',
        { name: 'Production', account_id: '111122223333', notes: 'Main estate' },
      ],
      ['DELETE', '/api/v1/isms/aws-accounts/aws2', null],
      [
        'POST',
        '/api/v1/isms/aws-accounts',
        { name: 'Disaster recovery', account_id: null, notes: '' },
      ],
    ],
  );
  assert.deepEqual(ui.prompts, [
    'Delete Staging? This removes it from access control matrix rows.',
  ]);
});

// --- Effectiveness and meetings --------------------------------------------------------

test('a metric entry needs a value and records against its measure', async (t) => {
  const ui = await isms(t, 'effectiveness');
  await settle(() => ui.el('effectivenessRows').textContent.includes('Agent coverage'));
  // The register names each measure's expected source with its collected label,
  // and keeps the stored name when that source no longer collects evidence.
  assert.equal(
    ui.el('effectivenessRows').querySelector('tr[data-isms-id="e1"] td:nth-child(2) a').textContent,
    'Build server',
  );
  assert.equal(
    ui.el('effectivenessRows').querySelector('tr[data-isms-id="e2"] td:nth-child(2)').textContent,
    'retired-source',
  );
  ui.click('[data-effectiveness-record="e1"]');
  assert.equal(ui.el('metricEntryFormCard').style.display, '');
  // A new entry starts with the measure's unit and expected source.
  assert.deepEqual(
    ['metricMeasureId', 'metricUnit', 'metricSourceType'].map((id) => ui.el(id).value),
    ['e1', '%', 'ci'],
  );
  // Choosing another measure follows its defaults; an expected source that no
  // longer collects evidence falls back to Other.
  ui.el('metricMeasureId').value = 'e2';
  ui.el('metricMeasureId').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(
    ['metricUnit', 'metricSourceType'].map((id) => ui.el(id).value),
    ['result', 'other'],
  );
  ui.el('metricMeasureId').value = 'e1';
  ui.el('metricMeasureId').dispatchEvent(new ui.window.Event('change'));
  ui.el('metricSourceType').value = 'other';
  ui.submit('metricEntryForm');
  await ui.statusIs('Could not save: Provide a numeric value or a qualitative value.');
  assert.deepEqual(ui.writes(), []);

  ui.el('metricValue').value = '97.5';
  ui.el('metricPeriodStart').value = '2026-10-01';
  ui.el('metricPeriodEnd').value = '2026-10-31';
  ui.el('metricSourceTitle').value = 'Agent report';
  ui.submit('metricEntryForm');
  await ui.statusIs('Effectiveness metric recorded');
  assert.deepEqual(ui.writes()[0], {
    method: 'POST',
    url: '/api/v1/isms/effectiveness-measures/e1/metrics?framework=A',
    body: {
      recorded_at: null,
      period_start: '2026-10-01',
      period_end: '2026-10-31',
      metric_value: 97.5,
      metric_unit: '%',
      qualitative_value: '',
      source_type: 'other',
      source_title: 'Agent report',
      source_url: null,
      source_reference: '',
      source_event_id: null,
      notes: '',
      raw_payload: {},
    },
  });
  assert.equal(ui.el('metricEntryFormCard').style.display, 'none');
});

test('a measure is edited with its target, and the target can be cleared', async (t) => {
  const ui = await isms(t, 'effectiveness');
  await settle(() => ui.el('effectivenessRows').textContent.includes('Agent coverage'));
  ui.click('[data-isms-edit="effectiveness"][data-isms-id="e1"]');
  assert.deepEqual(
    [
      'effSummary',
      'effMetricKey',
      'effSourceType',
      'effThresholdOperator',
      'effTargetValue',
      'effTargetUnit',
    ].map((id) => ui.el(id).value),
    ['Agent coverage', 'agent.coverage', 'ci', 'gte', '95', '%'],
  );
  // The expected source is a choice among collected sources and Other.
  assert.deepEqual(
    [...ui.el('effSourceType').options].map((o) => o.value),
    ['ci', 'other'],
  );
  assert.equal(ui.el('effMeasure'), null);
  assert.equal(ui.el('effMetric'), null);
  // The integration examples follow the chosen expected source.
  const examples = ui.el('effectivenessForm').querySelector('details');
  examples.open = true;
  await settle(() => examples.querySelector('code').textContent);
  assert.match(examples.querySelector('code').textContent, /"source_type": "ci"/);
  ui.el('effSourceType').value = 'other';
  ui.el('effSourceType').dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  assert.match(examples.querySelector('code').textContent, /"source_type": "other"/);
  ui.el('effSourceType').value = 'ci';
  ui.el('effTargetValue').value = '';
  ui.submit('effectivenessForm');
  await ui.statusIs('Effectiveness measure updated');
  const { body, url } = ui.writes()[0];
  assert.equal(url, '/api/v1/isms/effectiveness-measures/e1?framework=A');
  assert.equal(body.target_value, null);
  assert.equal(body.metric_key, 'agent.coverage');
  assert.equal(body.source_type, 'ci');
  assert.equal('effectiveness_measure' in body, false);
  assert.equal('metric' in body, false);
  assert.equal('clauses' in body, false);

  // A measure whose expected source has gone is edited as Other.
  ui.click('[data-isms-edit="effectiveness"][data-isms-id="e2"]');
  assert.equal(ui.el('effSourceType').value, 'other');
});

test('meeting minutes keep documents and external links, and add a missing person', async (t) => {
  const ui = await isms(t, 'meetings', {
    people: [{ id: 'p1', name: 'Dana', email: 'dana@example.test' }],
    respond: (method, u, body) =>
      u.pathname === '/api/v1/isms/people'
        ? { id: 'p2', name: body.name, email: body.email }
        : undefined,
  });
  await settle(() => ui.el('meetingsRows').textContent.includes('Annual review'));
  ui.click('[data-isms-edit="meeting"][data-isms-id="m1"]');
  assert.deepEqual(
    ['meetingTitle', 'meetingDate', 'meetingStart', 'meetingEnd'].map((id) => ui.el(id).value),
    ['Annual review', '2026-01-01', '09:00', '10:30'],
  );
  assert.deepEqual(
    [...ui.el('meetingDocuments').selectedOptions].map((o) => o.value),
    ['d1'],
  );
  const linkRows = () => [
    ...ui.el('meetingExternalLinks').querySelectorAll('.meeting-external-link-row'),
  ];
  assert.deepEqual(
    linkRows().map((r) => [
      r.querySelector('[data-meeting-link-title]').value,
      r.querySelector('[data-meeting-link-url]').value,
    ]),
    [['Minutes', 'https://docs.example/minutes']],
  );

  // A known person is selected rather than offered for creation.
  ui.el('meetingNewPersonName').value = 'dana';
  ui.el('meetingNewPersonName').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('meetingCreatePerson').hidden, true);
  assert.deepEqual(
    [...ui.el('meetingPersonAttendees').selectedOptions].map((o) => o.value),
    ['p1'],
  );
  ui.el('meetingNewPersonName').value = 'Eli';
  ui.el('meetingNewPersonEmail').value = 'eli@example.test';
  ui.el('meetingNewPersonEmail').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('meetingCreatePerson').hidden, false);
  ui.click('#meetingCreatePerson');
  await ui.statusIs('Person added to the directory and selected as an attendee.');
  assert.deepEqual(
    [...ui.el('meetingPersonAttendees').selectedOptions].map((o) => o.value),
    ['p1', 'p2'],
  );

  // Another link row; a link needs its address.
  ui.click('#addMeetingExternalLink');
  const added = linkRows()[1];
  added.querySelector('[data-meeting-link-title]').value = 'Recording';
  ui.submit('meetingForm');
  await ui.statusIs('Could not save: External meeting links need a URL.');
  added.querySelector('[data-meeting-link-url]').value = 'https://video.example/1';
  ui.submit('meetingForm');
  await ui.statusIs('Meeting minutes updated');
  const saved = ui.writes().find((w) => w.method === 'PATCH');
  assert.equal(saved.url, '/api/v1/isms/meetings/m1?framework=A');
  assert.deepEqual(saved.body.links, [
    { link_type: 'isms_document', document_id: 'd1' },
    { link_type: 'external_url', title: 'Minutes', url: 'https://docs.example/minutes' },
    { link_type: 'external_url', title: 'Recording', url: 'https://video.example/1' },
  ]);
  assert.deepEqual(saved.body.attendee_person_ids, ['p1', 'p2']);
  assert.deepEqual(saved.body.attendee_user_ids, ['u1']);
  assert.deepEqual(saved.body.apology_user_ids, ['u2']);
  assert.equal(saved.body.agenda_minutes_notes, '<p>Agenda</p>');
  assert.deepEqual(ui.writes()[0], {
    method: 'POST',
    url: '/api/v1/isms/people',
    body: { name: 'Eli', email: 'eli@example.test' },
  });

  // Removing the last link row leaves one empty row to type into.
  ui.click('[data-isms-edit="meeting"][data-isms-id="m1"]');
  for (const row of linkRows()) row.querySelector('[data-remove-meeting-link]').click();
  assert.equal(linkRows().length, 1);
  assert.equal(linkRows()[0].querySelector('[data-meeting-link-url]').value, '');
});
