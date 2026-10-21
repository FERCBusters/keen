// Visualisation workflows through the real page module, real d3 and real SVG:
// the histogram and its zoom, sunburst zoom and "Other" drill-down, links from
// marks to filtered events, exports, share links and saved defaults.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3 = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');
const DAY = 864e5;
const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);
const today = isoDay(Date.now());
const weekAgo = isoDay(Date.now() - 6 * DAY);
const range = `start_date=${weekAgo}&end_date=${today}`;

// Sources with the controls their events map to, as the graph endpoints describe them.
function graph(sources) {
  const nodes = [];
  const links = [];
  const controls = new Map();
  for (const s of sources) {
    const total = Object.values(s.controls).reduce((a, b) => a + b, 0);
    nodes.push({
      id: 'source:' + s.key,
      type: 'source',
      node_kind: 'source',
      label: s.label,
      source: s.key,
      color: s.color || '#2563eb',
      mapped_events: total,
    });
    for (const [ref, n] of Object.entries(s.controls)) {
      controls.set(ref, (controls.get(ref) || 0) + n);
      links.push({ source: 'source:' + s.key, target: 'control:' + ref, value: n });
    }
  }
  for (const [ref, n] of controls)
    nodes.push({
      id: 'control:' + ref,
      type: 'control',
      node_kind: 'control',
      label: ref,
      control_id: ref + '-id',
      title: 'Control ' + ref,
      mapped_events: n,
    });
  return { nodes, links };
}

const twoSources = graph([
  { key: 'github', label: 'GitHub', color: '#24292f', controls: { A1: 6, A2: 2 } },
  { key: 'okta', label: 'Okta', color: '#007dc1', controls: { A1: 1 } },
]);

// Six visible sources and seven single-event ones that merge into "Other".
const manySources = graph([
  { key: 'github', label: 'GitHub', controls: { A1: 300, A2: 200 } },
  { key: 'okta', label: 'Okta', controls: { A3: 300 } },
  ...['aws', 'gcp', 'jira', 'loki'].map((key) => ({
    key,
    label: key.toUpperCase(),
    controls: { A4: 50 },
  })),
  ...Array.from({ length: 7 }, (_, i) => ({
    key: 'tiny' + i,
    label: 'Tiny ' + i,
    controls: { A5: 1 },
  })),
]);

const daySeries = {
  interval: 'day',
  items: [
    { bucket: '2026-10-01', total: 5, mapped: 3, unmapped: 2, by_source: { github: 3, okta: 2 } },
    { bucket: '2026-10-02', total: 4, mapped: 4, unmapped: 0, by_source: { github: 1, okta: 3 } },
  ],
  sources: [
    { source: 'github', label: 'GitHub', color: '#24292f', total: 4 },
    { source: 'okta', label: 'Okta', color: '#007dc1', total: 5 },
  ],
};

async function viz(t, options = {}) {
  const opened = [];
  const copied = [];
  const files = [];
  const series = [];
  const patches = [];
  const realCreate = URL.createObjectURL;
  URL.createObjectURL = (blob) => {
    files.push({ blob });
    return 'blob:https://keen.example/' + files.length;
  };
  t.after(() => {
    URL.createObjectURL = realCreate;
  });
  let win;
  const ui = await page(t, 'visualisation', {
    htmlName: options.htmlName || 'sources',
    scripts: [d3],
    query: (options.mode ? '&mode=' + options.mode : '') + (options.query || ''),
    me: options.me || { is_admin: true },
    globals: {
      XMLSerializer: function XMLSerializer() {
        return new win.XMLSerializer();
      },
      ...(options.globals || {}),
    },
    setup(window) {
      win = window;
      window.document.getElementById('embeddedVisualisationsPane').classList.add('active');
      // A theme like the real page: light text on a dark background, and a
      // caption the stylesheet hides.
      const theme = window.document.createElement('style');
      theme.textContent =
        'body { background-color: rgb(24, 24, 32); } svg text { fill: rgb(250, 250, 250); } .hidden-caption { display: none; }';
      window.document.head.append(theme);
      window.document.querySelector('[data-keen-visualisation-mount]').dataset.vizModes = 'all';
      window.open = (href, target, features) => {
        opened.push({ href, target, features });
        return null;
      };
      Object.defineProperty(window.navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async (text) => void copied.push(text) },
      });
      // Downloads name their file on the anchor they click.
      window.HTMLAnchorElement.prototype.click = function () {
        files[files.length - 1].name = this.download;
      };
    },
    get: async (u) => {
      if (u.pathname.startsWith('/api/v1/graph/')) {
        if (options.graphFail) throw new Error('Graph offline');
        return typeof options.graph === 'function' ? options.graph(u) : options.graph || twoSources;
      }
      if (u.pathname === '/api/v1/charts/event_volume_timeseries') {
        series.push(Object.fromEntries(u.searchParams));
        if (options.seriesFail) throw new Error('Series offline');
        return typeof options.series === 'function' ? options.series(u) : options.series;
      }
      if (u.pathname === '/api/v1/stats/controls') return options.stats(u);
      if (options.get) return options.get(u);
      throw new Error('Unexpected GET ' + u);
    },
    post: options.post,
    patch: async (u, body) => {
      assert.equal(u.pathname, '/api/v1/me/preferences');
      // Bodies come from the page's realm; compare them as plain data.
      patches.push(JSON.parse(JSON.stringify(body)));
      if (options.patchFail) throw new Error(options.patchFail);
      return body;
    },
  });
  await settle(() => /Loaded\.|Failed/.test(ui.el('status').textContent));
  const click = (el, init = {}) =>
    el.dispatchEvent(
      new ui.window.MouseEvent('click', { bubbles: true, cancelable: true, ...init }),
    );
  const titled = (selector, title) =>
    [...ui.document.querySelectorAll(selector)].find(
      (el) => el.querySelector(':scope > title')?.textContent === title,
    );
  return { ...ui, opened, copied, files, series, patches, click, titled };
}

const bars = (ui) => ui.document.querySelectorAll('#viz g.layer rect');
const legend = (ui) =>
  [...ui.document.querySelectorAll('#viz .viz-legend text')].map((n) => n.textContent);

// --- Histogram ---------------------------------------------------------------------

test('the histogram stacks daily volume by source and opens one day of events', async (t) => {
  const ui = await viz(t, { mode: 'histogram', series: daySeries });
  await settle(() => bars(ui).length === 4);

  assert.deepEqual(ui.series, [
    {
      framework: 'A',
      start_date: weekAgo,
      end_date: today,
      breakdown: 'source',
      interval: 'day',
    },
  ]);
  // Stacks run from the busiest source; the legend follows the same order.
  assert.deepEqual(legend(ui), ['Okta', 'GitHub']);
  assert.equal(
    ui.el('vizCaption').textContent,
    'Mapped counts are distinct mapped events. Daily volume from ' +
      `${weekAgo} to ${today} (7 days). Shift-drag (or toggle Zoom mode) on the chart to ` +
      'select a time window.',
  );
  const bar = ui.titled(
    '#viz g.layer rect',
    '2026-10-02\nGitHub: 1\nTotal: 4\nMapped: 4\nUnmapped: 0',
  );
  ui.click(bar, { ctrlKey: true });
  assert.deepEqual(ui.opened, [
    {
      href: '/events.html?framework=A&source=github&start_date=2026-10-02&end_date=2026-10-02',
      target: '_blank',
      features: 'noopener',
    },
  ]);
  // There is no zoom to go back from.
  assert.ok(ui.el('histBack').classList.contains('d-none'));
});

test('sources beyond the twelfth share an Other stack that opens unfiltered events', async (t) => {
  const keys = Array.from({ length: 14 }, (_, i) => 'src' + i);
  const ui = await viz(t, {
    mode: 'histogram',
    series: {
      interval: 'day',
      items: [
        {
          bucket: '2026-10-03',
          total: 105,
          mapped: 105,
          unmapped: 0,
          by_source: Object.fromEntries(keys.map((k, i) => [k, 14 - i])),
        },
      ],
      sources: keys.map((k, i) => ({ source: k, label: 'Source ' + i, total: 14 - i })),
    },
  });
  await settle(() => bars(ui).length === 13);

  assert.equal(legend(ui).length, 13);
  assert.equal(legend(ui).at(-1), 'Other');
  // The two quietest sources (2 + 1 events) make up the Other stack.
  const other = ui.titled(
    '#viz g.layer rect',
    '2026-10-03\nOther: 3\nTotal: 105\nMapped: 105\nUnmapped: 0',
  );
  ui.click(other, { metaKey: true });
  assert.equal(
    ui.opened[0].href,
    '/events.html?framework=A&start_date=2026-10-03&end_date=2026-10-03',
  );

  ui.el('vizExportCsv').click();
  const [csv] = ui.files;
  assert.equal(csv.name, `keen_histogram_${weekAgo}_to_${today}.csv`);
  const [header, row] = (await csv.blob.text()).trim().split('\n');
  assert.equal(
    header,
    'bucket,total,' +
      keys
        .slice(0, 12)
        .map((k, i) => 'Source ' + i)
        .join(',') +
      ',Other',
  );
  assert.equal(row, '2026-10-03,105,14,13,12,11,10,9,8,7,6,5,4,3,3');
});

