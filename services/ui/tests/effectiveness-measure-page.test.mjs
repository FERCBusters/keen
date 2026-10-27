import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import { page, settle } from './support/page-fixture.mjs';

function measure(overrides = {}) {
  return {
    id: 'm1',
    framework: 'A',
    summary: '<b>Patch latency</b>',
    description: 'Days to patch',
    source_type: 'jira',
    notes: '',
    metric_key: 'patch-latency',
    metric_entry_count: 3,
    target_value: 7,
    target_unit: 'days',
    target_display: '≤ 7 days',
    threshold_operator: 'lte',
    frequency: 'Monthly',
    owner: 'dana',
    updated_at: '2026-03-01T00:00:00Z',
    controls: [{ id: 'c1', ref: 'A1', title: 'Patching' }],
    latest_entry: {
      id: 'e3',
      metric_value: 9,
      value_display: '9 days',
      period: 'March',
      source_type: 'jira',
    },
    ...overrides,
  };
}

const entries = [
  {
    id: 'e1',
    recorded_at: '2026-01-31T00:00:00Z',
    period: 'January',
    metric_value: 5,
    value_display: '5 days',
    source_type: 'jira',
    source_title: 'Jira <b>filter</b>',
    source_url: 'https://jira.example/filter',
    source_reference: 'JQL-1',
    source_event_id: 'ev1',
    notes: 'Good month',
  },
  {
    id: 'e2',
    recorded_at: '2026-02-28T00:00:00Z',
    period: 'February',
    // The API sends null for the numeric value of a qualitative-only entry.
    metric_value: null,
    qualitative_value: 'Delayed by holidays',
    source_type: 'manual',
    source_url: 'javascript:alert(1)',
  },
  {
    id: 'e3',
    recorded_at: '2026-03-31T00:00:00Z',
    period: 'March',
    metric_value: 9,
    value_display: '9 days',
    source_type: 'jira',
  },
];

async function measurePage(t, options = {}) {
  const state = { removed: [], posted: [] };
  const ui = await page(t, 'isms-effectiveness-measure', {
    query: options.query ?? '&id=m1',
    me: options.me || { is_admin: true },
    globals: { navigator: { clipboard: { writeText: async () => {} } }, ...options.globals },
    setup(window) {
      if (!options.noD3) window.d3 = d3;
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/sources') {
        if (options.sourcesFail) throw new Error('down');
        return { items: [{ source: 'jira', label: 'Jira', color: '#123' }, { source: '' }] };
      }
      if (p === '/api/v1/isms/effectiveness-measures/m1') {
        if (options.fail) throw new Error('Measure offline');
        return options.measure || measure();
      }
      if (p === '/api/v1/isms/effectiveness-measures/m1/metrics')
        return { items: options.entries ?? structuredClone(entries) };
      if (p === '/api/v1/isms/effectiveness_measure/m1/changelog') {
        if (options.changelogFail) throw new Error('log');
        return { items: [] };
      }
      if (p === '/api/v1/audits') return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
    remove: async (u) => {
      state.removed.push(u.pathname + u.search);
      if (options.deleteFail) throw new Error('Delete refused');
      return { ok: true };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: JSON.parse(JSON.stringify(b)) });
      return { ok: true };
    },
  });
  await settle(
    () =>
      (ui.el('measureChangelog').textContent &&
        !/Loading/.test(ui.el('measureChangelog').textContent)) ||
      ui.el('status').textContent,
  );
  return { ui, state };
}

