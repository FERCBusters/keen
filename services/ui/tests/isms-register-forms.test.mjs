// Creating and editing ISMS register records: what each form sends for blank
// optional fields, what an edit form shows for a record without an owner,
// parent or licence, and the actions that send nothing.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  assetRecord,
  accessRecord,
  asset,
  category,
  deferred,
  documentRecord,
  licence,
  measure,
  meeting,
  objective,
  orgNode,
  orgRecord,
  register,
  settle,
  user,
} from './support/isms-register-fixture.mjs';

const alice = user('u1', 'alice');

test('editing an objective shows its owner and status, and a record without an owner shows none', async (t) => {
  const records = {
    objectives: [
      objective('o1', 'Train operators', {
        owner: alice,
        owner_user_id: 'u1',
        status: 'in_progress',
      }),
      objective('o2', 'Patch servers'),
    ],
  };
  const ui = await register(t, { tab: 'objectives', records, lists: { users: [alice] } });
  await settle(() => ui.document.querySelector('[data-isms-edit="objective"][data-isms-id="o2"]'));
  ui.click('[data-isms-edit="objective"][data-isms-id="o1"]');
  assert.equal(ui.el('objOwner').value, 'u1');
  assert.equal(ui.el('objStatus').value, 'in_progress');

  ui.click('[data-isms-edit="objective"][data-isms-id="o2"]');
  assert.equal(ui.el('objOwner').selectedIndex, 0);
  assert.equal(ui.el('objOwner').selectedOptions[0].textContent, '-');
  assert.equal(ui.el('objStatus').value, 'defined');
  ui.submit('objectiveForm');
  await ui.statusIs('ISMS objective updated');
  const [saved] = ui.writes();
  assert.equal(saved.method, 'PATCH');
  assert.equal(saved.url, '/api/v1/isms/objectives/o2?framework=A');
  assert.equal(saved.body.owner_user_id, null);
  assert.equal(saved.body.status, 'defined');
});

test('a new organisation node at the root with its type cleared is saved as a role', async (t) => {
  const board = orgRecord('n1', 'Board');
  const team = orgRecord('n2', 'Security team', {
    parent_id: 'n1',
    parent: { id: 'n1', name: 'Board' },
  });
  const ui = await register(t, {
    tab: 'org',
    records: { org_nodes: [board, team] },
    lists: { org_nodes: [orgNode('n1', 'Board'), orgNode('n2', 'Security team')] },
    d3: false,
  });
  await settle(() => ui.document.querySelector('[data-isms-edit="org"][data-isms-id="n2"]'));
  // An edit form shows the record's own parent, Root included.
  ui.click('[data-isms-edit="org"][data-isms-id="n2"]');
  assert.equal(ui.el('orgParent').value, 'n1');
  ui.click('[data-isms-edit="org"][data-isms-id="n1"]');
  assert.equal(ui.el('orgParent').selectedOptions[0].textContent, 'Root');
  ui.click('[data-isms-cancel-edit="org"]');

  await ui.add('org', 'orgFormCard');
  ui.el('orgName').value = 'Platform';
  ui.el('orgType').value = '';
  ui.submit('orgForm');
  await ui.statusIs('Organisation chart node created');
  assert.deepEqual(ui.writes(), [
    {
      method: 'POST',
      url: '/api/v1/isms/org-nodes?framework=A',
      body: {
        parent_id: null,
        name: 'Platform',
        node_type: 'role',
        description: '',
        user_ids: [],
        controls: [],
        clauses: [],
      },
    },
  ]);
});

const assetLists = {
  asset_categories: [
    category('cat1', 'Data', [['sub1', 'Databases']]),
    category('cat2', 'Hardware', [['sub3', 'Laptops']]),
  ],
  licenses: [licence('l1', 'Enterprise')],
  org_nodes: [orgNode('n1', 'Finance')],
};