test('a zoomed histogram asks for the precise window and Back returns to days', async (t) => {
  const start = '2026-10-02T00:00:00.000Z';
  const end = '2026-10-02T03:00:00.000Z';
  const ui = await viz(t, {
    mode: 'histogram',
    query: `&hstart=${start}&hend=${end}&hint=hour`,
    series: (u) =>
      u.searchParams.get('interval') === 'hour'
        ? {
            interval: 'hour',
            start_ts: start,
            end_ts: end,
            items: ['00', '01', '02'].map((h) => ({
              bucket: `2026-10-02T${h}:00:00Z`,
              total: 2,
              mapped: 2,
              unmapped: 0,
              by_source: { github: 2 },
            })),
            sources: [{ source: 'github', label: 'GitHub', total: 6 }],
          }
        : daySeries,
  });
  await settle(() => bars(ui).length === 3);

  assert.deepEqual(ui.series[0], {
    framework: 'A',
    start_date: weekAgo,
    end_date: today,
    breakdown: 'source',
    interval: 'hour',
    start_ts: start,
    end_ts: end,
  });
  assert.match(
    ui.el('vizCaption').textContent,
    new RegExp(`Hour volume from ${start.replaceAll('.', '\\.')} to ${end.replaceAll('.', '\\.')}`),
  );
  assert.ok(!ui.el('histBack').classList.contains('d-none'));
  // An hour bar opens exactly that hour, with the day for the date pickers.
  ui.click(ui.document.querySelectorAll('#viz g.layer rect')[1], { ctrlKey: true });
  assert.equal(
    ui.opened[0].href,
    '/events.html?framework=A&source=github&start_ts=2026-10-02T01%3A00%3A00.000Z' +
      '&end_ts=2026-10-02T02%3A00%3A00.000Z&start_date=2026-10-02&end_date=2026-10-02',
  );

  ui.el('histBack').click();
  await settle(() => bars(ui).length === 4);
  assert.deepEqual(ui.series.at(-1), {
    framework: 'A',
    start_date: weekAgo,
    end_date: today,
    breakdown: 'source',
    interval: 'day',
  });
  assert.ok(ui.el('histBack').classList.contains('d-none'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('hstart'), null);
});

test('a coarser interval from the server replaces the requested zoom level', async (t) => {
  const ui = await viz(t, {
    mode: 'histogram',
    query: '&hstart=2026-10-02T00:00:00Z&hend=2026-10-02T02:00:00Z&hint=second',
    series: {
      interval: 'minute',
      start_ts: '2026-10-02T00:00:00Z',
      end_ts: '2026-10-02T02:00:00Z',
      items: [
        {
          bucket: '2026-10-02T00:01:00Z',
          total: 1,
          mapped: 0,
          unmapped: 1,
          by_source: { okta: 1 },
        },
      ],
      sources: [{ source: 'okta', label: 'Okta', total: 1 }],
    },
  });
  await settle(() => bars(ui).length === 1);
  assert.equal(ui.series[0].interval, 'second');
  assert.match(ui.el('vizCaption').textContent, /Minute volume from 2026-10-02T00:00:00Z/);

  ui.el('copyVizLink').click();
  await settle(() => ui.el('status').textContent === 'Copied link to clipboard.');
  const shared = new URL(ui.copied[0]).searchParams;
  assert.deepEqual(
    [shared.get('mode'), shared.get('hstart'), shared.get('hend'), shared.get('hint')],
    ['histogram', '2026-10-02T00:00:00Z', '2026-10-02T02:00:00Z', 'minute'],
  );
});

test('an empty window says so, and a failed request is reported', async (t) => {
  const empty = await viz(t, {
    mode: 'histogram',
    series: { interval: 'day', items: [], sources: [] },
  });
  await settle(() => empty.el('viz').textContent === 'No events in the selected window.');

  const failed = await viz(t, { mode: 'histogram', seriesFail: true });
  await settle(() => /Failed to load histogram data/.test(failed.el('status').textContent));
  assert.match(failed.el('status').textContent, /Series offline/);
  assert.equal(bars(failed).length, 0);
});

test('Days applies a window ending on the chosen end date and leaves any zoom', async (t) => {
  const ui = await viz(t, {
    mode: 'histogram',
    query: '&hstart=2026-10-02T00:00:00Z&hend=2026-10-02T02:00:00Z&hint=hour',
    series: daySeries,
  });
  await settle(() => ui.series.length === 1);
  ui.el('histDays').value = '3';
  ui.el('histApply').click();
  await settle(() => ui.series.length === 2);

  assert.deepEqual(ui.series[1], {
    framework: 'A',
    start_date: isoDay(Date.now() - 2 * DAY),
    end_date: today,
    breakdown: 'source',
    interval: 'day',
  });
  assert.ok(ui.el('histBack').classList.contains('d-none'));
  assert.equal(new URL(ui.window.location.href).searchParams.get('hint'), null);
});

test('a typed date range reloads in order, and a blank date is refused', async (t) => {
  const ui = await viz(t, { mode: 'histogram', series: daySeries });
  await settle(() => ui.series.length === 1);

  // The later date typed first is put back in order.
  ui.el('dateFromDmy').value = '07/10/2026';
  ui.el('dateToDmy').value = '01/10/2026';
  ui.el('dateApply').click();
  await settle(() => ui.series.length === 2);
  assert.equal(ui.series[1].start_date, '2026-10-01');
  assert.equal(ui.series[1].end_date, '2026-10-07');
  assert.deepEqual([ui.el('dateFrom').value, ui.el('dateTo').value], ['2026-10-01', '2026-10-07']);
  assert.equal(ui.el('histDays').value, '7');

  ui.el('dateFromDmy').value = '';
  ui.el('dateApply').click();
  assert.equal(ui.el('status').textContent, 'Please choose both start and end dates.');
  assert.equal(ui.el('status').dataset.alert, 'warning');
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(ui.series.length, 2);
});

// The design system's date field writes the ISO value and reports a change
// when its visible text changes.
function typeDate(ui, name, dmy) {
  ui.el(name + 'Dmy').value = dmy;
  ui.el(name).value = dmy.split('/').reverse().join('-');
  ui.el(name).dispatchEvent(new ui.window.Event('change'));
}

function typeDays(ui, days) {
  ui.el('histDays').value = days;
  ui.el('histDays').dispatchEvent(new ui.window.Event('input'));
}

test('with auto-apply, editing the dates or Days reloads the chart', async (t) => {
  const ui = await viz(t, {
    mode: 'histogram',
    series: daySeries,
    me: { is_admin: true, preferences: { auto_apply_filters: true } },
  });
  await settle(() => ui.series.length === 1);
  const window = () => [ui.series.at(-1).start_date, ui.series.at(-1).end_date];

  typeDate(ui, 'dateFrom', '01/10/2026');
  typeDate(ui, 'dateTo', '10/10/2026');

  await settle(() => window()[1] === '2026-10-10');
  assert.deepEqual(window(), ['2026-10-01', '2026-10-10']);
  assert.equal(ui.el('histDays').value, '10');

  // Days counts back from the end date and stays between one day and ten years.
  const tenYears = isoDay(Date.UTC(2026, 9, 10) - 3649 * DAY);
  for (const [days, start] of [
    ['3', '2026-10-08'],
    ['0', '2026-10-10'],
    ['99999', tenYears],
  ]) {
    typeDays(ui, days);
    await settle(() => window()[0] === start);
    assert.deepEqual(window(), [start, '2026-10-10'], days);
    assert.equal(ui.el('dateFromDmy').value, start.split('-').reverse().join('/'));
    assert.equal(ui.el('dateToDmy').value, '10/10/2026');
  }
});

test('without auto-apply, edited dates update Days and wait for Apply', async (t) => {
  const ui = await viz(t, { mode: 'histogram', series: daySeries });
  await settle(() => ui.series.length === 1);

  typeDate(ui, 'dateFrom', '01/10/2026');
  typeDate(ui, 'dateTo', '04/10/2026');
  assert.equal(ui.el('histDays').value, '4');
  typeDays(ui, '2');

  assert.deepEqual([ui.el('dateFrom').value, ui.el('dateTo').value], ['2026-10-03', '2026-10-04']);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(ui.series.length, 1);
  ui.el('dateApply').click();
  await settle(() => ui.series.length === 2);
  assert.deepEqual([ui.series[1].start_date, ui.series[1].end_date], ['2026-10-03', '2026-10-04']);
});

test('Zoom mode and Shift switch the histogram brush on and off', async (t) => {
  const ui = await viz(t, { mode: 'histogram', series: daySeries });
  await settle(() => bars(ui).length === 4);
  const brush = () => ui.document.querySelector('#viz .hist-main-brush');
  const overlay = () => brush().querySelector('.overlay').style.pointerEvents;
  assert.equal(overlay(), 'none');

  ui.el('histZoomMode').click();
  assert.equal(ui.el('histZoomMode').textContent, 'Zoom mode: on');
  assert.ok(brush().classList.contains('hist-brush-enabled'));
  assert.equal(overlay(), 'all');
  assert.equal(
    ui.el('status').textContent,
    'Zoom mode enabled: drag on the histogram to select a time window. Turn it off to click bars.',
  );
  ui.el('histZoomMode').click();
  assert.equal(ui.el('histZoomMode').textContent, 'Zoom mode');
  assert.equal(overlay(), 'none');
  assert.equal(
    ui.el('status').textContent,
    'Zoom mode disabled. Tip: hold Shift and drag to zoom temporarily.',
  );

  ui.window.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Shift' }));
  assert.equal(overlay(), 'all');
  ui.window.dispatchEvent(new ui.window.KeyboardEvent('keyup', { key: 'Shift' }));
  assert.equal(overlay(), 'none');
  ui.window.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Shift' }));
  ui.window.dispatchEvent(new ui.window.Event('blur'));
  assert.equal(overlay(), 'none');
});

test('the histogram exports what it shows, and saves a relative default range', async (t) => {
  const ui = await viz(t, { mode: 'histogram', series: daySeries });
  await settle(() => bars(ui).length === 4);

  ui.el('vizExportCsv').click();
  ui.el('vizExportJson').click();
  const hidden = ui.document.createElementNS('http://www.w3.org/2000/svg', 'text');
  hidden.setAttribute('class', 'hidden-caption');
  ui.el('viz').append(hidden);
  ui.el('vizExportSvg').click();
  const [csv, json, svg] = ui.files;
  assert.deepEqual(
    [csv.name, json.name, svg.name],
    [
      `keen_histogram_${weekAgo}_to_${today}.csv`,
      'keen_visualisation.json',
      `keen_histogram_${weekAgo}_to_${today}.svg`,
    ],
  );
  assert.equal(
    await csv.blob.text(),
    'bucket,total,Okta,GitHub\n2026-10-01,5,2,3\n2026-10-02,4,3,1\n',
  );
  const exported = JSON.parse(await json.blob.text());
  assert.equal(exported.mode, 'histogram');
  assert.deepEqual(exported.range, { from: weekAgo, to: today });
  assert.deepEqual(exported.last_view.payload.stack_keys, ['okta', 'github']);
  assert.deepEqual(exported.last_view.payload.items, daySeries.items);
  const drawing = await svg.blob.text();
  assert.match(drawing, /^<svg[^>]* xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  assert.equal((drawing.match(/<rect/g) || []).length >= 4, true);
  // The file carries what the stylesheet and theme gave the chart on screen:
  // text colours are written onto each element and the page background becomes
  // a backdrop, so the drawing reads the same way outside KEEN.
  assert.match(
    drawing,
    /<rect width="100%" height="100%" fill="rgb\(24, 24, 32\)" data-export-backdrop=""/,
  );
  assert.match(drawing, /<text[^>]*style="[^"]*fill: rgb\(250, 250, 250\)/);
  assert.doesNotMatch(drawing, /<text[^>]*class="hidden-caption"/);

  // A window that ends today is saved as "the last N days".
  ui.el('saveDefaultViz').click();
  await settle(() => ui.el('status').textContent === 'Saved as your default visualisation.');
  assert.deepEqual(ui.patches[0], {
    default_visualisation: { mode: 'histogram', date_range: { days: 7 } },
  });

  ui.el('dateFromDmy').value = '01/09/2026';
  ui.el('dateToDmy').value = '15/09/2026';
  ui.el('dateApply').click();
  await settle(() => ui.series.length === 2);
  ui.el('saveDefaultViz').click();
  await settle(() => ui.patches.length === 2);
  assert.deepEqual(ui.patches[1].default_visualisation.date_range, {
    start_date: '2026-09-01',
    end_date: '2026-09-15',
  });
});

test('a default needs a whole date range, and a refused save is reported', async (t) => {
  const ui = await viz(t, {
    mode: 'histogram',
    series: daySeries,
    patchFail: 'Preferences offline',
  });
  await settle(() => ui.series.length === 1);

  ui.el('saveDefaultViz').click();
  await settle(() => /Could not save/.test(ui.el('status').textContent));
  assert.equal(
    ui.el('status').textContent,
    'Could not save default visualisation: Error: Preferences offline',
  );
  assert.equal(ui.el('status').dataset.alert, 'danger');

  ui.el('dateFrom').value = '';
  ui.el('saveDefaultViz').click();
  assert.equal(ui.el('status').textContent, 'Please choose a valid date range first.');
  assert.equal(ui.el('status').dataset.alert, 'warning');
  assert.equal(ui.patches.length, 1);
});

// --- Sunbursts ----------------------------------------------------------------------

const centre = (ui) =>
  [...ui.document.querySelectorAll('#viz > g > text.small-muted')].map((n) => n.textContent);
const arc = (ui, title) => ui.titled('#viz path.sunburst-arc', title);

test('small sources merge into one Other wedge whose breakdown links to their events', async (t) => {
  const ui = await viz(t, { mode: 'sunburst_source', graph: manySources });

  // Six named sources, then a single Other wedge spanning both rings.
  const rings = [...ui.document.querySelectorAll('#viz path.sunburst-arc title')].map(
    (n) => n.textContent.split('\n')[0],
  );
  assert.deepEqual(
    rings.filter((name) => !name.includes('→')),
    ['GitHub', 'Okta', 'AWS', 'GCP', 'JIRA', 'LOKI', 'Other'],
  );
  const other = arc(ui, 'Other\nMerged: 7 segments\nMapped events: 7');
  assert.ok(other);
  assert.equal(ui.el('sunburstOtherPanel').style.display, 'none');

  // Shift-click lists the merged sources instead of drilling in.
  ui.click(other, { shiftKey: true });
  const panel = ui.el('sunburstOtherPanel');
  assert.equal(panel.style.display, '');
  assert.match(panel.textContent, /Sources merged into "Other"/);
  assert.match(panel.textContent, new RegExp(`7 segments • 7 events • ${weekAgo} → ${today}`));
  const links = [...panel.querySelectorAll('tbody a')];
  assert.deepEqual(
    links.map((a) => a.textContent),
    Array.from({ length: 7 }, (_, i) => 'Tiny ' + i),
  );
  assert.equal(links[0].getAttribute('href'), `/events.html?framework=A&source=tiny0&${range}`);
});

test('clicking Other drills into the merged sources and the centre goes back', async (t) => {
  const ui = await viz(t, { mode: 'sunburst_source', graph: manySources });
  ui.click(arc(ui, 'Other\nMerged: 7 segments\nMapped events: 7'));

  assert.equal(ui.el('vizTitle').textContent, 'Sunburst: by source — Other');
  const inner = [...ui.document.querySelectorAll('#viz path.sunburst-arc title')]
    .map((n) => n.textContent.split('\n')[0])
    .filter((name) => !name.includes('→'));
  assert.deepEqual(
    inner,
    Array.from({ length: 7 }, (_, i) => 'Tiny ' + i),
  );
  assert.equal(centre(ui).at(-1), 'Click center to go back');

  ui.click(ui.document.querySelector('#viz .sunburst-center-target'));
  assert.equal(ui.el('vizTitle').textContent, 'Sunburst: by source');
  assert.ok(arc(ui, 'Other\nMerged: 7 segments\nMapped events: 7'));
});

test('a source zooms in, its control opens events, and the zoom is saved as the default', async (t) => {
  const ui = await viz(t, { mode: 'sunburst_source', graph: manySources });
  assert.equal(centre(ui)[0], 'All');

  ui.click(arc(ui, 'GitHub\nMapped events: 500'));
  const [label, breadcrumb] = centre(ui);
  assert.deepEqual([label, breadcrumb], ['GitHub', 'All › GitHub']);

  ui.click(arc(ui, 'GitHub → A1\nMapped events: 300'), { ctrlKey: true });
  assert.equal(ui.opened[0].href, `/events.html?framework=A&source=github&control=A1&${range}`);

  ui.el('saveDefaultViz').click();
  await settle(() => ui.patches.length === 1);
  assert.deepEqual(ui.patches[0], {
    default_visualisation: {
      mode: 'sunburst_source',
      date_range: { days: 7 },
      sunburst_focus: { source_key: 'github' },
    },
  });

  // The centre zooms back out, and the zoom is no longer saved.
  ui.click(ui.document.querySelector('#viz .sunburst-center-target'));
  assert.equal(centre(ui)[0], 'All');
  ui.el('saveDefaultViz').click();
  await settle(() => ui.patches.length === 2);
  assert.equal('sunburst_focus' in ui.patches[1].default_visualisation, false);

  // Exports carry the source → control edges behind the sunburst.
  ui.el('vizExportCsv').click();
  const [csv] = ui.files;
  assert.equal(csv.name, `keen_sunburst_source_${weekAgo}_to_${today}.csv`);
  const lines = (await csv.blob.text()).trim().split('\n');
  assert.equal(lines[0], 'source,source_label,control_id,control_ref,control_title,mapped_events');
  assert.equal(lines[1], 'github,GitHub,A1-id,A1,Control A1,300');
  assert.equal(lines.length, 1 + manySources.links.length);
});

test('a saved focus reopens the sunburst on that source', async (t) => {
  const ui = await viz(t, {
    graph: manySources,
    me: {
      is_admin: true,
      preferences: {
        default_visualisation: {
          mode: 'sunburst_source',
          date_range: { start_date: '2026-09-01', end_date: '2026-09-30' },
          sunburst_focus: { source_key: 'okta' },
        },
      },
    },
  });
  assert.equal(ui.el('vizSelect').value, 'sunburst_source');
  assert.deepEqual(centre(ui).slice(0, 2), ['Okta', 'All › Okta']);
  assert.deepEqual([ui.el('dateFrom').value, ui.el('dateTo').value], ['2026-09-01', '2026-09-30']);
  assert.ok(
    ui.calls.some(
      (c) =>
        c.url ===
        '/api/v1/graph/control_source?framework=A&start_date=2026-09-01&end_date=2026-09-30',
    ),
  );
});

test('the control sunburst zooms into a control and opens its sources', async (t) => {
  const ui = await viz(t, { mode: 'sunburst_control', graph: twoSources });
  ui.click(arc(ui, 'A1\nMapped events: 7'));
  assert.deepEqual(centre(ui).slice(0, 2), ['A1', 'All › A1']);

  ui.click(arc(ui, 'A1 → Okta\nMapped events: 1'), { ctrlKey: true });
  assert.equal(ui.opened[0].href, `/events.html?framework=A&source=okta&control=A1&${range}`);

  ui.el('saveDefaultViz').click();
  await settle(() => ui.patches.length === 1);
  assert.deepEqual(ui.patches[0].default_visualisation.sunburst_focus, { control_id: 'A1-id' });
});

// --- Graphs and heatmaps ------------------------------------------------------------

const clauseSources = {
  nodes: [
    {
      id: 'source:github',
      type: 'source',
      node_kind: 'source',
      label: 'GitHub',
      source: 'github',
      mapped_events: 4,
    },
    {
      id: 'clause:k1',
      type: 'control',
      node_kind: 'clause',
      label: '5.1',
      ref: '5.1',
      clause_id: 'k1',
      mapped_events: 4,
    },
  ],
  links: [{ source: 'source:github', target: 'clause:k1', value: 4 }],
};

test('graph marks open their source, filtered events and clause pages', async (t) => {
  const ui = await viz(t, {
    mode: 'graph',
    graph: (u) => (u.pathname.endsWith('/clause_source') ? clauseSources : twoSources),
  });
  ui.click(ui.titled('#viz g.node-source', 'GitHub\nMapped events: 8'), { ctrlKey: true });
  ui.click(ui.titled('#viz .links path', 'GitHub → A2\nMapped events: 2'), { ctrlKey: true });
  assert.deepEqual(
    ui.opened.map((o) => o.href),
    [
      '/source.html?source=github&framework=A',
      `/events.html?framework=A&source=github&control=A2&${range}`,
    ],
  );
  assert.match(ui.el('vizCaption').textContent, /Showing 2 sources × 2 controls \(edges ≥ 1\)\./);

  ui.el('vizExportCsv').click();
  const rows = (await ui.files[0].blob.text()).trim().split('\n');
  assert.equal(ui.files[0].name, `keen_graph_${weekAgo}_to_${today}.csv`);
  assert.deepEqual(rows.slice(1).sort(), [
    'github,GitHub,A1-id,A1,Control A1,6',
    'github,GitHub,A2-id,A2,Control A2,2',
    'okta,Okta,A1-id,A1,Control A1,1',
  ]);

  // The source ↔ clause graph is fetched only when chosen.
  assert.ok(!ui.calls.some((c) => c.url.includes('/clause_source')));
  ui.el('vizSelect').value = 'graph_clause_events';
  ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.titled('#viz .links path', 'GitHub → 5.1\nMapped events: 4'));
  assert.ok(ui.calls.some((c) => c.url === `/api/v1/graph/clause_source?framework=A&${range}`));
  ui.click(ui.titled('#viz .links path', 'GitHub → 5.1\nMapped events: 4'), { ctrlKey: true });
  ui.click(ui.document.querySelector('#viz g.node-control'), { ctrlKey: true });
  assert.deepEqual(
    ui.opened.slice(2).map((o) => o.href),
    [
      `/events.html?framework=A&source=github&clause=5.1&${range}`,
      '/clause.html?id=k1&framework=A',
    ],
  );
});

