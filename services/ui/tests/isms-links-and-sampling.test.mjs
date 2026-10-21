// ISMS register entry points: links that open a record for editing or for a new
// metric entry, sampling a register row into an audit, table filters, the
// objectives board when a move fails, and BookStack documents on demand.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import { page, settle } from './support/page-fixture.mjs';

const meta = {
  framework: 'A',
  users: [{ id: 'u1', username: 'alice' }],
  org_nodes: [{ id: 'n1', name: 'Security team', node_type: 'role' }],
  documents: [],
  document_types: ['policy'],
  assets: [],
  asset_categories: [],
  licenses: [],
  aws_accounts: [],
  business_processes: [],
  controls: [{ id: 'c1', ref: 'A.5.1', title: 'Policies' }],
  clauses: [{ id: 'k1', ref: '5.1', title: 'Leadership' }],
  access_statuses: ['Pending Approval', 'Approved'],
  effectiveness_metric_sources: [
    { id: 'ci', source: 'ci', label: 'Build server' },
    { id: 'ci', source: 'ci', label: 'Duplicate' },
    { source: 'other' },
  ],
};

function records() {
  return {
    objectives: [
      {
        id: 'o1',
        goal: 'Train operators',
        requirement: 'Awareness',
        metric: 'Completion rate',
        status: 'not_started',
        owner_user_id: 'u1',
        start_date: '2026-01-01',
        completion_target_date: '2026-06-30',
        completion_method: 'Course records',
        evaluation_method: 'Quarterly review',
        resource_requirements_text: 'Two trainers',
        resource_user_ids: ['u1'],
        controls: [{ id: 'c1', ref: 'A.5.1' }],
        clauses: [{ id: 'k1', ref: '5.1' }],
      },
      {
        id: 'o2',
        goal: 'Patch servers',
        requirement: 'Maintenance',
        status: 'completed',
        controls: [],
        clauses: [],
      },
    ],
    effectiveness_measures: [
      {
        id: 'e1',
        summary: 'Agent coverage',
        target_value: 95,
        target_unit: '%',
        threshold_operator: 'gte',
        controls: [],
        metric_entries: [],
      },
    ],
  };
}

async function isms(
  t,
  {
    query = '',
    me = { is_admin: true },
    get,
    patch,
    post,
    remove,
    globals = {},
    setup,
    summary = (r) => r,
    meta: metaOverride,
  } = {},
) {
  const sent = [];
  const ui = await page(t, 'isms', {
    query,
    me,
    globals,
    setup(window) {
      window.d3 = d3;
      setup?.(window);
    },
    get: async (u) => {
      sent.push({ method: 'GET', url: u.pathname + u.search });
      const answer = await get?.(u);
      if (answer !== undefined) return answer;
      if (u.pathname === '/api/v1/isms/meta') return structuredClone(metaOverride || meta);
      if (u.pathname === '/api/v1/isms/summary')
        return { framework: 'A', counts: {}, ...summary(records()) };
      if (u.pathname === '/api/v1/isms/document-folders') return [];
      if (u.pathname === '/api/v1/isms/people') return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, body) => {
      sent.push({ method: 'PATCH', url: u.pathname + u.search, body });
      return patch ? patch(u, body) : body;
    },
    post: async (u, body) => {
      sent.push({
        method: 'POST',
        url: u.pathname + u.search,
        body: JSON.parse(JSON.stringify(body)),
      });
      return post ? post(u, body) : { ok: true };
    },
    remove: async (u) => {
      sent.push({ method: 'DELETE', url: u.pathname + u.search });
      return remove ? remove(u) : {};
    },
  });
  return { ...ui, sent };
}

