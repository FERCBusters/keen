import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const hostile = '<img src=x onerror=alert(1)>';
const controls = [{ id: 'c1', ref: 'A1', title: 'Access' }];
const clauses = [{ id: 'k1', ref: '5.2', framework: 'B' }];

// Each detail page loads one record, its changelog and (for some) related SWOT items.
async function detail(
  t,
  name,
  {
    path,
    row,
    swot = false,
    me = { is_admin: true },
    query = '&id=x1',
    fail = false,
    sources = false,
    setup,
  } = {},
) {
  const posted = [];
  const ui = await page(t, name, {
    query,
    me,
    setup,
    get: async (u) => {
      if (u.pathname === path) {
        if (fail) throw new Error('Record offline');
        return structuredClone(row);
      }
      if (u.pathname.endsWith('/changelog'))
        return { items: [{ summary: 'Changed ' + hostile, changes: [] }] };
      if (swot && u.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (sources && u.pathname === '/api/v1/sources')
        return { items: [{ source: 'jira', label: 'Jira' }] };
      if (u.pathname === '/api/v1/audits')
        return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      posted.push({ path: u.pathname, body: JSON.parse(JSON.stringify(b)) });
      return { ok: true };
    },
  });
  await settle(() => ui.calls.some((c) => c.url.startsWith(path)) || ui.el('status').textContent);
  await new Promise((r) => setTimeout(r, 30));
  return { ui, posted };
}

async function sample(ui, posted, buttonId) {
  ui.el(buttonId).click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => posted.length === 1);
  return posted[0].body;
}

test('effectiveness metric entry shows its value against the parent measure target', async (t) => {
  const row = {
    id: 'x1',
    measure_id: 'm1',
    value_display: hostile + '12 h',
    metric_value: 12,
    period: 'March',
    source_type: 'jira',
    source_title: 'Jira filter',
    source_reference: 'JQL-9',
    source_url: 'javascript:alert(1)',
    source_event_id: 'e5',
    notes: 'n',
    raw_payload: { a: hostile },
    measure: {
      id: 'm1',
      summary: 'MTTR',
      description: 'Hours from alert to recovery',
      metric_key: 'mttr',
      owner: 'dana',
      target_value: 8,
      threshold_operator: 'lte',
      controls,
    },
  };
  const { ui, posted } = await detail(t, 'isms-effectiveness-metric', {
    path: '/api/v1/isms/effectiveness-metrics/x1',
    row,
    sources: true,
  });
  assert.equal(ui.el('metricTitle').textContent, row.value_display);
  assert.equal(ui.el('metricMeta').textContent, 'MTTR • March • jira');
  assert.match(ui.el('metricValue').innerHTML, /eff-metric-value-bad/);
  assert.match(ui.el('metricValue').textContent, /Outside target/);
  assert.equal(ui.el('metricValue').querySelector('img'), null);
  assert.equal(ui.el('sourceUrl').querySelector('a').getAttribute('href'), '#');
  assert.match(ui.el('sourceEvent').querySelector('a').getAttribute('href'), /event\.html\?id=e5/);
  assert.equal(ui.el('rawPayload').textContent, JSON.stringify(row.raw_payload, null, 2));
  assert.match(
    ui.el('parentMeasureTitle').getAttribute('href'),
    /isms-effectiveness-measure\.html\?id=m1/,
  );
  assert.match(ui.el('parentMetricKey').innerHTML, /<code>mttr<\/code>/);
  assert.match(
    ui.el('metricControls').querySelector('a').getAttribute('href'),
    /control\.html\?id=c1&tab=isms/,
  );
  const body = await sample(ui, posted, 'sampleMetricAudit');
  assert.equal(body.entity_type, 'isms_effectiveness_metric');
  assert.equal(body.evidence_url, '/isms-effectiveness-metric.html?id=x1&framework=A');
});