// One RSS source split by system: each feed is its own node, as the server
// describes it, with the Events text filter that selects the feed's events.
const feedBreakdown = {
  nodes: [
    {
      id: 'source:rss|system:NVD',
      type: 'source',
      node_kind: 'source',
      label: 'RSS · NVD',
      source: 'rss',
      color: '#f97316',
      mapped_events: 2,
      breakdown: { field: 'system', value: 'NVD' },
      events_filter: 'system:equals:NVD',
    },
    {
      id: 'source:rss|system:',
      type: 'source',
      node_kind: 'source',
      label: 'RSS · Not recorded',
      source: 'rss',
      color: '#f97316',
      mapped_events: 1,
      breakdown: { field: 'system', value: '' },
      events_filter: 'system:not_regex:.',
    },
    {
      id: 'control:A1',
      type: 'control',
      node_kind: 'control',
      label: 'A1',
      control_id: 'A1-id',
      title: 'Control A1',
      mapped_events: 3,
    },
  ],
  links: [
    { source: 'source:rss|system:NVD', target: 'control:A1', value: 2 },
    { source: 'source:rss|system:', target: 'control:A1', value: 1 },
  ],
};

test('a source breaks down into sub-elements that open their own events', async (t) => {
  const requested = [];
  const ui = await viz(t, {
    mode: 'graph',
    graph: (u) => {
      requested.push(u.searchParams.get('breakdown'));
      return u.searchParams.get('breakdown') === 'system' ? feedBreakdown : twoSources;
    },
  });
  // The choice is offered for the source-based views and starts at source only.
  assert.equal(ui.el('controlsBreakdown').classList.contains('d-none'), false);
  assert.equal(ui.el('vizBreakdown').value, '');
  assert.deepEqual(
    [...ui.el('vizBreakdown').options].map((o) => o.value),
    ['', 'connection', 'system', 'actor', 'action', 'outcome'],
  );
  assert.deepEqual(requested, [null]);

  ui.el('vizBreakdown').value = 'system';
  ui.el('vizBreakdown').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.titled('#viz g.node-source', 'RSS · NVD\nMapped events: 2'));
  assert.deepEqual(requested, [null, 'system']);
  assert.match(new URL(ui.window.location.href).searchParams.get('by'), /^system$/);

  // A sub-element has no page of its own: it opens the events behind it, and
  // so does the link to a control; the empty sub-element selects events whose
  // field is not recorded.
  ui.click(ui.titled('#viz g.node-source', 'RSS · NVD\nMapped events: 2'), { ctrlKey: true });
  ui.click(ui.titled('#viz .links path', 'RSS · NVD → A1\nMapped events: 2'), { ctrlKey: true });
  ui.click(ui.titled('#viz .links path', 'RSS · Not recorded → A1\nMapped events: 1'), {
    ctrlKey: true,
  });
  assert.deepEqual(
    ui.opened.map((o) => o.href),
    [
      `/events.html?framework=A&source=rss&f=system%3Aequals%3ANVD&${range}`,
      `/events.html?framework=A&source=rss&f=system%3Aequals%3ANVD&control=A1&${range}`,
      `/events.html?framework=A&source=rss&f=system%3Anot_regex%3A.&control=A1&${range}`,
    ],
  );
  // The distribution links each sub-element to its events, and exports keep
  // the sub-element in the label.
  const rows = [...ui.el('topSources').querySelectorAll('tr')];
  assert.equal(rows[0].textContent.trim().replace(/\s+/g, ' '), 'RSS · NVD 2');
  assert.equal(
    rows[0].querySelector('a').getAttribute('href'),
    `/events.html?framework=A&source=rss&f=system%3Aequals%3ANVD&${range}`,
  );
  ui.el('vizExportCsv').click();
  const csv = (await ui.files[0].blob.text()).trim().split('\n');
  assert.deepEqual(csv.slice(1).sort(), [
    'rss,RSS · NVD,A1-id,A1,Control A1,2',
    'rss,RSS · Not recorded,A1-id,A1,Control A1,1',
  ]);

  // The heatmap reuses the dataset; its cells open the same events.
  ui.el('vizSelect').value = 'heatmap';
  ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.titled('#viz rect.heat-cell', 'RSS · NVD → A1\nMapped events: 2'));
  assert.deepEqual(requested, [null, 'system']);
  ui.click(ui.titled('#viz rect.heat-cell', 'RSS · NVD → A1\nMapped events: 2'), { ctrlKey: true });
  assert.equal(
    ui.opened.at(-1).href,
    `/events.html?framework=A&source=rss&f=system%3Aequals%3ANVD&control=A1&${range}`,
  );

  // Share links carry the breakdown; the histogram has no sources to split.
  ui.el('copyVizLink').click();
  await settle(() => ui.copied.length === 1);
  assert.equal(new URL(ui.copied[0]).searchParams.get('by'), 'system');
  ui.el('vizSelect').value = 'histogram';
  ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('controlsBreakdown').classList.contains('d-none'), true);
});

