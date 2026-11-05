// What the charts show while their data loads, when it fails, and when the
// coverage gaps panel is open; exports taken before a chart is drawn or with
// the dates cleared; and controls without titles in the gaps list.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  charts,
  deferred,
  pause,
  settle,
  today,
  twoSources,
  weekAgo,
} from './support/visualisation-charts.mjs';

const statsRequests = (ui) => ui.calls.filter((c) => c.url.startsWith('/api/v1/stats/controls'));
const openGaps = (ui) => {
  ui.el('coverageGapsPanel').classList.add('show');
  ui.el('coverageGapsPanel').dispatchEvent(new ui.window.Event('shown.bs.collapse'));
};

test('a failed reload says the lists could not load, and an open gaps panel says so too', async (t) => {
  let graphCalls = 0;
  const stats = deferred();
  const ui = await charts(t, {
    mode: 'graph',
    graph: () => {
      graphCalls += 1;
      if (graphCalls > 1) throw new Error('Graph offline');
      return twoSources;
    },
    // The controls are still on their way when the graph fails.
    stats: () => stats.promise,
  });
  ui.el('coverageGapsPanel').classList.add('show');
  ui.el('dateApply').click();
  await settle(() => /Failed to load visualisation/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Failed to load visualisation: Graph offline');
  assert.equal(ui.el('gaps').textContent, 'Failed to load controls.');
  assert.equal(ui.el('topSources').textContent, 'Failed to load sources.');
  assert.equal(ui.el('topControls').textContent, 'Failed to load controls.');
});

test('with the gaps panel open, applying new dates reloads the controls for them', async (t) => {
  const ui = await charts(t, {
    mode: 'graph',
    stats: () => ({
      items: [{ id: 'c9', ref: 'A9', title: 'Logging', in_scope: true, evidence_count: 0 }],
    }),
  });
  ui.el('coverageGapsPanel').classList.add('show');
  ui.el('dateFromDmy').value = '2026-09-01';
  ui.el('dateToDmy').value = '2026-09-30';
  ui.el('dateApply').click();
  await settle(() => ui.el('gaps').querySelectorAll('.gap-item').length === 1);
  assert.deepEqual(
    statsRequests(ui).map((c) => c.url),
    ['/api/v1/stats/controls?framework=A&limit=5000&start_date=2026-09-01&end_date=2026-09-30'],
  );
  assert.match(ui.el('gaps').textContent, /A9\s+Logging/);
});

test('exports taken before a chart is drawn are empty files named for the chart', async (t) => {
  const graph = await charts(t, { mode: 'graph', graphFail: true });
  graph.el('vizExportCsv').click();
  graph.el('vizExportSvg').click();
  await settle(() => graph.files.length === 2);
  assert.deepEqual(
    graph.files.map((f) => f.name),
    [`keen_graph_${weekAgo}_to_${today}.csv`, `keen_graph_${weekAgo}_to_${today}.svg`],
  );
  assert.equal(await graph.files[0].blob.text(), '');

  const histogram = await charts(t, { mode: 'histogram', seriesFail: true });
  await settle(() => /Failed to load histogram data/.test(histogram.el('status').textContent));
  histogram.el('vizExportCsv').click();
  await settle(() => histogram.files.length === 1);
  assert.equal(histogram.files[0].name, `keen_histogram_${weekAgo}_to_${today}.csv`);
  assert.equal(await histogram.files[0].blob.text(), '');
});

test('with both dates cleared, a breakdown loads every date and exports are named for today', async (t) => {
  const ui = await charts(t, { mode: 'graph' });
  ui.el('dateFromDmy').value = '';
  ui.el('dateToDmy').value = '';
  ui.input('vizBreakdown', 'actor', 'change');
  await settle(() => ui.graphRequests().length === 2);
  assert.equal(
    ui.graphRequests()[1].url,
    '/api/v1/graph/control_source?framework=A&breakdown=actor',
  );
  await settle(() => ui.document.querySelectorAll('#viz g.node-source').length === 2);
  ui.el('vizExportCsv').click();
  ui.el('vizExportSvg').click();
  await settle(() => ui.files.length === 2);
  assert.deepEqual(
    ui.files.map((f) => f.name),
    [`keen_graph_${today}.csv`, `keen_graph_${today}.svg`],
  );
});

test('switching to a chart of the same data while it loads waits for that request', async (t) => {
  const slow = deferred();
  const ui = await charts(t, { mode: 'graph', graph: () => slow.promise, wait: false });
  await settle(() => ui.graphRequests().length === 1);
  ui.choose('heatmap');
  assert.equal(ui.el('viz').textContent, 'Loading visualisation data');
  slow.resolve(twoSources);
  await settle(() => ui.document.querySelectorAll('#viz rect.heat-cell').length === 4);
  assert.equal(ui.graphRequests().length, 1);
  assert.equal(ui.el('vizSelect').value, 'heatmap');
});

test('typing in the gaps filter while the controls load sends no second request', async (t) => {
  const stats = deferred();
  const ui = await charts(t, { mode: 'graph', stats: () => stats.promise });
  openGaps(ui);
  await settle(() => statsRequests(ui).length === 1);
  ui.input('gapQ', 'crypto');
  await pause();
  assert.equal(statsRequests(ui).length, 1);
  stats.resolve({
    items: [
      { id: 'c2', ref: 'A2', title: 'Backups', in_scope: true, evidence_count: 0 },
      { id: 'c3', ref: 'A3', title: 'Crypto', in_scope: true, evidence_count: 0 },
    ],
  });
  await settle(() => ui.el('gaps').querySelectorAll('.gap-item').length === 1);
  assert.match(ui.el('gaps').textContent, /A3\s+Crypto/);
});

test('a control without a title is listed by its reference, and full coverage says so', async (t) => {
  let covered = false;
  const ui = await charts(t, {
    mode: 'graph',
    stats: () => ({
      items: [
        { id: 'c3', ref: 'A3', title: null, in_scope: true, evidence_count: covered ? 2 : 0 },
        { id: 'c2', ref: 'A2', title: 'Backups', in_scope: true, evidence_count: covered ? 1 : 0 },
      ],
    }),
  });
  openGaps(ui);
  await settle(() => ui.el('gaps').querySelectorAll('.gap-item').length === 2);
  const untitled = ui.el('gaps').querySelector('.gap-item');
  assert.equal(untitled.querySelector('.badge').textContent, 'A3');
  assert.equal(untitled.querySelector('.small-muted').textContent, '');
  assert.equal(untitled.querySelector('a').getAttribute('href'), '/control.html?id=c3&framework=A');
  // The missing title is not searchable text.
  ui.input('gapQ', 'null');
  assert.equal(ui.el('gaps').textContent, 'No matches.');

  covered = true;
  ui.input('gapQ', '');
  ui.el('vizReloadGaps').click();
  await settle(() => statsRequests(ui).length === 2 && ui.idle());
  await settle(() => ui.el('gaps').textContent === 'None 🎉');
});