test('an edit link opens the record in its form with every saved value', async (t) => {
  const ui = await isms(t, { query: '&edit=objective:o1' });

  await settle(() => ui.el('objGoal').value === 'Train operators');

  assert.equal(ui.el('objRequirement').value, 'Awareness');
  assert.equal(ui.el('objMetric').value, 'Completion rate');
  assert.equal(ui.el('objOwner').value, 'u1');
  assert.equal(ui.el('objStart').value, '2026-01-01');
  assert.equal(ui.el('objTarget').value, '2026-06-30');
  // A record not yet started appears as defined.
  assert.equal(ui.el('objStatus').value, 'defined');
  assert.equal(ui.el('objCompletion').value, 'Course records');
  assert.equal(ui.el('objEvaluation').value, 'Quarterly review');
  assert.equal(ui.el('objResourceText').value, 'Two trainers');
  const chosen = (id) => [...ui.el(id).selectedOptions].map((o) => o.value);
  assert.deepEqual(chosen('objResources'), ['u1']);
  assert.deepEqual(chosen('objControls'), ['c1']);
  assert.deepEqual(chosen('objClauses'), ['k1']);
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), null);
});

test('a record link opens a new metric entry for that measure', async (t) => {
  const ui = await isms(t, { query: '&record=e1' });

  await settle(() => ui.el('metricEntryFormCard').style.display === '');

  assert.equal(ui.el('metricMeasureId').value, 'e1');
  assert.equal(ui.el('metricUnit').value, '%');
  assert.equal(ui.el('metricSourceType').value, 'other');
  // Each collected source is offered once, after which comes Other.
  assert.deepEqual(
    [...ui.el('metricSourceType').options].map((o) => [o.value, o.textContent]),
    [
      ['ci', 'Build server (ci)'],
      ['other', 'Other'],
    ],
  );
});

test('a measure link highlights that measure in the register', async (t) => {
  const scrolled = [];
  const ui = await isms(t, { query: '&measure=e1' });
  ui.window.HTMLElement.prototype.scrollIntoView = function () {
    scrolled.push(this.dataset.ismsId);
  };

  await settle(() =>
    ui.document
      .querySelector('[data-isms-row="effectiveness"][data-isms-id="e1"]')
      ?.classList.contains('table-warning'),
  );
  assert.deepEqual(scrolled, ['e1']);
});

test('a register row samples its record into an audit with a link back to it', async (t) => {
  const ui = await isms(t, {
    query: '&tab=objectives',
    get: (u) =>
      u.pathname === '/api/v1/audits'
        ? { items: [{ id: 'a1', title: 'Annual audit', status: 'open' }] }
        : undefined,
  });
  await settle(() => ui.document.querySelector('[data-audit-sample-entity="objective"]'));

  ui.document.querySelector('[data-audit-sample-entity="objective"][data-isms-id="o1"]').click();

  await settle(() => ui.el('entityAuditSampleSelect')?.value === 'a1');
  assert.equal(ui.el('entityAuditSampleTitle').textContent, 'Sample “Train operators” into audit');
  ui.el('entityAuditSampleSubmit').click();
  await settle(() => ui.sent.some((c) => c.method === 'POST'));
  const post = ui.sent.find((c) => c.method === 'POST');
  assert.equal(post.url, '/api/v1/audits/a1/evidence');
  assert.deepEqual(post.body, {
    entity_type: 'isms_objective',
    entity_id: 'o1',
    evidence_url: '/isms-objective.html?id=o1&framework=A',
    title: 'Train operators',
    notes: '',
  });
});

test('Reset clears an objectives search and status filter', async (t) => {
  const ui = await isms(t, { query: '&tab=objectives' });
  await settle(() => ui.el('objectivesRows').querySelectorAll('[data-isms-row]').length === 2);
  const visible = () =>
    [...ui.el('objectivesRows').querySelectorAll('tr')]
      .filter((row) => row.style.display !== 'none')
      .map((row) => row.dataset.ismsId);
  ui.el('objectivesStatus').value = 'completed';
  ui.el('objectivesStatus').dispatchEvent(new ui.window.Event('change'));
  ui.el('objectivesTableFilter').value = 'patch';
  ui.el('objectivesTableFilter').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.deepEqual(visible(), ['o2']);

  ui.document.querySelector('[data-isms-filter-clear="objectivesTableFilter"]').click();

  assert.equal(ui.el('objectivesTableFilter').value, '');
  assert.equal(ui.el('objectivesStatus').value, '');
  assert.deepEqual(visible(), ['o1', 'o2']);
  assert.equal(ui.document.activeElement, ui.el('objectivesTableFilter'));
});