test('editing an asset without a licence or owner after one with both leaves them blank', async (t) => {
  const records = {
    assets: [
      assetRecord('a1', 'Payroll', {
        license_id: 'l1',
        license: 'Enterprise',
        license_name: 'Enterprise',
        owner_org_node_id: 'n1',
        owner_org_node: orgNode('n1', 'Finance'),
      }),
      assetRecord('a2', 'Spare laptop', {
        category: { id: 'cat2', name: 'Hardware' },
        category_id: 'cat2',
        category_name: 'Hardware',
        subcategory: { id: 'sub3', name: 'Laptops', category_id: 'cat2' },
        subcategory_id: 'sub3',
        subcategory_name: 'Laptops',
      }),
    ],
  };
  const ui = await register(t, { tab: 'assets', records, lists: assetLists });
  await settle(() => ui.document.querySelector('[data-isms-edit="asset"][data-isms-id="a2"]'));
  ui.click('[data-isms-edit="asset"][data-isms-id="a1"]');
  assert.equal(ui.el('assetLicense').value, 'l1');
  assert.equal(ui.el('assetOwner').value, 'n1');

  ui.click('[data-isms-edit="asset"][data-isms-id="a2"]');
  assert.equal(ui.el('assetCategory').value, 'cat2');
  assert.equal(ui.el('assetSubcategory').value, 'sub3');
  assert.equal(ui.el('assetLicense').selectedOptions[0].textContent, '-');
  assert.equal(ui.el('assetOwner').selectedOptions[0].textContent, '-');
  ui.submit('assetForm');
  await ui.statusIs('ISMS asset updated');
  const [saved] = ui.writes();
  assert.equal(saved.url, '/api/v1/isms/assets/a2?framework=A');
  assert.equal(saved.body.license_id, null);
  assert.equal(saved.body.owner_org_node_id, null);
});

test('a new asset without a licence or owner sends neither', async (t) => {
  const ui = await register(t, { tab: 'assets', lists: assetLists });
  await ui.add('asset', 'assetFormCard');
  ui.el('assetName').value = 'Badge reader';
  ui.submit('assetForm');
  await ui.statusIs('ISMS asset created');
  const [saved] = ui.writes();
  assert.equal(saved.method, 'POST');
  assert.equal(saved.url, '/api/v1/isms/assets?framework=A');
  assert.deepEqual(
    {
      category_id: saved.body.category_id,
      subcategory_id: saved.body.subcategory_id,
      license_id: saved.body.license_id,
      owner_org_node_id: saved.body.owner_org_node_id,
    },
    {
      category_id: 'cat1',
      subcategory_id: 'sub1',
      license_id: null,
      owner_org_node_id: null,
    },
  );
});

test('a category needs a name, and a subcategory needs a category', async (t) => {
  const ui = await register(t, { tab: 'assets', lists: { asset_categories: [] } });
  await settle(() => ui.el('assetsRows').textContent.includes('No assets yet.'));
  ui.click('[data-isms-toggle-card="assetCategoryCard"]');
  assert.equal(ui.el('assetCategoryCard').style.display, '');
  ui.el('assetCategoryName').value = '   ';
  ui.el('addAssetCategory').click();
  ui.el('assetSubcategoryName').value = 'Laptops';
  ui.el('addAssetSubcategory').click();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(ui.writes(), []);
  assert.equal(ui.el('assetSubcategoryCategory').disabled, true);
  // The same button closes the card again.
  ui.click('[data-isms-toggle-card="assetCategoryCard"]');
  assert.equal(ui.el('assetCategoryCard').style.display, 'none');
});

test('a category removed elsewhere is replaced by the first remaining one after a refresh', async (t) => {
  let categories = assetLists.asset_categories;
  const ui = await register(t, {
    tab: 'assets',
    get: (u) =>
      u.pathname === '/api/v1/isms/meta' && u.searchParams.get('section') === 'assets'
        ? {
            framework: 'A',
            section: 'assets',
            users: [],
            org_nodes: [],
            licenses: [],
            controls: [],
            clauses: [],
            asset_categories: structuredClone(categories),
          }
        : undefined,
  });
  await settle(() => ui.el('assetsRows').textContent.includes('No assets yet.'));
  ui.click('[data-isms-toggle-card="assetCategoryCard"]');
  ui.el('assetSubcategoryCategory').value = 'cat2';
  assert.equal(ui.el('assetSubcategoryCategory').value, 'cat2');
  // Hardware is deleted on the risks page while this card is open.
  categories = [assetLists.asset_categories[0], category('cat4', 'Facilities')];
  ui.el('assetCategoryName').value = 'Facilities';
  ui.el('addAssetCategory').click();
  await ui.statusIs('Asset category added');
  ui.click('[data-isms-toggle-card="assetCategoryCard"]');
  assert.equal(ui.el('assetSubcategoryCategory').value, 'cat1');
  ui.el('assetSubcategoryName').value = 'Backups';
  ui.el('addAssetSubcategory').click();
  await ui.statusIs('Asset subcategory added');
  assert.deepEqual(
    ui.writes().map((call) => call.body),
    [{ name: 'Facilities' }, { name: 'Backups', category_id: 'cat1' }],
  );
});