test('a shared breakdown is restored, and Reset returns to whole sources', async (t) => {
  const requested = [];
  const ui = await viz(t, {
    mode: 'heatmap',
    query: '&by=system',
    graph: (u) => {
      requested.push(u.searchParams.get('breakdown'));
      return feedBreakdown;
    },
  });
  assert.equal(ui.el('vizBreakdown').value, 'system');
  assert.deepEqual(requested, ['system']);
  ui.el('vizReset').click();
  await settle(() => requested.length === 2 && /Loaded\./.test(ui.el('status').textContent));
  assert.equal(ui.el('vizBreakdown').value, '');
  assert.equal(requested[1], null);
  assert.equal(new URL(ui.window.location.href).searchParams.has('by'), false);
  // An unknown breakdown in a link is ignored.
  const odd = await viz(t, { mode: 'graph', query: '&by=summary', graph: twoSources });
  assert.equal(odd.el('vizBreakdown').value, '');
  assert.ok(odd.calls.every((c) => !c.url.includes('breakdown=')));
});

test('the clause ↔ control graph ignores the date window and opens clauses', async (t) => {
  const relationships = {
    graph_kind: 'clause_control_relationship',
    nodes: [
      {
        id: 'clause:k1',
        type: 'source',
        node_kind: 'clause',
        label: '5.1',
        clause_id: 'k1',
        mapped_events: 1,
      },
      {
        id: 'control:c1',
        type: 'control',
        node_kind: 'control',
        label: 'A1',
        control_id: 'c1',
        mapped_events: 1,
      },
    ],
    links: [{ source: 'clause:k1', target: 'control:c1', value: 1, link_kind: 'relationship' }],
  };
  const ui = await viz(t, { mode: 'graph_clause_control', graph: relationships });
  assert.ok(ui.calls.some((c) => c.url === '/api/v1/graph/clause_control?framework=A'));
  assert.ok(ui.titled('#viz .links path', '5.1 → A1\nLink count: 1'));
  ui.click(ui.document.querySelector('#viz g.node-source'), { ctrlKey: true });
  assert.equal(ui.opened[0].href, '/clause.html?id=k1&framework=A');
  assert.match(ui.el('vizCaption').textContent, /Showing 1 clauses × 1 controls/);
});

