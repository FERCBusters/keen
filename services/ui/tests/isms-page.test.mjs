import test from 'node:test';
import * as d3 from 'd3';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';
const records = {
  objectives: [
    {
      id: 'o1',
      goal: 'Protect records',
      requirement: '<img src=x>',
      metric: '100%',
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
      controls: [],
      clauses: [],
    },
    {
      id: 'd2',
      title: 'Operations guide',
      external_url: 'javascript:alert(1)',
      tags: ['Operations'],
      controls: [],
      clauses: [],
    },
  ],
  org_nodes: [
    { id: 'n1', name: 'Security team', node_type: 'role', people: [], controls: [], clauses: [] },
  ],
  assets: [{ id: 'a1', asset: 'Database', name: 'Database', controls: [], clauses: [] }],
  licenses: [{ id: 'l1', name: 'License' }],
  aws_accounts: [{ id: 'aws1', name: 'Production' }],
  access_control_matrix: [],
  effectiveness_measures: [
    {
      id: 'e1',
      summary: 'Agent coverage',
      metric: 'Percent',
      controls: [],
      clauses: [],
      metric_entries: [],
    },
  ],
  meetings: [
    {
      id: 'm1',
      title: 'Annual review',
      date: '2026-01-01',
      attendees: [],
      apologies: [],
      links: [{ url: 'javascript:alert(1)', title: 'Unsafe legacy link' }],
      controls: [],
      clauses: [],
    },
  ],
};
async function fixture(t, options = {}) {
  const ui = await page(t, 'isms', {
    ...options,
    get: async (u) => {
      if (options.fail) throw new Error('Offline');
      if (u.pathname === '/api/v1/isms/meta')
        return {
          framework: 'A',
          users: [],
          org_nodes: [],
          documents: [],
          assets: [],
          asset_categories: [],
          licenses: [],
          aws_accounts: [],
          business_processes: [],
          controls: [],
          clauses: [],
        };
      if (u.pathname === '/api/v1/isms/summary')
        return { framework: 'A', counts: { objectives: 1, documents: 2 }, ...records };
      if (u.pathname === '/api/v1/isms/document-folders') return [];
      if (u.pathname === '/api/v1/isms/people') return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      assert.equal(u.pathname, '/api/v1/isms/objectives');
      return { id: 'o2', ...b };
    },
  });
  await settle(() =>
    options.fail
      ? ui.el('status').textContent.includes('Offline')
      : ui
          .el('ismsMeta')
          .textContent.includes(
            options.me?.features?.enabled?.objectives === false ? 'A' : '1 objectives',
          ),
  );
  return ui;
}
for (const [tab, id, text] of [
  ['objectives', 'objectivesRows', 'Protect records'],
  ['documents', 'documentsRows', 'Security policy'],
  ['org', 'orgRows', 'Security team'],
  ['assets', 'assetsRows', 'Database'],
  ['access', 'accessMatrixRows', 'No access control'],
  ['effectiveness', 'effectivenessRows', 'Agent coverage'],
  ['meetings', 'meetingsRows', 'Annual review'],
]) {
  test('ISMS ' + tab + ' tab loads and renders its section', async (t) => {
    const ui = await fixture(t, { query: '&tab=' + tab });
    await settle(() => ui.el(id).textContent.includes(text));
    assert.equal(ui.el(id).querySelector('img'), null);
    assert.ok(
      ui.calls.some((c) => c.url.includes('/isms/summary?') && c.url.includes('section=' + tab)),
    );
  });
}
test('ISMS overview does not eagerly fetch all sections', async (t) => {
  const ui = await fixture(t);
  assert.equal(ui.calls.filter((c) => c.url.includes('/isms/summary?')).length, 1);
  assert.match(ui.calls.find((c) => c.url.includes('/isms/summary?')).url, /section=overview/);
});
test('ISMS document tag filtering is local and reversible', async (t) => {
  const ui = await fixture(t, { query: '&tab=documents' });
  await settle(() => ui.el('documentsRows').textContent.includes('Operations guide'));
  const before = ui.calls.length;
  ui.el('docTagCloud').querySelector('[data-filter-doc-tag="Security"]').click();
  assert.match(ui.el('documentsRows').textContent, /Security policy/);
  assert.doesNotMatch(ui.el('documentsRows').textContent, /Operations guide/);
  ui.el('docTagCloud').querySelector('[data-filter-doc-tag=""]').click();
  assert.match(ui.el('documentsRows').textContent, /Operations guide/);
  assert.equal(ui.calls.length, before);
});
test('ISMS objective form sends entered values and refreshes after save', async (t) => {
  const ui = await fixture(t, { query: '&tab=objectives' });
  await settle(() => ui.el('objectivesRows').textContent.includes('Protect records'));
  ui.el('objRequirement').value = 'Training';
  ui.el('objGoal').value = 'All staff trained';
  ui.el('objMetric').value = '100%';
  ui.el('objectiveForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'ISMS objective created');
  const call = ui.calls.find((c) => c.method === 'POST');
  assert.equal(call.body.goal, 'All staff trained');
  assert.equal(call.body.requirement, 'Training');
  assert.equal(ui.el('objGoal').value, '');
});
test('ISMS read-only users cannot see management forms', async (t) => {
  const ui = await fixture(t, { me: { can_view_isms: true } });
  assert.equal(ui.el('objectiveFormCard').style.display, 'none');
});
test('ISMS request failures remain visible', async (t) => {
  const ui = await fixture(t, { fail: true });
  assert.match(ui.el('status').textContent, /Failed to load ISMS.*Offline/);
});

for (const [tab, id] of [
  ['documents', 'documentsRows'],
  ['meetings', 'meetingsRows'],
]) {
  test('ISMS ' + tab + ' neutralises unsafe links already stored in the database', async (t) => {
    const ui = await fixture(t, { query: '&tab=' + tab });
    await settle(() => ui.el(id).querySelector('a[href="#"]'));
    for (const link of ui.el(id).querySelectorAll('a[href]'))
      assert.doesNotMatch(link.href, /^(javascript|data|vbscript):/i);
  });
}

test('SWOT overview is paginated and read only even for managers', async (t) => {
  const ui = await page(t, 'isms', {
    get(url) {
      if (url.pathname === '/api/v1/isms/meta') return { users: [] };
      if (url.pathname === '/api/v1/isms/summary') return { counts: {} };
      if (url.pathname === '/api/v1/isms/document-folders') return [];
      if (url.pathname === '/api/v1/isms/swot') {
        assert.equal(url.searchParams.get('framework'), 'A');
        assert.equal(url.searchParams.get('limit'), '100');
        return {
          items: [{ id: 's1', title: 'Stable team', kind: 'strength', status: 'defined' }],
          total: 101,
        };
      }
      throw Error('Unexpected GET ' + url);
    },
  });
  ui.el('swotTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('swotQuadrants').textContent.includes('Stable team'));
  assert.equal(
    ui.el('swotQuadrants').querySelectorAll('select, button, [draggable], .btn').length,
    0,
  );
  assert.equal(ui.el('swotEdit').hidden, false);
  assert.equal(new URL(ui.el('swotEdit').href).pathname, '/swot.html');
  ui.el('swotNext').click();
  await settle(() => ui.calls.some((call) => call.url.includes('offset=100')));
  assert.equal(
    ui.calls.some((call) => call.method !== 'GET'),
    false,
  );
});

for (const editable of [true, false]) {
  test(`Objectives share table, board, timeline and filters (${editable ? 'manager' : 'reader'})`, async (t) => {
    let state = 'defined';
    const ui = await page(t, 'isms', {
      query: '&tab=objectives&view=kanban',
      me: editable ? { is_admin: true } : { can_read_isms: true },
      setup(window) {
        window.d3 = d3;
      },
      get(url) {
        if (url.pathname === '/api/v1/isms/meta') return { users: [] };
        if (url.pathname === '/api/v1/isms/summary')
          return {
            counts: {},
            objectives: [
              {
                id: 'o1',
                goal: 'Train operators',
                status: state,
                start_date: '2026-10-01',
                completion_target_date: '2026-12-01',
                resource_users: [],
                controls: [],
                clauses: [],
              },
              {
                id: 'o2',
                goal: 'Review suppliers',
                status: 'completed',
                resource_users: [],
                controls: [],
                clauses: [],
              },
            ],
          };
        if (url.pathname === '/api/v1/isms/document-folders') return [];
        throw Error('Unexpected GET ' + url);
      },
      patch(url, body) {
        assert.equal(url.pathname, '/api/v1/isms/objectives/o1');
        state = body.status;
        return {};
      },
    });
    await settle(() => ui.el('objectivesPlanningView').textContent.includes('Train operators'));
    assert.equal(ui.el('objectivesTable').hidden, true);
    assert.equal(ui.el('objectivesPlanningView').querySelectorAll('.planning-card').length, 2);
    const select = ui.el('objectivesPlanningView').querySelector('[data-status-id="o1"]');
    if (editable) {
      select.value = 'planned';
      select.dispatchEvent(new ui.window.Event('change'));
      await settle(
        () => state === 'planned' && !ui.el('objectivesPlanningView').hasAttribute('aria-busy'),
      );
      assert.equal(
        ui.el('objectivesRows').querySelector('[data-isms-id="o1"]').dataset.objectiveStatus,
        'planned',
      );
    } else assert.equal(select, null);
    ui.el('objectivesTableFilter').value = 'Train';
    ui.el('objectivesTableFilter').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
    assert.equal(ui.el('objectivesPlanningView').querySelectorAll('.planning-card').length, 1);
    ui.el('objectivesView').value = 'timeline';
    ui.el('objectivesView').dispatchEvent(new ui.window.Event('change'));
    assert.equal(ui.el('objectivesPlanningView').querySelectorAll('rect').length, 1);
    ui.el('objectivesView').value = 'table';
    ui.el('objectivesView').dispatchEvent(new ui.window.Event('change'));
    assert.equal(ui.el('objectivesTable').hidden, false);
    assert.equal(ui.el('objectivesPlanning').hidden, true);
    assert.equal(
      ui.el('objectivesRows').querySelector('[data-isms-id="o2"]').style.display,
      'none',
    );
    assert.equal(
      ui.calls.some((call) => call.url.includes('/api/v1/isms/swot')),
      false,
    );
  });
}

test('a disabled Objectives deep link loads Overview without requesting objective data', async (t) => {
  const ui = await fixture(t, {
    query: '&tab=objectives',
    me: { is_admin: true, features: { enabled: { objectives: false } } },
  });
  assert.ok(ui.el('objectivesTab').closest('li').hasAttribute('data-feature-hidden'));
  assert.ok(!ui.calls.some((call) => call.url.includes('section=objectives')));
  assert.ok(ui.calls.some((call) => call.url.includes('section=overview')));
});

test('ISMS flow places SWOT beside measures and respects feature availability', async (t) => {
  const ui = await fixture(t);
  const link = ui.el('overviewCards').querySelector('a[aria-label="Open SWOT"]');
  assert.match(link.getAttribute('href'), /tab=swot/);
  assert.match(link.getAttribute('href'), /framework=A/);
  assert.equal(link.querySelector('rect').getAttribute('y'), '238');
});
test('ISMS flow omits disabled SWOT', async (t) => {
  const ui = await fixture(t, { me: { is_admin: true, features: { enabled: { swot: false } } } });
  assert.equal(ui.el('overviewCards').querySelector('a[aria-label="Open SWOT"]'), null);
});
