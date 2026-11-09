// The ISMS register's tables with records as the API serves them: empty
// sections, parent and owner objects, services deleted from under access rows,
// metric entries without a period, source or event, and meeting links.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  asset,
  assetRecord,
  accessRecord,
  category,
  clause,
  control,
  documentLink,
  documentRecord,
  externalLink,
  licence,
  measure,
  meeting,
  metricEntry,
  orgNode,
  orgRecord,
  register,
  settle,
  user,
} from './support/isms-register-fixture.mjs';

const hostile = '<img src=x onerror=alert(1)>';

for (const [tab, rowsId, message] of [
  ['objectives', 'objectivesRows', 'No ISMS objectives yet.'],
  ['documents', 'documentsRows', 'No ISMS documents yet.'],
  ['assets', 'assetsRows', 'No assets yet.'],
  ['effectiveness', 'effectivenessRows', 'No effectiveness measures yet.'],
  ['meetings', 'meetingsRows', 'No meeting minutes yet.'],
]) {
  test(`an empty ${tab} register says it has no records`, async (t) => {
    const ui = await register(t, { tab });
    await settle(() => ui.el(rowsId).textContent.includes(message));
    assert.equal(ui.el(rowsId).querySelectorAll('tr').length, 1);
    assert.equal(ui.el(rowsId).querySelector('td').getAttribute('colspan') !== null, true);
  });
}

test('an organisation node names its parent, and a top-level node sits under Root', async (t) => {
  const board = orgRecord('n1', 'Board');
  const team = orgRecord('n2', 'Security <team>', {
    parent_id: 'n1',
    parent: { id: 'n1', name: 'Board ' + hostile },
    node_type: 'team',
  });
  const ui = await register(t, { tab: 'org', records: { org_nodes: [board, team] } });
  await settle(() => ui.el('orgRows').querySelectorAll('tr').length === 2);
  const [first, second] = [...ui.el('orgRows').querySelectorAll('tr')].map((row) =>
    [...row.querySelectorAll('td')].slice(0, 3).map((cell) => cell.textContent),
  );
  assert.deepEqual(first, ['Board', 'Root', 'role']);
  assert.deepEqual(second, ['Security <team>', 'Board ' + hostile, 'team']);
  assert.equal(ui.el('orgRows').querySelector('img'), null);
});

test('assets show their owner, and licences without a description show a dash', async (t) => {
  const finance = orgNode('n1', 'Finance <ops>');
  const records = {
    assets: [
      assetRecord('a1', 'Payroll', {
        owner_org_node: finance,
        owner_org_node_id: 'n1',
      }),
      assetRecord('a2', 'Spare laptop'),
    ],
  };
  const lists = {
    asset_categories: [
      category('cat1', 'Data', [['sub1', 'Databases']]),
      category('cat2', 'Empty'),
    ],
    licenses: [
      licence('l1', 'Internal use', { asset_count: 2 }),
      licence('l2', 'Apache-2.0', { description: 'Open source' }),
    ],
  };
  const ui = await register(t, { tab: 'assets', records, lists });
  await settle(() => ui.el('assetsRows').querySelectorAll('tr').length === 2);
  const cells = (row) =>
    [...ui.el('assetsRows').querySelectorAll('tr')[row].querySelectorAll('td')].map(
      (cell) => cell.textContent,
    );
  assert.deepEqual(cells(0).slice(0, 5), ['Payroll', 'Data', 'Databases', '-', 'Finance <ops>']);
  assert.deepEqual(cells(1).slice(4, 5), ['-']);
  const licences = [...ui.el('licensesRows').querySelectorAll('tr')].map((row) =>
    [...row.querySelectorAll('td')].slice(0, 3).map((cell) => cell.textContent.trim()),
  );
  assert.deepEqual(licences, [
    ['Internal use', '-', '2'],
    ['Apache-2.0', 'Open source', '0'],
  ]);
  // A category without subcategories says so in the category card.
  const categories = [...ui.el('assetCategoryList').querySelectorAll('.border')].map((card) =>
    card.textContent.replace(/\s+/g, ' ').trim(),
  );
  assert.deepEqual(categories, ['Data Databases', 'Empty No subcategories']);
});

