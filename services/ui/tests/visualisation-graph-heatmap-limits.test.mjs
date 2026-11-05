// Graph and heatmap limits as people type them (negative, blank or past the
// maximum), totals that arrive as zero, charts whose data failed to load,
// Apply in the clause views, and exports of charts with nothing in them.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  charts,
  graph,
  pause,
  settle,
  today,
  twoSources,
  weekAgo,
} from './support/visualisation-charts.mjs';

const edges = (ui) => ui.document.querySelectorAll('#viz .links path').length;
const caption = (ui) => ui.el('vizCaption').textContent;
const sourceTiles = (ui) =>
  [...ui.document.querySelectorAll('#viz g.node-source > title')].map((n) => n.textContent);
const heatRows = (ui) =>
  new Set(
    [...ui.document.querySelectorAll('#viz rect.heat-cell > title')].map(
      (n) => n.textContent.split(' × ')[0],
    ),
  ).size;
const heatColumns = (ui) =>
  new Set(
    [...ui.document.querySelectorAll('#viz rect.heat-cell > title')].map(
      (n) => n.textContent.split(' × ')[1].split('\n')[0],
    ),
  ).size;

// The same sources mapped to clauses, as the source × clause endpoint sends them.
function clauseGraph(sources) {
  const g = graph(sources);
  for (const n of g.nodes)
    if (n.type === 'control')
      Object.assign(n, { node_kind: 'clause', clause_id: n.control_id, ref: n.label });
  return { ...g, graph_kind: 'source_clause_events' };
}

const sevenSources = Array.from({ length: 7 }, (_, i) => ({
  key: 'src' + i,
  label: 'Source ' + i,
  controls: { ['A' + i]: 10 - i },
}));

test('a negative minimum edge reads as one, and one past the maximum is capped', async (t) => {
  const ui = await charts(t, {
    mode: 'graph',
    graph: graph([
      { key: 'github', label: 'GitHub', controls: { A1: 2000000, A2: 3 } },
      { key: 'okta', label: 'Okta', controls: { A1: 1 } },
    ]),
  });
  ui.el('graphMinEdge').value = '-5';
  ui.el('graphApply').click();
  assert.equal(edges(ui), 3);
  assert.match(caption(ui), /Showing 2 sources × 2 controls \(edges ≥ 1\)\.$/);

  // Edges are counted up to a million events; a higher minimum asks for that.
  ui.el('graphMinEdge').value = '5000000';
  ui.el('graphApply').click();
  assert.equal(edges(ui), 1);
  assert.match(caption(ui), /Showing 1 sources × 1 controls \(edges ≥ 1000000\)\.$/);

  // A negative number of top sources shows them all, as zero does.
  ui.el('graphMinEdge').value = '1';
  ui.el('graphTopSources').value = '-3';
  ui.el('graphApply').click();
  assert.deepEqual(
    sourceTiles(ui).map((s) => s.split('\n')[0]),
    ['GitHub', 'Okta'],
  );
});

test('a source whose total arrives as zero is ranked and described by its edges', async (t) => {
  // Source totals and edges come from separate queries, so a source can carry
  // edges while its own total reads zero.
  const ui = await charts(t, {
    mode: 'graph',
    graph: graph([
      { key: 'beta', label: 'Beta', controls: { A1: 5 } },
      { key: 'alpha', label: 'Alpha', mapped_events: 0, controls: { A1: 9 } },
    ]),
  });
  assert.deepEqual(sourceTiles(ui), ['Alpha\nMapped events: 9', 'Beta\nMapped events: 5']);
});

test('blank heatmap limits fall back to eighteen sources and thirty-six controls', async (t) => {
  const ui = await charts(t, { mode: 'heatmap', graph: graph(sevenSources) });
  ui.el('heatmapTopSources').value = '';
  ui.el('heatmapTopControls').value = '';
  ui.el('heatmapApply').click();
  assert.deepEqual([heatRows(ui), heatColumns(ui)], [7, 7]);
  assert.match(caption(ui), /Showing top 7 sources × top 7 controls \(ranked by mapped events\)\./);

  // Limits below five are raised to five.
  ui.el('heatmapTopSources').value = '1';
  ui.el('heatmapTopControls').value = '2';
  ui.el('heatmapApply').click();
  assert.deepEqual([heatRows(ui), heatColumns(ui)], [5, 5]);
});