test('a refused move on the objectives board is reported and the board reloaded', async (t) => {
  let summaries = 0;
  let failRefresh = false;
  const ui = await isms(t, {
    query: '&tab=objectives&view=kanban',
    get: (u) => {
      if (u.pathname !== '/api/v1/isms/summary') return undefined;
      summaries += 1;
      if (failRefresh) throw new Error('Summary unavailable');
      return undefined;
    },
    patch: () => {
      throw new Error('Objective changed elsewhere');
    },
  });
  await settle(() => ui.el('objectivesPlanningView').querySelector('[data-status-id]'));
  const before = summaries;
  const move = () => {
    const select = ui.el('objectivesPlanningView').querySelector('[data-status-id="o1"]');
    select.value = 'in_progress';
    select.dispatchEvent(new ui.window.Event('change'));
  };

  move();

  await settle(() => /Objective changed elsewhere/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Error: Objective changed elsewhere');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  await settle(() => summaries === before + 1);
  assert.equal(ui.el('objectivesPlanningView').hasAttribute('aria-busy'), false);

  // When the board cannot be refreshed either, the message says to reload.
  failRefresh = true;
  move();
  await settle(() => /reload before trying again/.test(ui.el('status').textContent));
  assert.equal(
    ui.el('status').textContent,
    'Error: Objective changed elsewhere Unable to refresh objectives; reload before trying again.',
  );
});

test('BookStack documents load when first opened and open from a link', async (t) => {
  const me = { can_view_isms: true, bookstack_enabled: true };
  const snapshots = [];
  const ui = await isms(t, {
    me,
    get: (u) => {
      if (u.pathname !== '/api/v1/isms/bookstack-sections') return undefined;
      snapshots.push(u);
      return { items: [] };
    },
  });
  const details = ui.el('bookstackDocuments');
  assert.equal(details.hidden, false);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(snapshots.length, 0);

  details.open = true;
  details.dispatchEvent(new ui.window.Event('toggle'));
  await settle(() => snapshots.length === 1);
  details.open = false;
  details.dispatchEvent(new ui.window.Event('toggle'));
  details.open = true;
  details.dispatchEvent(new ui.window.Event('toggle'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(snapshots.length, 1);

  const linked = await isms(t, {
    me,
    query: '&bookstack=1',
    get: (u) => (u.pathname === '/api/v1/isms/bookstack-sections' ? { items: [] } : undefined),
  });
  assert.equal(linked.el('bookstackDocuments').open, true);
});

test('BookStack documents that fail to open are reported and retried on the next opening', async (t) => {
  const me = { can_view_isms: true, bookstack_enabled: true };
  const snapshots = [];
  const ui = await isms(t, {
    me,
    get: (u) => {
      if (u.pathname !== '/api/v1/isms/bookstack-sections') return undefined;
      snapshots.push(u);
      return { items: [] };
    },
  });
  const details = ui.el('bookstackDocuments');
  const toggle = (open) => {
    details.open = open;
    details.dispatchEvent(new ui.window.Event('toggle'));
  };
  // Without its content area the documents module cannot start.
  const content = ui.el('bookstackDocumentContent');
  content.remove();

  toggle(true);

  await settle(() => /Could not open BookStack documents/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').dataset.alert, 'danger');
  // Closing the section leaves it alone; opening it again tries again.
  details.append(content);
  toggle(false);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(snapshots.length, 0);
  toggle(true);
  await settle(() => snapshots.length === 1);
});

test('without BookStack the documents section stays hidden', async (t) => {
  const ui = await isms(t, { me: { can_view_isms: true } });
  assert.equal(ui.el('bookstackDocuments').hidden, true);
});

// --- Every register row samples into an audit --------------------------------------

const registerRows = () => ({
  documents: [{ id: 'd1', title: 'Security policy', tags: [], controls: [], clauses: [] }],
  org_nodes: [{ id: 'n1', name: 'Security team', node_type: 'role', people: [] }],
  assets: [{ id: 'a1', asset: 'Database', name: 'Database', controls: [], clauses: [] }],
  access_control_matrix: [
    {
      id: 'ac1',
      task_action: 'Deploy',
      service: { id: 'a1', asset: 'Database' },
      status: 'Pending Approval',
      roles: [],
      aws_accounts: [],
    },
    {
      id: 'ac2',
      task_action: 'Restore',
      service: { id: 'a1', asset: 'Database' },
      status: 'Revoked',
      roles: [],
      aws_accounts: [],
      notes: '<img src=x onerror=alert(1)> review',
    },
  ],
  meetings: [
    { id: 'm1', title: 'Annual review', date: '2026-01-01', links: [], controls: [], clauses: [] },
  ],
});

// Each kind of register row names its record in the sample dialogue and sends the
// audit a link to that record's own page; a node has no page, so the register is sent.
for (const [kind, tab, rowsId, type, title, evidenceUrl] of [
  [
    'document',
    'documents',
    'documentsRows',
    'isms_document',
    'Security policy',
    '/isms-document.html?id=d1&framework=A',
  ],
  ['org', 'org', 'orgRows', 'isms_org_node', 'Security team', '/isms.html?framework=A&tab=org'],
  ['asset', 'assets', 'assetsRows', 'isms_asset', 'Database', '/isms-asset.html?id=a1&framework=A'],
  [
    'access',
    'access',
    'accessMatrixRows',
    'isms_access_control_matrix',
    'Deploy',
    '/isms-access.html?id=ac1&framework=A',
  ],
  [
    'effectiveness',
    'effectiveness',
    'effectivenessRows',
    'isms_effectiveness_measure',
    'Agent coverage',
    '/isms-effectiveness-measure.html?id=e1&framework=A',
  ],
  [
    'meeting',
    'meetings',
    'meetingsRows',
    'isms_meeting',
    'Annual review',
    '/isms-meeting.html?id=m1&framework=A',
  ],
]) {
  test(`a ${kind} row samples into an audit with its title and record link`, async (t) => {
    const ui = await isms(t, {
      query: '&tab=' + tab,
      summary: (r) => ({ ...r, ...registerRows() }),
      get: (u) =>
        u.pathname === '/api/v1/audits'
          ? { items: [{ id: 'au1', title: 'Annual audit', status: 'open' }] }
          : undefined,
    });
    await settle(() => ui.el(rowsId).querySelector(`[data-audit-sample-entity="${kind}"]`));
    const id = ui.el(rowsId).querySelector(`[data-audit-sample-entity="${kind}"]`).dataset.ismsId;

    ui.el(rowsId).querySelector(`[data-audit-sample-entity="${kind}"]`).click();

    await settle(() => ui.el('entityAuditSampleSelect')?.value === 'au1');
    assert.equal(ui.el('entityAuditSampleTitle').textContent, `Sample “${title}” into audit`);
    ui.el('entityAuditSampleSubmit').click();
    await settle(() => ui.sent.some((c) => c.method === 'POST'));
    const post = ui.sent.find((c) => c.method === 'POST');
    assert.equal(post.url, '/api/v1/audits/au1/evidence');
    assert.equal(post.body.entity_type, type);
    assert.equal(post.body.entity_id, id);
    assert.equal(post.body.title, title);
    assert.equal(post.body.evidence_url, evidenceUrl);
  });
}

// --- Access matrix status colouring and note tooltips ---------------------------------

test('access rows colour pending approvals, leave other states plain and keep notes as text', async (t) => {
  const created = [];
  const ui = await isms(t, {
    query: '&tab=access',
    summary: (r) => ({ ...r, ...registerRows() }),
    setup(window) {
      window.bootstrap = {
        Tooltip: class {
          static getInstance() {
            return null;
          }
          constructor(el, options) {
            created.push({ el, options });
          }
        },
      };
    },
  });
  await settle(() => ui.el('accessMatrixRows').textContent.includes('Restore'));
  const statusCells = [...ui.el('accessMatrixRows').querySelectorAll('.isms-access-status')];
  assert.deepEqual(
    statusCells.map((td) => [
      td.textContent.trim(),
      td.classList.contains('isms-access-cell-pending'),
      td.classList.contains('isms-app-cell-empty'),
    ]),
    [
      ['Pending Approval', true, false],
      ['Revoked', false, true],
    ],
  );
  // The hostile note becomes a tooltip title, never markup.
  assert.equal(ui.el('accessMatrixRows').querySelector('img'), null);
  const trigger = ui.el('accessMatrixRows').querySelector('[data-bs-toggle="tooltip"]');
  assert.equal(trigger.title, '<img src=x onerror=alert(1)> review');
  assert.equal(created.length, 1);
  assert.equal(created[0].el, trigger);
  assert.equal(created[0].options.container, 'body');
  assert.equal(created[0].options.trigger, 'hover focus');
});

// --- Section loading edge cases ------------------------------------------------------

test('an inactive deep-linked tab is shown through the tab component', async (t) => {
  const shown = [];
  const ui = await isms(t, {
    query: '&tab=access',
    summary: (r) => ({ ...r, ...registerRows() }),
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  await settle(() => ui.el('accessMatrixRows').textContent.includes('Deploy'));
  assert.deepEqual(shown, ['accessControlTab']);
});

test('the SoA tab shows the summary counts without requesting a register section', async (t) => {
  const ui = await isms(t, {
    query: '&tab=soa',
    get: (u) =>
      u.pathname === '/api/v1/isms/summary'
        ? { framework: 'A', counts: { objectives: 4, documents: 2 } }
        : undefined,
  });
  await settle(() => ui.el('ismsMeta').textContent.includes('4 objectives'));
  assert.match(ui.el('ismsMeta').textContent, /2 documents/);
  const sections = ui.sent
    .filter((c) => c.url.includes('/isms/summary?'))
    .map((c) => new URL(c.url, 'https://keen.example').searchParams.get('section'));
  assert.deepEqual(sections, ['overview']);
});

test('a People directory that cannot load is reported and the meetings still render', async (t) => {
  const ui = await isms(t, {
    query: '&tab=meetings',
    summary: (r) => ({ ...r, ...registerRows() }),
    get: (u) => {
      if (u.pathname === '/api/v1/isms/people') throw new Error('People offline');
      return undefined;
    },
  });
  await settle(() => ui.el('meetingsRows').textContent.includes('Annual review'));
  assert.equal(ui.el('status').textContent, 'Could not load People: Error: People offline');
  assert.equal(ui.el('status').dataset.alert, 'warning');
  assert.equal(ui.el('meetingPersonAttendees').options.length, 0);
});

test('an empty organisation chart says so, and filtering keeps the empty row visible', async (t) => {
  const ui = await isms(t, {
    query: '&tab=org',
    summary: (r) => ({ ...r, org_nodes: [] }),
  });
  await settle(() => ui.el('orgChartViz').textContent.includes('No organisation chart nodes yet.'));
  assert.equal(ui.el('orgChartViz').querySelector('svg'), null);
  const emptyRow = ui.el('orgRows').querySelector('td[colspan]');
  assert.match(emptyRow.textContent, /No organisation chart nodes yet/);
  ui.el('orgTableFilter').value = 'platform';
  ui.el('orgTableFilter').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.equal(emptyRow.closest('tr').style.display, '');
});

// --- Documents: folders, media and refused folder creation ---------------------------

test('folder choices show their path, and a cycle of parents ends', async (t) => {
  const ui = await isms(t, {
    query: '&tab=documents',
    summary: (r) => ({ ...r, ...registerRows() }),
    get: (u) =>
      u.pathname === '/api/v1/isms/document-folders'
        ? [
            { id: 'f1', name: 'Governance', parent_id: null },
            { id: 'f2', name: 'Policies', parent_id: 'f1' },
            { id: 'f3', name: '<b>Standards</b>', parent_id: 'f2' },
            { id: 'f4', name: 'Loop A', parent_id: 'f5' },
            { id: 'f5', name: 'Loop B', parent_id: 'f4' },
          ]
        : undefined,
  });
  await settle(() => ui.el('docFolder').options.length === 6);
  assert.deepEqual(
    [...ui.el('docFolder').options].map((o) => o.textContent),
    [
      'Unfiled',
      'Governance',
      'Governance / Policies',
      'Governance / Policies / <b>Standards</b>',
      'Loop B / Loop A',
      'Loop A / Loop B',
    ],
  );
  assert.equal(ui.el('docFolder').querySelector('b'), null);
});

test('editing a document in a folder added elsewhere fetches the folders again', async (t) => {
  let folderRequests = 0;
  let archived = false;
  const ui = await isms(t, {
    query: '&tab=documents',
    summary: (r) => ({
      ...r,
      documents: [
        {
          id: 'd1',
          title: 'Security policy',
          folder_id: 'f9',
          has_file: true,
          filename: 'policy.pdf',
          tags: [],
          controls: [],
          clauses: [],
        },
        {
          id: 'd2',
          title: 'Supplier portal',
          external_url: 'https://portal.example/',
          tags: [],
          controls: [],
          clauses: [],
        },
      ],
    }),
    get: (u) => {
      if (u.pathname !== '/api/v1/isms/document-folders') return undefined;
      folderRequests += 1;
      return archived ? [{ id: 'f9', name: 'Archive', parent_id: null }] : [];
    },
  });
  await settle(() => ui.el('documentsRows').textContent.includes('Security policy'));
  const before = folderRequests;
  archived = true;
  assert.equal(ui.el('docFolder').options.length, 1);

  ui.document.querySelector('[data-isms-edit="document"][data-isms-id="d1"]').click();

  await settle(() => ui.el('docFolder').value === 'f9');
  assert.equal(folderRequests, before + 1);
  // An uploaded document opens on its upload pane and names the current file.
  assert.equal(ui.el('docMediumUpload').checked, true);
  assert.equal(ui.el('docUploadPane').hidden, false);
  assert.equal(ui.el('docExistingFile').textContent, 'Current file: policy.pdf');

  ui.document.querySelector('[data-isms-edit="document"][data-isms-id="d2"]').click();
  assert.equal(ui.el('docMediumLink').checked, true);
  assert.equal(ui.el('docLinkPane').hidden, false);
  assert.equal(ui.el('docUrl').value, 'https://portal.example/');
  assert.equal(ui.el('docExistingFile').textContent, '');
});

test('a folder list that cannot be refreshed while editing is reported as a warning', async (t) => {
  let editing = false;
  const ui = await isms(t, {
    query: '&tab=documents',
    summary: (r) => ({
      ...r,
      documents: [
        {
          id: 'd1',
          title: 'Security policy',
          folder_id: 'f9',
          tags: [],
          controls: [],
          clauses: [],
        },
      ],
    }),
    get: (u) => {
      if (u.pathname !== '/api/v1/isms/document-folders') return undefined;
      if (editing) throw new Error('Folders offline');
      return [];
    },
  });
  await settle(() => ui.el('documentsRows').textContent.includes('Security policy'));
  editing = true;

  ui.document.querySelector('[data-isms-edit="document"][data-isms-id="d1"]').click();

  await settle(() => ui.el('status').textContent.includes('Folders offline'));
  assert.equal(
    ui.el('status').textContent,
    'Could not load document folder: Error: Folders offline',
  );
  assert.equal(ui.el('status').dataset.alert, 'warning');
  assert.equal(ui.el('docFolder').value, '');
  assert.equal(ui.el('docTitle').value, 'Security policy');
});

test('a refused folder is reported and the typed name is kept', async (t) => {
  const ui = await isms(t, {
    query: '&tab=documents',
    summary: (r) => ({ ...r, ...registerRows() }),
    post: (u) => {
      if (u.pathname === '/api/v1/isms/document-folders') throw new Error('Folder name taken');
      return { ok: true };
    },
  });
  await settle(() => ui.el('documentsRows').textContent.includes('Security policy'));
  ui.document.querySelector('[data-isms-add="document"]').click();
  await settle(() => ui.el('documentFormCard').style.display === '');
  ui.el('docNewFolder').value = 'Policies';

  ui.el('docAddFolder').click();

  await settle(() => ui.el('status').textContent === 'Error: Folder name taken');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('docNewFolder').value, 'Policies');
  assert.equal(ui.el('docFolder').options.length, 1);
});

// --- Refused actions are reported in place -------------------------------------------

test('a refused asset category is reported as a failed save', async (t) => {
  const ui = await isms(t, {
    query: '&tab=assets',
    summary: (r) => ({ ...r, ...registerRows() }),
    post: () => {
      throw new Error('Category exists');
    },
  });
  await settle(() => ui.el('assetsRows').textContent.includes('Database'));
  ui.document.querySelector('[data-isms-toggle-card="assetCategoryCard"]').click();
  ui.el('assetCategoryName').value = 'Facilities';

  ui.el('addAssetCategory').click();

  await settle(() => ui.el('status').textContent === 'Could not save: Category exists');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.deepEqual(
    ui.sent.filter((c) => c.method === 'POST').map((c) => c.url),
    ['/api/v1/isms/asset-categories'],
  );
});

test('a delete the API refuses is reported and the row stays', async (t) => {
  const ui = await isms(t, {
    query: '&tab=assets',
    summary: (r) => ({ ...r, ...registerRows() }),
    globals: { confirm: () => true },
    remove: () => {
      throw new Error('Asset has risks');
    },
  });
  await settle(() => ui.el('assetsRows').textContent.includes('Database'));

  ui.document.querySelector('[data-isms-delete="asset"][data-isms-id="a1"]').click();

  await settle(() => ui.el('status').textContent === 'ISMS action failed: Error: Asset has risks');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.match(ui.el('assetsRows').textContent, /Database/);
  assert.deepEqual(
    ui.sent.filter((c) => c.method === 'DELETE').map((c) => c.url),
    ['/api/v1/isms/assets/a1?framework=A'],
  );
});

test('a person the directory refuses is reported and nothing is selected', async (t) => {
  const ui = await isms(t, {
    query: '&tab=meetings',
    summary: (r) => ({ ...r, ...registerRows() }),
    post: () => {
      throw new Error('Email in use');
    },
  });
  await settle(() => ui.el('meetingsRows').textContent.includes('Annual review'));
  ui.document.querySelector('[data-isms-add="meeting"]').click();
  await settle(() => ui.el('meetingFormCard').style.display === '');
  ui.el('meetingNewPersonName').value = 'Eli';
  ui.el('meetingNewPersonEmail').value = 'eli@example.test';
  ui.el('meetingNewPersonEmail').dispatchEvent(new ui.window.Event('input'));

  ui.el('meetingCreatePerson').click();

  await settle(() => ui.el('status').textContent === 'Could not add Person: Error: Email in use');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('meetingNewPersonName').value, 'Eli');
  assert.equal(ui.el('meetingPersonAttendees').selectedOptions.length, 0);
});

test('Cancel closes a metric entry without sending anything', async (t) => {
  const ui = await isms(t, { query: '&tab=effectiveness' });
  await settle(() => ui.document.querySelector('[data-effectiveness-record="e1"]'));
  ui.document.querySelector('[data-effectiveness-record="e1"]').click();
  assert.equal(ui.el('metricEntryFormCard').style.display, '');
  ui.el('metricValue').value = '42';

  ui.el('cancelMetricEntry').click();

  assert.equal(ui.el('metricEntryFormCard').style.display, 'none');
  assert.equal(ui.sent.filter((c) => c.method !== 'GET').length, 0);
});

// --- Latest metric entries -----------------------------------------------------------

test('the latest metric entry links to its own page, its event and a safe source only', async (t) => {
  const ui = await isms(t, {
    query: '&tab=effectiveness',
    summary: (r) => ({
      ...r,
      effectiveness_measures: [
        {
          ...r.effectiveness_measures[0],
          latest_entry: {
            id: 'me1',
            metric_value: 90,
            metric_unit: '%',
            period_start: '2026-10-01',
            period_end: '2026-10-31',
            source_url: 'javascript:alert(1)',
            source_title: 'CI <run>',
            source_event_id: 'ev1',
          },
        },
      ],
    }),
  });
  await settle(() => ui.el('effectivenessRows').textContent.includes('Agent coverage'));
  const cell = ui.el('effectivenessRows').querySelector('tr[data-isms-id="e1"] td:nth-child(3)');
  const links = [...cell.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')]);
  assert.deepEqual(links, [
    ['90 %', '/isms-effectiveness-metric.html?id=me1&framework=A'],
    ['CI <run>', '#'],
    ['ev1', '/event.html?id=ev1&framework=A'],
  ]);
  assert.match(cell.textContent, /2026-10-01 → 2026-10-31/);
});

test('metric sources fall back to the collected source types when no catalogue is given', async (t) => {
  const ui = await isms(t, {
    query: '&tab=effectiveness',
    meta: {
      ...meta,
      effectiveness_metric_sources: [],
      effectiveness_metric_source_types: ['ci', 'other', 'ci'],
    },
  });
  await settle(() => ui.document.querySelector('[data-effectiveness-record="e1"]'));
  ui.document.querySelector('[data-effectiveness-record="e1"]').click();
  assert.deepEqual(
    [...ui.el('metricSourceType').options].map((o) => [o.value, o.textContent]),
    [
      ['ci', 'ci (ci)'],
      ['other', 'Other'],
    ],
  );
});

// --- Objectives board: a move while one is saving -------------------------------------

test('a second move while the first is saving is ignored and the board redrawn', async (t) => {
  let release;
  const ui = await isms(t, {
    query: '&tab=objectives&view=kanban',
    patch: () => new Promise((resolve) => (release = resolve)),
  });
  await settle(() => ui.el('objectivesPlanningView').querySelector('[data-status-id="o1"]'));
  const move = (status) => {
    const select = ui.el('objectivesPlanningView').querySelector('[data-status-id="o1"]');
    select.value = status;
    select.dispatchEvent(new ui.window.Event('change'));
  };

  move('in_progress');
  await settle(() => release);
  move('completed');

  assert.equal(ui.sent.filter((c) => c.method === 'PATCH').length, 1);
  assert.equal(ui.sent.find((c) => c.method === 'PATCH').body.status, 'in_progress');
  // The redrawn board shows the stored status until the save finishes.
  assert.equal(
    ui.el('objectivesPlanningView').querySelector('[data-status-id="o1"]').value,
    'defined',
  );
  release({});
  await settle(() => !ui.el('objectivesPlanningView').hasAttribute('aria-busy'));
});

// --- SWOT overview paging and failure -------------------------------------------------

test('a SWOT page that cannot load says so, and paging back returns to the start', async (t) => {
  let fail = false;
  const ui = await isms(t, {
    get: (u) => {
      if (u.pathname !== '/api/v1/isms/swot') return undefined;
      if (fail) throw new Error('SWOT offline');
      return {
        items: [{ id: 's1', title: 'Stable team', kind: 'strength', status: 'defined' }],
        total: 201,
      };
    },
  });
  ui.el('swotTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('swotSummary').textContent === '1–1 of 201 items.');
  assert.equal(ui.el('swotPrevious').disabled, true);

  fail = true;
  ui.el('swotNext').click();

  await settle(() => ui.el('swotSummary').textContent.includes('SWOT offline'));
  assert.equal(ui.el('swotSummary').textContent, 'Unable to load SWOT: Error: SWOT offline');
  assert.equal(ui.el('swotQuadrants').childElementCount, 0);
  // The failed page can still be left for the one before it.
  assert.equal(ui.el('swotPrevious').disabled, false);
  fail = false;
  ui.el('swotPrevious').click();
  await settle(() => ui.el('swotSummary').textContent === '1–1 of 201 items.');
  assert.equal(ui.el('swotPrevious').disabled, true);
  assert.match(ui.el('swotQuadrants').textContent, /Stable team/);
  assert.deepEqual(
    ui.sent
      .filter((c) => c.url.includes('/isms/swot'))
      .map((c) => new URL(c.url, 'https://keen.example').searchParams.get('offset'))
      .slice(-3),
    ['0', '100', '0'],
  );
});