test('effectiveness measure details render with threshold state and linked controls', async (t) => {
  const { ui } = await measurePage(t);
  assert.equal(ui.el('measureTitle').textContent, '<b>Patch latency</b>');
  assert.equal(ui.document.title, 'Keen - <b>Patch latency</b>');
  assert.equal(ui.el('measureMeta').textContent, 'A · key: patch-latency · 3 metric entries');
  assert.equal(ui.el('measureNotes').textContent, 'No notes recorded.');
  assert.equal(ui.el('metricKey').innerHTML, '<code>patch-latency</code>');
  assert.equal(ui.el('metricCount').textContent, '3 metric entries');
  // The expected source renders as a source badge (the harness stubs the badge).
  assert.equal(ui.el('measureSource').textContent.trim(), 'jira');
  // The explanation names the target and the unit values must use.
  assert.match(ui.el('measureThresholdExplanation').textContent, /target ≤ 7 days/);
  assert.match(ui.el('measureThresholdExplanation').textContent, /recorded in days/);
  assert.match(ui.el('measureThresholdExplanation').textContent, /Meets target/);
  // 9 days does not satisfy ≤ 7 days.
  assert.match(ui.el('latestValue').innerHTML, /eff-metric-value-bad/);
  assert.match(ui.el('latestValue').textContent, /9 days\s*Outside target\s*March/);
  assert.match(
    ui.el('linkedControls').querySelector('a').getAttribute('href'),
    // Measures are listed on the control's default Requirements tab.
    /control\.html\?id=c1&framework=A/,
  );
  assert.match(
    ui.el('editMeasureLink').getAttribute('href'),
    /edit=effectiveness%3Am1|edit=effectiveness:m1/,
  );
  assert.match(ui.el('recordMetricLink').getAttribute('href'), /record=m1/);
  assert.equal(ui.el('sampleMeasureAudit').style.display, '');
  assert.match(ui.el('measureChangelog').textContent, /No effectiveness measure changes/);
});

test('metric entries list, filter and link sources safely', async (t) => {
  const { ui } = await measurePage(t);
  assert.equal(ui.el('metricsMeta').textContent, '3 / 3');
  const rows = [...ui.el('metricsRows').querySelectorAll('tr')];
  assert.match(rows[0].innerHTML, /eff-metric-value-ok/);
  assert.match(rows[0].textContent, /Meets target/);
  assert.equal(rows[0].querySelector('b'), null);
  const sourceLinks = [...rows[0].querySelectorAll('td.wrap a')].map((a) => a.getAttribute('href'));
  assert.ok(sourceLinks.includes('https://jira.example/filter'));
  assert.ok(sourceLinks.some((h) => /event\.html\?id=ev1&framework=A/.test(h)));
  assert.match(rows[0].textContent, /JQL-1/);
  // Unsafe source URLs fall back to an inert link target.
  assert.equal(rows[1].querySelector('td.wrap a').getAttribute('href'), '#');
  assert.match(rows[1].textContent, /Delayed by holidays/);
  ui.el('metricsFilter').value = 'holiday';
  ui.el('metricsFilter').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('metricsMeta').textContent, '1 / 3');
  ui.el('metricsFilter').value = 'nothing';
  ui.el('metricsFilter').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('metricsRows').textContent, /No metric entries recorded yet/);
});

test('metric trend plots numeric entries for the selected source and date range', async (t) => {
  const { ui } = await measurePage(t);
  // The latest entry's source is preselected and the range covers the data.
  assert.equal(ui.el('trendSource').value, 'jira');
  assert.deepEqual(
    [...ui.el('trendSource').options].map((o) => o.value),
    ['', 'jira', 'manual'],
  );
  assert.equal(ui.el('trendFrom').value, '2026-01-31');
  assert.equal(ui.el('trendTo').value, '2026-03-31');
  const svg = ui.el('metricTrendSvg');
  assert.equal(svg.querySelectorAll('rect.metric-trend-bar').length, 2);
  assert.ok(svg.querySelector('line.metric-trend-target'));
  assert.match(svg.querySelector('.metric-trend-target-label').textContent, /Target ≤ 7 days/);
  assert.equal(ui.el('trendMeta').textContent, '2 numeric metric entries plotted for jira.');

  ui.el('trendSource').value = '';
  ui.el('trendSource').dispatchEvent(new ui.window.Event('change'));
  assert.equal(
    ui.el('trendMeta').textContent,
    '2 numeric metric entries plotted (1 qualitative/non-numeric omitted).',
  );
  // The qualitative entry is omitted from the chart, never drawn as a zero value.
  assert.equal(svg.querySelectorAll('rect.metric-trend-bar').length, 2);
  ui.el('trendFrom').value = '2026-03-01';
  ui.el('trendApply').click();
  assert.equal(svg.querySelectorAll('rect.metric-trend-bar').length, 1);
  // Entries outside the period are not reported as qualitative omissions.
  assert.equal(ui.el('trendMeta').textContent, '1 numeric metric entry plotted.');
  ui.el('trendFrom').value = '2026-04-01';
  ui.el('trendTo').value = '2026-04-30';
  ui.el('trendApply').click();
  assert.equal(ui.el('trendMeta').textContent, 'No metric entries match this source and period.');
  ui.el('trendReset').click();
  assert.equal(ui.el('trendFrom').value, '2026-01-31');
  assert.equal(svg.querySelectorAll('rect.metric-trend-bar').length, 2);
  ui.el('trendSource').value = 'manual';
  ui.el('trendSource').dispatchEvent(new ui.window.Event('change'));
  assert.match(ui.el('trendMeta').textContent, /qualitative entries cannot be plotted/);
  assert.match(svg.textContent, /No numeric metric values to display/);
});