test('a minimal metric entry falls back to placeholders', async (t) => {
  const { ui } = await detail(t, 'isms-effectiveness-metric', {
    path: '/api/v1/isms/effectiveness-metrics/x1',
    row: { id: 'x1', measure: { owner: 'erin' } },
    sources: true,
    me: { can_view_isms: true },
  });
  assert.equal(ui.el('metricTitle').textContent, 'Metric entry');
  assert.equal(ui.el('metricPeriod').textContent, 'No period recorded');
  assert.equal(ui.el('sourceUrl').textContent, '—');
  assert.equal(ui.el('sourceEvent').textContent, '—');
  assert.match(ui.el('metricControls').textContent, /No controls linked/);
  assert.equal(ui.el('sampleMetricAudit').style.display, 'none');
});

test('meeting minutes render attendees, sanitised notes and safe supporting links', async (t) => {
  const row = {
    id: 'x1',
    title: hostile + 'Board review',
    date: '2026-02-01',
    start_time: '09:00',
    end_time: '10:00',
    attendees: [{ username: 'dana' }],
    apologies: [],
    agenda_minutes_notes: '<p>Agreed</p><script>alert(1)</script>',
    links: [
      { link_type: 'isms_document', document: { id: 'd1', title: 'Policy <b>' } },
      { url: 'javascript:alert(1)', title: 'Bad' },
      { url: 'https://docs.example/minutes', title: '' },
      { title: 'Empty' },
    ],
    controls,
    clauses,
  };
  const { ui } = await detail(t, 'isms-meeting', { path: '/api/v1/isms/meetings/x1', row });
  assert.equal(ui.el('meetingTitle').textContent, row.title);
  assert.equal(ui.el('meetingMeta').textContent, '2026-02-01 • 09:00–10:00');
  assert.equal(ui.el('meetingNotes').querySelector('script'), null);
  assert.equal(ui.el('meetingNotes').querySelector('p').textContent, 'Agreed');
  const links = [...ui.el('meetingLinks').querySelectorAll('a')];
  assert.deepEqual(
    links.map((a) => a.getAttribute('href')),
    ['/isms-document.html?id=d1&framework=A', '#', 'https://docs.example/minutes'],
  );
  assert.equal(links[0].textContent, 'Policy <b>');
  assert.equal(links[2].textContent, 'https://docs.example/minutes');
  assert.match(
    ui.el('meetingClauses').querySelector('a').getAttribute('href'),
    /clause\.html\?id=k1&tab=isms&framework=B/,
  );
  assert.match(ui.el('editMeetingLink').getAttribute('href'), /edit=meeting%3Ax1|edit=meeting:x1/);
  assert.match(ui.el('meetingChangelog').textContent, /Changed <img/);
  assert.equal(ui.el('meetingChangelog').querySelector('img'), null);
});

test('meetings without details show placeholders and hide editing from readers', async (t) => {
  const { ui } = await detail(t, 'isms-meeting', {
    path: '/api/v1/isms/meetings/x1',
    row: { id: 'x1', links: [] },
    me: { can_view_isms: true },
  });
  assert.equal(ui.el('meetingMeta').textContent, 'No date • No time recorded');
  assert.match(ui.el('meetingNotes').textContent, /No agenda\/minutes\/notes recorded/);
  assert.match(ui.el('meetingLinks').textContent, /No supporting links recorded/);
  assert.equal(ui.el('editMeetingLink').style.display, 'none');
});

test('ISMS documents link uploaded files and only safe external URLs', async (t) => {
  const row = {
    id: 'x1',
    title: hostile,
    document_type: 'policy',
    has_file: true,
    filename: 'policy.pdf',
    external_url: 'javascript:alert(1)',
    description: 'Desc',
    content_html: '<p>Inline</p><img src=x onerror=alert(1)>',
    tags: ['iso'],
    controls,
    clauses,
    uploaded_by: 'dana',
  };
  const { ui, posted } = await detail(t, 'isms-document', {
    path: '/api/v1/isms/documents/x1',
    row,
    swot: true,
  });
  assert.equal(ui.el('documentTitle').textContent, hostile);
  assert.match(
    ui.el('documentMeta').textContent,
    /^policy • uploaded file • external URL • updated/,
  );
  assert.equal(ui.el('documentContentCard').hidden, false);
  assert.equal(ui.el('documentContent').querySelector('img'), null);
  assert.equal(ui.el('documentUrl').querySelector('a').getAttribute('href'), '#');
  assert.equal(
    ui.el('documentFile').querySelector('a').getAttribute('href'),
    '/api/v1/isms/documents/x1/file',
  );
  // The open button prefers a safe external URL and otherwise the uploaded file.
  assert.equal(ui.el('openDocumentLink').getAttribute('href'), '/api/v1/isms/documents/x1/file');
  assert.equal(ui.el('openDocumentLink').style.display, '');
  assert.match(ui.el('swotRelatedPane').textContent, /No SWOT items linked/);
  const body = await sample(ui, posted, 'sampleDocumentAudit');
  assert.equal(body.entity_type, 'isms_document');
});

