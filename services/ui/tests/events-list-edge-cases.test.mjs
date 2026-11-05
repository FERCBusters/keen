// The Events page with unusual addresses and records: page sizes outside the
// menu, unreadable dates and times, catalogue entries without titles, facet
// answers without some lists, events without a source or summary, and saved
// searches with every filter.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle, filterChips, chosenChips } from './support/page-fixture.mjs';

async function events(t, options = {}) {
  const requests = { events: [], facets: [], panels: [] };
  const ui = await page(t, 'events', {
    ...options,
    get: async (u) => {
      if (u.pathname === '/api/v1/sources/meta')
        return { items: options.sources ?? [{ source: 'ci', label: 'CI' }] };
      if (u.pathname === '/api/v1/controls')
        return { items: options.controls ?? [{ ref: 'A.1', title: 'Access', type: 'control' }] };
      if (u.pathname === '/api/v1/events/facets') {
        requests.facets.push(u.searchParams);
        return options.facets ?? { sources: [], controls: [], rules: [] };
      }
      if (u.pathname === '/api/v1/events') {
        requests.events.push(u.searchParams);
        const items = options.items ?? [
          { id: 'e1', source: 'ci', summary: 'Deploy', timestamp: '2026-01-01', controls: [] },
        ];
        return { total: options.total ?? items.length, items };
      }
      if (u.pathname.startsWith('/api/v1/events/')) {
        requests.panels.push(u.pathname);
        return { id: 'e1', summary: 'Deploy', controls: [] };
      }
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => requests.events.length > 0 && ui.idle());
  return { ...ui, requests, lastEvents: () => requests.events.at(-1) };
}

const submit = (ui) =>
  ui.el('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));

for (const [limit, sent, note] of [
  ['abc', '50', ''],
  ['12.5', '50', ''],
  ['0', '50', ' · page size capped at 50'],
  ['-5', '1', ' · page size capped at 1'],
])
  test(`a page size of ${limit} in the address is requested as ${sent}`, async (t) => {
    const ui = await events(t, { query: '&limit=' + limit });
    assert.equal(ui.lastEvents().get('limit'), sent);
    assert.equal(ui.el('resultMeta').textContent, `1-1 of 1${note}`);
  });

test('a page size the menu does not offer becomes 50 when filters are applied', async (t) => {
  const ui = await events(t, { query: '&limit=37' });
  assert.equal(ui.lastEvents().get('limit'), '37');
  // The menu has no 37, so it shows no choice.
  assert.equal(ui.el('limit').value, '');
  submit(ui);
  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().get('limit'), '50');
});

test('a picked date the page cannot read keeps the date filter from the address', async (t) => {
  // A date field accepts a year of five digits, which the page's own date
  // format cannot express: picking one and leaving unreadable text in the
  // visible field keeps the range the page had.
  const ui = await events(t, { query: '&start_date=20260-01-01&end_date=20261-01-01' });
  assert.equal(ui.el('startDate').value, '20260-01-01');
  ui.el('startDate').value = '20270-01-01';
  ui.el('endDate').value = '20271-01-01';
  ui.el('startDateDmy').value = 'soon';
  ui.el('endDateDmy').value = 'later';
  submit(ui);
  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().get('start_date'), '20260-01-01');
  assert.equal(ui.lastEvents().get('end_date'), '20261-01-01');
  assert.equal(ui.el('startDate').value, '20260-01-01');
  assert.equal(ui.el('status').textContent, "Invalid 'To' date. Use YYYY-MM-DD.");
  assert.equal(ui.el('status').dataset.alert, 'warning');
});

test('a picked date the page cannot read, with no date filter before, leaves the range open', async (t) => {
  const ui = await events(t);
  assert.equal(ui.lastEvents().has('start_date'), false);
  ui.el('startDate').value = '20270-01-01';
  ui.el('endDate').value = '20271-01-01';
  ui.el('startDateDmy').value = 'soon';
  ui.el('endDateDmy').value = 'later';
  submit(ui);
  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().has('start_date'), false);
  assert.equal(ui.lastEvents().has('end_date'), false);
  assert.equal(ui.el('startDate').value, '');
  assert.equal(ui.el('endDate').value, '');
  assert.equal(ui.el('status').textContent, "Invalid 'To' date. Use YYYY-MM-DD.");
});

test('a time window bound that cannot be read is left out', async (t) => {
  const ui = await events(t, { query: '&start_ts=not-a-time&end_ts=2026-01-01T10:00:00' });
  assert.equal(ui.lastEvents().has('start_ts'), false);
  assert.equal(ui.lastEvents().get('end_ts'), '2026-01-01T10:00:00.000Z');
});

test('a source labelled by its own name is offered by that name alone', async (t) => {
  const ui = await events(t, {
    query: '&unmapped=true',
    sources: [
      { source: 'loki', label: 'loki' },
      { source: 'ci', label: 'CI' },
    ],
  });
  assert.deepEqual(filterChips(ui, 'source'), [
    ['ci', 'CI (ci)'],
    ['loki', 'loki'],
  ]);
});

test('catalogue controls without a title are named by their reference', async (t) => {
  const ui = await events(t, {
    query: '&unmapped=true&control=A.1',
    controls: [
      { ref: 'A.1', title: null, type: 'control' },
      { ref: '4.1', title: null, type: 'clause' },
    ],
  });
  assert.equal(ui.el('pageTitle').textContent, 'Events for control A.1');
  assert.deepEqual(chosenChips(ui, 'control'), [['A.1', 'A.1']]);
  const titles = [
    ...ui.document.querySelectorAll('#controlFilterPillModal .keen-filter-pill-grid [data-value]'),
  ].map((chip) => [chip.dataset.value, chip.title]);
  assert.deepEqual(titles, [
    ['A.1', 'control: A.1'],
    ['4.1', 'clause: 4.1'],
  ]);
});

