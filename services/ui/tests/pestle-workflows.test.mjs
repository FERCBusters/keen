import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3src = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');
const plain = (v) => JSON.parse(JSON.stringify(v));
const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';

const meta = {
  types: ['Legal', 'Social'],
  lenses: ['Internal', 'External'],
  relevance_levels: [
    { id: 1, code: 'high', label: 'High' },
    { id: 2, code: 'medium', label: 'Medium' },
    { id: 3, code: 'low', label: 'Low' },
    { id: 4, code: 'na', label: 'N/A' },
  ],
};
const items = [
  {
    id: 'p1',
    type: 'Legal',
    lens: 'External',
    item: HOSTILE + 'Privacy law',
    overall_relevance: { code: 'high', label: 'High' },
    rationale: 'Fines',
    business_process_count: 1,
    clause_count: 2,
  },
  {
    id: 'p2',
    type: 'Social',
    lens: 'Internal',
    item: 'Hybrid work',
    overall_relevance: { code: 'medium', label: 'Medium' },
    business_process_count: 2,
    clause_count: 1,
  },
  { id: 'p3', type: 'Social', lens: 'Internal', item: 'Skills', overall_relevance: 'low' },
  {
    id: 'p4',
    type: 'Legal',
    lens: 'Internal',
    item: 'Contracts',
    overall_relevance: { code: 'na' },
  },
];
const longLabel =
  'A very long PESTLE(E) item name describing several overlapping obligations across many jurisdictions, contracts, regulators and supply chain partners in every region';
const visualisations = {
  types: ['Legal', 'Social'],
  lenses: ['Internal', 'External'],
  lens_type_counts: { External: { Legal: 1 }, Internal: { Social: 2, Legal: 1 } },
  relevance_counts: [
    { code: 'high', label: 'High', count: 1 },
    { code: 'medium', label: 'Medium', count: 1 },
    { code: 'low', count: 1 },
  ],
  clause_links: [
    {
      item_id: 'p1',
      clause_id: 'cl1',
      clause_ref: '4.1',
      clause_title: 'Context',
      item: HOSTILE + 'Privacy law',
      type: 'Legal',
      lens: 'External',
      relevance_code: 'high',
      relevance_label: 'High',
    },
    {
      item_id: 'p2',
      clause_id: 'cl1',
      clause_ref: '4.1',
      clause_title: 'Context',
      item: longLabel,
      type: 'Social',
      lens: 'Internal',
      relevance_code: 'low',
    },
    {
      item_id: 'p2',
      clause_id: 'cl10',
      clause_ref: '4.10',
      clause_title: 'Interested parties',
      item: longLabel,
      item_type: 'Social',
      lens: 'Internal',
      relevance_code: 'medium',
      relevance_label: 'Medium',
    },
    { item_id: '', clause_id: 'cl2', relevance_code: 'high' },
  ],
};

async function pestlePage(t, options = {}) {
  const state = {
    itemQueries: [],
    posted: [],
    removed: [],
    processes: structuredClone(options.processes ?? [{ id: 'b1', name: '<b>Payroll</b>' }]),
  };
  const ui = await page(t, 'pestle', {
    query: options.query || '',
    me: options.me || { is_admin: true },
    scripts: options.noD3 ? [] : [d3src, 'window.d3 = d3;'],
    globals: { confirm: options.confirm ?? (() => true), ...options.globals },
    setup(window) {
      if (options.tabs) window.bootstrap = options.tabs;
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/pestle/meta') {
        if (options.metaFail) throw new Error('Meta offline');
        return structuredClone(meta);
      }
      if (p === '/api/v1/pestle/items') {
        state.itemQueries.push(Object.fromEntries(u.searchParams));
        if (options.itemsFail?.(state.itemQueries.length)) throw new Error('Items offline');
        return {
          items: structuredClone(options.items ?? items),
          total: (options.items ?? items).length,
          framework: 'A',
        };
      }
      if (p === '/api/v1/pestle/business-processes')
        return { items: structuredClone(state.processes) };
      if (p === '/api/v1/pestle/visualisations') {
        if (options.vizFail) throw new Error('Viz offline');
        return structuredClone(options.visualisations ?? visualisations);
      }
      if (p === '/api/v1/audits') return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, body) => {
      state.posted.push({ path: u.pathname, body: plain(body) });
      if (options.postFail) throw new Error('Name taken');
      if (u.pathname === '/api/v1/pestle/business-processes')
        state.processes.push({ id: 'b9', name: body.name });
      return { ok: true };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.removeFail) throw new Error('In use');
      state.processes = state.processes.filter(
        (bp) => decodeURIComponent(u.pathname.split('/').pop()) !== bp.id,
      );
      return { ok: true };
    },
  });
  await settle(
    () =>
      state.itemQueries.length ||
      ui.el('status').textContent ||
      /permission/.test(ui.el('pestleRows').textContent),
  );
  await new Promise((r) => setTimeout(r, 10));
  return { ui, state };
}