test('declining to delete a licence sends nothing', async (t) => {
  const ui = await register(t, {
    tab: 'assets',
    lists: { licenses: [licence('l1', 'Enterprise')] },
    confirm: false,
  });
  await settle(() => ui.document.querySelector('[data-license-delete="l1"]'));
  ui.click('[data-isms-toggle-card="licenseCard"]');
  ui.click('[data-license-delete="l1"]');
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(ui.writes(), []);
  assert.equal(ui.el('licensesRows').querySelectorAll('tr').length, 1);
});

test('a new measure sends its target as a number and a blank metric key as none', async (t) => {
  const ui = await register(t, { tab: 'effectiveness', lists: { users: [alice] } });
  await ui.add('effectiveness', 'effectivenessFormCard');
  ui.el('effSummary').value = 'Patch latency';
  ui.el('effThresholdOperator').value = 'lte';
  ui.el('effTargetValue').value = '7';
  ui.el('effTargetUnit').value = 'days';
  ui.submit('effectivenessForm');
  await ui.statusIs('Effectiveness measure created');
  const [saved] = ui.writes();
  assert.equal(saved.url, '/api/v1/isms/effectiveness-measures?framework=A');
  assert.deepEqual(saved.body, {
    summary: 'Patch latency',
    description: '',
    metric_key: null,
    source_type: 'other',
    threshold_operator: 'lte',
    target_value: 7,
    target_unit: 'days',
    owner_user_id: null,
    frequency: '',
    notes: '',
    controls: [],
  });
});

test('an open measure form falls back to Other when its source is no longer offered', async (t) => {
  let sources = [
    { id: 'jira', source: 'jira', label: 'Jira', color: '' },
    { id: 'other', source: 'other', label: 'Other', color: '' },
  ];
  let measures = [measure('e1', 'Ticket age', { source_type: 'jira' })];
  const ui = await register(t, {
    tab: 'effectiveness',
    get: (u) => {
      if (u.pathname === '/api/v1/isms/meta')
        return {
          framework: 'A',
          users: [],
          controls: [],
          effectiveness_metric_sources: structuredClone(sources),
          effectiveness_metric_source_types: sources.map((s) => s.id),
        };
      if (u.pathname === '/api/v1/isms/summary')
        return { framework: 'A', counts: {}, effectiveness_measures: structuredClone(measures) };
    },
    respond: (method) => {
      if (method === 'DELETE') {
        // Readers without event access are offered the sources measures use.
        measures = [];
        sources = sources.slice(1);
      }
    },
  });
  await ui.add('effectiveness', 'effectivenessFormCard');
  ui.el('effSourceType').value = 'jira';
  ui.click('[data-isms-delete="effectiveness"][data-isms-id="e1"]');
  await ui.statusIs('ISMS item deleted');
  assert.equal(ui.el('effectivenessFormCard').style.display, '');
  assert.deepEqual(
    [...ui.el('effSourceType').options].map((o) => o.value),
    ['other'],
  );
  assert.equal(ui.el('effSourceType').value, 'other');
  assert.equal(ui.el('effSourceType').selectedIndex, 0);
});

test('a new access row is created against the chosen service', async (t) => {
  const db = asset('a1', 'Prod DB');
  const ui = await register(t, {
    tab: 'access',
    lists: { assets: [db], org_nodes: [orgNode('n1', 'DBA')] },
  });
  await ui.add('access', 'accessControlFormCard');
  ui.el('accessTaskAction').value = 'Rotate keys';
  ui.select('accessRole', 'n1');
  ui.submit('accessControlForm');
  await ui.statusIs('Access control matrix row created');
  assert.deepEqual(ui.writes(), [
    {
      method: 'POST',
      url: '/api/v1/isms/access-control-matrix?framework=A',
      body: {
        task_action: 'Rotate keys',
        service_asset_id: 'a1',
        role_org_node_ids: ['n1'],
        notes: '',
      },
    },
  ]);
});