test('documents with neither file nor URL hide the open action', async (t) => {
  const { ui } = await detail(t, 'isms-document', {
    path: '/api/v1/isms/documents/x1',
    row: { id: 'x1', external_url: 'https://docs.example/p' },
    swot: true,
    me: { can_view_isms: true },
  });
  assert.equal(ui.el('openDocumentLink').getAttribute('href'), 'https://docs.example/p');
  const none = await detail(t, 'isms-document', {
    path: '/api/v1/isms/documents/x1',
    row: { id: 'x1' },
    swot: true,
  });
  assert.equal(none.ui.el('openDocumentLink').style.display, 'none');
  assert.equal(none.ui.el('documentContentCard').hidden, true);
  assert.equal(none.ui.el('documentDescription').textContent, 'No description recorded.');
});

test('access control rows show their service, accounts, approver and roles', async (t) => {
  const row = {
    id: 'x1',
    task_action: hostile + 'Grant prod access',
    status: 'Approved',
    service: { id: 'as1', asset: 'Prod DB' },
    aws_accounts: [{ name: 'prod', account_id: '1234' }, {}],
    approved_by: 'dana',
    roles: [{ name: '<b>DBA</b>' }],
    notes: '',
  };
  const { ui, posted } = await detail(t, 'isms-access', {
    path: '/api/v1/isms/access-control-matrix/x1',
    row,
  });
  assert.equal(ui.el('accessTitle').textContent, row.task_action);
  assert.match(ui.el('accessMeta').textContent, /^Prod DB • Approved • updated/);
  assert.match(
    ui.el('accessService').querySelector('a').getAttribute('href'),
    /isms-asset\.html\?id=as1/,
  );
  assert.deepEqual(
    [...ui.el('accessAccounts').querySelectorAll('.badge')].map((b) => b.textContent),
    ['prod · 1234', 'hosting account'],
  );
  assert.equal(ui.el('accessRoles').querySelector('b'), null);
  assert.equal(ui.el('accessNotes').textContent, 'No notes recorded.');
  const body = await sample(ui, posted, 'sampleAccessAudit');
  assert.equal(body.entity_type, 'isms_access_control_matrix');
});

test('access rows without a linked service or accounts show placeholders', async (t) => {
  const { ui } = await detail(t, 'isms-access', {
    path: '/api/v1/isms/access-control-matrix/x1',
    row: { id: 'x1', service: { name: 'Legacy <i>' } },
    me: { can_view_isms: true },
  });
  assert.equal(ui.el('accessService').textContent, 'Legacy <i>');
  assert.equal(ui.el('accessService').querySelector('a'), null);
  assert.equal(ui.el('accessStatusValue').textContent, 'Pending Approval');
  assert.equal(ui.el('accessAccounts').textContent, '—');
  assert.equal(ui.el('editAccessLink').style.display, 'none');
});

