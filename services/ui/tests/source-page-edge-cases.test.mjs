// The source page: opened without a source, filters that keep the open tab,
// sparse events and metric entries, insight wording, insight requests that
// overtake each other, and tab deep links.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const sources = [
  {
    source: 'github',
    label: 'GitHub',
    color: '#000000',
    total_events: 3,
    mapped_events: 1,
    unmapped_events: 2,
    last_seen: '2026-05-01T00:00:00Z',
  },
];

const insights = (overrides = {}) => ({
  source: 'github',
  days: 90,
  status: 'active',
  window_events: 30,
  sample_size: 30,
  sampled: false,
  last_seen: '2026-05-01T00:00:00Z',
  since_last_seconds: 3600,
  typical_gap_seconds: 600,
  usual_longest_gap_seconds: 1200,
  average_events_per_day: 0.33,
  message: 'Last event 1 hour ago.',
  quiet_periods: [],
  daily: [{ date: '2026-05-01', count: 3 }],
  spikes: [],
  trend: { recent_7_days: 3, previous_7_days: 3, change_pct: 0 },
  repeated_summaries: [],
  ...overrides,
});

async function sourcePage(t, options = {}) {
  const shown = [];
  const ui = await page(t, 'source', {
    query: options.query ?? '&source=github',
    me: { is_admin: true },
    setup(window) {
      if (options.bootstrap !== false)
        window.bootstrap = {
          Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
        };
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/sources') return { total: 1, items: sources };
      if (p === '/api/v1/events')
        return { total: (options.events ?? []).length, items: options.events ?? [] };
      if (p === '/api/v1/sources/github/insights')
        return options.insights ? options.insights(u) : insights();
      if (p === '/api/v1/sources/github/effectiveness-measures')
        return { items: options.measures ?? [] };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.idle() && (ui.el('sourceInsightStatus') || ui.el('status').textContent));
  const requests = (prefix) => ui.calls.filter((c) => c.url.startsWith(prefix));
  return { ui, shown, requests };
}

test('a page opened without a source explains how to open one and requests nothing', async (t) => {
  const { ui, requests } = await sourcePage(t, { query: '' });
  assert.equal(ui.el('sourceTitle').textContent, 'Source not specified');
  assert.equal(ui.el('sourceMeta').textContent, 'Add ?source=<name> to open a source page.');
  assert.equal(ui.el('status').textContent, 'Missing source name.');
  // The filter and insight forms stay inert without a source.
  ui.el('eventQ').value = 'deploy';
  ui.el('sourceEventFilters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  ui.el('sourceInsightsDays').value = '30';
  ui.el('sourceInsightsDays').dispatchEvent(new ui.window.Event('change'));
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(requests('/api/v1/events'), []);
  assert.deepEqual(requests('/api/v1/sources/'), []);
});

test('a page size that is not a number falls back to 50 events', async (t) => {
  const { requests } = await sourcePage(t, { query: '&source=github&limit=many' });
  const sent = new URL(requests('/api/v1/events?')[0].url, 'https://keen.example');
  assert.equal(sent.searchParams.get('limit'), '50');
});

test('applying filters keeps the open tab in the address, apart from Events', async (t) => {
  const insightsTab = await sourcePage(t, { query: '&source=github&tab=insights' });
  insightsTab.ui.el('eventQ').value = 'deploy';
  insightsTab.ui
    .el('sourceEventFilters')
    .dispatchEvent(new insightsTab.ui.window.Event('submit', { cancelable: true }));
  const kept = new URL(insightsTab.ui.window.location.href).searchParams;
  assert.equal(kept.get('tab'), 'insights');
  assert.equal(kept.get('q'), 'deploy');
  const eventsTab = await sourcePage(t, { query: '&source=github&tab=events' });
  eventsTab.ui.el('eventReset').click();
  assert.equal(new URL(eventsTab.ui.window.location.href).searchParams.has('tab'), false);
});

test('events without context, summary or many controls read plainly', async (t) => {
  const { ui } = await sourcePage(t, {
    events: [
      {
        id: 'e1',
        timestamp: '2026-05-01T00:00:00Z',
        summary: '',
        identifier: null,
        system: null,
        source_url: null,
        artifact_count: 0,
        controls: [
          { id: 'c1', ref: 'A.5.1', title: null },
          { id: 'c2', ref: 'A.5.2', title: 'Policies <b>review</b>' },
        ],
      },
    ],
  });
  const cells = ui.el('eventRows').querySelector('tr').cells;
  const text = (cell) => cell.textContent.trim();
  // With selection, the first cell is the row checkbox.
  const offset = cells.length - 7;
  assert.equal(text(cells[offset + 2]), '-');
  assert.equal(text(cells[offset + 3]), '(no summary)');
  const badges = [...cells[offset + 4].querySelectorAll('a.badge')];
  assert.deepEqual(
    badges.map((a) => [a.textContent, a.title]),
    [
      ['A.5.1', 'A.5.1'],
      ['A.5.2', 'Policies <b>review</b>'],
    ],
  );
  assert.doesNotMatch(cells[offset + 4].textContent, /\+/);
  assert.equal(ui.el('eventRows').querySelector('b'), null);
});

test('a metric entry without a source title is named by its source type', async (t) => {
  const entry = (overrides) => ({
    source_type: 'github',
    recorded_at: '2026-02-01T00:00:00',
    metric_value: 3,
    value_display: '3 h',
    source_title: '',
    source_url: null,
    ...overrides,
  });
  const { ui } = await sourcePage(t, {
    query: '&source=github&tab=effectiveness',
    measures: [
      {
        id: 'm1',
        summary: 'Linked',
        controls: [],
        metric_entries: [entry({ source_url: 'https://ci.example/run/1' })],
      },
      { id: 'm2', summary: 'Plain', controls: [], metric_entries: [entry({})] },
    ],
  });
  await settle(() => ui.el('sourceEffectivenessRows').querySelectorAll('tr').length === 2);
  const [linked, plain] = ui.el('sourceEffectivenessRows').querySelectorAll('tr');
  const link = linked.querySelector('a[href="https://ci.example/run/1"]');
  assert.equal(link.textContent, 'github');
  assert.equal(plain.querySelector('a[target="_blank"]'), null);
  assert.match(plain.cells[2].querySelector('.small-muted').textContent, /^github$/);
});

test('insights round long silences and describe rising activity and fully mapped repeats', async (t) => {
  const { ui } = await sourcePage(t, {
    insights: () =>
      insights({
        since_last_seconds: 93780,
        trend: { recent_7_days: 5, previous_7_days: 4, change_pct: 25 },
        repeated_summaries: [
          {
            pattern: 'backup # finished',
            example: 'backup 7 finished',
            example_event_id: 'e7',
            count: 4,
            unmapped_count: 0,
            share_pct: 13,
            last_seen: '2026-05-01T00:00:00Z',
          },
        ],
      }),
  });
  const text = ui.el('sourceInsights').textContent;
  // A duration names its two largest units.
  assert.match(text, /\(1 day 2 hours ago\)/);
  assert.match(text, /5 in the last 7 days, up 25% on the 7 days before/);
  const repeat = ui.el('sourceInsights').querySelector('tbody tr');
  assert.equal(repeat.cells[2].textContent, '0');
  assert.equal(repeat.cells[2].querySelector('a'), null);
});

test('an insight window chosen later replaces an answer still on its way', async (t) => {
  const pending = [];
  const { ui } = await sourcePage(t, {
    insights: (u) => {
      if (!pending.length && u.searchParams.get('days') === '90') {
        pending.push(null);
        return insights();
      }
      return new Promise((resolve, reject) => pending.push({ resolve, reject }));
    },
  });
  const message = () => ui.el('sourceInsightMessage')?.textContent;
  const choose = (days) => {
    ui.el('sourceInsightsDays').value = days;
    ui.el('sourceInsightsDays').dispatchEvent(new ui.window.Event('change'));
  };
  choose('30');
  choose('180');
  await settle(() => pending.length === 3);
  pending[2].resolve(insights({ days: 180, message: 'Window of 180 days.' }));
  await settle(() => message() === 'Window of 180 days.');
  pending[1].resolve(insights({ days: 30, message: 'Window of 30 days.' }));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(message(), 'Window of 180 days.');
  // Submitting the window form asks again; the earlier request's failure is ignored.
  choose('365');
  ui.el('sourceInsightsForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => pending.length === 5);
  pending[4].resolve(insights({ days: 365, message: 'Window of 365 days.' }));
  await settle(() => message() === 'Window of 365 days.');
  pending[3].reject(new Error('Insights offline'));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(message(), 'Window of 365 days.');
  assert.doesNotMatch(ui.el('sourceInsights').textContent, /Failed to load/);
});

test('deep links and the quiet notice open their tabs through Bootstrap', async (t) => {
  const charts = await sourcePage(t, { query: '&source=github&tab=Visualizations' });
  assert.deepEqual(charts.shown, ['sourceVisualisationsTab']);
  const quiet = await sourcePage(t, {
    insights: () => insights({ status: 'quiet', message: 'No events for 3 days.' }),
  });
  assert.equal(quiet.ui.el('sourceQuietNotice').hidden, false);
  quiet.ui.el('sourceQuietOpen').click();
  assert.deepEqual(quiet.shown, ['sourceInsightsTab']);
});
