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
  const ui = await page(t, 'control', {
    query: options.query ?? '&id=c1',
    me: options.me || { is_admin: true },
    globals: options.globals,
    frameworks: [
      { slug: 'A', name: 'Framework A' },
      { slug: 'B', name: 'Framework <b>B</b>' },
    ],
    setup(window) {
      window.CSS = { escape: (v) => String(v).replace(/[^a-zA-Z0-9_-]/g, (c) => '\\' + c) };
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/controls/c1') {
        if (fail.control) throw new Error('Control offline');
        return structuredClone(state.control);
      }
      if (p === '/api/v1/controls/c1/cross-framework')
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
      if (p === '/api/v1/controls' && u.searchParams.get('framework') === 'B')
        return {
          items: [
            { id: 'b7', ref: 'B7', title: '<b>Backup</b>' },
            { id: 'b8', ref: 'B8', type: 'control' },
          ],
        };
      if (p === '/api/v1/controls/c1/risks')
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
      if (p === '/api/v1/controls/c1/interested-parties') {
        if (fail.parties) throw new Error('IP offline');
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
      if (p === '/api/v1/controls/c1/isms')
        return {
          swot: [{ id: 's1', title: 'Strength <b>x</b>', updated_at: '2026-01-01' }],
          objectives: [{ requirement: 'Reduce incidents' }],
          documents: [{ title: 'Policy' }],
          org_nodes: [{}],
          assets: [{ asset: 'Laptop' }],
          effectiveness_measures: [{ id: 'm1', metric: 'MTTR' }],
          meetings: [{ id: 'mt1', title: 'Review' }, { title: 'No id' }],
        };
      if (p === '/api/v1/controls/c1/changelog')
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
      if (p === '/api/v1/clauses') {
        if (fail.clauses) throw new Error('Clauses offline');
        return clauseTree;
      }
      if (p === '/api/v1/events/facets')
        return {
          sources: [
            { source: 'github', count: 2 },
            { source: 'aws', count: 5 },
            { source: 'none', count: 0 },
          ],
        };
      if (p === '/api/v1/events') {
        if (fail.events) throw new Error('Events offline');
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
      if (u.pathname.endsWith('/justification')) state.control.justification = b.justification;
      if (u.pathname.endsWith('/upstream-url')) state.control.upstream_url = b.upstream_url;
      return { ok: true };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      return { ok: true };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      return { ok: true };
    },
  });
  await settle(
    () => ui.calls.some((c) => c.url.startsWith('/api/v1/events?')) || ui.el('status').textContent,
  );
  await new Promise((r) => setTimeout(r, 10));
  return { ui, state };
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
    'A • control • in scope • Justification: Best Practice',
  );
  const description = ui.el('osaControlDescription');
  assert.equal(description.querySelector('script'), null);
  assert.match(description.textContent, /Line one\n<script>alert\(1\)<\/script>/);
  assert.match(description.textContent, /Open Security Architecture/);
  assert.equal(ui.el('ctrlLinks').querySelector('a').href, 'https://guidance.example/a51');
  assert.match(
    ui.el('viewAllEvents').getAttribute('href'),
    /events\.html\?control=A\.5\.1&framework=A/,
  );
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
  assert.equal(ui.el('resultMeta').textContent, '1–50 of 120');
  assert.deepEqual(
    [...ui.el('sourceFilter').options].map((o) => o.value),
    ['', 'aws', 'github'],
  );
  assert.match(ui.el('sourceFilter').options[1].textContent, /aws \(5\)/);
  // Administrators see the justification and cross-framework link controls.
  assert.equal(ui.el('controlJustificationCard').style.display, '');
  assert.equal(ui.el('controlJustification').value, 'Best Practice');
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
  assert.equal(ui.el('ctrlMeta').textContent, 'A • control • in scope');
  assert.equal(ui.el('osaControlDescription').textContent, '');
});

test('the KEEN Assurance Framework attribution is shown for its controls', async (t) => {
  const { ui } = await controlPage(t, {
    control: baseControl({ framework: 'KEEN-AF:1.0', metadata: {} }),
  });
  assert.match(ui.el('osaControlDescription').textContent, /Vanta’s controls/);
});