test('a result control without a title or catalogue entry is named by its reference', async (t) => {
  const ui = await events(t, {
    controls: [],
    facets: {
      sources: [],
      controls: [{ ref: 'A.9', title: null, type: 'control', count: 2 }],
      rules: [],
    },
  });
  await settle(() => filterChips(ui, 'control').length === 1);
  assert.deepEqual(filterChips(ui, 'control'), [['A.9', 'A.9 (2)']]);
  const chip = ui.document.querySelector('#controlFilterPillModal [data-value="A.9"]');
  assert.equal(chip.title, 'control: A.9');
});

test('a facets answer without some lists keeps the chosen values offered', async (t) => {
  const ui = await events(t, { query: '&source=ci&control=A.1', facets: {} });
  await settle(() => ui.requests.facets.length === 1 && ui.idle());
  assert.deepEqual(chosenChips(ui, 'source'), [['ci', 'CI (0)']]);
  assert.deepEqual(chosenChips(ui, 'control'), [['A.1', 'Current filter: A.1 (0)']]);
  assert.deepEqual(filterChips(ui, 'source'), [['ci', 'CI (0)']]);
});

test('events without a source or summary, and with connections, render as text', async (t) => {
  const ui = await events(t, {
    // A badge the design system would draw for any source, even a blank one.
    vendor: { sourceBadgeHtml: (source) => `<span class="badge source-badge">${source}</span>` },
    items: [
      { id: 'e1', source: '', summary: '', timestamp: '2026-01-01', controls: [] },
      {
        id: 'e2',
        source: 'ci',
        connection_id: 'conn-<1>',
        connection_name: 'Primary <b>CI</b>',
        summary: 'Deploy',
        timestamp: '2026-01-02',
        controls: [],
      },
      {
        id: 'e3',
        source: 'ci',
        connection_id: 'conn-2',
        summary: 'Build',
        timestamp: '2026-01-03',
        controls: [],
      },
    ],
  });
  const rows = [...ui.el('rows').querySelectorAll('tr[data-event-id]')];
  const sourceCell = (row) => row.cells[2];
  assert.equal(sourceCell(rows[0]).querySelector('.source-badge'), null);
  assert.equal(sourceCell(rows[0]).textContent.trim(), '');
  assert.equal(sourceCell(rows[0]).dataset.sort, '');
  assert.ok(sourceCell(rows[2]).querySelector('.source-badge'));
  const summary = rows[0].querySelector('[data-event-summary]');
  assert.equal(summary.textContent, '(no summary)');
  assert.equal(summary.title, '(no summary)');
  const named = sourceCell(rows[1]).querySelector('.text-muted');
  assert.equal(named.textContent, 'Primary <b>CI</b>');
  assert.equal(named.title, 'conn-<1>');
  assert.equal(sourceCell(rows[1]).querySelector('b'), null);
  assert.equal(sourceCell(rows[2]).querySelector('.text-muted').textContent, 'conn-2');
});

test('a click on the empty results message opens no event', async (t) => {
  const ui = await events(t, { items: [], total: 0 });
  ui.el('emptyState').querySelector('td').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(ui.requests.panels, []);
  assert.equal(ui.el('eventPanel').classList.contains('show'), false);
});

test('without a framework the page sends and keeps none', async (t) => {
  const ui = await events(t, { vendor: { getCurrentFramework: () => '' } });
  assert.equal(ui.lastEvents().has('framework'), false);
  submit(ui);
  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().has('framework'), false);
  assert.equal(new URL(ui.window.location.href).searchParams.has('framework'), false);
});

test('a saved search keeps controls, clauses, dates and the time window, and names them', async (t) => {
  const names = [];
  const ui = await events(t, {
    query:
      '&control=A.1&clause=4.1&start_date=2026-01-01&end_date=2026-01-31&start_ts=2026-01-02T00:00:00Z&end_ts=2026-01-03T00:00:00Z',
    globals: {
      prompt: (message, suggested) => {
        names.push(suggested);
        return suggested;
      },
    },
    post: async (u, body) => body,
  });
  ui.el('saveSearch').click();
  await settle(() => ui.calls.some((c) => c.method === 'POST'));
  const saved = ui.calls.find((c) => c.method === 'POST').body;
  const url = new URL(saved.url, 'https://keen.example');
  assert.equal(url.pathname, '/events.html');
  assert.deepEqual(Object.fromEntries(url.searchParams), {
    control: 'A.1',
    clause: '4.1',
    start_date: '2026-01-01',
    end_date: '2026-01-31',
    start_ts: '2026-01-02T00:00:00.000Z',
    end_ts: '2026-01-03T00:00:00.000Z',
    limit: '50',
  });
  assert.deepEqual(names, ['Events (control=A.1, clause=4.1)']);
});

test('cancelling the name prompt saves nothing', async (t) => {
  const ui = await events(t, { globals: { prompt: () => null } });
  ui.el('saveSearch').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(ui.calls.filter((c) => c.method === 'POST').length, 0);
});

test('Prev goes back one page and stops at the first', async (t) => {
  const ui = await events(t, { query: '&offset=70', total: 200 });
  ui.el('prev').click();
  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().get('offset'), '20');
  ui.el('prevBottom').click();
  await settle(() => ui.requests.events.length === 3);
  assert.equal(ui.lastEvents().get('offset'), '0');
});