test('the PESTLE(E) register lists items safely with relevance badges and counts', async (t) => {
  const { ui, state } = await pestlePage(t);
  const rows = [...ui.el('pestleRows').querySelectorAll('tr')];
  assert.equal(rows.length, 4);
  assert.equal(ui.el('pestleRows').querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
  assert.equal(rows[0].querySelector('a').textContent, HOSTILE + 'Privacy law');
  assert.equal(
    rows[0].querySelector('a').getAttribute('href'),
    '/pestle_item.html?id=p1&tab=details&framework=A',
  );
  const badges = rows.map((r) => r.querySelectorAll('td')[3].querySelector('.badge'));
  assert.deepEqual(
    badges.map((b) => [b.textContent, b.className]),
    [
      ['High', 'badge text-bg-danger'],
      ['Medium', 'badge text-bg-warning'],
      ['Low', 'badge text-bg-success'],
      ['N/A', 'badge text-bg-light'],
    ],
  );
  assert.match(rows[0].textContent, /1 process\b/);
  assert.match(rows[0].textContent, /2 clauses/);
  assert.match(rows[1].textContent, /2 processes/);
  assert.match(rows[1].textContent, /1 clause\b/);
  assert.equal(rows[1].cells[4].textContent.trim(), '-');
  assert.equal(rows[0].querySelector('.btn-outline-primary').textContent, 'Edit');
  assert.ok(rows[0].querySelector('[data-sample-pestle="p1"]'));
  assert.equal(ui.el('pestleItemsMeta').textContent, '4 PESTLE(E) items for A.');
  assert.deepEqual(state.itemQueries[0], { framework: 'A', limit: '5000' });
  assert.equal(ui.el('newPestleItem').classList.contains('d-none'), false);
});

test('filters and search send CSV parameters, and reload reports failures', async (t) => {
  let offline = false;
  const { ui, state } = await pestlePage(t, { itemsFail: () => offline });
  const pick = (value) =>
    ui.document.querySelector(`.keen-filter-pill-grid [data-value="${value}"]`).click();
  pick('Legal');
  pick('Social');
  await settle(() => state.itemQueries.some((q) => q.type === 'Legal,Social'));
  pick('External');
  await settle(() => state.itemQueries.some((q) => q.lens === 'External'));
  pick('high');
  await settle(() => state.itemQueries.some((q) => q.relevance_code === 'high'));
  ui.el('pestleQ').value = '  privacy ';
  ui.el('pestleQ').dispatchEvent(new ui.window.Event('input'));
  await settle(() => state.itemQueries.at(-1).q === 'privacy');
  assert.deepEqual(state.itemQueries.at(-1), {
    framework: 'A',
    q: 'privacy',
    type: 'Legal,Social',
    lens: 'External',
    relevance_code: 'high',
    limit: '5000',
  });
  offline = true;
  ui.el('reloadPestle').click();
  await settle(
    () => ui.el('status').textContent === 'Failed to load PESTLE(E) items: Error: Items offline',
  );
  assert.equal(ui.el('status').dataset.alert, 'danger');
});

test('an empty register says no items match', async (t) => {
  const { ui } = await pestlePage(t, { items: [] });
  assert.match(ui.el('pestleRows').textContent, /No PESTLE\(E\) items match/);
  assert.equal(ui.el('pestleItemsMeta').textContent, '0 PESTLE(E) items for A.');
});

test('readers can view items and processes without management actions', async (t) => {
  const { ui } = await pestlePage(t, { me: { can_view_pestle: true } });
  const row = ui.el('pestleRows').querySelector('tr');
  assert.equal(row.querySelector('.btn-outline-primary').textContent, 'View');
  assert.equal(row.querySelector('[data-sample-pestle]'), null);
  assert.equal(ui.el('newPestleItem').classList.contains('d-none'), true);
  assert.equal(ui.el('addBusinessProcess').style.display, 'none');
  assert.equal(ui.el('businessProcessName').disabled, true);
  assert.equal(ui.el('businessProcessList').querySelector('button'), null);
  assert.match(ui.el('businessProcessList').textContent, /<b>Payroll<\/b>/);
  assert.equal(ui.el('businessProcessList').querySelector('b'), null);
});

test('users without PESTLE(E) access see an explanation and no register requests', async (t) => {
  const { ui, state } = await pestlePage(t, { me: { can_view_events: true } });
  assert.match(
    ui.el('pestleRows').textContent,
    /You do not have permission to view PESTLE\(E\) assessments/,
  );
  assert.equal(state.itemQueries.length, 0);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/pestle/business-processes')));
});

test('a metadata failure is reported', async (t) => {
  const { ui } = await pestlePage(t, { metaFail: true });
  assert.equal(ui.el('status').textContent, 'Failed to load PESTLE(E): Error: Meta offline');
});

test('business processes are deleted only after confirmation and failures are reported', async (t) => {
  const prompts = [];
  const { ui, state } = await pestlePage(t, {
    processes: [
      { id: 'b1', name: 'Payroll' },
      { id: 'b/2', name: 'Sales' },
    ],
    confirm: (message) => (prompts.push(message), prompts.length > 1),
  });
  const del = (id) => ui.el('businessProcessList').querySelector(`[data-delete-process="${id}"]`);
  del('b1').click();
  assert.match(
    prompts[0],
    /Delete business process “Payroll”\? Relevance links for it will also be removed\./,
  );
  assert.deepEqual(state.removed, []);
  del('b/2').click();
  await settle(() => ui.el('status').textContent === 'Business process deleted');
  assert.deepEqual(state.removed, ['/api/v1/pestle/business-processes/b%2F2']);
  // The register reloads because item process counts change.
  assert.equal(state.itemQueries.length, 2);
  assert.equal(del('b/2'), null);

  const failing = await pestlePage(t, { removeFail: true });
  failing.ui.el('businessProcessList').querySelector('[data-delete-process="b1"]').click();
  await settle(
    () =>
      failing.ui.el('status').textContent === 'Failed to delete business process: Error: In use',
  );
});

test('adding a business process trims the name and reports failures', async (t) => {
  const { ui, state } = await pestlePage(t, { processes: [] });
  assert.match(
    ui.el('businessProcessList').textContent,
    /No business processes have been created yet/,
  );
  ui.el('businessProcessName').value = '  Customer onboarding  ';
  ui.el('addBusinessProcess').click();
  await settle(() => ui.el('status').textContent === 'Business process added');
  assert.deepEqual(state.posted, [
    { path: '/api/v1/pestle/business-processes', body: { name: 'Customer onboarding' } },
  ]);
  assert.equal(ui.el('businessProcessName').value, '');
  assert.match(ui.el('businessProcessList').textContent, /Customer onboarding/);

  const failing = await pestlePage(t, { postFail: true });
  failing.ui.el('businessProcessName').value = 'Payroll';
  failing.ui.el('addBusinessProcess').click();
  await settle(
    () =>
      failing.ui.el('status').textContent === 'Failed to add business process: Error: Name taken',
  );
  assert.equal(failing.ui.el('addBusinessProcess').disabled, false);
  assert.equal(failing.ui.el('businessProcessName').value, 'Payroll');
});

test('an item can be sampled into an audit from the register', async (t) => {
  const { ui, state } = await pestlePage(t);
  ui.el('pestleRows').querySelector('[data-sample-pestle="p1"]').click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  assert.equal(
    ui.document.getElementById('entityAuditSampleTitle').textContent,
    `Sample “${HOSTILE}Privacy law” into audit`,
  );
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.entity_type, 'pestle_item');
  assert.equal(state.posted[0].body.entity_id, 'p1');
});