test('a heatmap cell opens the events behind it; an empty cell does nothing', async (t) => {
  const ui = await viz(t, { mode: 'heatmap', graph: twoSources });
  const cell = ui.titled('#viz rect.heat-cell', 'Okta → A1\nMapped events: 1');
  ui.click(cell, { ctrlKey: true });
  ui.click(ui.titled('#viz rect.heat-cell', 'Okta → A2\nMapped events: 0'), { ctrlKey: true });
  assert.deepEqual(
    ui.opened.map((o) => o.href),
    [`/events.html?framework=A&source=okta&control=A1&${range}`],
  );
  assert.match(
    ui.el('vizCaption').textContent,
    /Showing top 2 sources × top 2 controls \(ranked by mapped events\)\./,
  );
});

test('the heatmap redraws for a new top N without fetching again', async (t) => {
  const ui = await viz(t, { mode: 'heatmap', graph: manySources });
  const graphCalls = () => ui.calls.filter((c) => c.url.startsWith('/api/v1/graph/')).length;
  const before = graphCalls();
  const rows = () => [
    ...new Set(
      [...ui.document.querySelectorAll('#viz rect.heat-cell')].map(
        (cell) => cell.querySelector(':scope > title').textContent.split(' → ')[0],
      ),
    ),
  ];
  assert.equal(rows().length, 13);

  // At least five sources stay in view.
  ui.el('heatmapTopSources').value = '1';
  // The graph's Apply button belongs to the graph views.
  ui.el('graphApply').click();
  assert.equal(rows().length, 13);
  ui.el('heatmapApply').click();

  assert.deepEqual(rows(), ['GitHub', 'Okta', 'AWS', 'GCP', 'JIRA']);
  assert.match(ui.el('vizCaption').textContent, /Showing top 5 sources × top 5 controls/);
  assert.equal(graphCalls(), before);
});

// --- Distribution, gaps, sharing and reset -------------------------------------------

test('the distribution lists the busiest sources and controls with their links', async (t) => {
  const ui = await viz(t, { mode: 'graph', graph: manySources });
  const rows = (id) =>
    [...ui.el(id).querySelectorAll('tr')].map((tr) => [
      tr.querySelector('a').textContent,
      tr.querySelector('a').getAttribute('href'),
      tr.cells[1].textContent,
    ]);
  const sources = rows('topSources');
  // Twelve at most, busiest first; sources open their filtered events.
  assert.equal(sources.length, 12);
  assert.deepEqual(sources[0], [
    'GitHub',
    `/events.html?framework=A&source=github&${range}`,
    '500',
  ]);
  assert.deepEqual(rows('topControls').slice(0, 2), [
    ['A1', '/control.html?id=A1-id&framework=A', '300'],
    ['A3', '/control.html?id=A3-id&framework=A', '300'],
  ]);
});

test('coverage gaps load on demand, follow the search and report failures', async (t) => {
  let fail = false;
  const ui = await viz(t, {
    mode: 'graph',
    stats: (u) => {
      assert.deepEqual(Object.fromEntries(u.searchParams), {
        framework: 'A',
        limit: '5000',
        start_date: weekAgo,
        end_date: today,
      });
      if (fail) throw new Error('Stats offline');
      return {
        items: [
          { id: 'c1', ref: 'A1', title: 'Access', in_scope: true, evidence_count: 4 },
          { id: 'c2', ref: 'A2', title: 'Backups <b>', in_scope: true, evidence_count: 0 },
          { id: 'c3', ref: 'A3', title: 'Crypto', in_scope: true, evidence_count: 0 },
          { id: 'c4', ref: 'A4', title: 'Out of scope', in_scope: false, evidence_count: 0 },
        ],
      };
    },
  });
  assert.equal(
    ui.el('gaps').textContent,
    'Open this panel to load controls without events for the selected date range.',
  );
  ui.el('coverageGapsPanel').classList.add('show');
  ui.el('coverageGapsPanel').dispatchEvent(new ui.window.Event('shown.bs.collapse'));
  await settle(() => ui.el('gaps').querySelectorAll('.gap-item').length === 2);
  const items = [...ui.el('gaps').querySelectorAll('.gap-item a')];
  assert.deepEqual(
    items.map((a) => [a.textContent.replace(/\s+/g, ' ').trim(), a.getAttribute('href')]),
    [
      ['A2 Backups <b>', '/control.html?id=c2&framework=A'],
      ['A3 Crypto', '/control.html?id=c3&framework=A'],
    ],
  );
  assert.equal(ui.el('gaps').querySelector('b'), null);

  ui.el('gapQ').value = 'crypto';
  ui.el('gapQ').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('gaps').querySelectorAll('.gap-item').length, 1);
  ui.el('gapQ').value = 'nothing';
  ui.el('gapQ').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('gaps').textContent, 'No matches.');

  fail = true;
  ui.el('vizReloadGaps').click();
  await settle(() => ui.el('gaps').textContent === 'Failed to load controls.');
  await settle(() => /Stats offline/.test(ui.el('status').textContent));
});

test('a shared link restores the view, and Reset returns to the defaults', async (t) => {
  const ui = await viz(t, {
    mode: 'heatmap',
    query: '&from=2026-09-01&to=2026-09-10&hs=6&hc=7&gs=8&gc=9&minw=3',
    graph: twoSources,
  });
  assert.equal(ui.el('vizSelect').value, 'heatmap');
  assert.deepEqual(
    [
      'heatmapTopSources',
      'heatmapTopControls',
      'graphTopSources',
      'graphTopControls',
      'graphMinEdge',
    ].map((id) => ui.el(id).value),
    ['6', '7', '8', '9', '3'],
  );
  assert.deepEqual([ui.el('dateFrom').value, ui.el('dateTo').value], ['2026-09-01', '2026-09-10']);
  assert.equal(ui.el('histDays').value, '10');

  ui.el('copyVizLink').click();
  await settle(() => ui.copied.length === 1);
  const shared = new URL(ui.copied[0]);
  assert.deepEqual(
    ['mode', 'from', 'to', 'days', 'hs', 'hc', 'gs', 'gc', 'minw'].map((k) =>
      shared.searchParams.get(k),
    ),
    ['heatmap', '2026-09-01', '2026-09-10', '10', '6', '7', '8', '9', '3'],
  );

  const before = ui.calls.length;
  ui.el('vizReset').click();
  await settle(() => ui.calls.length > before && /Loaded\./.test(ui.el('status').textContent));
  assert.equal(ui.el('vizSelect').value, 'graph');
  assert.deepEqual(
    [
      'heatmapTopSources',
      'heatmapTopControls',
      'graphTopSources',
      'graphTopControls',
      'graphMinEdge',
    ].map((id) => ui.el(id).value),
    ['18', '36', '30', '60', '1'],
  );
  assert.deepEqual([ui.el('dateFrom').value, ui.el('dateTo').value], [weekAgo, today]);
  const search = new URL(ui.window.location.href).searchParams;
  assert.deepEqual(
    ['mode', 'from', 'to', 'hs', 'minw'].map((k) => search.get(k)),
    [null, null, null, null, null],
  );
  assert.equal(search.get('framework'), 'A');
});

// --- Stories from the histogram ---------------------------------------------------

const hourSeries = (start, end) => ({
  interval: 'hour',
  start_ts: start,
  end_ts: end,
  items: ['00', '01', '02'].map((h) => ({
    bucket: `2026-10-02T${h}:00:00Z`,
    total: 2,
    mapped: 2,
    unmapped: 0,
    by_source: { github: 2 },
  })),
  sources: [{ source: 'github', label: 'GitHub', total: 6 }],
});

// The page reads its filters and zoom from the address, so the stand-in
// location carries the same query as the page under test.
function storyLocation(assigned, query = '') {
  const search = `?framework=A&mode=histogram${query}`;
  return {
    origin: 'https://keen.example',
    href: `https://keen.example/sources.html${search}`,
    search,
    assign: (u) => assigned.push(u),
  };
}