test('the trend explains when D3 is unavailable', async (t) => {
  const { ui } = await measurePage(t, { noD3: true });
  assert.match(ui.el('trendMeta').textContent, /D3 is unavailable/);
});

test('administrators see measurement integration examples for the metric key', async (t) => {
  const { ui } = await measurePage(t);
  const details = ui.el('measurement-examples').querySelector('details');
  // Opening the disclosure fills the examples through its toggle event.
  details.open = true;
  await settle(() => details.querySelector('code').textContent);
  const code = [...details.querySelectorAll('code')].map((c) => c.textContent);
  assert.match(
    code[0],
    /curl --fail-with-body --show-error 'https:\/\/keen\.example\/api\/v1\/webhooks\/isms\/effectiveness-metrics\/patch-latency'/,
  );
  assert.match(code[0], /KEEN_METRICS_SECRET:\?Set KEEN_METRICS_SECRET/);
  // The example payload names the measure's expected source, so the entries it
  // creates are attributed the same way as entries recorded by hand.
  assert.match(code[0], /"source_type": "jira"/);
  assert.match(code[1], /\\"source_type\\": \\"jira\\"/);
  assert.match(code[1], /urlopen\(request, timeout=30\)/);
  assert.match(details.textContent, /Example value is 0/);
  details.querySelector('button').click();
  await settle(() => /Copied curl/.test(details.textContent));
});

test('measures without a key disable the integration examples', async (t) => {
  const { ui } = await measurePage(t, {
    measure: measure({ metric_key: '', metric_entry_count: 0, latest_entry: null }),
    entries: [],
  });
  assert.equal(ui.el('metricKey').textContent, '-');
  assert.equal(ui.el('metricCount').textContent, '0');
  assert.equal(ui.el('measureMeta').textContent, 'A · No metric entries');
  assert.match(ui.el('latestValue').textContent, /No metric entries yet/);
  const details = ui.el('measurement-examples').querySelector('details');
  details.open = true;
  await settle(() => /Enter and save a metric key/.test(details.textContent));
  assert.ok([...details.querySelectorAll('button')].every((b) => b.disabled));
});

test('readers cannot edit, record, sample or delete metric entries', async (t) => {
  const { ui } = await measurePage(t, { me: { can_view_isms: true } });
  for (const id of ['editMeasureLink', 'recordMetricLink', 'sampleMeasureAudit'])
    assert.equal(ui.el(id).style.display, 'none', id);
  assert.equal(ui.el('measurement-examples'), null);
  assert.equal(ui.document.querySelector('[data-delete-metric]'), null);
  assert.equal(ui.document.querySelector('[data-sample-metric]'), null);
});

test('metric entries can be deleted after confirmation', async (t) => {
  const cancelled = await measurePage(t, { globals: { confirm: () => false } });
  cancelled.ui.document.querySelector('[data-delete-metric="e1"]').click();
  assert.deepEqual(cancelled.state.removed, []);
  const { ui, state } = await measurePage(t, { globals: { confirm: () => true } });
  ui.document.querySelector('[data-delete-metric="e1"] i').click();
  await settle(() => ui.el('status').textContent === 'Effectiveness metric entry deleted.');
  assert.deepEqual(state.removed, ['/api/v1/isms/effectiveness-metrics/e1?framework=A']);
  assert.equal(ui.el('metricsMeta').textContent, '2 / 2');
});

