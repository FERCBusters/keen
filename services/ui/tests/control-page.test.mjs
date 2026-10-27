import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

function baseControl(overrides = {}) {
  return {
    id: 'c1',
    ref: 'A.5.1',
    title: '<b>Policies</b>',
    framework: 'A',
    type: 'control',
    in_scope: true,
    justification: 'Best Practice',
    upstream_url: 'https://guidance.example/a51',
    metadata: {
      description: 'Line one\n<script>alert(1)</script>',
      source: 'https://www.opensecurityarchitecture.org/',
    },
    clauses: [
      { clause_id: 'k10', ref: '10', title: 'Improvement', applicability: 'applicable' },
      {
        clause_id: 'k2',
        ref: '4.2',
        title: '<u>Parties</u>',
        applicability: 'partially_applicable',
      },
      { ref: '9', title: 'Unlinked', applicability: 'other' },
    ],
    ...overrides,
  };
}

const clauseTree = {
  items: [{ id: 'k4' }],
  tree: [
    {
      id: 'k4',
      ref: '4',
      title: 'Context',
      children: [
        { id: 'k41', ref: '4.1', title: 'Org', parent_id: 'k4', children: [] },
        {
          id: 'k2',
          ref: '4.2',
          title: 'Parties',
          parent_id: 'k4',
          children: [
            { id: 'k421', ref: '4.2.1', title: 'Needs', parent_id: 'k2', children: [] },
            { id: 'k422', ref: '4.2.2', title: 'Expectations', parent_id: 'k2', children: [] },
          ],
        },
      ],
    },
    { id: 'k10', ref: '10', title: 'Improvement', children: [] },
  ],
};

async function controlPage(t, options = {}) {
  const state = { control: options.control || baseControl(), patched: [], posted: [], removed: [] };
  const fail = options.fail || {};
  const empty = options.empty || {};
  const query = options.query ?? '&id=c1&tab=evidence';
  const ui = await page(t, 'control', {
    query,
    me: options.me || { is_admin: true },
    globals: options.globals,
    confirm: options.confirm,
    frameworks: [
      { slug: 'A', name: 'Framework A' },
      { slug: 'B', name: 'Framework <b>B</b>' },
    ],
    setup(window) {
      window.CSS = { escape: (v) => String(v).replace(/[^a-zA-Z0-9_-]/g, (c) => '\\' + c) };
      options.setup?.(window);
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/controls/c1') {
        if (fail.control) throw new Error('Control offline');
        await options.gate;
        return structuredClone(state.control);
      }
      if (p === '/api/v1/controls/c1/cross-framework') {
        if (fail.cross) throw new Error('Links offline');
        return {
          items: [
            {
              id: 'x1',
              source_control_id: 'b7',
              source_framework: 'B',
              source_ref: 'B7',
              source_title: 'Linked',
              rationale: 'Overlap',
            },
          ],
        };
      }
      if (p === '/api/v1/controls' && u.searchParams.get('framework') === 'B') {
        if (fail.sourceControls) throw new Error('Catalogue B offline');
        return {
          items: [
            { id: 'b7', ref: 'B7', title: '<b>Backup</b>' },
            { id: 'b8', ref: 'B8', type: 'control' },
          ],
        };
      }
      if (p === '/api/v1/controls/c1/risks') {
        if (fail.risks) throw new Error('Risks forbidden');
        if (empty.risks) return { items: [] };
        return {
          items: [
            {
              id: 'r1',
              asset: '<img src=x>Server',
              threat_summary: 'Loss',
              category: { name: 'Ops' },
              subcategory: { name: 'Hosting' },
              risk_types: ['Availability'],
              risk_score: 4,
              residual_risk_score: 16,
              risk_owner: 'dana',
            },
            { id: 'r2', asset: 'Laptop', risk_score: 9, residual_risk_score: 0 },
          ],
        };
      }
      if (p === '/api/v1/controls/c1/interested-parties') {
        if (fail.parties) throw new Error('IP offline');
        if (empty.parties) return { items: [] };
        return {
          items: [
            {
              id: 'ip1',
              name: { name: 'Regulator <script>' },
              nature: { name: 'Legal' },
              note: 'Annual returns',
              communications: [
                { event: 'Breach', with_whom: 'DPO', methods: ['Email', 'Phone'] },
                { event: 'Audit', with_whom: 'CFO', methods: ['Email'] },
              ],
            },
          ],
        };
      }
      if (p === '/api/v1/controls/c1/isms') {
        if (fail.isms) throw new Error('ISMS offline');
        // Policies and measures appear under Requirements, not the ISMS tab.
        if (empty.ismsRecords)
          return {
            documents: [{ id: 'd1', title: 'Access policy' }],
            effectiveness_measures: [{ id: 'm1', summary: 'MTTR' }],
          };
        return {
          swot: [{ id: 's1', title: 'Strength <b>x</b>', updated_at: '2026-01-01' }],
          objectives: [{ requirement: 'Reduce incidents' }],
          documents: empty.documents
            ? []
            : [
                {
                  id: 'd1',
                  title: '<img src=x>Access policy',
                  document_type: 'policy',
                  content_html: '<p>Text</p>',
                  content_version: 3,
                  updated_at: '2026-02-01T00:00:00Z',
                },
                {
                  id: 'd2',
                  title: 'Leavers process',
                  document_type: 'process',
                  has_file: true,
                  filename: 'leavers.pdf',
                },
                { id: 'd3', document_type: 'record', external_url: 'https://docs.example/r' },
                { id: 'd4', title: 'Draft' },
              ],
          org_nodes: [{}],
          assets: [{ asset: 'Laptop' }],
          effectiveness_measures: [{ id: 'm1', summary: 'MTTR' }],
          meetings: [{ id: 'mt1', title: 'Review' }, { title: 'No id' }],
        };
      }
      if (p === '/api/v1/controls/c1/evidence/summary') {
        if (fail.summary) throw new Error('Summary offline');
        if (empty.summary) return { total: 0, latest: null, sources: [] };
        return {
          control_id: 'c1',
          framework: 'A',
          total: 41,
          latest: '2026-03-05T09:00:00',
          sources: [
            { source: 'jenkins', count: 38, latest: '2026-03-05T09:00:00', link_entries: 0 },
            { source: 'diary', count: 3, latest: '2026-02-14T09:00:00', link_entries: 1 },
            { source: '', count: 1, latest: null, link_entries: 0 },
          ],
        };
      }
      if (p === '/api/v1/controls/c1/effectiveness-measures') {
        if (fail.measures) throw new Error('Measures offline');
        if (empty.measures) return { items: [] };
        if (options.measures) return { items: options.measures };
        return {
          items: [
            {
              id: 'm1',
              summary: '<b>Disposal certificates</b>',
              frequency: 'monthly',
              target_value: 100,
              target_unit: '%',
              threshold_operator: 'gte',
              target_display: '≥ 100 %',
              latest_entry: {
                metric_value: 100,
                metric_unit: '%',
                value_display: '100 %',
                recorded_at: '2026-03-01T00:00:00Z',
              },
            },
            {
              id: 'm2',
              summary: 'Laptop audit',
              target_value: 95,
              target_unit: '%',
              threshold_operator: 'gte',
              target_display: '≥ 95 %',
              latest_entry: { metric_value: 82, metric_unit: '%', value_display: '82 %' },
            },
            { id: 'm3', summary: 'Tabletop exercise', latest_entry: null },
            {
              id: 'm4',
              summary: 'Supplier review',
              latest_entry: { qualitative_value: 'Completed', recorded_at: '2026-01-10T00:00:00Z' },
            },
          ],
        };
      }
      if (p === '/api/v1/controls/c1/changelog') {
        if (fail.changelog) throw new Error('Changelog offline');
        return {
          items: [
            {
              summary: 'Justification changed',
              changed_by_username: 'admin',
              action: 'justification_updated',
              changes: [{ field: 'justification', before: 'Old', after: '<b>New</b>' }],
            },
          ],
        };
      }
      if (p === '/api/v1/clauses') {
        if (fail.clauses) throw new Error('Clauses offline');
        if (empty.clauseTree) return { items: [], tree: [] };
        return clauseTree;
      }
      if (p === '/api/v1/events/facets') {
        if (fail.facets) throw new Error('Facets offline');
        return {
          sources: [
            { source: 'github', count: 2 },
            { source: 'aws', count: 5 },
            { source: 'none', count: 0 },
          ],
        };
      }
      if (p === '/api/v1/events') {
        if (fail.events) throw new Error('Events offline');
        if (empty.events) return { total: 0, items: [] };
        return {
          total: 120,
          items: [
            {
              id: 'e1',
              timestamp: '2026-02-01T00:00:00Z',
              source: 'github',
              summary: '<img src=x onerror=alert(1)> pushed',
              source_url: 'https://github.example/commit/1',
            },
          ],
        };
      }
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (fail.patch) throw new Error('Patch refused');
      if (u.pathname.endsWith('/justification')) state.control.justification = b.justification;
      if (u.pathname.endsWith('/upstream-url')) state.control.upstream_url = b.upstream_url;
      return { ok: true };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (fail.post) throw new Error('Post refused');
      return { ok: true };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (fail.remove) throw new Error('Delete refused');
      return { ok: true };
    },
  });
  // A gated control load is awaited by the test itself.
  if (options.gate) return { ui, state };
  // Wait for the tab the page opens on: the evidence list, Requirements, or
  // the control itself for the other tabs.
  const tab = new URLSearchParams(query).get('tab') || 'requirements';
  await settle(() => {
    if (ui.el('status').textContent) return true;
    if (!ui.idle()) return false;
    if (tab === 'evidence') return ui.calls.some((c) => c.url.startsWith('/api/v1/events?'));
    if (tab === 'requirements')
      return !/Loading requirements/.test(ui.el('controlRequirements').textContent);
    return ui.calls.some((c) => c.url.startsWith('/api/v1/controls/c1?'));
  });
  await new Promise((r) => setTimeout(r, 10));
  return { ui, state };
}