test('a zoomed window becomes a story with the annotation as its first note', async (t) => {
  const start = '2026-10-02T00:00:00.000Z';
  const end = '2026-10-02T03:00:00.000Z';
  const assigned = [];
  const requests = [];
  const posted = [];
  const query = `&source=github&hstart=${start}&hend=${end}&hint=hour`;
  const ui = await viz(t, {
    // The Events page scopes its charts to the list filters.
    htmlName: 'events',
    mode: 'histogram',
    me: { can_view_events: true, can_manage_stories: true },
    query,
    series: (u) => (u.searchParams.get('interval') === 'hour' ? hourSeries(start, end) : daySeries),
    globals: { location: storyLocation(assigned, query) },
    get: async (u) => {
      requests.push(u.pathname + u.search);
      if (u.pathname === '/api/v1/events')
        return { total: 3, items: [{ id: 'e3' }, { id: 'e2' }, { id: 'e1' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, body) => {
      posted.push([u.pathname + u.search, JSON.parse(JSON.stringify(body))]);
      return { id: 'story-1' };
    },
  });
  await settle(() => bars(ui).length === 3);
  await settle(() => !ui.el('histStory').classList.contains('d-none'));
  assert.equal(ui.el('histStoryPanel').hidden, true);
  ui.el('histStory').click();
  assert.equal(ui.el('histStoryPanel').hidden, false);
  const panel = ui.el('histStoryPanel');
  assert.equal(panel.querySelector('[data-window]').textContent, `${start} – ${end} · 6 events`);
  assert.equal(panel.querySelector('[data-window-title]').value, `Events from ${start} to ${end}`);
  panel.querySelector('[data-window-note]').value = '  Deploy storm after the release  ';
  panel.querySelector('[data-window-create]').click();
  await settle(() => assigned.length === 1);
  // The page's own source filter scopes the events taken into the story.
  assert.deepEqual(requests, [
    `/api/v1/events?framework=A&start_ts=${encodeURIComponent(start)}&end_ts=${encodeURIComponent(end)}&limit=200&source=github`,
  ]);
  assert.deepEqual(posted, [
    [
      '/api/v1/stories?framework=A',
      {
        title: `Events from ${start} to ${end}`,
        items: [
          {
            kind: 'note',
            label: 'Annotation',
            note: 'Deploy storm after the release',
            occurred_at: start,
          },
          { kind: 'event', entity_id: 'e3' },
          { kind: 'event', entity_id: 'e2' },
          { kind: 'event', entity_id: 'e1' },
        ],
      },
    ],
  ]);
  assert.deepEqual(assigned, ['/story.html?id=story-1&framework=A']);
});

test('right-clicking a bar picks that bar alone, and a full window is reported', async (t) => {
  const assigned = [];
  const requests = [];
  const posted = [];
  const ui = await viz(t, {
    mode: 'histogram',
    me: { can_view_events: true, can_manage_stories: true },
    series: daySeries,
    globals: { location: storyLocation(assigned) },
    get: async (u) => {
      requests.push(Object.fromEntries(u.searchParams));
      return { total: 201, items: [{ id: 'e1' }] };
    },
    post: async (u, body) => {
      posted.push(JSON.parse(JSON.stringify(body)));
      return { id: 'story-2' };
    },
  });
  await settle(() => bars(ui).length === 4);
  assert.ok(ui.el('histStory').classList.contains('d-none'));
  const bar = ui.titled(
    '#viz g.layer rect',
    '2026-10-02\nGitHub: 1\nTotal: 4\nMapped: 4\nUnmapped: 0',
  );
  const menu = new ui.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true });
  bar.dispatchEvent(menu);
  assert.equal(menu.defaultPrevented, true);
  assert.equal(ui.el('histStoryPanel').hidden, false);
  assert.equal(
    ui.el('histStoryPanel').querySelector('[data-window]').textContent,
    '2026-10-02T00:00:00.000Z – 2026-10-03T00:00:00.000Z · 4 events · source github',
  );
  ui.el('histStoryPanel').querySelector('[data-window-title]').value = 'The spike';
  ui.el('histStoryPanel').querySelector('[data-window-create]').click();
  await settle(() => assigned.length === 1);
  assert.deepEqual(requests, [
    {
      framework: 'A',
      start_ts: '2026-10-02T00:00:00.000Z',
      end_ts: '2026-10-03T00:00:00.000Z',
      limit: '200',
      source: 'github',
    },
  ]);
  assert.deepEqual(posted, [{ title: 'The spike', items: [{ kind: 'event', entity_id: 'e1' }] }]);
  assert.equal(
    ui.el('status').textContent,
    'The window holds 201 events; the newest 1 were added to the story.',
  );
  // Cancel hides the panel without a request.
  ui.el('histStoryPanel').querySelector('[data-window-cancel]').click();
  assert.equal(ui.el('histStoryPanel').hidden, true);
});

test('readers get no story controls on the histogram', async (t) => {
  const ui = await viz(t, {
    mode: 'histogram',
    me: { can_view_events: true, can_view_stories: true },
    query: '&hstart=2026-10-02T00:00:00.000Z&hend=2026-10-02T03:00:00.000Z&hint=hour',
    series: (u) =>
      u.searchParams.get('interval') === 'hour'
        ? hourSeries('2026-10-02T00:00:00.000Z', '2026-10-02T03:00:00.000Z')
        : daySeries,
  });
  await settle(() => bars(ui).length === 3);
  assert.ok(ui.el('histStory').classList.contains('d-none'));
  const menu = new ui.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true });
  bars(ui)[0].dispatchEvent(menu);
  assert.equal(menu.defaultPrevented, false);
  assert.equal(ui.el('histStoryPanel').hidden, true);
});

// --- Stories: empty windows and refused saves ----------------------------------------

test('a window with no events and no note is refused, and a failed save is reported', async (t) => {
  const assigned = [];
  let postFail = false;
  const posted = [];
  const ui = await viz(t, {
    mode: 'histogram',
    me: { can_view_events: true, can_manage_stories: true },
    series: daySeries,
    globals: { location: storyLocation(assigned) },
    get: async () => ({ total: 0, items: [] }),
    post: async (u, body) => {
      posted.push(JSON.parse(JSON.stringify(body)));
      if (postFail) throw new Error('Stories offline');
      return { id: 'story-3' };
    },
  });
  await settle(() => bars(ui).length === 4);
  const panel = ui.el('histStoryPanel');
  bars(ui)[0].dispatchEvent(
    new ui.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }),
  );
  assert.equal(panel.hidden, false);
  panel.querySelector('[data-window-create]').click();
  await settle(() => /No events in this window/.test(ui.el('status').textContent));
  assert.equal(
    ui.el('status').textContent,
    'No events in this window; add an annotation to start a story anyway.',
  );
  assert.equal(ui.el('status').dataset.alert, 'warning');
  assert.deepEqual(posted, []);

  // An annotation alone starts the story; a refused save stays on the page.
  postFail = true;
  panel.querySelector('[data-window-note]').value = 'Nothing happened here';
  panel.querySelector('[data-window-title]').value = '';
  panel.querySelector('[data-window-create]').click();
  await settle(() => /Failed to create the story/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Failed to create the story: Error: Stories offline');
  assert.equal(posted.length, 1);
  assert.equal(posted[0].items[0].kind, 'note');
  assert.match(posted[0].title, /^Events from /);
  assert.deepEqual(assigned, []);
});

// --- Plain clicks navigate in the same tab -----------------------------------------

// The page reads its state from the address, so the stand-in location carries
// the page's query and records where plain clicks send the tab.
function navLocation(assigned, query = '') {
  const search = `?framework=A${query}`;
  const href = `https://keen.example/sources.html${search}`;
  return {
    origin: 'https://keen.example',
    search,
    get href() {
      return href;
    },
    set href(value) {
      assigned.push(value);
    },
    assign: (u) => assigned.push(u),
  };
}

test('graph marks clicked without a modifier open their pages in this tab', async (t) => {
  const assigned = [];
  const relationships = {
    graph_kind: 'clause_control_relationship',
    nodes: [
      { id: 'clause:k1', type: 'source', node_kind: 'clause', label: '5.1', clause_id: 'k1' },
      { id: 'control:c1', type: 'control', node_kind: 'control', label: 'A1', control_id: 'c1' },
    ],
    links: [{ source: 'clause:k1', target: 'control:c1', value: 1, link_kind: 'relationship' }],
  };
  const ui = await viz(t, {
    mode: 'graph',
    graph: (u) => (u.pathname.endsWith('/clause_control') ? relationships : twoSources),
    globals: { location: navLocation(assigned, '&mode=graph') },
  });
  ui.click(ui.titled('#viz g.node-source', 'GitHub\nMapped events: 8'));
  ui.click(ui.document.querySelector('#viz g.node-control'));
  ui.click(ui.titled('#viz .links path', 'Okta → A1\nMapped events: 1'));
  assert.deepEqual(assigned, [
    '/source.html?source=github&framework=A',
    '/control.html?id=A1-id&framework=A',
    `/events.html?framework=A&source=okta&control=A1&${range}`,
  ]);
  assert.deepEqual(ui.opened, []);

  // Relationship edges lead to the control; clause nodes lead to the clause.
  ui.el('vizSelect').value = 'graph_clause_control';
  ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.titled('#viz .links path', '5.1 → A1\nLink count: 1'));
  ui.click(ui.titled('#viz .links path', '5.1 → A1\nLink count: 1'));
  ui.click(ui.document.querySelector('#viz g.node-source'));
  assert.deepEqual(assigned.slice(3), [
    '/control.html?id=c1&framework=A',
    '/clause.html?id=k1&framework=A',
  ]);
});