test('objectives show status, owners and linked requirements', async (t) => {
  const row = {
    id: 'x1',
    goal: hostile + 'Reduce incidents',
    requirement: 'Req',
    status: 'in_progress',
    completion_target_date: '2026-12-31',
    owner: 'dana',
    resource_users: ['erin', 'frank'],
    controls,
    clauses,
  };
  const { ui, posted } = await detail(t, 'isms-objective', {
    path: '/api/v1/isms/objectives/x1',
    row,
    swot: true,
  });
  assert.equal(ui.el('objectiveTitle').textContent, row.goal);
  assert.match(ui.el('objectiveMeta').textContent, /^2026-12-31 • In Progress • updated/);
  assert.equal(ui.el('objectiveStatusValue').textContent, 'In Progress');
  assert.equal(ui.el('objectiveMetric').textContent, 'No metric recorded.');
  const body = await sample(ui, posted, 'sampleObjectiveAudit');
  assert.equal(body.entity_type, 'isms_objective');
  const empty = await detail(t, 'isms-objective', {
    path: '/api/v1/isms/objectives/x1',
    row: { id: 'x1' },
    swot: true,
  });
  assert.equal(empty.ui.el('objectiveTitle').textContent, 'ISMS objective');
  assert.equal(empty.ui.el('objectiveStatusValue').textContent, 'Not Started');
  assert.equal(empty.ui.el('objectiveResources').textContent, '—');
});

test("a detail page link opens only the page's own tabs", async (t) => {
  const open = async (tab) => {
    const shown = [];
    await detail(t, 'isms-objective', {
      path: '/api/v1/isms/objectives/x1',
      row: { id: 'x1' },
      swot: true,
      query: `&id=x1&tab=${tab}`,
      setup: (window) => {
        window.bootstrap = {
          Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
        };
      },
    });
    return shown;
  };
  assert.deepEqual(await open('changelog'), ['objectiveChangelogTab']);
  // Inherited object names select nothing, leaving the details tab in front.
  for (const name of ['constructor', '__proto__', 'toString'])
    assert.deepEqual(await open(name), [], name);
});

test('assets show classification, owners and risk counts', async (t) => {
  const row = {
    id: 'x1',
    asset: hostile + 'Payroll',
    category: { name: 'Data' },
    subcategory_name: 'PII',
    license: 'Proprietary',
    owner_org_node: { name: 'Finance' },
    risk_count: 3,
    controls,
    clauses: [],
  };
  const { ui, posted } = await detail(t, 'isms-asset', {
    path: '/api/v1/isms/assets/x1',
    row,
    swot: true,
  });
  assert.equal(ui.el('assetTitle').textContent, row.asset);
  assert.match(ui.el('assetMeta').textContent, /^Data • PII • Proprietary • updated/);
  assert.equal(ui.el('assetOwner').textContent, 'Finance');
  assert.equal(ui.el('assetRegister').textContent, '—');
  assert.equal(ui.el('assetRiskCount').textContent, '3');
  assert.equal(ui.el('assetClauses').textContent, '—');
  const body = await sample(ui, posted, 'sampleAssetAudit');
  assert.equal(body.entity_type, 'isms_asset');
  const reader = await detail(t, 'isms-asset', {
    path: '/api/v1/isms/assets/x1',
    row: { id: 'x1', name: 'Laptop' },
    swot: true,
    me: { can_view_isms: true },
  });
  assert.equal(reader.ui.el('assetTitle').textContent, 'Laptop');
  assert.equal(reader.ui.el('editAssetLink').style.display, 'none');
  assert.equal(reader.ui.el('sampleAssetAudit').style.display, 'none');
});

for (const [name, path, message] of [
  [
    'isms-effectiveness-metric',
    '/api/v1/isms/effectiveness-metrics/x1',
    /Failed to load effectiveness metric: Error: Record offline/,
  ],
  [
    'isms-meeting',
    '/api/v1/isms/meetings/x1',
    /Failed to load meeting minutes: Error: Record offline/,
  ],
  ['isms-document', '/api/v1/isms/documents/x1', /Failed to load document: Error: Record offline/],
  [
    'isms-access',
    '/api/v1/isms/access-control-matrix/x1',
    /Failed to load access control row: Error: Record offline/,
  ],
  [
    'isms-objective',
    '/api/v1/isms/objectives/x1',
    /Failed to load objective: Error: Record offline/,
  ],
  ['isms-asset', '/api/v1/isms/assets/x1', /Failed to load asset: Error: Record offline/],
])
  test(`${name} reports load failures and missing ids`, async (t) => {
    const failed = await detail(t, name, { path, fail: true, swot: true, sources: true });
    assert.match(failed.ui.el('status').textContent, message);
    const missing = await detail(t, name, { path, query: '', swot: true, sources: true });
    assert.match(missing.ui.el('status').textContent, /^Missing /);
  });
