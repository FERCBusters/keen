// Effectiveness measures and metric entries recorded with little detail: one
// entry, no unit, period, description or owner, zero values and values without
// a display; and the trend redrawn as the window resizes and entries go.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import { page, settle } from './support/page-fixture.mjs';

const pause = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));
const entry = (overrides = {}) => ({
  id: 'e1',
  recorded_at: '2026-01-31T00:00:00',
  created_at: '2026-01-31T00:00:00',
  updated_at: '2026-01-31T00:00:00',
  period: '',
  metric_value: 94,
  metric_unit: '',
  qualitative_value: '',
  value_display: '94',
  source_type: 'other',
  source_title: '',
  source_url: null,
  source_reference: '',
  source_event_id: null,
  notes: '',
  raw_payload: {},
  ...overrides,
});
const measure = (overrides = {}) => ({
  id: 'm1',
  framework: 'A',
  summary: '',
  description: '',
  metric_key: null,
  source_type: 'other',
  target_value: 90,
  target_unit: '',
  threshold_operator: 'gte',
  target_display: '≥ 90',
  owner: null,
  frequency: '',
  notes: '',
  metric_entry_count: 1,
  latest_entry: entry(),
  updated_at: '2026-02-01T00:00:00',
  controls: [],
  quiz: null,
  submission_secret_issued_at: null,
  ...overrides,
});

