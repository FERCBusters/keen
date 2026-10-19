import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle, pickFilter, filterChips, chosenChips } from './support/page-fixture.mjs';

const sources = [
  {
    source: 'github',
    label: 'GitHub',
    color: '#000',
    total_events: 1200,
    mapped_events: 1000,
    unmapped_events: 200,
    last_seen: '2026-05-01T00:00:00Z',
  },
  { source: 'aws', label: '', total_events: 3, mapped_events: 3, unmapped_events: 0 },
  { source: '' },
];

function event(i, overrides = {}) {
  return {
    id: `e${i}`,
    timestamp: `2026-05-0${i}T00:00:00Z`,
    summary: `<img src=x onerror=alert(${i})> push ${i}`,
    identifier: 'repo/<b>keen</b>',
    source_url: 'https://github.example/commit/' + i,
    artifact_count: i,
    controls: Array.from({ length: 7 }, (_, n) => ({ id: `c${n}`, ref: `A${n}` })),
    ...overrides,
  };
}

async function sourcePage(t, options = {}) {
  const fail = options.fail || {};
  const ui = await page(t, 'source', {
    query: options.query ?? '&source=github',
    me: options.me || { is_admin: true },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/sources') {
        if (fail.sources) throw new Error('down');
        return { items: sources };
      }
      if (p === '/api/v1/events') {
        if (fail.events) throw new Error('Events offline');
        return {
          total: 130,
          items: options.events ?? [
            event(1),
            event(2, {
              identifier: undefined,
              system: 'sys',
              source_url: 'javascript:alert(1)',
              artifact_count: 0,
              controls: [],
            }),
          ],
        };
      }
      if (p === '/api/v1/sources/github/effectiveness-measures') {
        if (fail.effectiveness) throw new Error('down');
        return {
          items: options.measures ?? [
            {
              id: 'm1',
              summary: '<b>MTTR</b>',
              metric: 'Hours',
              metric_key: 'mttr',
              target_value: 4,
              threshold_operator: 'lt',
              controls: [{ id: 'c1', ref: 'A1' }],
              metric_entries: [
                {
                  source_type: 'github',
                  recorded_at: '2026-01-01',
                  metric_value: 9,
                  value_display: '9 h',
                },
                {
                  source_type: 'github',
                  recorded_at: '2026-02-01',
                  metric_value: 3,
                  value_display: '3 h',
                  period: 'Feb',
                  source_url: 'https://github.example/report',
                  source_title: 'Report <i>Feb</i>',
                  source_reference: 'REF-2',
                  source_event_id: 'e9',
                },
                { source_type: 'jira', recorded_at: '2026-03-01', metric_value: 1 },
              ],
            },
            { id: 'm2', metric: 'Count', metric_entries: [{ source_type: 'jira' }] },
          ],
        };
      }
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(
    () =>
      (ui.el('eventResultMeta').textContent &&
        ui.el('eventResultMeta').textContent !== 'Loading…') ||
      ui.el('status').textContent,
  );
  return ui;
}

const lastEvents = (ui) =>
  new URL(
    ui.calls.filter((c) => c.url.startsWith('/api/v1/events?')).at(-1).url,
    'https://keen.example',
  );

test('source page shows summary counts, filtered event links and safe event rows', async (t) => {
  const ui = await sourcePage(t);
  assert.match(ui.el('sourceTitle').textContent, /GitHub/);
  assert.equal(ui.document.title, 'Keen – GitHub');
  assert.equal(
    ui.el('sourceMeta').textContent,
    '1,200 events • 1,000 mapped • 200 unmapped • last seen 2026-05-01T00:00:00Z',
  );
  assert.equal(
    ui.el('openEventsLink').getAttribute('href'),
    '/events.html?framework=A&source=github',
  );
  assert.equal(
    ui.el('openUnmappedLink').getAttribute('href'),
    '/events.html?framework=A&source=github&unmapped=true',
  );
  // Administrators get a shortcut to this source's retention settings.
  const retention = [...ui.document.querySelectorAll('a')].find(
    (a) => a.textContent === 'Evidence retention',
  );
  assert.equal(retention.getAttribute('href'), '/admin.html?retention_source=github#retention');
  assert.equal(ui.el('eventResultMeta').textContent, '1–50 of 130');
  const rows = [...ui.el('eventRows').querySelectorAll('tr')];
  assert.equal(ui.el('eventRows').querySelector('img'), null);
  assert.equal(ui.el('eventRows').querySelector('b'), null);
  assert.match(rows[0].textContent, /repo\/<b>keen<\/b>/);
  assert.match(rows[1].textContent, /sys/);
  // Five control badges are shown, with the remainder summarised.
  assert.equal(rows[0].querySelectorAll('a.badge').length, 5);
  assert.match(rows[0].textContent, /\+2/);
  // Only absolute HTTP(S) upstream links are rendered.
  assert.ok(rows[0].querySelector('a[href="https://github.example/commit/1"]'));
  assert.equal(rows[1].querySelector('a[href^="javascript"]'), null);
  assert.match(rows[1].textContent, /—/);
  assert.equal(lastEvents(ui).searchParams.get('source'), 'github');
});

test('event filters normalise dates, persist in the URL and drive paging', async (t) => {
  const ui = await sourcePage(t);
  ui.el('eventQ').value = '  deploy ';
  ui.el('eventStartDate').value = '2026-05-31';
  ui.el('eventEndDate').value = '2026-05-01';
  ui.el('eventLimit').value = '100';
  ui.el('eventUnmapped').checked = true;
  ui.el('sourceEventFilters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => lastEvents(ui).searchParams.get('q') === 'deploy');
  const sent = lastEvents(ui).searchParams;
  // Reversed dates are swapped into a usable range.
  assert.equal(sent.get('start_date'), '2026-05-01');
  assert.equal(sent.get('end_date'), '2026-05-31');
  assert.equal(sent.get('unmapped'), 'true');
  assert.equal(sent.get('limit'), '100');
  assert.match(
    ui.window.location.search,
    /q=deploy.*start_date=2026-05-01.*unmapped=true.*limit=100/,
  );
  ui.el('eventNext').click();
  await settle(() => lastEvents(ui).searchParams.get('offset') === '100');
  // Previous becomes available once the second page has loaded.
  await settle(() => !ui.el('eventPrev').disabled);
  assert.equal(ui.el('eventNext').disabled, true);
  ui.el('eventPrev').click();
  await settle(() => lastEvents(ui).searchParams.get('offset') === '0');
  ui.el('eventReset').click();
  await settle(() => !lastEvents(ui).searchParams.has('q'));
  assert.equal(ui.el('eventUnmapped').checked, false);
  assert.equal(ui.el('eventStartDate').value, '');
});

test('initial URL filters are validated before use', async (t) => {
  const ui = await sourcePage(t, {
    query: '&source=github&q=x&start_date=bad&limit=7&offset=-3&unmapped=yes',
  });
  const sent = lastEvents(ui).searchParams;
  assert.equal(sent.get('limit'), '50');
  assert.equal(sent.get('offset'), '0');
  assert.equal(sent.get('unmapped'), 'true');
  assert.equal(ui.el('eventStartDate').value, '');
  assert.equal(ui.el('eventUnmapped').checked, true);
});

test('effectiveness measures show the latest entry recorded against this source', async (t) => {
  const ui = await sourcePage(t);
  ui.el('sourceEffectivenessTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('sourceEffectivenessRows').querySelectorAll('tr').length === 2);
  assert.match(ui.window.location.search, /tab=effectiveness/);
  const [first, second] = ui.el('sourceEffectivenessRows').querySelectorAll('tr');
  assert.equal(ui.el('sourceEffectivenessRows').querySelector('b'), null);
  assert.equal(ui.el('sourceEffectivenessRows').querySelector('i'), null);
  assert.match(first.textContent, /3 h/);
  assert.match(first.textContent, /Meets target/);
  assert.match(first.textContent, /Feb/);
  assert.match(first.textContent, /REF-2/);
  assert.ok(first.querySelector('a[href="https://github.example/report"]'));
  assert.match(first.querySelector('a.font-monospace').getAttribute('href'), /event\.html\?id=e9/);
  assert.match(first.querySelector('code').textContent, /mttr/);
  assert.match(second.textContent, /Count/);
  assert.match(ui.el('sourceEffectivenessMeta').textContent, /recorded against github/);
  // Switching back clears the tab parameter; the tab loads once.
  ui.el('sourceEventsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.doesNotMatch(ui.window.location.search, /tab=/);
  ui.el('sourceVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=visualisations/);
  const before = ui.calls.length;
  ui.el('sourceEffectivenessTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.calls.length, before);
});

test('effectiveness deep links load immediately; empty and failed loads are explained', async (t) => {
  const empty = await sourcePage(t, { query: '&source=github&tab=effectiveness', measures: [] });
  await settle(() =>
    /No Effectiveness Measures have metric entries for github/.test(
      empty.el('sourceEffectivenessRows').textContent,
    ),
  );
  const failed = await sourcePage(t, {
    query: '&source=github&tab=effectiveness-measures',
    fail: { effectiveness: true },
  });
  await settle(() =>
    /Failed to load Effectiveness Measures/.test(failed.el('sourceEffectivenessRows').textContent),
  );
});

test('readers without ISMS access do not see source effectiveness measures', async (t) => {
  const ui = await sourcePage(t, { me: { can_view_events: true } });
  assert.ok(ui.el('sourceEffectivenessTabItem').classList.contains('d-none'));
  assert.ok(
    ![...ui.document.querySelectorAll('a')].some((a) => a.textContent === 'Evidence retention'),
  );
  ui.el('sourceEffectivenessTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.ok(!ui.calls.some((c) => c.url.includes('/effectiveness-measures')));
});

test('source summary and event failures are reported', async (t) => {
  const ui = await sourcePage(t, { fail: { sources: true, events: true } });
  assert.equal(ui.el('sourceMeta').textContent, 'Source summary unavailable.');
  await settle(() =>
    /Failed to load source events: Error: Events offline/.test(ui.el('status').textContent),
  );
  assert.match(ui.el('eventRows').textContent, /Failed to load source events/);
  const empty = await sourcePage(t, { events: [] });
  assert.match(empty.el('eventRows').textContent, /No events match this source/);
});

test('a source page without a source name explains how to open one', async (t) => {
  const ui = await sourcePage(t, { query: '' });
  assert.equal(ui.el('status').textContent, 'Missing source name.');
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/events')));
});

async function sourcesPage(t, options = {}) {
  const ui = await page(t, 'sources', {
    query: options.query || '',
    get: async (u) => {
      if (u.pathname === '/api/v1/sources') {
        if (options.fail) throw new Error('Offline');
        return { items: options.items || sources.slice(0, 2) };
      }
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(
    () =>
      ui.el('rows').querySelector('td:not([colspan])') ||
      /No sources|Failed/.test(ui.el('rows').textContent),
  );
  return ui;
}

test('sources list links each source and its unmapped events', async (t) => {
  const ui = await sourcesPage(t);
  const rows = [...ui.el('rows').querySelectorAll('tr')];
  assert.equal(rows.length, 2);
  const links = [...rows[0].querySelectorAll('a.btn')].map((a) => a.getAttribute('href'));
  assert.deepEqual(links, [
    '/source.html?framework=A&source=github',
    '/source.html?framework=A&source=github&unmapped=true',
  ]);
  assert.match(rows[0].textContent, /1200[\s\S]*1000[\s\S]*200/);
  ui.el('sourcesVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=visualisations/);
  ui.el('sourcesRegisterTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.doesNotMatch(ui.window.location.search, /tab=/);
  ui.el('reload').click();
  await settle(() => ui.calls.filter((c) => c.url.startsWith('/api/v1/sources')).length === 2);
});

test('the sources list can be filtered by name with an escaped filter label', async (t) => {
  const ui = await sourcesPage(t, { query: '&source=AWS' });
  assert.equal(ui.el('rows').querySelectorAll('tr').length, 1);
  assert.match(ui.el('sourcesFilter').textContent, /Filtered: AWS/);
  const none = await sourcesPage(t, { query: '&source=<b>zzz</b>' });
  assert.equal(none.el('sourcesFilter').querySelector('b'), null);
  assert.match(none.el('rows').textContent, /No sources match "<b>zzz<\/b>"/);
  assert.equal(none.el('rows').querySelector('b'), null);
});

test('the register narrows by several source types, a name and coverage, keeping the address current', async (t) => {
  const ui = await sourcesPage(t, { query: '&type=aws,retired' });
  const shown = () =>
    [...ui.el('rows').querySelectorAll('tr td[data-sort]:first-child')].map(
      (td) => td.dataset.sort,
    );
  assert.deepEqual(shown(), ['aws']);
  assert.deepEqual(chosenChips(ui, 'sourceTypes'), [
    ['aws', 'aws'],
    ['retired', 'retired'],
  ]);
  assert.deepEqual(filterChips(ui, 'sourceTypes'), [
    ['aws', 'aws'],
    ['retired', 'retired'],
    ['github', 'GitHub (github)'],
  ]);
  assert.match(ui.el('sourcesFilter').textContent, /Filtered: 2 source types/);

  pickFilter(ui, 'sourceTypes', 'github');
  assert.deepEqual(shown(), ['github', 'aws']);
  const address = () => new URL(ui.window.location.href).searchParams;
  assert.equal(address().get('source'), 'aws,retired,github');
  assert.equal(address().has('type'), false);

  ui.el('sourcesCoverage').value = 'unmapped';
  ui.el('sourcesCoverage').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(shown(), ['github']);
  assert.equal(address().get('coverage'), 'unmapped');
  ui.el('sourcesCoverage').value = 'none';
  ui.el('sourcesCoverage').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(shown(), ['aws']);

  // The name filter reads labels as well as source keys.
  ui.el('sourcesCoverage').value = '';
  ui.el('sourcesCoverage').dispatchEvent(new ui.window.Event('change'));
  ui.el('sourcesSearch').value = 'hub';
  ui.el('sourcesSearch').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(shown(), ['github']);
  assert.equal(address().get('q'), 'hub');
  assert.match(ui.el('sourcesFilter').textContent, /name contains “hub”/);
  ui.el('sourcesSearch').value = 'nothing';
  ui.el('sourcesSearch').dispatchEvent(new ui.window.Event('input'));
  assert.match(
    ui.el('rows').textContent,
    /No sources match "3 source types; name contains “nothing”"/,
  );
});

test('the name filter reads source labels as well as keys', async (t) => {
  const ui = await sourcesPage(t, {
    items: [
      {
        source: 'github',
        label: 'Code host',
        total_events: 1,
        mapped_events: 1,
        unmapped_events: 0,
      },
      { source: 'aws', label: '', total_events: 1, mapped_events: 1, unmapped_events: 0 },
    ],
  });
  ui.el('sourcesSearch').value = 'code';
  ui.el('sourcesSearch').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(
    [...ui.el('rows').querySelectorAll('td[data-sort]:first-child')].map((td) => td.dataset.sort),
    ['github'],
  );
});

test('filters set in the address narrow the register when it loads', async (t) => {
  const ui = await sourcesPage(t, { query: '&q=git&coverage=mapped' });
  assert.equal(ui.el('rows').querySelectorAll('tr').length, 1);
  assert.equal(ui.el('sourcesSearch').value, 'git');
  assert.equal(ui.el('sourcesCoverage').value, 'mapped');
  const unknown = await sourcesPage(t, { query: '&coverage=weird' });
  assert.equal(unknown.el('sourcesCoverage').value, '');
  assert.equal(unknown.el('rows').querySelectorAll('tr').length, 2);
});

test('source list failures are reported', async (t) => {
  const ui = await sourcesPage(t, { fail: true });
  assert.match(ui.el('status').textContent, /Failed to load sources: Error: Offline/);
});

test('metric source links on the source page only allow web or KEEN URLs', async (t) => {
  const entry = (source_url) => ({
    source_type: 'github',
    recorded_at: '2026-02-01',
    metric_value: 3,
    value_display: '3 h',
    source_url,
    source_title: 'Report',
  });
  const ui = await sourcePage(t, {
    query: '&source=github&tab=effectiveness',
    measures: [
      { id: 'm1', metric: 'Scripted', metric_entries: [entry('javascript:alert(1)')] },
      {
        id: 'm2',
        metric: 'Data',
        metric_entries: [entry('data:text/html,<script>alert(1)</script>')],
      },
      { id: 'm3', metric: 'Protocol-relative', metric_entries: [entry('//evil.example/x')] },
      { id: 'm4', metric: 'Web', metric_entries: [entry('https://reports.example/1')] },
    ],
  });
  ui.el('sourceEffectivenessTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('sourceEffectivenessRows').querySelectorAll('tr').length === 4);
  const hrefs = [...ui.el('sourceEffectivenessRows').querySelectorAll('tr')].map((row) =>
    [...row.querySelectorAll('a')].find((a) => a.textContent === 'Report').getAttribute('href'),
  );
  assert.deepEqual(hrefs, ['#', '#', '#', 'https://reports.example/1']);
});
