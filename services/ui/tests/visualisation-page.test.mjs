import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';
const d3 = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');
const dataset = {
  nodes: [
    {
      id: 's1',
      type: 'source',
      label: 'Agent',
      source: 'keen-agent',
      color: '#123456',
      mapped_events: 5,
    },
    { id: 'c1', type: 'control', label: 'A1', control_id: 'c1', title: 'Access', mapped_events: 5 },
  ],
  links: [{ source: 's1', target: 'c1', value: 5 }],
};
async function fixture(t, mode, options = {}) {
  const ui = await page(t, 'visualisation', {
    htmlName: 'events',
    scripts: [d3],
    query: '&mode=' + mode,
    ...options,
    setup(window) {
      window.document.getElementById('embeddedVisualisationsPane').classList.add('active');
      window.document.querySelector('[data-keen-visualisation-mount]').dataset.vizModes = 'all';
    },
    get: async (u) => {
      if (options.fail) throw new Error('Offline');
      if (u.pathname.startsWith('/api/v1/graph/'))
        return options.empty ? { nodes: [], links: [] } : dataset;
      if (u.pathname === '/api/v1/stats/controls')
        return {
          items: [
            { id: 'c1', ref: 'A1', mapped_events: 5 },
            { id: 'c2', ref: 'A2', mapped_events: 0 },
          ],
        };
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, b) => {
      assert.equal(u.pathname, '/api/v1/me/preferences');
      return b;
    },
  });
  await settle(() => /Loaded\.|Failed to load/.test(ui.el('status').textContent));
  return ui;
}
for (const mode of [
  'graph',
  'heatmap',
  'sunburst_source',
  'sunburst_control',
  'graph_clause_events',
  'heatmap_clause_events',
  'graph_clause_control',
])
  test(mode + ' renders real SVG from relationship data', async (t) => {
    const ui = await fixture(t, mode);
    assert.equal(ui.el('status').textContent, 'Loaded.');
    assert.ok(ui.el('viz').querySelector('path,rect,circle'));
    assert.ok(
      ui.el('viz').textContent.includes('A1') || ui.el('viz').textContent.includes('Agent'),
    );
    assert.equal(ui.el('vizSelect').value, mode);
    assert.ok(
      ui.calls
        .filter((c) => c.url.startsWith('/api/v1/graph/'))
        .every((c) => c.url.includes('framework=A')),
    );
  });
test('visualisation fetch failure exposes an error rather than stale relationships', async (t) => {
  const ui = await fixture(t, 'graph', { fail: true });
  assert.match(ui.el('status').textContent, /Offline/);
});
test('visualisation graph threshold hides weak links without re-fetching', async (t) => {
  const ui = await fixture(t, 'graph');
  const before = ui.calls.length;
  ui.el('graphMinEdge').value = '6';
  ui.el('graphApply').click();
  assert.equal(ui.el('viz').querySelectorAll('.links path').length, 0);
  assert.equal(ui.calls.length, before);
});
test('switching graph to heatmap reuses the existing dataset', async (t) => {
  const ui = await fixture(t, 'graph');
  const before = ui.calls.length;
  ui.el('vizSelect').value = 'heatmap';
  ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('viz').querySelector('rect'));
  assert.equal(ui.calls.length, before);
});

test('disabled embedded charts neither activate from saved URLs nor fetch chart data', async (t) => {
  const ui = await page(t, 'visualisation', {
    htmlName: 'events',
    scripts: [d3],
    query: '&tab=visualisations',
    setup(window) {
      window.history.replaceState(null, '', '/events.html?framework=A&tab=visualisations');
    },
    me: { is_admin: true, features: { enabled: { visualisations: false } } },
    get: async (url) => {
      throw new Error('Unexpected GET ' + url);
    },
  });
  assert.ok(ui.el('eventsVisualisationsTab').parentElement.hasAttribute('data-feature-hidden'));
  assert.ok(ui.el('embeddedVisualisationsPane').hasAttribute('data-feature-hidden'));
  ui.el('eventsVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(
    ui.calls.filter((call) => call.url.includes('/graph/') || call.url.includes('/stats/')).length,
    0,
  );
});

// --- Charts scoped to the page's own filters -----------------------------------

const daySeries = {
  interval: 'day',
  items: [{ bucket: '2026-10-01', total: 2, mapped: 1, unmapped: 1, by_source: { diary: 2 } }],
  sources: [{ source: 'diary', label: 'Diary', color: '#333', total: 2 }],
};

async function scopedEvents(t, query) {
  const requests = [];
  const ui = await page(t, 'visualisation', {
    htmlName: 'events',
    scripts: [d3],
    query,
    get: async (u) => {
      requests.push(u);
      if (u.pathname === '/api/v1/charts/event_volume_timeseries') return daySeries;
      if (u.pathname.startsWith('/api/v1/graph/')) return dataset;
      throw new Error('Unexpected GET ' + u);
    },
  });
  const tab = ui.el('eventsVisualisationsTab');
  return { ...ui, requests, show: () => tab.dispatchEvent(new ui.window.Event('shown.bs.tab')) };
}