test('new minutes without a title or times send the defaults, and only filled links', async (t) => {
  const ui = await register(t, { tab: 'meetings' });
  await ui.add('meeting', 'meetingFormCard');
  ui.el('meetingTitle').value = '';
  ui.el('meetingDate').value = '2026-04-01';
  ui.document.querySelector('[data-meeting-link-url]').value = 'https://docs.example/agenda';
  ui.el('addMeetingExternalLink').click();
  assert.equal(ui.document.querySelectorAll('.meeting-external-link-row').length, 2);
  ui.submit('meetingForm');
  await ui.statusIs('Meeting minutes created');
  const [saved] = ui.writes();
  assert.equal(saved.url, '/api/v1/isms/meetings?framework=A');
  assert.equal(saved.body.title, 'ISMS meeting');
  assert.equal(saved.body.start_time, null);
  assert.equal(saved.body.end_time, null);
  assert.deepEqual(saved.body.links, [
    { link_type: 'external_url', title: null, url: 'https://docs.example/agenda' },
  ]);
});

test('editing minutes without times after minutes with times leaves Start and End empty', async (t) => {
  const records = {
    meetings: [
      meeting('m1', 'Quarterly review', '2026-02-01', {
        start_time: '09:00:00',
        end_time: '10:30:00',
      }),
      meeting('m2', 'Ad hoc review', '2026-02-08'),
    ],
  };
  const ui = await register(t, { tab: 'meetings', records });
  await settle(() => ui.document.querySelector('[data-isms-edit="meeting"][data-isms-id="m2"]'));
  ui.click('[data-isms-edit="meeting"][data-isms-id="m1"]');
  assert.deepEqual([ui.el('meetingStart').value, ui.el('meetingEnd').value], ['09:00', '10:30']);
  ui.click('[data-isms-edit="meeting"][data-isms-id="m2"]');
  assert.equal(ui.el('meetingTitle').value, 'Ad hoc review');
  assert.deepEqual([ui.el('meetingStart').value, ui.el('meetingEnd').value], ['', '']);
});

test('an uploaded document is saved again without choosing a new file', async (t) => {
  const records = {
    documents: [documentRecord('d1', 'Scanned register', { has_file: true, filename: '' })],
  };
  const ui = await register(t, { tab: 'documents', records });
  await settle(() => ui.document.querySelector('[data-isms-edit="document"][data-isms-id="d1"]'));
  ui.click('[data-isms-edit="document"][data-isms-id="d1"]');
  assert.equal(ui.el('docMediumUpload').checked, true);
  assert.equal(ui.el('docExistingFile').textContent, 'Current file: uploaded file');
  ui.el('docTitle').value = 'Scanned register 2026';
  ui.submit('documentForm');
  await ui.statusIs('Document updated');
  assert.deepEqual(
    ui.writes().map((call) => [call.method, call.url]),
    [['PATCH', '/api/v1/isms/documents/d1?framework=A']],
  );
});

test('deleting the document being edited closes its form before the register reloads', async (t) => {
  const records = { documents: [documentRecord('d1', 'Security policy')] };
  let reload = null;
  const ui = await register(t, {
    tab: 'documents',
    records,
    get: (u) => (reload && u.pathname === '/api/v1/isms/meta' ? reload.promise : undefined),
  });
  await settle(() => ui.document.querySelector('[data-isms-edit="document"][data-isms-id="d1"]'));
  ui.click('[data-isms-edit="document"][data-isms-id="d1"]');
  assert.equal(ui.el('docTitle').value, 'Security policy');
  reload = deferred();
  ui.click('[data-isms-delete="document"][data-isms-id="d1"]');
  await settle(() => ui.writes().length === 1);
  await settle(() => ui.el('documentFormCard').style.display === 'none');
  assert.equal(ui.el('docTitle').value, '');
  assert.equal(ui.el('documentForm').dataset.editingId, '');
  reload.reject(new Error('Offline'));
  await ui.statusIs('Failed to reload ISMS: Offline');
  assert.equal(ui.el('documentFormCard').style.display, 'none');
});

test('an access row whose service was deleted opens with no service chosen', async (t) => {
  const db = asset('a1', 'Prod DB');
  const ui = await register(t, {
    tab: 'access',
    records: { access_control_matrix: [accessRecord('ac1', 'Rotate keys', null, [])] },
    lists: { assets: [db] },
  });
  await settle(() => ui.document.querySelector('[data-isms-edit="access"][data-isms-id="ac1"]'));
  ui.click('[data-isms-edit="access"][data-isms-id="ac1"]');
  assert.equal(ui.el('accessTaskAction').value, 'Rotate keys');
  assert.equal(ui.el('accessService').selectedIndex, -1);
});