test('access rows whose service was deleted are grouped under Service without a link', async (t) => {
  const role = orgNode('n1', 'DBA');
  const db = asset('a1', 'Prod DB');
  const records = {
    access_control_matrix: [
      accessRecord('ac1', 'Rotate keys', null, [role]),
      accessRecord('ac2', 'Restore backup', null, []),
      accessRecord('ac3', 'Grant access', db, [role]),
    ],
  };
  const ui = await register(t, {
    tab: 'access',
    records,
    lists: { org_nodes: [role], assets: [db] },
  });
  await settle(() => ui.el('accessMatrixRows').querySelectorAll('tr').length === 3);
  const groups = [...ui.el('accessMatrixRows').querySelectorAll('td.isms-app-group')];
  assert.deepEqual(
    groups.map((cell) => [cell.textContent, cell.getAttribute('rowspan')]),
    [
      ['Prod DB', '1'],
      ['Service', '2'],
    ],
  );
  assert.equal(
    groups[0].querySelector('a').getAttribute('href'),
    '/isms-asset.html?id=a1&framework=A',
  );
  assert.equal(groups[1].querySelector('a'), null);
  // Filtering by the service placeholder finds the rows without a service.
  ui.el('accessTableFilter').value = 'Service · DBA';
  ui.el('accessTableFilter').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  const shown = [...ui.el('accessMatrixRows').querySelectorAll('tr')]
    .filter((row) => row.style.display !== 'none')
    .map((row) => row.querySelector('.isms-app-key-linkage').textContent);
  assert.deepEqual(shown, ['Rotate keys']);
});

test('effectiveness measures show their description, history and the latest entry as recorded', async (t) => {
  const alice = user('u1', 'alice');
  const latest = metricEntry('me1', 'e1', {
    metric_value: 12,
    metric_unit: 'h',
    value_display: '12 h',
    source_type: 'jira',
    source_url: 'https://jira.example/filter/9',
  });
  const records = {
    effectiveness_measures: [
      measure('e1', 'Mean time to recover', {
        description: 'Hours from alert to recovery ' + hostile,
        owner: alice,
        owner_user_id: 'u1',
        metric_entry_count: 3,
        latest_entry: latest,
        metric_entries: [latest],
      }),
      measure('e2', 'Phishing reports', {
        metric_entry_count: 1,
        latest_entry: metricEntry('me2', 'e2', {
          qualitative_value: 'Improving',
          value_display: 'Improving',
          period_start: '2026-01-01',
          period_end: '2026-01-31',
          period: '2026-01-01 → 2026-01-31',
          source_type: 'other',
          source_title: 'Monthly <report>',
          source_event_id: 'ev1',
        }),
      }),
      // A PATCH may clear a measure's summary.
      measure('e3', ''),
      measure('e4', 'Build failures', {
        metric_entry_count: 1,
        latest_entry: metricEntry('me4', 'e4', {
          metric_value: 2,
          value_display: '2',
          source_type: 'ci',
        }),
      }),
    ],
  };
  const ui = await register(t, {
    tab: 'effectiveness',
    records,
    get: (u) =>
      u.pathname === '/api/v1/audits'
        ? { items: [{ id: 'au1', title: 'Annual audit', status: 'open' }] }
        : undefined,
    lists: {
      users: [alice],
      effectiveness_metric_sources: [
        { id: 'jira', source: 'jira', label: 'Jira', color: '' },
        { id: 'other', source: 'other', label: 'Other', color: '' },
      ],
    },
  });
  await settle(() => ui.el('effectivenessRows').querySelectorAll('tr').length === 4);
  const row = (id) => ui.el('effectivenessRows').querySelector(`tr[data-isms-id="${id}"]`);

  assert.equal(
    row('e1').querySelector('.whitespace-pre-wrap').textContent,
    'Hours from alert to recovery ' + hostile,
  );
  assert.equal(row('e1').querySelector('img'), null);
  const latestCell = row('e1').querySelectorAll('td')[2];
  assert.match(latestCell.textContent, /No period · jira/);
  const source = latestCell.querySelector('a[target="_blank"]');
  assert.equal(source.textContent, 'jira');
  assert.equal(source.getAttribute('href'), 'https://jira.example/filter/9');
  assert.equal(latestCell.querySelector('a.font-monospace'), null);
  const history = latestCell.querySelector('a[href$="#metrics"]');
  assert.equal(history.textContent, '3 metric entries in metric history');
  assert.equal(
    history.getAttribute('href'),
    '/isms-effectiveness-measure.html?id=e1&framework=A#metrics',
  );

  const second = row('e2').querySelectorAll('td')[2];
  assert.match(second.textContent, /2026-01-01 → 2026-01-31 · Monthly <report>/);
  assert.equal(second.querySelector('a[target="_blank"]'), null);
  assert.equal(
    second.querySelector('a.font-monospace').getAttribute('href'),
    '/event.html?id=ev1&framework=A',
  );
  assert.equal(
    second.querySelector('a[href$="#metrics"]').textContent,
    '1 metric entry in metric history',
  );
  assert.equal(row('e2').querySelector('.whitespace-pre-wrap'), null);

  assert.equal(row('e3').querySelector('.fw-semibold a').textContent, 'Effectiveness measure');
  assert.equal(row('e3').querySelector('a[href$="#metrics"]'), null);
  row('e3').querySelector('[data-audit-sample-entity="effectiveness"]').click();
  await settle(() => ui.el('entityAuditSampleSelect')?.value === 'au1');
  assert.equal(
    ui.el('entityAuditSampleTitle').textContent,
    'Sample “Effectiveness measure” into audit',
  );
  // Without a title or address, an entry names the source type it came from.
  assert.equal(
    row('e4').querySelectorAll('td')[2].querySelector('.small-muted').textContent,
    'No period · ci',
  );

  // The owner's name finds their measure.
  ui.el('effectivenessTableFilter').value = 'alice';
  ui.el('effectivenessTableFilter').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  const shown = [...ui.el('effectivenessRows').querySelectorAll('tr')]
    .filter((tr) => tr.style.display !== 'none')
    .map((tr) => tr.dataset.ismsId);
  assert.deepEqual(shown, ['e1']);
  // A measure without a summary is offered by its placeholder title.
  assert.deepEqual(
    [...ui.el('metricMeasureId').options].map((o) => o.textContent),
    ['Mean time to recover', 'Phishing reports', 'Effectiveness measure', 'Build failures'],
  );
});