test('after a failed load, resizing and Apply leave the chart without data alone', async (t) => {
  const ui = await charts(t, { mode: 'graph', graphFail: true });
  assert.equal(ui.el('status').textContent, 'Failed to load visualisation: Graph offline');
  const before = ui.el('viz').innerHTML;
  ui.window.dispatchEvent(new ui.window.Event('resize'));
  await pause();
  assert.equal(ui.el('viz').innerHTML, before);
  assert.equal(edges(ui), 0);

  // The heatmap asks again; its Apply then has nothing to draw.
  ui.choose('heatmap');
  await settle(() => /Failed to load visualisation data/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Failed to load visualisation data: Graph offline');
  ui.el('heatmapApply').click();
  assert.equal(ui.document.querySelectorAll('#viz rect.heat-cell').length, 0);
  assert.equal(ui.el('viz').childElementCount, 0);
});

test('heatmap Apply redraws the clause heatmap and leaves the graph alone', async (t) => {
  const ui = await charts(t, {
    mode: 'graph',
    graph: (u) =>
      u.pathname.endsWith('/clause_source') ? clauseGraph(sevenSources) : graph(sevenSources),
  });
  const shown = edges(ui);
  ui.el('heatmapTopSources').value = '5';
  ui.el('heatmapApply').click();
  assert.equal(edges(ui), shown);
  assert.equal(ui.document.querySelectorAll('#viz rect.heat-cell').length, 0);

  ui.el('heatmapTopSources').value = '18';
  ui.choose('heatmap_clause_events');
  await settle(() => heatRows(ui) === 7);
  ui.el('heatmapTopSources').value = '5';
  ui.el('heatmapApply').click();
  assert.equal(heatRows(ui), 5);
  assert.match(caption(ui), /Showing top 5 sources × top 7 clauses/);
});

test('with auto-apply, graph limits redraw both clause graphs and leave a heatmap as drawn', async (t) => {
  const relationships = {
    graph_kind: 'clause_control_relationship',
    nodes: [
      { id: 'clause:k1', type: 'source', node_kind: 'clause', label: '5.1', source: 'clause:k1' },
      { id: 'clause:k2', type: 'source', node_kind: 'clause', label: '5.2', source: 'clause:k2' },
      { id: 'control:c1', type: 'control', node_kind: 'control', label: 'A1', control_id: 'c1' },
      { id: 'control:c2', type: 'control', node_kind: 'control', label: 'A2', control_id: 'c2' },
    ].map((n) => ({ ...n, mapped_events: 1 })),
    links: [
      { source: 'clause:k1', target: 'control:c1', value: 1, link_kind: 'relationship' },
      { source: 'clause:k2', target: 'control:c2', value: 1, link_kind: 'relationship' },
    ],
  };
  const ui = await charts(t, {
    mode: 'graph_clause_events',
    me: { is_admin: true, preferences: { auto_apply_filters: true } },
    graph: (u) => {
      if (u.pathname.endsWith('/clause_source')) return clauseGraph(sevenSources);
      if (u.pathname.endsWith('/clause_control')) return relationships;
      return graph(sevenSources);
    },
  });
  assert.equal(edges(ui), 7);
  ui.input('graphMinEdge', '8');
  assert.equal(edges(ui), 3);

  ui.el('graphMinEdge').value = '1';
  ui.choose('graph_clause_control');
  await settle(() => edges(ui) === 2);
  ui.input('graphTopControls', '1');
  assert.equal(edges(ui), 1);

  // A graph limit that lands while the heatmap shows leaves its cells in place.
  ui.choose('heatmap');
  await settle(() => heatRows(ui) === 7);
  const cell = ui.document.querySelector('#viz rect.heat-cell');
  ui.input('graphMinEdge', '9');
  assert.ok(cell.isConnected);
  assert.equal(heatRows(ui), 7);
});

test('a graph with no matching edges exports an empty file', async (t) => {
  const ui = await charts(t, { mode: 'graph', graph: twoSources });
  ui.el('graphMinEdge').value = '1000';
  ui.el('graphApply').click();
  assert.equal(ui.el('viz').textContent, 'No edges match the current Graph filters.');
  ui.el('vizExportCsv').click();
  await settle(() => ui.files.length === 1);
  assert.equal(ui.files[0].name, `keen_graph_${weekAgo}_to_${today}.csv`);
  assert.equal(await ui.files[0].blob.text(), '');
});

test('an SVG export from a page without a background has no backdrop', async (t) => {
  const ui = await charts(t, { mode: 'graph', graph: twoSources, theme: false });
  ui.el('vizExportSvg').click();
  await settle(() => ui.files.length === 1);
  const svg = await ui.files[0].blob.text();
  assert.match(svg, /^<svg[^>]*xmlns="http:\/\/www.w3.org\/2000\/svg"/);
  assert.ok(svg.includes('GitHub'));
  assert.ok(!svg.includes('data-export-backdrop'));
});