async function measurePage(t, { row = measure(), entries = [entry()], confirm = true } = {}) {
  const removed = [];
  const ui = await page(t, 'isms-effectiveness-measure', {
    query: '&id=m1',
    me: { is_admin: true },
    confirm,
    setup(window) {
      window.d3 = d3;
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/sources') return { items: [] };
      if (p === '/api/v1/isms/effectiveness-measures/m1') return structuredClone(row);
      if (p === '/api/v1/isms/effectiveness-measures/m1/metrics')
        return { items: structuredClone(entries) };
      if (p === '/api/v1/isms/effectiveness_measure/m1/changelog') return { items: [] };
      if (p === '/api/v1/audits') return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async () => ({ ok: true }),
    remove: async (u) => {
      removed.push(u.pathname);
      return { ok: true };
    },
  });
  await settle(() => ui.el('measureChangelog').textContent && ui.idle());
  await pause();
  return { ui, removed };
}
const sampleTitle = async (ui) => {
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  return ui.document.getElementById('entityAuditSampleTitle').textContent;
};

test('a measure without a summary, unit, description, frequency or period shows plain placeholders', async (t) => {
  const { ui } = await measurePage(t);
  assert.equal(ui.el('measureTitle').textContent, 'Effectiveness measure');
  assert.equal(ui.el('metricCount').textContent, '1 metric entry');
  assert.equal(ui.el('measureMeta').textContent, 'A · 1 metric entries');
  assert.equal(ui.el('measureDescription').textContent, 'No description recorded.');
  assert.equal(ui.el('measureFrequency').textContent, '-');
  const explanation = ui.el('measureThresholdExplanation').textContent;
  assert.match(explanation, /compares that value with the target ≥ 90 and marks it/);
  assert.doesNotMatch(explanation, /values must be recorded in/);
  // Without a period the latest value stands alone.
  assert.match(ui.el('latestValue').textContent, /^94\s*Meets target$/);
  assert.equal(ui.el('latestValue').querySelector('div'), null);
  const cells = [...ui.el('metricsRows').querySelector('tr').cells].map((c) =>
    c.textContent.trim(),
  );
  assert.equal(cells[1], '-');
  // The trend's axis names no unit.
  assert.equal(ui.document.querySelector('.metric-trend-axis-label').textContent, 'Value');

  ui.el('sampleMeasureAudit').click();
  assert.equal(await sampleTitle(ui), 'Sample “Effectiveness measure” into audit');
});

test('a metric entry without a value is sampled as a metric entry', async (t) => {
  const blank = entry({ metric_value: null, value_display: '' });
  const { ui } = await measurePage(t, {
    row: measure({ latest_entry: blank }),
    entries: [blank],
  });
  ui.document.querySelector('[data-sample-metric="e1"]').click();
  assert.equal(await sampleTitle(ui), 'Sample “Metric entry” into audit');
});

test('values of zero against no target are drawn on a scale from 0 to 1', async (t) => {
  const zero = (id, day) =>
    entry({ id, recorded_at: `2026-01-${day}T00:00:00`, metric_value: 0, value_display: '0' });
  const { ui } = await measurePage(t, {
    row: measure({ target_value: null, threshold_operator: '', target_display: '' }),
    entries: [zero('e1', '10'), zero('e2', '20')],
  });
  // The value axis sits at the left margin.
  const axis = ui.el('metricTrendSvg').querySelector('g[transform="translate(72,0)"]');
  const yTicks = [...axis.querySelectorAll('.tick text')].map((tick) => tick.textContent);
  assert.equal(yTicks[0], '0.0');
  assert.equal(yTicks.at(-1), '1.0');
  assert.equal(ui.el('trendMeta').textContent, '2 numeric metric entries plotted for other.');
  assert.equal(ui.el('metricTrendSvg').querySelector('.metric-trend-target'), null);
});

test('removing the latest source moves the trend to the one source left', async (t) => {
  const entries = [
    entry({ id: 'e1', recorded_at: '2026-01-10T00:00:00', source_type: 'jira' }),
    entry({ id: 'e2', recorded_at: '2026-01-20T00:00:00', source_type: 'jira' }),
    entry({ id: 'e3', recorded_at: '2026-01-30T00:00:00', source_type: 'webhook' }),
  ];
  const { ui, removed } = await measurePage(t, {
    row: measure({ metric_entry_count: 3, latest_entry: entries[2] }),
    entries,
  });
  assert.equal(ui.el('trendSource').value, 'webhook');
  ui.document.querySelector('[data-delete-metric="e3"]').click();
  await settle(() => ui.el('status').textContent === 'Effectiveness metric entry deleted.');
  assert.deepEqual(removed, ['/api/v1/isms/effectiveness-metrics/e3']);
  assert.deepEqual(
    [...ui.el('trendSource').options].map((o) => o.value),
    ['', 'jira'],
  );
  assert.equal(ui.el('trendSource').value, 'jira');
  assert.equal(ui.el('trendMeta').textContent, '2 numeric metric entries plotted for jira.');
});

test('declining a metric deletion keeps the entry', async (t) => {
  const { ui, removed } = await measurePage(t, { confirm: false });
  ui.document.querySelector('[data-delete-metric="e1"]').click();
  await settle(() => ui.confirmations.length === 1);
  await pause();
  assert.equal(ui.confirmations[0].title, 'Delete effectiveness metric entry');
  assert.deepEqual(removed, []);
  assert.equal(ui.el('metricsMeta').textContent, '1 / 1');
  assert.equal(ui.el('status').textContent, '');
});

test('resizing redraws the trend once per frame, and only while its tab is open', async (t) => {
  const { ui } = await measurePage(t);
  const svg = ui.el('metricTrendSvg');
  let draws = 0;
  const select = ui.window.d3.select;
  ui.window.d3 = {
    ...d3,
    select: (target) => {
      if (target === svg) draws += 1;
      return select(target);
    },
  };
  const resize = () => ui.window.dispatchEvent(new ui.window.Event('resize'));
  resize();
  await pause();
  assert.equal(draws, 0);

  ui.el('measureVisualisationsPane').classList.add('active');
  resize();
  resize();
  resize();
  await settle(() => draws > 0);
  await pause();
  assert.equal(draws, 1);
  assert.ok(svg.querySelector('rect.metric-trend-bar'));
});

async function metricPage(t, { row, sourcesFail = false }) {
  const ui = await page(t, 'isms-effectiveness-metric', {
    query: '&id=x1',
    me: { is_admin: true },
    get: async (u) => {
      if (u.pathname === '/api/v1/sources') {
        if (sourcesFail) throw new Error('Sources unavailable');
        return { items: [] };
      }
      if (u.pathname === '/api/v1/isms/effectiveness-metrics/x1') return structuredClone(row);
      if (u.pathname === '/api/v1/audits')
        return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('metricTitle').textContent !== 'Metric entry' || ui.idle());
  await pause();
  return ui;
}
const metric = (overrides = {}) => ({ ...entry({ id: 'x1', measure_id: 'm1' }), ...overrides });

test('a metric entry still opens when the source list cannot be loaded', async (t) => {
  const ui = await metricPage(t, {
    row: metric({ measure: measure({ metric_key: 'phish-rate', summary: 'Phishing' }) }),
    sourcesFail: true,
  });
  assert.equal(ui.el('metricTitle').textContent, '94');
  assert.equal(ui.el('status').textContent, '');
  // A key without an owner is shown on its own.
  assert.equal(ui.el('parentMetricKey').innerHTML, '<code>phish-rate</code>');
});

test('a measure with neither key nor owner shows a dash, and a valueless entry samples as a metric entry', async (t) => {
  const ui = await metricPage(t, {
    row: metric({ metric_value: null, value_display: '', measure: measure() }),
  });
  assert.equal(ui.el('parentMetricKey').textContent, '-');
  assert.equal(ui.el('metricTitle').textContent, 'Metric entry');
  ui.el('sampleMetricAudit').click();
  assert.equal(await sampleTitle(ui), 'Sample “Metric entry” into audit');
});