test('the Events charts follow the list filters and say so', async (t) => {
  const ui = await scopedEvents(
    t,
    '&source=diary,ci&control=A.1&rule=deploys&f=any:contains:review&f=actor:contains:alice&f=outcome:equals:ok&unmapped=true',
  );
  ui.show();
  await settle(() =>
    ui.requests.some((u) => u.pathname === '/api/v1/charts/event_volume_timeseries'),
  );

  const series = ui.requests.find((u) => u.pathname === '/api/v1/charts/event_volume_timeseries');
  assert.equal(series.searchParams.get('source'), 'diary,ci');
  assert.equal(series.searchParams.get('control'), 'A.1');
  assert.equal(series.searchParams.get('rule'), 'deploys');
  // Search text reaches the charts as a text filter, never as q.
  assert.equal(series.searchParams.has('q'), false);
  assert.deepEqual(series.searchParams.getAll('f'), [
    'any:contains:review',
    'actor:contains:alice',
    'outcome:equals:ok',
  ]);
  assert.equal(series.searchParams.get('unmapped'), 'true');
  const scope = ui.el('vizScope');
  assert.equal(scope.hidden, false);
  assert.equal(
    scope.textContent,
    'Scoped to the page filters: sources diary, ci; control A.1; mapping rule deploys; text filter any:contains:review; text filter actor:contains:alice; text filter outcome:equals:ok; Unmapped only.',
  );
  // The sunburst by source is offered here and carries the same scope.
  assert.deepEqual(
    [...ui.el('vizSelect').options].map((o) => o.value),
    ['histogram', 'sunburst_source', 'heatmap'],
  );
  ui.el('vizSelect').value = 'sunburst_source';
  ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.requests.some((u) => u.pathname === '/api/v1/graph/control_source'));
  const graph = ui.requests.find((u) => u.pathname === '/api/v1/graph/control_source');
  assert.equal(graph.searchParams.get('source'), 'diary,ci');
  assert.equal(graph.searchParams.get('rule'), 'deploys');
  assert.deepEqual(graph.searchParams.getAll('f'), [
    'any:contains:review',
    'actor:contains:alice',
    'outcome:equals:ok',
  ]);
});

test('filters changed on the list reach the charts when the tab is shown again', async (t) => {
  const ui = await scopedEvents(t, '&source=diary');
  ui.show();
  await settle(() =>
    ui.requests.some((u) => u.pathname === '/api/v1/charts/event_volume_timeseries'),
  );
  assert.equal(ui.el('vizScope').textContent, 'Scoped to the page filters: source diary.');
  const before = ui.requests.length;

  // Showing the tab again with the same filters fetches nothing new.
  ui.show();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(ui.requests.length, before);

  // The list changed its filters in the address; the chart reloads with them.
  ui.window.history.replaceState({}, '', '/events.html?framework=A&source=ci&tab=visualisations');
  ui.show();
  await settle(() => ui.requests.length > before);
  const latest = ui.requests
    .filter((u) => u.pathname === '/api/v1/charts/event_volume_timeseries')
    .at(-1);
  assert.equal(latest.searchParams.get('source'), 'ci');
  assert.equal(ui.el('vizScope').textContent, 'Scoped to the page filters: source ci.');

  // With no filters the note disappears.
  ui.window.history.replaceState({}, '', '/events.html?framework=A&tab=visualisations');
  ui.show();
  await settle(() => ui.el('vizScope').hidden);
  const unscoped = ui.requests
    .filter((u) => u.pathname === '/api/v1/charts/event_volume_timeseries')
    .at(-1);
  assert.equal(unscoped.searchParams.has('source'), false);
});

test('the clause-to-control graph describes the catalogue and takes no event scope', async (t) => {
  const requests = [];
  const ui = await page(t, 'visualisation', {
    htmlName: 'controls',
    scripts: [d3],
    query: '&source=ci&mode=graph_clause_control',
    setup(window) {
      const mount = window.document.querySelector('[data-keen-visualisation-mount]');
      mount.closest('.tab-pane').classList.add('active');
      mount.dataset.vizModes = 'all';
      mount.dataset.vizFilterParams = 'source';
    },
    get: async (u) => {
      requests.push(u);
      if (u.pathname.startsWith('/api/v1/graph/')) return dataset;
      if (u.pathname === '/api/v1/stats/controls') return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => requests.some((u) => u.pathname === '/api/v1/graph/clause_control'));
  const relationships = requests.find((u) => u.pathname === '/api/v1/graph/clause_control');
  assert.equal(relationships.searchParams.has('source'), false);
  ui.el('vizSelect').value = 'graph';
  ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => requests.some((u) => u.pathname === '/api/v1/graph/control_source'));
  assert.equal(
    requests.find((u) => u.pathname === '/api/v1/graph/control_source').searchParams.get('source'),
    'ci',
  );
});

test('a saved address opens the charts tab, and leaving it drops the marker', async (t) => {
  const shown = [];
  const ui = await page(t, 'visualisation', {
    htmlName: 'events',
    scripts: [d3],
    query: '&tab=visualisations',
    setup(window) {
      window.bootstrap = {
        Tab: {
          getOrCreateInstance: (button) => ({ show: () => shown.push(button.id) }),
        },
      };
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/charts/event_volume_timeseries') return daySeries;
      throw new Error('Unexpected GET ' + u);
    },
  });
  // The page asks Bootstrap to show the tab; the real tab then reports it.
  assert.deepEqual(shown, ['eventsVisualisationsTab']);
  ui.el('eventsVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => /Loaded\./.test(ui.el('status').textContent));
  assert.equal(new URL(ui.window.location.href).searchParams.get('tab'), 'visualisations');

  ui.el('eventsRegisterTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(new URL(ui.window.location.href).searchParams.has('tab'), false);
  assert.equal(new URL(ui.window.location.href).searchParams.get('framework'), 'A');
});