test('readers without risk or ISMS permission do not request restricted links', async (t) => {
  const { ui } = await controlPage(t, { me: { can_view_controls: true } });
  assert.ok(ui.el('controlRisksTabItem').classList.contains('d-none'));
  assert.ok(ui.el('controlInterestedPartiesTabItem').classList.contains('d-none'));
  assert.equal(ui.el('risksCard').style.display, 'none');
  assert.equal(ui.el('interestedPartiesCard').style.display, 'none');
  assert.ok(!ui.calls.some((c) => /\/(risks|interested-parties)\?/.test(c.url)));
  assert.equal(ui.el('editClauseApplicability').style.display, 'none');
  assert.equal(ui.el('controlJustificationCard').style.display, 'none');
  assert.notEqual(ui.el('crossFrameworkForm').style.display, '');
  ui.el('controlIsmsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() =>
    /do not have permission to view ISMS/.test(ui.el('controlIsmsView').textContent),
  );
  assert.ok(!ui.calls.some((c) => c.url.includes('/isms?')));
});

test('ISMS and changelog tabs load on demand and update the URL', async (t) => {
  const { ui } = await controlPage(t);
  ui.el('controlIsmsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('controlIsmsView').querySelector('table'));
  assert.match(ui.window.location.search, /tab=isms/);
  const rows = [...ui.el('controlIsmsView').querySelectorAll('tbody tr')];
  assert.deepEqual(
    rows.map((r) => r.cells[0].textContent),
    [
      'SWOT',
      'Objectives',
      'Documents',
      'Organisation Chart',
      'Assets',
      'Effectiveness Measures',
      'Meetings',
      'Meetings',
    ],
  );
  assert.equal(ui.el('controlIsmsView').querySelector('b'), null);
  const hrefs = rows.map((r) => r.querySelector('a').getAttribute('href'));
  assert.match(hrefs[0], /^\/swot\.html\?id=s1&framework=A$/);
  assert.match(hrefs[5], /isms-effectiveness-measure\.html\?id=m1/);
  assert.match(hrefs[6], /isms-meeting\.html\?id=mt1/);
  assert.match(hrefs[7], /isms\.html\?tab=meetings/);
  // A second activation reuses the loaded references.
  const before = ui.calls.length;
  ui.el('controlIsmsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.calls.length, before);

  ui.el('controlChangelogTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => /Justification changed/.test(ui.el('controlChangelog').textContent));
  assert.match(ui.window.location.search, /tab=changelog/);
  assert.equal(ui.el('controlChangelog').querySelector('b'), null);
  ui.el('controlEvidenceTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.doesNotMatch(ui.window.location.search, /tab=/);
  ui.el('controlInterestedPartiesTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=interested-parties/);
});

test('deep links to the ISMS and changelog tabs load their data with the page', async (t) => {
  const isms = await controlPage(t, { query: '&id=c1&tab=isms' });
  assert.ok(isms.ui.el('controlIsmsView').querySelector('table'));
  const log = await controlPage(t, { query: '&id=c1&tab=changelog' });
  assert.match(log.ui.el('controlChangelog').textContent, /Justification changed/);
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
      url: '/control.html?id=c1&framework=A&q=deploy&source=github&limit=100',
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
  const { ui } = await controlPage(t, { query: '&id=c1&source=jira&limit=13&offset=-5' });
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
  await settle(() =>
    /Failed to load clauses: Error: Clauses offline/.test(ui.el('status').textContent),
  );
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
  await settle(() => ui.el('ctrlMeta').textContent === 'A • control • in scope');

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
    globals: { confirm: (m) => (prompts.push(m), true) },
  });
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
  assert.match(prompts[0], /Remove inherited evidence relationship/);
  await settle(() => /Control link removed/.test(ui.el('status').textContent));
});

test('control load failures and evidence failures are reported', async (t) => {
  const broken = await controlPage(t, { fail: { control: true } });
  await settle(() =>
    /Failed to load control: Error: Control offline/.test(broken.ui.el('status').textContent),
  );
  assert.equal(broken.ui.el('resultMeta').textContent, '—');

  const events = await controlPage(t, { fail: { events: true } });
  await settle(() => /Failed to load evidence/.test(events.ui.el('status').textContent));
  assert.match(events.ui.el('rows').textContent, /Failed to load evidence/);

  const parties = await controlPage(t, { fail: { parties: true } });
  assert.match(
    parties.ui.el('interestedPartiesView').textContent,
    /Failed to load Interested Parties/,
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