test('meeting links open the ISMS document, and an untitled external link shows its address', async (t) => {
  const policy = documentRecord('d1', 'Access <policy>');
  const records = {
    meetings: [
      meeting('m1', 'Quarterly review', '2026-02-01', {
        links: [
          documentLink('l1', policy),
          externalLink('l2', 'https://docs.example/minutes'),
          externalLink('l3', 'javascript:alert(1)', 'Legacy'),
        ],
      }),
    ],
  };
  const ui = await register(t, { tab: 'meetings', records, lists: { documents: [policy] } });
  await settle(() => ui.el('meetingsRows').querySelector('a.badge'));
  const links = [...ui.el('meetingsRows').querySelectorAll('td')[5].querySelectorAll('a')];
  assert.deepEqual(
    links.map((a) => [a.textContent, a.getAttribute('href')]),
    [
      ['Access <policy>', '/isms-document.html?id=d1&framework=A'],
      ['https://docs.example/minutes', 'https://docs.example/minutes'],
      ['Legacy', '#'],
    ],
  );
});

test('a control without a title is offered by its reference alone', async (t) => {
  const ui = await register(t, {
    tab: 'objectives',
    lists: {
      controls: [control('c1', 'A.5.1', 'Policies'), control('c2', 'A.9.9', null)],
      clauses: [clause('k1', '5.1', 'Leadership'), clause('k2', '6.2')],
    },
  });
  await settle(() => ui.el('objControls').options.length === 2);
  assert.deepEqual(
    [...ui.el('objControls').options].map((o) => [o.value, o.textContent]),
    [
      ['c1', 'A.5.1 Policies'],
      ['c2', 'A.9.9'],
    ],
  );
  assert.deepEqual(
    [...ui.el('objClauses').options].map((o) => o.textContent),
    ['5.1 Leadership', '6.2'],
  );
});

test('a person without an email address is offered by name alone', async (t) => {
  const ui = await register(t, {
    tab: 'meetings',
    people: [
      { id: 'p1', name: 'Dana', email: 'dana@example.test' },
      { id: 'p2', name: 'Erin <ext>', email: '' },
    ],
  });
  await settle(() => ui.el('meetingPersonAttendees').options.length === 2);
  assert.deepEqual(
    [...ui.el('meetingPersonAttendees').options].map((o) => o.textContent),
    ['Dana · dana@example.test', 'Erin <ext>'],
  );
});

test('an uploaded document whose stored name is empty links as Uploaded file', async (t) => {
  // The API keeps the base name of the upload, which is empty for a name
  // ending in a slash.
  const records = {
    documents: [
      documentRecord('d1', 'Scanned register', {
        has_file: true,
        filename: '',
        content_type: 'application/pdf',
      }),
    ],
  };
  const ui = await register(t, { tab: 'documents', records });
  await settle(() => ui.el('documentsRows').querySelector('a[target="_blank"]'));
  const link = ui.el('documentsRows').querySelector('a[target="_blank"]');
  assert.equal(link.textContent, 'Uploaded file');
  assert.equal(link.getAttribute('href'), '/api/v1/isms/documents/d1/file');
});