test('heatmap labels open sources and controls, and clause cells filter by clause', async (t) => {
  const assigned = [];
  const ui = await viz(t, {
    mode: 'heatmap',
    graph: (u) => (u.pathname.endsWith('/clause_source') ? clauseSources : twoSources),
    globals: { location: navLocation(assigned, '&mode=heatmap') },
  });
  const axisText = (label) =>
    [...ui.document.querySelectorAll('#viz .viz-axis text')].find(
      (el) => el.firstChild?.textContent === label,
    );
  ui.click(axisText('A2'));
  ui.click(axisText('Okta'));
  ui.click(ui.titled('#viz .viz-axis rect', 'GitHub'));
  ui.click(ui.titled('#viz rect.heat-cell', 'GitHub → A2\nMapped events: 2'));
  assert.deepEqual(assigned, [
    '/control.html?id=A2-id&framework=A',
    '/source.html?source=okta&framework=A',
    '/source.html?source=github&framework=A',
    `/events.html?framework=A&source=github&control=A2&${range}`,
  ]);

  ui.el('vizSelect').value = 'heatmap_clause_events';
  ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.titled('#viz rect.heat-cell', 'GitHub → 5.1\nMapped events: 4'));
  ui.click(ui.titled('#viz rect.heat-cell', 'GitHub → 5.1\nMapped events: 4'), { ctrlKey: true });
  ui.click(axisText('5.1'));
  assert.equal(ui.opened[0].href, `/events.html?framework=A&source=github&clause=5.1&${range}`);
  assert.equal(assigned.at(-1), '/clause.html?id=k1&framework=A');
});

// --- Saved defaults ----------------------------------------------------------------

test('a saved relative range counts back from today, and a malformed one falls back', async (t) => {
  const relative = await viz(t, {
    graph: twoSources,
    me: { is_admin: true, preferences: { default_visualisation: { date_range: { days: 3 } } } },
  });
  assert.deepEqual(
    [relative.el('dateFrom').value, relative.el('dateTo').value, relative.el('histDays').value],
    [isoDay(Date.now() - 2 * DAY), today, '3'],
  );
  assert.ok(
    relative.calls.some(
      (c) =>
        c.url ===
        `/api/v1/graph/control_source?framework=A&start_date=${isoDay(Date.now() - 2 * DAY)}&end_date=${today}`,
    ),
  );

  const malformed = await viz(t, {
    graph: twoSources,
    me: {
      is_admin: true,
      preferences: {
        default_visualisation: { date_range: { start_date: 'yesterday', end_date: '' } },
      },
    },
  });
  assert.deepEqual(
    [malformed.el('dateFrom').value, malformed.el('dateTo').value],
    [weekAgo, today],
  );
});

test('a saved control focus reopens the control sunburst, and Reset restores a saved focus', async (t) => {
  const ui = await viz(t, {
    mode: 'graph',
    graph: twoSources,
    me: {
      is_admin: true,
      preferences: {
        default_visualisation: {
          mode: 'sunburst_control',
          date_range: { days: 7 },
          sunburst_focus: { control_id: 'A2-id' },
        },
      },
    },
  });
  // The address chose the graph; the saved default waits for Reset.
  assert.equal(ui.el('vizSelect').value, 'graph');
  ui.el('vizReset').click();
  await settle(() => ui.el('vizSelect').value === 'sunburst_control' && centre(ui)[0] === 'A2');
  assert.deepEqual(centre(ui).slice(0, 2), ['A2', 'All › A2']);

  const direct = await viz(t, {
    graph: twoSources,
    me: {
      is_admin: true,
      preferences: {
        default_visualisation: {
          mode: 'sunburst_control',
          date_range: { days: 7 },
          sunburst_focus: { control_id: 'A1-id' },
        },
      },
    },
  });
  assert.deepEqual(centre(direct).slice(0, 2), ['A1', 'All › A1']);
  // A focus on a control that is no longer there leaves the whole chart in view.
  const gone = await viz(t, {
    graph: twoSources,
    me: {
      is_admin: true,
      preferences: {
        default_visualisation: {
          mode: 'sunburst_control',
          sunburst_focus: { control_id: 'missing' },
        },
      },
    },
  });
  assert.equal(centre(gone)[0], 'All');
});

// --- Sunburst edge cases -----------------------------------------------------------

test('a sunburst without mapped events says so', async (t) => {
  const ui = await viz(t, {
    mode: 'sunburst_source',
    graph: { nodes: twoSources.nodes.map((n) => ({ ...n, mapped_events: 0 })), links: [] },
  });
  assert.equal(ui.el('viz').textContent, 'No mapped events yet.');
  assert.equal(ui.document.querySelectorAll('#viz path.sunburst-arc').length, 0);
  assert.equal(
    ui.el('topSources').textContent.trim(),
    'No mapped source evidence in this date range.',
  );
});

test('long names are shortened on the wedge and in the breadcrumb', async (t) => {
  const longName = 'An extraordinarily long source label that keeps going and going';
  const ui = await viz(t, {
    mode: 'sunburst_source',
    graph: graph([
      { key: 'long', label: longName, controls: { A1: 6 } },
      { key: 'okta', label: 'Okta', controls: { A1: 1 } },
    ]),
  });
  const labels = [...ui.document.querySelectorAll('#viz .sunburst-label')].map(
    (n) => n.firstChild?.textContent,
  );
  assert.ok(labels.includes(`${longName.slice(0, 16)}…`));
  assert.ok(!labels.includes(longName));
  // The tooltip keeps the whole name.
  ui.click(arc(ui, `${longName}\nMapped events: 6`));
  const breadcrumb = centre(ui)[1];
  assert.equal(centre(ui)[0], longName);
  assert.ok(breadcrumb.includes('…'));
  assert.ok(breadcrumb.startsWith('All › An extraordinarily'));
  assert.ok(breadcrumb.endsWith(longName.slice(-27)));
  assert.ok(breadcrumb.length < `All › ${longName}`.length);
});

test('tiny controls merge into Other on the control sunburst and list their events', async (t) => {
  const ui = await viz(t, {
    mode: 'sunburst_control',
    graph: graph([
      { key: 'github', label: 'GitHub', controls: { A1: 500, C1: 1, C2: 1 } },
      { key: 'okta', label: 'Okta', controls: { A2: 500, C3: 1 } },
    ]),
  });
  const other = arc(ui, 'Other\nMerged: 3 segments\nMapped events: 3');
  assert.ok(other);
  ui.click(other, { shiftKey: true });
  const panel = ui.el('sunburstOtherPanel');
  assert.match(panel.textContent, /Controls merged into "Other"/);
  assert.match(panel.textContent, /3 segments • 3 events/);
  assert.equal(panel.querySelector('thead th').textContent, 'Control');
  const links = [...panel.querySelectorAll('tbody a')];
  assert.deepEqual(links.map((a) => a.textContent).sort(), ['C1', 'C2', 'C3']);
  assert.equal(
    links.find((a) => a.textContent === 'C3').getAttribute('href'),
    `/events.html?framework=A&control=C3&${range}`,
  );
});

// --- Clipboard, exports and zoom controls -------------------------------------------

test('without a clipboard the link is copied through a hidden field, or the failure is shown', async (t) => {
  const ui = await viz(t, { mode: 'graph', graph: twoSources });
  Object.defineProperty(ui.window.navigator, 'clipboard', {
    configurable: true,
    value: {
      writeText: async () => {
        throw new Error('Clipboard blocked');
      },
    },
  });
  const fields = () => ui.document.querySelectorAll('textarea[readonly]').length;
  const before = fields();
  let selected = '';
  ui.document.execCommand = (command) => {
    const field = ui.document.querySelector('textarea[readonly]');
    selected = command === 'copy' ? field.value : '';
    return true;
  };
  ui.el('copyVizLink').click();
  await settle(() => ui.el('status').textContent === 'Copied link to clipboard.');
  assert.equal(new URL(selected).searchParams.get('mode'), 'graph');
  assert.equal(fields(), before);

  delete ui.document.execCommand;
  ui.el('copyVizLink').click();
  await settle(() => ui.el('status').textContent === 'Could not copy link.');
  assert.equal(ui.el('status').dataset.alert, 'warning');
  assert.deepEqual(ui.copied, []);
});

test('a share link leaves out the range when the dates are blank', async (t) => {
  const ui = await viz(t, { mode: 'histogram', series: daySeries });
  await settle(() => ui.series.length === 1);
  for (const id of ['dateFrom', 'dateTo', 'dateFromDmy', 'dateToDmy']) ui.el(id).value = '';
  // A new breakdown reloads the chart; without dates the server is asked for days.
  ui.el('vizBreakdown').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.series.length === 2);
  assert.deepEqual(ui.series[1], {
    framework: 'A',
    days: '7',
    breakdown: 'source',
    interval: 'day',
  });
  assert.match(ui.el('vizCaption').textContent, /Daily volume for the last 7 days\./);

  ui.el('copyVizLink').click();
  await settle(() => ui.copied.length === 1);
  const shared = new URL(ui.copied[0]).searchParams;
  assert.deepEqual([shared.has('from'), shared.has('to'), shared.get('days')], [false, false, '7']);
});

test('a failed download is reported for each export format', async (t) => {
  const ui = await viz(t, { mode: 'histogram', series: daySeries });
  await settle(() => bars(ui).length === 4);
  URL.createObjectURL = () => {
    throw new Error('No blob URLs here');
  };
  for (const id of ['vizExportCsv', 'vizExportJson', 'vizExportSvg']) {
    ui.el('status').textContent = '';
    ui.el(id).click();
    assert.equal(ui.el('status').textContent, 'Export failed: Error: No blob URLs here', id);
    assert.equal(ui.el('status').dataset.alert, 'danger');
  }
  assert.deepEqual(ui.files, []);
});