// Related controls load when their tab first opens.
async function openRelated(ui) {
  ui.el('controlRelatedTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('crossFrameworkLinks').textContent !== 'Loading linked controls');
  await settle(() => ui.idle());
}

const lastEventsUrl = (ui) =>
  new URL(
    ui.calls.filter((c) => c.url.startsWith('/api/v1/events?')).at(-1).url,
    'https://keen.example',
  );

test('control page renders details, linked records and evidence with untrusted text inert', async (t) => {
  const { ui } = await controlPage(t);
  assert.equal(ui.el('ctrlTitle').textContent, 'A.5.1  <b>Policies</b>');
  assert.equal(
    ui.el('ctrlMeta').textContent,
    'A · control · in scope · Justification: Best Practice',
  );
  const description = ui.el('osaControlDescription');
  assert.equal(description.querySelector('script'), null);
  assert.match(description.textContent, /Line one\n<script>alert\(1\)<\/script>/);
  assert.match(description.textContent, /Open Security Architecture/);
  assert.equal(ui.el('ctrlLinks').querySelector('a').href, 'https://guidance.example/a51');
  assert.match(ui.el('controlsBreadcrumb').getAttribute('href'), /controls\.html\?framework=A/);

  // Clauses are sorted numerically; only linked clauses become links.
  const clauseRows = [...ui.el('clausesView').querySelectorAll('tbody tr')];
  assert.deepEqual(
    clauseRows.map((r) => r.cells[0].textContent.trim()),
    ['4.2', '9', '10'],
  );
  assert.equal(clauseRows[1].querySelector('a'), null);
  assert.match(
    clauseRows[0].querySelector('a').getAttribute('href'),
    /clause\.html\?id=k2&framework=A/,
  );
  assert.deepEqual(
    clauseRows.map((r) => r.cells[2].textContent),
    ['Partially applicable', 'Not applicable', 'Applicable'],
  );
  assert.equal(ui.el('clausesView').querySelector('u'), null);
  assert.equal(ui.el('clausesCount').textContent, '3');

  // Risks show score bands and escape the asset name.
  assert.equal(ui.el('risksCount').textContent, '2');
  assert.equal(ui.el('risksView').querySelector('img'), null);
  const badges = [...ui.el('risksView').querySelectorAll('tbody tr:first-child .badge')].map(
    (b) => b.className,
  );
  assert.ok(badges.some((c) => c.includes('text-bg-success')));
  assert.ok(badges.some((c) => c.includes('text-bg-danger')));
  assert.ok(ui.el('risksView').querySelector('tbody tr:nth-child(2) .text-bg-warning'));
  assert.match(
    ui.el('risksView').querySelector('a').getAttribute('href'),
    /risk\.html\?id=r1&framework=A/,
  );

  // Interested parties de-duplicate communication methods.
  assert.equal(ui.el('interestedPartiesCount').textContent, '1');
  assert.match(ui.el('interestedPartiesView').textContent, /Regulator <script>/);
  assert.match(ui.el('interestedPartiesView').innerHTML, /Breach \/ DPO<br>Audit \/ CFO/);
  assert.equal(ui.el('interestedPartiesView').querySelectorAll('.badge').length, 2);

  // Evidence rows and source facets.
  assert.equal(ui.el('rows').querySelector('img'), null);
  assert.match(ui.el('rows').textContent, /pushed/);
  assert.match(
    ui.el('rows').querySelector('a').getAttribute('href'),
    /event\.html\?id=e1&framework=A/,
  );
  assert.equal(ui.el('resultMeta').textContent, '1-50 of 120');
  assert.deepEqual(
    [...ui.el('sourceFilter').options].map((o) => o.value),
    ['', 'aws', 'github'],
  );
  assert.match(ui.el('sourceFilter').options[1].textContent, /aws \(5\)/);
  // The page asks for source counts only.
  const facets = new URL(
    ui.calls.find((c) => c.url.startsWith('/api/v1/events/facets?')).url,
    'https://keen.example',
  );
  assert.equal(facets.searchParams.get('max_controls'), '0');
  // Administrators see the justification and cross-framework link controls.
  assert.equal(ui.el('controlJustificationCard').style.display, '');
  assert.equal(ui.el('controlJustification').value, 'Best Practice');
  // Related controls wait for their tab.
  assert.equal(
    ui.calls.some((c) => c.url.startsWith('/api/v1/controls/c1/cross-framework')),
    false,
  );
  await openRelated(ui);
  assert.match(ui.window.location.search, /tab=related/);
  assert.equal(ui.el('crossFrameworkForm').style.display, '');
  assert.deepEqual(
    [...ui.el('crossFrameworkSource').options].map((o) => o.value),
    ['', 'B'],
  );
});

test('non-HTTP guidance URLs are not rendered as links', async (t) => {
  const { ui } = await controlPage(t, {
    control: baseControl({ upstream_url: 'javascript:alert(1)', metadata: {}, justification: '' }),
  });
  assert.equal(ui.el('ctrlLinks').querySelector('a'), null);
  assert.equal(ui.el('ctrlMeta').textContent, 'A · control · in scope');
  assert.equal(ui.el('osaControlDescription').textContent, '');
});

test('the Keen Assurance Framework attribution is shown for its controls', async (t) => {
  const { ui } = await controlPage(t, {
    control: baseControl({ framework: 'KEEN-AF:1.0', metadata: {} }),
  });
  assert.match(ui.el('osaControlDescription').textContent, /Vanta's controls/);
});

test('readers without risk or ISMS permission do not request restricted links', async (t) => {
  const { ui } = await controlPage(t, { me: { can_view_controls: true } });
  assert.ok(ui.el('risksCard').classList.contains('d-none'));
  assert.ok(ui.el('interestedPartiesCard').classList.contains('d-none'));
  assert.ok(ui.el('controlGovernancePane').contains(ui.el('risksCard')));
  assert.equal(ui.el('risksCard').style.display, 'none');
  assert.equal(ui.el('interestedPartiesCard').style.display, 'none');
  assert.ok(!ui.calls.some((c) => /\/(risks|interested-parties)\?/.test(c.url)));
  assert.equal(ui.el('editClauseApplicability').style.display, 'none');
  assert.equal(ui.el('controlJustificationCard').style.display, 'none');
  await openRelated(ui);
  assert.ok([...ui.el('crossFrameworkView').options].some((o) => o.value === 'B'));
  assert.notEqual(ui.el('crossFrameworkForm').style.display, '');
  ui.el('controlGovernanceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() =>
    /do not have permission to view ISMS/.test(ui.el('controlIsmsView').textContent),
  );
  assert.ok(!ui.calls.some((c) => c.url.includes('/isms?')));
});

test('the governance pane loads ISMS and changelog on demand and updates the URL', async (t) => {
  const { ui } = await controlPage(t);
  const ismsCalls = () =>
    ui.calls.filter((c) => c.url.startsWith('/api/v1/controls/c1/isms?')).length;
  ui.el('controlGovernanceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('controlIsmsView').querySelector('table'));
  assert.match(ui.window.location.search, /tab=governance/);
  const rows = [...ui.el('controlIsmsView').querySelectorAll('tbody tr')];
  assert.deepEqual(
    rows.map((r) => r.cells[0].textContent),
    ['SWOT', 'Objectives', 'Organisation chart', 'Assets', 'Meetings', 'Meetings'],
  );
  assert.equal(ui.el('controlIsmsView').querySelector('b'), null);
  const hrefs = rows.map((r) => r.querySelector('a').getAttribute('href'));
  assert.match(hrefs[0], /^\/swot\.html\?id=s1&framework=A$/);
  assert.match(hrefs[4], /isms-meeting\.html\?id=mt1/);
  assert.match(hrefs[5], /isms\.html\?tab=meetings/);
  await settle(() => /Justification changed/.test(ui.el('controlChangelog').textContent));
  assert.equal(ui.el('controlChangelog').querySelector('b'), null);
  // A second activation reuses the loaded references.
  const before = ismsCalls();
  ui.el('controlGovernanceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(ismsCalls(), before);

  ui.el('controlEvidenceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=evidence/);
  // Requirements is the default tab, so the address drops the tab name.
  ui.el('controlRequirementsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.doesNotMatch(ui.window.location.search, /tab=/);
  ui.el('controlGovernanceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=governance/);
});

test('deep links to the ISMS and changelog tabs load their data with the page', async (t) => {
  const isms = await controlPage(t, { query: '&id=c1&tab=governance' });
  assert.ok(isms.ui.el('controlIsmsView').querySelector('table'));
  const log = await controlPage(t, { query: '&id=c1&tab=governance' });
  assert.match(log.ui.el('controlChangelog').textContent, /Justification changed/);
  const related = await controlPage(t, { query: '&id=c1&tab=related' });
  assert.ok([...related.ui.el('crossFrameworkView').options].some((o) => o.value === 'B'));
  // A second activation reuses the loaded links.
  const before = related.ui.calls.length;
  related.ui.el('controlRelatedTab').dispatchEvent(new related.ui.window.Event('shown.bs.tab'));
  assert.equal(related.ui.calls.length, before);
  related.ui.el('controlGovernanceTab').dispatchEvent(new related.ui.window.Event('shown.bs.tab'));
  assert.match(related.ui.window.location.search, /tab=governance/);
});

test('a reader deep-linked to the justification tab sees Requirements', async (t) => {
  const shown = [];
  await controlPage(t, {
    me: { can_view_controls: true },
    query: '&id=c1&tab=justification',
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  assert.deepEqual(shown, ['controlRequirementsTab']);
});

test('the tab bar shows four top-level tabs and the governance pane holds the rest', async (t) => {
  const { ui } = await controlPage(t);
  assert.deepEqual(
    [...ui.el('controlPageTabs').querySelectorAll('[role="tab"]')].map((b) => b.id),
    ['controlRequirementsTab', 'controlEvidenceTab', 'controlRelatedTab', 'controlGovernanceTab'],
  );
  for (const id of [
    'controlJustificationCard',
    'clausesCard',
    'adminClausesCard',
    'risksCard',
    'interestedPartiesCard',
    'controlChangelog',
  ]) {
    assert.ok(ui.el('controlGovernancePane').contains(ui.el(id)), id);
  }
  assert.ok(ui.el('controlIsmsView').closest('#controlGovernancePane'));
  assert.equal(ui.el('controlJustificationCard').style.display, '');
});

test('an unknown or inherited tab name in the address opens Requirements', async (t) => {
  for (const name of ['constructor', '__proto__', 'nothing', 'effectiveness']) {
    const shown = [];
    await controlPage(t, {
      query: `&id=c1&tab=${name}`,
      setup(window) {
        window.bootstrap = {
          Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
        };
      },
    });
    assert.deepEqual(shown, ['controlRequirementsTab'], name);
  }
});

test('evidence appears while slower sections are still loading', async (t) => {
  let releaseRisks;
  const risksPending = new Promise((resolve) => {
    releaseRisks = resolve;
  });
  const ui = await page(t, 'control', {
    query: '&id=c1&tab=evidence',
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/controls/c1') return baseControl();
      if (p === '/api/v1/controls/c1/risks') {
        await risksPending;
        return { items: [] };
      }
      if (p === '/api/v1/controls/c1/interested-parties') return { items: [] };
      if (p === '/api/v1/events/facets') return { sources: [{ source: 'github', count: 1 }] };
      if (p === '/api/v1/events')
        return {
          total: 1,
          items: [
            { id: 'e1', timestamp: '2026-02-01T00:00:00Z', source: 'github', summary: 'Push' },
          ],
        };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => /Push/.test(ui.el('rows').textContent));
  assert.equal(ui.el('resultMeta').textContent, '1-1 of 1');
  assert.equal(ui.el('risksCount').textContent, '0');
  releaseRisks();
  await settle(() => /No risks mapped/.test(ui.el('risksView').textContent));
});

test('evidence filters, paging and saved searches use the canonical control URL', async (t) => {
  let named = '';
  const { ui, state } = await controlPage(t, {
    globals: { prompt: (_, def) => ((named = def), 'My search') },
  });
  ui.el('localQ').value = '  deploy ';
  ui.el('localQ').dispatchEvent(new ui.window.Event('input'));
  await settle(() => lastEventsUrl(ui).searchParams.get('q') === 'deploy');
  ui.el('sourceFilter').value = 'github';
  ui.el('sourceFilter').dispatchEvent(new ui.window.Event('change'));
  await settle(() => lastEventsUrl(ui).searchParams.get('source') === 'github');
  ui.el('limit').value = '100';
  ui.el('limit').dispatchEvent(new ui.window.Event('change'));
  await settle(() => lastEventsUrl(ui).searchParams.get('limit') === '100');
  ui.el('next').click();
  await settle(() => lastEventsUrl(ui).searchParams.get('offset') === '100');
  assert.match(ui.window.location.search, /offset=100/);
  ui.el('prev').click();
  await settle(() => lastEventsUrl(ui).searchParams.get('offset') === '0');

  ui.el('saveSearch').click();
  await settle(() => state.posted.length === 1);
  assert.equal(named, 'A.5.1  <b>Policies</b> (q=deploy, source=github, limit=100)');
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/me/saved-searches',
    body: {
      name: 'My search',
      url: '/control.html?id=c1&framework=A&tab=evidence&q=deploy&source=github&limit=100',
    },
  });

  ui.el('clearFilters').click();
  await settle(() => !lastEventsUrl(ui).searchParams.has('q'));
  assert.equal(ui.el('localQ').value, '');
  assert.equal(lastEventsUrl(ui).searchParams.get('limit'), '50');
});

test('cancelling the saved-search prompt sends nothing', async (t) => {
  const { ui, state } = await controlPage(t, { globals: { prompt: () => '  ' } });
  ui.el('saveSearch').click();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(state.posted.length, 0);
});

test('a selected source with no remaining results stays selectable', async (t) => {
  const { ui } = await controlPage(t, {
    query: '&id=c1&tab=evidence&source=jira&limit=13&offset=-5',
  });
  assert.equal(ui.el('sourceFilter').value, 'jira');
  assert.match(ui.el('sourceFilter').selectedOptions[0].textContent, /jira \(0\)/);
  // Invalid limits and offsets are clamped before the first request.
  assert.equal(lastEventsUrl(ui).searchParams.get('limit'), '50');
  assert.equal(lastEventsUrl(ui).searchParams.get('offset'), '0');
});

test('administrators edit clause applicability with inferred parent values', async (t) => {
  const { ui, state } = await controlPage(t);
  assert.equal(ui.el('adminClausesCard').style.display, 'none');
  ui.el('editClauseApplicability').click();
  await settle(() => ui.el('adminClausesList').querySelector('select'));
  assert.equal(ui.el('editClauseApplicability').textContent, 'Hide edit');
  assert.equal(ui.el('controlUpstreamUrl').value, 'https://guidance.example/a51');
  const sel = (id) => ui.el('adminClausesList').querySelector(`[data-clause-id="${id}"]`);
  // Existing link values are selected; k2 is an intermediate parent, so its value
  // is inferred and its select stays blank.
  assert.equal(sel('k10').value, 'applicable');
  assert.equal(sel('k2').value, '');
  assert.equal(sel('k4').value, '');
  sel('k41').value = 'applicable';
  sel('k41').dispatchEvent(new ui.window.Event('change'));
  assert.equal(sel('k4').value, 'partially_applicable');
  sel('k421').value = 'applicable';
  sel('k422').value = 'applicable';
  sel('k422').dispatchEvent(new ui.window.Event('change'));
  assert.equal(sel('k4').value, 'applicable');
  assert.equal(sel('k2').value, '');
  sel('k422').value = 'partially_applicable';
  sel('k422').dispatchEvent(new ui.window.Event('change'));
  assert.equal(sel('k4').value, 'partially_applicable');

  ui.el('saveClauseLinks').click();
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/admin/controls/c1/clauses',
    body: {
      clauses: [
        { clause_id: 'k4', applicability: 'partially_applicable' },
        { clause_id: 'k41', applicability: 'applicable' },
        { clause_id: 'k421', applicability: 'applicable' },
        { clause_id: 'k422', applicability: 'partially_applicable' },
        { clause_id: 'k10', applicability: 'applicable' },
      ],
    },
  });
  await settle(() => ui.el('status').textContent === 'Clause applicability saved');
  await settle(() => ui.el('saveClauseLinks').disabled === false);
  ui.el('editClauseApplicability').click();
  assert.equal(ui.el('adminClausesCard').style.display, 'none');
  // The clause catalogue is fetched once.
  assert.equal(ui.calls.filter((c) => c.url.startsWith('/api/v1/clauses?')).length, 1);
});

test('a clause catalogue failure leaves edit mode closed with an error', async (t) => {
  const { ui } = await controlPage(t, { fail: { clauses: true } });
  ui.el('editClauseApplicability').click();
  await settle(() => /Failed to load clauses: Clauses offline/.test(ui.el('status').textContent));
  assert.equal(ui.el('editClauseApplicability').textContent, 'Edit');
  assert.equal(ui.el('adminClausesCard').style.display, 'none');
});

test('administrators save justification and guidance URL, refreshing the guidance link', async (t) => {
  const { ui, state } = await controlPage(t);
  ui.el('controlJustification').value = '';
  ui.el('saveControlJustification').click();
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/admin/controls/c1/justification',
    body: { justification: null },
  });
  await settle(() => ui.el('ctrlMeta').textContent === 'A · control · in scope');

  ui.el('editClauseApplicability').click();
  await settle(() => ui.el('adminClausesList').querySelector('select'));
  ui.el('controlUpstreamUrl').value = ' javascript:alert(1) ';
  ui.el('saveControlUpstreamUrl').click();
  await settle(() => state.patched.length === 2);
  assert.deepEqual(state.patched[1].body, { upstream_url: 'javascript:alert(1)' });
  await settle(() => ui.el('status').textContent === 'Control upstream URL saved');
  await settle(() => ui.el('saveControlUpstreamUrl').disabled === false);
  assert.equal(ui.el('ctrlLinks').querySelector('a'), null);
});

test('administrators link and unlink controls from another framework', async (t) => {
  const prompts = [];
  const { ui, state } = await controlPage(t, {
    confirm: (options) => (prompts.push(options), true),
  });
  await openRelated(ui);
  ui.el('crossFrameworkForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  assert.equal(state.posted.length, 0);
  ui.el('crossFrameworkSource').value = 'B';
  ui.el('crossFrameworkSource').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('crossFrameworkControl').options.length === 3);
  assert.equal(ui.el('crossFrameworkControl').querySelector('b'), null);
  assert.equal(ui.el('crossFrameworkControl').options[2].textContent, 'B8 · control');
  ui.el('crossFrameworkControl').value = 'b7';
  ui.el('crossFrameworkRationale').value = 'Same evidence';
  ui.el('crossFrameworkForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/controls/c1/cross-framework',
    body: { source_control_id: 'b7', rationale: 'Same evidence' },
  });
  await settle(() => /Control link saved/.test(ui.el('status').textContent));
  assert.equal(ui.el('crossFrameworkRationale').value, '');
  // The selected framework survives the refresh.
  assert.equal(ui.el('crossFrameworkSource').value, 'B');

  ui.el('crossFrameworkView').value = 'B';
  ui.el('crossFrameworkView').dispatchEvent(new ui.window.Event('change'));
  ui.el('crossFrameworkLinks').querySelector('[data-remove-cross-link]').click();
  await settle(() => state.removed.length === 1);
  assert.deepEqual(state.removed, ['/api/v1/controls/c1/cross-framework/x1']);
  assert.equal(prompts[0].title, 'Remove inherited evidence relationship');
  await settle(() => /Control link removed/.test(ui.el('status').textContent));
});

test('control load failures and evidence failures are reported', async (t) => {
  const broken = await controlPage(t, { fail: { control: true } });
  await settle(() =>
    /Failed to load control: Control offline/.test(broken.ui.el('status').textContent),
  );
  assert.equal(broken.ui.el('resultMeta').textContent, '-');

  const events = await controlPage(t, { fail: { events: true } });
  await settle(() => /Failed to load evidence/.test(events.ui.el('status').textContent));
  assert.match(events.ui.el('rows').textContent, /Failed to load evidence/);

  const parties = await controlPage(t, { fail: { parties: true } });
  assert.match(
    parties.ui.el('interestedPartiesView').textContent,
    /Failed to load interested parties/,
  );
  assert.equal(parties.ui.el('interestedPartiesCount').textContent, '0');
});

test('a control page without an id explains the missing parameter', async (t) => {
  const ui = await page(t, 'control', {
    get: async (u) => {
      throw new Error('Unexpected GET ' + u);
    },
  });
  assert.equal(ui.el('status').textContent, 'Missing control id.');
});

test('a control with nothing linked says so in every tab', async (t) => {
  const { ui } = await controlPage(t, {
    control: baseControl({ clauses: [] }),
    empty: { risks: true, parties: true, events: true, clauseTree: true },
    fail: { facets: true },
  });
  assert.equal(ui.el('clausesView').textContent, 'No clauses linked to this control.');
  assert.equal(ui.el('clausesCount').textContent, '0');
  assert.equal(ui.el('rows').textContent, 'No evidence matches your filters.');
  await settle(() => /No risks mapped/.test(ui.el('risksView').textContent));
  assert.equal(ui.el('risksCount').textContent, '0');
  ui.el('controlGovernanceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() =>
    /No interested parties mapped/.test(ui.el('interestedPartiesView').textContent),
  );
  // Without the facets the source filter keeps whatever it had.
  assert.equal(ui.el('status').dataset.alert, undefined);
  ui.el('editClauseApplicability').click();
  await settle(() => /No clauses are loaded/.test(ui.el('adminClausesList').textContent));
  assert.equal(ui.el('adminClausesList').querySelector('select'), null);
});

test('failed or forbidden link lookups are contained in their own panels', async (t) => {
  const { ui } = await controlPage(t, {
    fail: { risks: true, isms: true, changelog: true, cross: true },
  });
  await settle(() => ui.el('risksCard').style.display === 'none');
  await openRelated(ui);
  assert.equal(
    ui.el('crossFrameworkLinks').textContent,
    'Could not load control links: Links offline',
  );
  ui.el('controlGovernanceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => /Failed to load ISMS references/.test(ui.el('controlIsmsView').textContent));
  await settle(() => /Failed to load changelog/.test(ui.el('controlChangelog').textContent));
  // The evidence table is unaffected.
  assert.match(ui.el('rows').textContent, /pushed/);
});

test('refused saves and link changes are reported and leave the controls usable', async (t) => {
  const { ui, state } = await controlPage(t, {
    fail: { patch: true, post: true, remove: true, sourceControls: true },
    globals: { confirm: () => true },
  });
  ui.el('saveControlJustification').click();
  await settle(() => ui.el('status').textContent === 'Failed to save justification: Patch refused');
  assert.equal(ui.el('saveControlJustification').disabled, false);

  ui.el('editClauseApplicability').click();
  await settle(() => ui.el('adminClausesList').querySelector('select'));
  ui.el('saveClauseLinks').click();
  await settle(() => ui.el('status').textContent === 'Failed to save clauses: Patch refused');
  assert.equal(ui.el('saveClauseLinks').disabled, false);
  ui.el('controlUpstreamUrl').value = 'https://guidance.example/new';
  ui.el('saveControlUpstreamUrl').click();
  await settle(() => ui.el('status').textContent === 'Failed to save control URL: Patch refused');
  assert.equal(ui.el('saveControlUpstreamUrl').disabled, false);
  assert.equal(state.patched.length, 3);

  await openRelated(ui);
  ui.el('crossFrameworkSource').value = 'B';
  ui.el('crossFrameworkSource').dispatchEvent(new ui.window.Event('change'));
  await settle(() => /Could not load source controls/.test(ui.el('status').textContent));
  assert.equal(ui.el('crossFrameworkControl').options.length, 1);
  ui.el('crossFrameworkControl').add(new ui.window.Option('B7', 'b7'));
  ui.el('crossFrameworkControl').value = 'b7';
  ui.el('crossFrameworkForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Could not link controls: Post refused');
  ui.el('crossFrameworkView').value = 'B';
  ui.el('crossFrameworkView').dispatchEvent(new ui.window.Event('change'));
  ui.el('crossFrameworkLinks').querySelector('[data-remove-cross-link]').click();
  await settle(
    () => ui.el('status').textContent === 'Could not remove control link: Delete refused',
  );
  assert.deepEqual(state.removed, ['/api/v1/controls/c1/cross-framework/x1']);
});

test('a saved guidance URL becomes the Guidance link, and a failed saved search is reported', async (t) => {
  const { ui, state } = await controlPage(t, {
    control: baseControl({ upstream_url: null }),
    globals: { prompt: () => 'My search' },
  });
  assert.equal(ui.el('ctrlLinks').querySelector('a'), null);
  ui.el('editClauseApplicability').click();
  await settle(() => ui.el('adminClausesList').querySelector('select'));
  ui.el('controlUpstreamUrl').value = 'https://guidance.example/a51 ';
  ui.el('saveControlUpstreamUrl').click();
  await settle(() => ui.el('ctrlLinks').querySelector('a'));
  assert.equal(state.patched[0].body.upstream_url, 'https://guidance.example/a51');
  const guidance = ui.el('ctrlLinks').querySelector('a');
  assert.equal(guidance.getAttribute('href'), 'https://guidance.example/a51');
  assert.equal(guidance.getAttribute('rel'), 'noopener noreferrer');
  assert.equal(guidance.textContent.trim(), 'Guidance');

  const refused = await controlPage(t, {
    fail: { post: true },
    globals: { prompt: () => 'My search' },
  });
  refused.ui.el('saveSearch').click();
  await settle(() => refused.state.posted.length === 1);
  await settle(() => refused.ui.el('status').textContent === 'Failed to save: Post refused');
  assert.equal(refused.ui.el('status').dataset.alert, 'danger');
});

test('a deep-linked tab is shown through Bootstrap when it is available', async (t) => {
  const shown = [];
  const { ui } = await controlPage(t, {
    query: '&id=c1&tab=governance',
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  assert.deepEqual(shown, ['controlGovernanceTab']);
  assert.ok(ui.el('controlIsmsView').querySelector('table'));
});

const requirementsText = (ui) => ui.el('controlRequirements').textContent.replace(/\s+/g, ' ');
const tileTexts = (ui) =>
  [...ui.el('controlRequirements').querySelectorAll('.kpi')].map((tile) =>
    tile.textContent.replace(/\s+/g, ' ').trim(),
  );

test('Requirements opens first and assembles policies, evidence and measures', async (t) => {
  const { ui } = await controlPage(t, { query: '&id=c1' });
  const root = ui.el('controlRequirements');
  assert.doesNotMatch(ui.window.location.search, /tab=/);
  // Untrusted titles, descriptions and summaries stay text.
  assert.equal(root.querySelector('img, b'), null);
  assert.deepEqual(tileTexts(ui), [
    'Policies and processes: In place 4 linked to this control',
    'Evidence: In place 41 events 3 sources, latest 2026-03-05T09:00:00',
    'Measures and tests: Needs attention 4 1 of 2 meeting target; 1 without a result',
  ]);
  assert.deepEqual(
    [...root.querySelectorAll('.kpi')].map((tile) => tile.getAttribute('href')),
    ['#requirementsPolicies', '#requirementsEvidence', '#requirementsMeasures'],
  );

  const policies = [...root.querySelectorAll('#requirementsPolicies tbody tr')];
  assert.deepEqual(
    policies.map((row) => [...row.cells].map((cell) => cell.textContent.trim())),
    [
      ['<img src=x>Access policy', 'Policy', 'Written in Keen', 'v3', '2026-02-01T00:00:00Z'],
      ['Leavers process', 'Process', 'leavers.pdf', '-', '-'],
      ['Document', 'Record', 'Link', '-', '-'],
      ['Draft', 'Document', '-', '-', '-'],
    ],
  );
  assert.equal(
    policies[0].querySelector('a').getAttribute('href'),
    '/isms-document.html?id=d1&framework=A',
  );

  const sources = [...root.querySelectorAll('#requirementsEvidence tbody tr')];
  assert.deepEqual(
    sources.map((row) => row.cells[0].querySelector('.fw-semibold').textContent),
    ['jenkins', 'diary', 'Unknown source'],
  );
  assert.equal(
    sources[1].cells[0].querySelector('.small-muted').textContent,
    '1 entry recorded when a record was linked',
  );
  assert.equal(
    sources[0].querySelector('a').getAttribute('href'),
    '/control.html?id=c1&tab=evidence&source=jenkins&framework=A',
  );
  const actions = Object.fromEntries(
    [...root.querySelectorAll('#requirementsEvidence .card-header a')].map((a) => [
      a.textContent,
      a.getAttribute('href'),
    ]),
  );
  assert.deepEqual(actions, {
    'Add diary evidence': '/admin.html?control=c1&framework=A#diary',
    'Open evidence': '/control.html?id=c1&tab=evidence&framework=A',
  });

  const measures = [...root.querySelectorAll('#requirementsMeasures tbody tr')];
  assert.deepEqual(
    measures.map((row) => [
      row.cells[1].firstElementChild.textContent,
      row.cells[1].querySelector('.eff-metric-threshold-label')?.textContent || '',
    ]),
    [
      ['100 %', 'Meets target'],
      ['82 %', 'Outside target'],
      ['No result yet', ''],
      ['Completed', ''],
    ],
  );
  assert.match(measures[0].cells[0].textContent, /<b>Disposal certificates<\/b>\s*Monthly/);
  assert.equal(
    measures[0].querySelector('a').getAttribute('href'),
    '/isms-effectiveness-measure.html?id=m1&framework=A',
  );
  assert.equal(
    measures[2].querySelector('td:last-child a').getAttribute('href'),
    '/isms.html?tab=effectiveness&record=m3&framework=A',
  );

  // The evidence list waits for its tab, and the ISMS section reuses the
  // references Requirements read.
  assert.equal(
    ui.calls.some((c) => c.url.startsWith('/api/v1/events?')),
    false,
  );
  ui.el('controlGovernanceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('controlIsmsView').querySelector('table'));
  assert.equal(ui.calls.filter((c) => c.url.startsWith('/api/v1/controls/c1/isms?')).length, 1);
  ui.el('controlEvidenceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => /pushed/.test(ui.el('rows').textContent));
  assert.match(ui.window.location.search, /tab=evidence/);
  // Opening Requirements again reuses what it loaded.
  const before = ui.calls.length;
  ui.el('controlRequirementsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.calls.length, before);
});

test('a source count opens the evidence list filtered to that source', async (t) => {
  // An earlier search in the address leaves the list once a count is chosen.
  const { ui } = await controlPage(t, { query: '&id=c1&q=deploy' });
  assert.equal(ui.el('localQ').value, 'deploy');
  const link = () => ui.el('controlRequirements').querySelector('a[data-open-evidence="jenkins"]');
  // A click with a modifier opens the link in the usual way.
  const opened = new ui.window.MouseEvent('click', {
    bubbles: true,
    cancelable: true,
    ctrlKey: true,
  });
  link().dispatchEvent(opened);
  assert.equal(opened.defaultPrevented, false);
  assert.equal(
    ui.calls.some((c) => c.url.startsWith('/api/v1/events?')),
    false,
  );

  const plainClick = new ui.window.MouseEvent('click', { bubbles: true, cancelable: true });
  link().dispatchEvent(plainClick);
  assert.equal(plainClick.defaultPrevented, true);
  await settle(() => /pushed/.test(ui.el('rows').textContent));
  assert.equal(lastEventsUrl(ui).searchParams.get('source'), 'jenkins');
  assert.equal(lastEventsUrl(ui).searchParams.has('q'), false);
  assert.match(ui.window.location.search, /source=jenkins/);
  assert.doesNotMatch(ui.window.location.search, /q=/);
  assert.equal(ui.el('localQ').value, '');
  assert.equal(ui.el('sourceFilter').value, 'jenkins');

  // With the list loaded, Open evidence clears the source filter and reloads.
  const all = ui.el('controlRequirements').querySelector('a[data-open-evidence=""]');
  all.dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true, cancelable: true }));
  await settle(() => !lastEventsUrl(ui).searchParams.has('source'));
  assert.doesNotMatch(ui.window.location.search, /source=/);
});

test('each reader sees the requirement parts they can read', async (t) => {
  const evidenceOnly = await controlPage(t, { query: '&id=c1', me: { can_view_events: true } });
  const text = requirementsText(evidenceOnly.ui);
  assert.match(text, /You do not have permission to view policies and processes/);
  assert.match(text, /You do not have permission to view effectiveness measures/);
  assert.ok(!evidenceOnly.ui.calls.some((c) => /\/(isms|effectiveness-measures)\?/.test(c.url)));
  assert.deepEqual(
    tileTexts(evidenceOnly.ui).map((tile) => tile.split(':')[0]),
    ['Policies and processes', 'Evidence', 'Measures and tests'],
  );
  assert.match(tileTexts(evidenceOnly.ui)[0], /Not available - Not available/);
  // Only administrators add diary evidence, and ISMS managers record results.
  const root = evidenceOnly.ui.el('controlRequirements');
  assert.deepEqual(
    [...root.querySelectorAll('.card-header a')].map((a) => a.textContent),
    ['Open evidence'],
  );

  const ismsReader = await controlPage(t, { query: '&id=c1', me: { can_view_isms: true } });
  assert.match(requirementsText(ismsReader.ui), /You do not have permission to view evidence/);
  assert.ok(!ismsReader.ui.calls.some((c) => c.url.includes('/evidence/summary')));
  assert.equal(ismsReader.ui.el('controlRequirements').querySelector('a[href*="record="]'), null);
});

test('switched-off features leave their requirement parts out', async (t) => {
  const features = (enabled) => ({ is_admin: true, features: { enabled } });
  const evidence = await controlPage(t, {
    query: '&id=c1',
    me: features({ documents: false, effectiveness: false }),
  });
  assert.equal(evidence.ui.el('requirementsPolicies'), null);
  assert.equal(evidence.ui.el('requirementsMeasures'), null);
  assert.ok(evidence.ui.el('requirementsEvidence'));
  assert.deepEqual(tileTexts(evidence.ui).length, 1);
  assert.ok(!evidence.ui.calls.some((c) => /\/(isms|effectiveness-measures)\?/.test(c.url)));

  const none = await controlPage(t, {
    query: '&id=c1',
    me: features({ isms: false, evidence: false }),
  });
  assert.equal(
    requirementsText(none.ui),
    'Policies, evidence and measures are switched off for this installation.',
  );
});

test('requirement load failures stay in their own sections', async (t) => {
  const { ui } = await controlPage(t, {
    query: '&id=c1',
    fail: { isms: true, summary: true, measures: true },
  });
  const text = requirementsText(ui);
  assert.match(text, /Failed to load linked policies and processes/);
  assert.match(text, /Failed to load the evidence summary/);
  assert.match(text, /Failed to load linked effectiveness measures/);
  assert.equal(tileTexts(ui).filter((tile) => /Not available/.test(tile)).length, 3);
  // A failed reference load is tried again when the governance tab opens.
  ui.el('controlGovernanceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => /Failed to load ISMS references/.test(ui.el('controlIsmsView').textContent));
  assert.equal(ui.calls.filter((c) => c.url.startsWith('/api/v1/controls/c1/isms?')).length, 2);
});

test('a control with nothing linked asks for each requirement', async (t) => {
  const { ui } = await controlPage(t, {
    query: '&id=c1',
    empty: { documents: true, summary: true, measures: true },
  });
  const text = requirementsText(ui);
  assert.match(text, /No policies or processes are linked yet/);
  assert.match(text, /No evidence is mapped to this control yet/);
  assert.match(text, /No effectiveness measures are linked yet/);
  assert.deepEqual(tileTexts(ui), [
    'Policies and processes: Needs attention 0 None linked yet',
    'Evidence: Needs attention 0 events None mapped yet',
    'Measures and tests: Needs attention 0 None linked yet',
  ]);
});

test('measures with every result on target show as in place', async (t) => {
  const onTarget = {
    id: 'm1',
    summary: 'Restore test',
    target_value: 1,
    threshold_operator: 'gte',
    latest_entry: { metric_value: 1, recorded_at: '2026-03-01T00:00:00Z' },
  };
  const met = await controlPage(t, { query: '&id=c1', measures: [onTarget] });
  assert.equal(tileTexts(met.ui)[2], 'Measures and tests: In place 1 1 of 1 meeting target');
  // Results without a numeric target are listed without a comparison.
  const described = await controlPage(t, {
    query: '&id=c1',
    measures: [{ id: 'm5', summary: 'Review', latest_entry: { qualitative_value: 'Done' } }],
  });
  assert.equal(
    tileTexts(described.ui)[2],
    'Measures and tests: In place 1 No results compared with a target',
  );
});

test('a tab chosen while the control loads fills once the control arrives', async (t) => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const { ui } = await controlPage(t, { query: '&id=c1', gate });
  ui.el('controlEvidenceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=evidence/);
  release();
  await settle(() => /pushed/.test(ui.el('rows').textContent));
  // Requirements waits for its own tab.
  assert.equal(
    ui.calls.some((c) => c.url.includes('/evidence/summary')),
    false,
  );
});

test('linking or unlinking a control reloads Requirements and evidence when next shown', async (t) => {
  const { ui } = await controlPage(t, {
    query: '&id=c1',
    globals: { confirm: () => true },
  });
  const count = (part) => ui.calls.filter((c) => c.url.includes(part)).length;
  ui.el('controlEvidenceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => /pushed/.test(ui.el('rows').textContent));
  assert.deepEqual([count('/evidence/summary'), count('/api/v1/events?')], [1, 1]);

  await openRelated(ui);
  ui.el('crossFrameworkSource').value = 'B';
  ui.el('crossFrameworkSource').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('crossFrameworkControl').options.length === 3);
  ui.el('crossFrameworkControl').value = 'b7';
  ui.el('crossFrameworkForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Control link saved/.test(ui.el('status').textContent));
  await settle(() => ui.idle());
  // Nothing reloads behind the Related controls tab.
  assert.deepEqual([count('/evidence/summary'), count('/api/v1/events?')], [1, 1]);
  ui.el('controlRequirementsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => count('/evidence/summary') === 2);
  ui.el('controlEvidenceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => count('/api/v1/events?') === 2);

  ui.el('crossFrameworkView').value = 'B';
  ui.el('crossFrameworkView').dispatchEvent(new ui.window.Event('change'));
  ui.el('crossFrameworkLinks').querySelector('[data-remove-cross-link]').click();
  await settle(() => /Control link removed/.test(ui.el('status').textContent));
  ui.el('controlRequirementsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => count('/evidence/summary') === 3);
});

test('the ISMS tab names what it lists when only policies or measures are linked', async (t) => {
  const { ui } = await controlPage(t, {
    query: '&id=c1&tab=governance',
    empty: { ismsRecords: true },
  });
  await settle(() => !/Loading/.test(ui.el('controlIsmsView').textContent));
  assert.equal(
    ui.el('controlIsmsView').textContent,
    'No SWOT items, objectives, organisation roles, assets or meetings are linked to this control.',
  );
});