test('a legacy item link redirects to the item page and maps the editor tab', async (t) => {
  for (const [query, target] of [
    ['&id=p%2F1&tab=editor', '/pestle_item.html?id=p%2F1&tab=details&framework=A'],
    ['&id=p1&tab=questions', '/pestle_item.html?id=p1&tab=questions&framework=A'],
    ['&id=p1', '/pestle_item.html?id=p1&framework=A'],
  ]) {
    const replaced = [];
    const location = {
      search: '?framework=A' + query,
      origin: 'https://keen.example',
      pathname: '/pestle.html',
      replace: (u) => replaced.push(u),
    };
    await pestlePage(t, { globals: { location } });
    assert.deepEqual(replaced, [target]);
  }
});

function fakeTabs() {
  const shown = [];
  return {
    shown,
    Tab: {
      getOrCreateInstance: (el) => ({
        show() {
          shown.push(el.id);
          el.dispatchEvent(new el.ownerDocument.defaultView.Event('shown.bs.tab'));
        },
      }),
    },
  };
}

test('tabs follow the tab query parameter and record the active tab', async (t) => {
  const tabs = fakeTabs();
  const { ui } = await pestlePage(t, { query: '&tab=processes', tabs });
  assert.deepEqual(tabs.shown, ['businessProcessesTab']);
  assert.match(ui.window.location.search, /tab=processes/);
  ui.el('pestleItemsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.doesNotMatch(ui.window.location.search, /tab=/);
  assert.match(ui.window.location.search, /framework=A/);

  const editor = fakeTabs();
  await pestlePage(t, { query: '&tab=editor', tabs: editor });
  assert.deepEqual(editor.shown, ['pestleItemsTab']);
});

test('visualisations draw the lens/type heatmap, relevance circles and clause network', async (t) => {
  const tabs = fakeTabs();
  const { ui } = await pestlePage(t, { query: '&tab=visualisations', tabs });
  await settle(() => ui.el('pestleClauseNetwork').querySelector('svg'));
  assert.deepEqual(tabs.shown, ['pestleVisualisationsTab']);
  const vizCalls = ui.calls.filter((c) => c.url.startsWith('/api/v1/pestle/visualisations'));
  // The forced initial render and the tab event share the cached result.
  assert.equal(vizCalls.length >= 1 && vizCalls.length <= 2, true);
  assert.equal(vizCalls[0].url, '/api/v1/pestle/visualisations?framework=A');

  const heat = ui.el('pestleLensTypeViz').querySelector('svg');
  // Without an opaque page background, labels are drawn for the default white page.
  const axisLabel = heat.querySelector('g text');
  assert.equal(axisLabel.getAttribute('fill'), '#111827');
  assert.equal(axisLabel.getAttribute('stroke'), 'rgb(255, 255, 255)');
  const cells = [...heat.querySelectorAll('rect')];
  assert.equal(cells.length, 4);
  const cellText = [...heat.querySelectorAll('g:last-child text')].map((x) => x.textContent);
  // Rows are lenses (Internal, External); columns are types (Legal, Social).
  assert.deepEqual(cellText, ['1', '2', '1', '']);
  assert.equal(cells.find((_, i) => cellText[i] === '').getAttribute('fill'), '#e2e8f0');

  const venn = ui.el('pestleRelevanceVenn').querySelector('svg');
  assert.deepEqual(
    [...venn.querySelectorAll('text.count')].map((x) => x.textContent),
    ['1', '1', '1', '0'],
  );
  // Missing labels fall back to the configured relevance names.
  assert.deepEqual(
    [...venn.querySelectorAll('text.label')].map((x) => x.textContent),
    ['High', 'Medium', 'Low', 'N/A'],
  );
  assert.deepEqual(
    [...venn.querySelectorAll('circle')].map((c) => c.getAttribute('fill')),
    ['#dc2626', '#f59e0b', '#16a34a', '#64748b'],
  );

  const network = ui.el('pestleClauseNetwork');
  assert.equal(network.querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
  const svg = network.querySelector('svg');
  assert.match(svg.textContent, /2 clauses, 2 PESTLE\(E\) items, 3 relevance mappings/);
  const nodes = [...svg.querySelectorAll('.interested-party-bipartite-node')];
  assert.equal(nodes.length, 4);
  // Clauses sort numerically; items with more mappings come first.
  assert.match(nodes[0].querySelector('text').textContent, /^4\.1 \(2\)/);
  assert.match(nodes[1].querySelector('text').textContent, /^4\.10 \(1\)/);
  const longNode = nodes[2];
  assert.equal(
    longNode.querySelector(':scope > title').textContent,
    `${longLabel}\nSocial / Internal\nMappings: 2`,
  );
  // Three display lines at most; the tooltip keeps the full label.
  assert.equal(longNode.querySelector('text').querySelectorAll('tspan').length, 3);
  assert.match(longNode.querySelector('text').textContent, /…$/);
  assert.match(
    nodes[3].querySelector(':scope > title').textContent,
    /Privacy law\nLegal \/ External\nMappings: 1/,
  );
  const linkTitles = [...svg.querySelectorAll('path > title')].map((x) => x.textContent);
  assert.deepEqual(linkTitles, [
    `4.1 Context × ${HOSTILE}Privacy law\nRelevance: High`,
    `4.10 Interested parties × ${longLabel}\nRelevance: Medium`,
    `4.1 Context × ${longLabel}\nRelevance: Low`,
  ]);
  network.querySelector('[data-zoom="in"]').click();
  network.querySelector('[data-zoom="out"]').click();
  network.querySelector('[data-zoom="reset"]').click();
  await new Promise((r) => setTimeout(r, 200));
  assert.match(
    svg.querySelector('.pestle-clause-bipartite-scene').getAttribute('transform'),
    /translate\(.+\) scale\(.+\)/,
  );
});

test('visualisations explain empty data, missing D3 and load failures', async (t) => {
  const empty = await pestlePage(t, {
    query: '&tab=visualisations',
    tabs: fakeTabs(),
    visualisations: {
      types: [],
      lenses: [],
      relevance_counts: [],
      clause_links: [{ item_id: 'p1' }],
    },
  });
  await settle(() =>
    /No non-N\/A clause relevance mappings exist yet/.test(
      empty.ui.el('pestleClauseNetwork').textContent,
    ),
  );
  assert.match(
    empty.ui.el('pestleLensTypeViz').textContent,
    /No PESTLE\(E\) items to visualise yet/,
  );
  assert.ok(empty.ui.el('pestleRelevanceVenn').querySelector('svg'));

  const noD3 = await pestlePage(t, { noD3: true, query: '&tab=visualisations', tabs: fakeTabs() });
  await settle(() => /D3 is not available/.test(noD3.ui.el('pestleClauseNetwork').textContent));
  assert.match(noD3.ui.el('pestleLensTypeViz').textContent, /D3 is not available/);

  const failed = await pestlePage(t, { vizFail: true });
  failed.ui.el('pestleVisualisationsTab').dispatchEvent(new failed.ui.window.Event('shown.bs.tab'));
  await settle(
    () =>
      failed.ui.el('status').textContent === 'Failed to load visualisations: Error: Viz offline',
  );
});

test('changing business processes refreshes visualisations on the next visit', async (t) => {
  const { ui } = await pestlePage(t);
  const vizCalls = () =>
    ui.calls.filter((c) => c.url.startsWith('/api/v1/pestle/visualisations')).length;
  ui.el('pestleVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('pestleClauseNetwork').querySelector('svg'));
  ui.el('pestleVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(vizCalls(), 1);
  ui.el('businessProcessName').value = 'Logistics';
  ui.el('addBusinessProcess').click();
  await settle(() => ui.el('status').textContent === 'Business process added');
  ui.el('pestleVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => vizCalls() === 2);
});

test('audit managers can sample items without event access, unlike plain readers', async (t) => {
  // Entity samples need the sampling feature and item access; event sampling is separate.
  const features = { enabled: { event_sampling: false } };
  const manager = await pestlePage(t, {
    me: { can_view_pestle: true, can_manage_audits: true, features },
  });
  assert.ok(manager.ui.el('pestleRows').querySelector('[data-sample-pestle="p1"]'));
  const reader = await pestlePage(t, { me: { can_view_pestle: true, features } });
  assert.equal(reader.ui.el('pestleRows').querySelector('[data-sample-pestle]'), null);
  const disabled = await pestlePage(t, {
    me: {
      can_view_pestle: true,
      can_manage_audits: true,
      features: { enabled: { sampling: false } },
    },
  });
  assert.equal(disabled.ui.el('pestleRows').querySelector('[data-sample-pestle]'), null);
});

test('visualisation labels follow a dark hex page background and equal relevance sorts by clause', async (t) => {
  const { ui } = await pestlePage(t, {
    query: '&tab=visualisations',
    tabs: fakeTabs(),
    visualisations: {
      ...visualisations,
      clause_links: [
        // Two links of equal relevance sort by clause reference; an unknown relevance
        // weighs as low and is labelled N/A.
        {
          item_id: 'p1',
          clause_id: 'cl10',
          clause_ref: '4.10',
          item: 'Second',
          relevance_code: 'medium',
        },
        {
          item_id: 'p1',
          clause_id: 'cl2',
          clause_ref: '4.2',
          item: 'Second',
          relevance_code: 'medium',
        },
        {
          item_id: 'p2',
          clause_id: 'cl2',
          clause_ref: '4.2',
          item: 'Mystery',
          relevance_code: 'unusual',
        },
      ],
    },
  });
  const real = ui.window.getComputedStyle.bind(ui.window);
  // The design system paints the page with a hex colour the heatmap must read.
  ui.window.getComputedStyle = (el, pseudo) =>
    el === ui.window.document.body ? { backgroundColor: '#123' } : real(el, pseudo);
  ui.el('pestleVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('pestleClauseNetwork').querySelector('svg'));
  // The forced initial render used the white default; redraw against the dark page.
  ui.el('pestleLensTypeViz').innerHTML = '';
  ui.el('businessProcessName').value = 'Logistics';
  ui.el('addBusinessProcess').click();
  await settle(() => ui.el('status').textContent === 'Business process added');
  ui.el('pestleVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('pestleLensTypeViz').querySelector('svg g text'));
  const axisLabel = ui.el('pestleLensTypeViz').querySelector('svg g text');
  assert.equal(axisLabel.getAttribute('fill'), '#f8fafc');
  assert.equal(axisLabel.getAttribute('stroke'), 'rgb(17, 34, 51)');

  const svg = ui.el('pestleClauseNetwork').querySelector('svg');
  const linkTitles = [...svg.querySelectorAll('path > title')].map((x) => x.textContent);
  assert.deepEqual(linkTitles, [
    '4.2 × Second\nRelevance: Medium',
    '4.10 × Second\nRelevance: Medium',
    '4.2 × Mystery\nRelevance: N/A',
  ]);
  const nodes = [...svg.querySelectorAll('.interested-party-bipartite-node')];
  assert.match(nodes[0].querySelector('text').textContent, /^4\.2 \(2\)/);
  assert.equal(nodes[0].style.cursor, 'pointer');
});