test('Reset zoom leaves every zoom level at once', async (t) => {
  const start = '2026-10-02T00:00:00.000Z';
  const end = '2026-10-02T03:00:00.000Z';
  const ui = await viz(t, {
    mode: 'histogram',
    query: `&hstart=${start}&hend=${end}&hint=hour`,
    series: (u) => (u.searchParams.get('interval') === 'hour' ? hourSeries(start, end) : daySeries),
  });
  await settle(() => bars(ui).length === 3);
  assert.ok(!ui.el('histResetZoom').classList.contains('d-none'));
  ui.el('histResetZoom').click();
  await settle(() => bars(ui).length === 4);
  assert.equal(ui.series.at(-1).interval, 'day');
  assert.equal('start_ts' in ui.series.at(-1), false);
  assert.ok(ui.el('histResetZoom').classList.contains('d-none'));
  assert.ok(ui.el('histBack').classList.contains('d-none'));
  const search = new URL(ui.window.location.href).searchParams;
  assert.deepEqual(
    [search.get('hstart'), search.get('hend'), search.get('hint')],
    [null, null, null],
  );
  // A second press has nothing to leave.
  const requests = ui.series.length;
  ui.el('histResetZoom').click();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(ui.series.length, requests);
});

// Drags the histogram brush from one x position to another, as a pointer would.
function dragBrush(ui, fromX, toX) {
  const overlay = ui.document.querySelector('#viz .hist-main-brush .overlay');
  const init = (clientX) => ({
    bubbles: true,
    cancelable: true,
    view: ui.window,
    clientX,
    clientY: 200,
  });
  overlay.dispatchEvent(new ui.window.MouseEvent('mousedown', init(fromX)));
  ui.window.dispatchEvent(new ui.window.MouseEvent('mousemove', init(toX)));
  ui.window.dispatchEvent(new ui.window.MouseEvent('mouseup', init(toX)));
}

test('dragging over bars zooms into those buckets at the next finer interval', async (t) => {
  const ui = await viz(t, {
    mode: 'histogram',
    series: (u) =>
      u.searchParams.get('interval') === 'hour'
        ? hourSeries(u.searchParams.get('start_ts'), u.searchParams.get('end_ts'))
        : daySeries,
  });
  await settle(() => bars(ui).length === 4);
  ui.el('histZoomMode').click();
  // The chart is 900px wide with a 54px left margin; the first band spans
  // roughly 54–468 and the second 468–882.
  dragBrush(ui, 80, 180);
  await settle(() => ui.series.length === 2);
  assert.deepEqual(ui.series[1], {
    framework: 'A',
    start_date: weekAgo,
    end_date: today,
    breakdown: 'source',
    interval: 'hour',
    start_ts: '2026-10-01T00:00:00.000Z',
    end_ts: '2026-10-02T00:00:00.000Z',
  });
  await settle(() => bars(ui).length === 3);
  assert.ok(!ui.el('histBack').classList.contains('d-none'));
  const search = new URL(ui.window.location.href).searchParams;
  assert.deepEqual(
    [search.get('hstart'), search.get('hend'), search.get('hint')],
    ['2026-10-01T00:00:00.000Z', '2026-10-02T00:00:00.000Z', 'hour'],
  );

  // A drag across both days covers both; the hint keeps hour resolution.
  ui.el('histBack').click();
  await settle(() => bars(ui).length === 4);
  dragBrush(ui, 80, 600);
  await settle(() => ui.series.length === 3);
  assert.deepEqual(
    [ui.series[2].start_ts, ui.series[2].end_ts, ui.series[2].interval],
    ['2026-10-01T00:00:00.000Z', '2026-10-03T00:00:00.000Z', 'hour'],
  );
});

test('a drag that fails to load reports the zoom failure', async (t) => {
  let hourRequests = 0;
  const ui = await viz(t, {
    mode: 'histogram',
    series: (u) => {
      if (u.searchParams.get('interval') === 'hour') {
        hourRequests += 1;
        throw new Error('Zoom offline');
      }
      return daySeries;
    },
  });
  await settle(() => bars(ui).length === 4);
  ui.el('histZoomMode').click();
  dragBrush(ui, 80, 180);
  await settle(() => hourRequests === 1);
  await settle(() => /Failed to load histogram zoom/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Failed to load histogram zoom: Error: Zoom offline');
  assert.equal(ui.el('status').dataset.alert, 'danger');
});

// --- Auto-apply, resize and the gaps filter ----------------------------------------

test('with auto-apply, edited heatmap and graph limits redraw without fetching', async (t) => {
  const ui = await viz(t, {
    mode: 'heatmap',
    graph: manySources,
    me: { is_admin: true, preferences: { auto_apply_filters: true } },
  });
  const graphCalls = () => ui.calls.filter((c) => c.url.startsWith('/api/v1/graph/')).length;
  const before = graphCalls();
  const rows = () =>
    new Set(
      [...ui.document.querySelectorAll('#viz rect.heat-cell')].map(
        (cell) => cell.querySelector(':scope > title').textContent.split(' → ')[0],
      ),
    ).size;
  assert.equal(rows(), 13);
  ui.el('heatmapTopSources').value = '1';
  ui.el('heatmapTopSources').dispatchEvent(new ui.window.Event('input'));
  assert.equal(rows(), 5);

  ui.el('vizSelect').value = 'graph';
  ui.el('vizSelect').dispatchEvent(new ui.window.Event('change'));
  const links = () => ui.document.querySelectorAll('#viz .links path').length;
  assert.equal(links(), manySources.links.length);
  ui.el('graphMinEdge').value = '100';
  ui.el('graphMinEdge').dispatchEvent(new ui.window.Event('input'));
  assert.equal(links(), 3);
  // A heatmap limit typed while the graph shows changes nothing.
  ui.el('heatmapTopControls').value = '2';
  ui.el('heatmapTopControls').dispatchEvent(new ui.window.Event('input'));
  assert.equal(links(), 3);
  assert.equal(graphCalls(), before);
});

test('resizing the window redraws the chart it shows', async (t) => {
  const ui = await viz(t, { mode: 'graph', graph: twoSources });
  ui.el('viz').innerHTML = '';
  assert.equal(ui.document.querySelectorAll('#viz g.node-source').length, 0);
  ui.window.dispatchEvent(new ui.window.Event('resize'));
  assert.equal(ui.document.querySelectorAll('#viz g.node-source').length, 2);
});

test('typing in the gaps filter of an open panel loads the controls first', async (t) => {
  const ui = await viz(t, {
    mode: 'graph',
    graph: twoSources,
    stats: () => ({
      items: [
        { id: 'c2', ref: 'A2', title: 'Backups', in_scope: true, evidence_count: 0 },
        { id: 'c3', ref: 'A3', title: 'Crypto', in_scope: true, evidence_count: 0 },
      ],
    }),
  });
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/stats/controls')));
  ui.el('coverageGapsPanel').classList.add('show');
  ui.el('gapQ').value = 'crypto';
  ui.el('gapQ').dispatchEvent(new ui.window.Event('input'));
  await settle(() => ui.el('gaps').querySelectorAll('.gap-item').length === 1);
  assert.equal(ui.calls.filter((c) => c.url.startsWith('/api/v1/stats/controls')).length, 1);
  assert.match(ui.el('gaps').textContent, /A3\s+Crypto/);
});

test('at second resolution a drag within one bucket zooms no further', async (t) => {
  const start = '2026-10-02T00:00:00Z';
  const end = '2026-10-02T00:00:10Z';
  const seconds = (s, e) => ({
    interval: 'second',
    start_ts: s,
    end_ts: e,
    items: Array.from({ length: 10 }, (_, i) => ({
      bucket: `2026-10-02T00:00:0${i}Z`,
      total: 1,
      mapped: 1,
      unmapped: 0,
      by_source: { github: 1 },
    })).filter(
      (it) => Date.parse(it.bucket) >= Date.parse(s) && Date.parse(it.bucket) < Date.parse(e),
    ),
    sources: [{ source: 'github', label: 'GitHub', total: 10 }],
  });
  const ui = await viz(t, {
    mode: 'histogram',
    query: `&hstart=${start}&hend=${end}&hint=second`,
    series: (u) =>
      u.searchParams.get('interval') === 'second'
        ? seconds(u.searchParams.get('start_ts'), u.searchParams.get('end_ts'))
        : daySeries,
  });
  await settle(() => bars(ui).length === 10);
  ui.el('histZoomMode').click();
  // Ten bands of about 83px start at x=54: a drag inside the first band.
  dragBrush(ui, 60, 100);
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(ui.series.length, 1);
  assert.equal(bars(ui).length, 10);

  // Two seconds can still be selected together.
  dragBrush(ui, 60, 200);
  await settle(() => ui.series.length === 2);
  assert.deepEqual(
    [ui.series[1].start_ts, ui.series[1].end_ts, ui.series[1].interval],
    ['2026-10-02T00:00:00.000Z', '2026-10-02T00:00:02.000Z', 'second'],
  );
  await settle(() => bars(ui).length === 2);
});

test('a histogram without source metadata exports the raw per-source columns', async (t) => {
  const ui = await viz(t, {
    mode: 'histogram',
    series: { interval: 'day', items: daySeries.items, sources: [] },
  });
  await settle(() => /Loaded\./.test(ui.el('status').textContent));
  await settle(() => ui.el('vizCaption').textContent.includes('Daily volume from'));
  assert.deepEqual(legend(ui), []);
  ui.el('vizExportCsv').click();
  await settle(() => ui.files.length === 1);
  assert.equal(
    await ui.files[0].blob.text(),
    'bucket,total,github,okta\n2026-10-01,5,3,2\n2026-10-02,4,1,3\n',
  );
});