test('failed metric deletion is reported and leaves the entry', async (t) => {
  const { ui } = await measurePage(t, { deleteFail: true, globals: { confirm: () => true } });
  ui.document.querySelector('[data-delete-metric="e2"]').click();
  await settle(() =>
    /Failed to delete metric entry: Delete refused/.test(ui.el('status').textContent),
  );
  assert.equal(ui.el('metricsMeta').textContent, '3 / 3');
});

test('measures and individual metric entries can be sampled into audits', async (t) => {
  const { ui, state } = await measurePage(t);
  ui.document.querySelector('[data-sample-metric="e3"]').click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0].body, {
    entity_type: 'isms_effectiveness_metric',
    entity_id: 'e3',
    evidence_url: '/isms-effectiveness-metric.html?id=e3&framework=A',
    title: '9 days',
    notes: '',
  });
  ui.el('sampleMeasureAudit').click();
  // Wait for the modal to reload its audit list before submitting.
  await settle(() => ui.calls.filter((c) => c.url.startsWith('/api/v1/audits?')).length === 2);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(
    ui.document.getElementById('entityAuditSampleTitle').textContent,
    'Sample “<b>Patch latency</b>” into audit',
  );
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 2);
  assert.equal(state.posted[1].body.entity_type, 'isms_effectiveness_measure');
  assert.equal(
    state.posted[1].body.evidence_url,
    '/isms-effectiveness-measure.html?id=m1&framework=A',
  );
});

test('tabs update the URL and the visualisation tab re-renders the trend', async (t) => {
  const { ui } = await measurePage(t);
  ui.el('measureMetricsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.href, /tab=metrics#metrics$/);
  ui.el('measureVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=visualisations/);
  ui.el('measureDetailsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.doesNotMatch(ui.window.location.search, /tab=/);
});

test('load failures, changelog failures and a missing id are reported', async (t) => {
  const failed = await measurePage(t, { fail: true, sourcesFail: true });
  assert.match(
    failed.ui.el('status').textContent,
    /Failed to load effectiveness measure: Measure offline/,
  );
  const log = await measurePage(t, { changelogFail: true });
  assert.match(log.ui.el('measureChangelog').textContent, /Failed to load changelog/);
  const missing = await measurePage(t, { query: '' });
  assert.equal(missing.ui.el('status').textContent, 'Missing effectiveness measure id.');
});

test('a measure without a target explains that values are reviewed as a trend', async (t) => {
  const { ui } = await measurePage(t, {
    measure: measure({
      target_display: '',
      target_value: null,
      threshold_operator: '',
      source_type: 'other',
      summary: '',
    }),
  });
  assert.equal(ui.el('measureTitle').textContent, 'Effectiveness measure');
  assert.equal(ui.el('measureSource').textContent, 'Other');
  assert.match(ui.el('measureThresholdExplanation').textContent, /no numeric target/);
  assert.doesNotMatch(ui.el('measureThresholdExplanation').textContent, /Outside target/);
});

test("a quiz's measure points to its quiz and offers no edits of its own", async (t) => {
  const { ui } = await measurePage(t, {
    measure: measure({ metric_key: null, quiz: { id: 'q1', title: 'Security <basics>' } }),
  });
  const notice = ui.el('quizNotice');
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /results of the quiz Security <basics>/);
  assert.equal(notice.querySelector('a').getAttribute('href'), '/quiz.html?id=q1&framework=A');
  for (const id of ['editMeasureLink', 'recordMetricLink'])
    assert.equal(ui.el(id).style.display, 'none', id);
  assert.equal(ui.el('measurement-examples'), null);
  assert.equal(ui.document.querySelector('[data-delete-metric]'), null);
  // Results can still be sampled into an audit.
  assert.ok(ui.document.querySelector('[data-sample-metric]'));
  const own = await measurePage(t);
  assert.equal(own.ui.el('quizNotice').hidden, true);
});
