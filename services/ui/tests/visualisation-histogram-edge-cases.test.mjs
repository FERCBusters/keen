// Histogram edge cases: long ranges with dense bars, windows without events,
// sources missing from a bucket, the Other stack as a story window, a cleared
// Days field or end date, the Shift key and Zoom mode, and shared zooms that
// name an interval the chart does not use.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DAY,
  bars,
  charts,
  isoDay,
  pause,
  settle,
  today,
  weekAgo,
} from './support/visualisation-charts.mjs';

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

const days = (n, start = '2025-01-01') =>
  Array.from({ length: n }, (_, i) => {
    const bucket = isoDay(Date.parse(start) + i * DAY);
    return { bucket, total: 1, mapped: 1, unmapped: 0, by_source: { github: 1 } };
  });
const github = [{ source: 'github', label: 'GitHub', color: '#24292f', total: 1 }];

test('a long range draws dense bars, and a wide drag zooms in by days', async (t) => {
  const ui = await charts(t, {
    mode: 'histogram',
    series: (u) =>
      u.searchParams.has('start_ts')
        ? { interval: 'day', items: days(3, '2025-01-03'), sources: github }
        : { interval: 'day', items: days(300), sources: github },
  });
  await settle(() => bars(ui).length === 300);
  // 300 bands share 828px with 2% padding rather than 10%.
  const width = Number(bars(ui)[0].getAttribute('width'));
  assert.ok(Math.abs(width - 2.7046) < 0.001, `bar width ${width}`);

  ui.el('histZoomMode').click();
  // From the third bar to the 271st: 269 days are too many hours to draw.
  dragBrush(ui, 60, 800);
  await settle(() => ui.series.length === 2);
  assert.deepEqual(
    [ui.series[1].interval, ui.series[1].start_ts, ui.series[1].end_ts],
    ['day', '2025-01-03T00:00:00.000Z', '2025-09-29T00:00:00.000Z'],
  );
  await settle(() => bars(ui).length === 3);
});

test('a window without events draws an empty axis up to one', async (t) => {
  const quiet = days(3).map((it) => ({ ...it, total: 0, mapped: 0, by_source: {} }));
  const ui = await charts(t, {
    mode: 'histogram',
    series: { interval: 'day', items: quiet, sources: [] },
  });
  await settle(() => ui.document.querySelectorAll('#viz g.viz-axis').length === 2);
  assert.equal(bars(ui).length, 0);
  const ticks = [...ui.document.querySelectorAll('#viz g.viz-axis')[1].querySelectorAll('text')];
  assert.deepEqual(
    ticks.map((n) => n.textContent),
    ['0.0', '0.2', '0.4', '0.6', '0.8', '1.0'],
  );
});

test('a source without events in a bucket has an empty segment that opens nothing', async (t) => {
  const ui = await charts(t, {
    mode: 'histogram',
    series: {
      interval: 'day',
      items: [
        { bucket: '2026-10-01', total: 3, mapped: 3, unmapped: 0, by_source: { github: 3 } },
        {
          bucket: '2026-10-02',
          total: 4,
          mapped: 4,
          unmapped: 0,
          by_source: { github: 1, okta: 3 },
        },
      ],
      sources: [
        { source: 'github', label: 'GitHub', color: '#24292f', total: 4 },
        { source: 'okta', label: 'Okta', color: '#007dc1', total: 3 },
      ],
    },
  });
  await settle(() => bars(ui).length === 4);
  const empty = ui.titled(
    '#viz g.layer rect',
    '2026-10-01 UTC\nOkta: 0\nTotal: 3\nMapped: 3\nUnmapped: 0',
  );
  assert.equal(empty.getAttribute('height'), '0');
  assert.equal(empty.style.cursor, 'default');
  ui.click(empty, { ctrlKey: true });
  assert.deepEqual(ui.opened, []);

  // The export counts the missing source as no events.
  ui.el('vizExportCsv').click();
  await settle(() => ui.files.length === 1);
  assert.equal(
    await ui.files[0].blob.text(),
    'bucket,total,GitHub,Okta\n2026-10-01,3,3,0\n2026-10-02,4,1,3\n',
  );
});

test('right-clicking the Other stack starts a story from every source in that bar', async (t) => {
  const keys = Array.from({ length: 14 }, (_, i) => 'src' + i);
  const assigned = [];
  const requests = [];
  const search = '?framework=A&mode=histogram';
  const ui = await charts(t, {
    mode: 'histogram',
    me: { can_view_events: true, can_manage_stories: true },
    globals: {
      location: {
        origin: 'https://keen.example',
        href: `https://keen.example/sources.html${search}`,
        search,
        assign: (u) => assigned.push(u),
      },
    },
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
    events: (u) => {
      requests.push(Object.fromEntries(u.searchParams));
      return { total: 1, items: [{ id: 'e1' }] };
    },
    post: async () => ({ id: 'story-9' }),
  });
  await settle(() => bars(ui).length === 13);
  const other = ui.titled(
    '#viz g.layer rect',
    '2026-10-03 UTC\nOther: 3\nTotal: 105\nMapped: 105\nUnmapped: 0',
  );
  other.dispatchEvent(new ui.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
  const panel = ui.el('histStoryPanel');
  assert.equal(panel.hidden, false);
  assert.equal(
    panel.querySelector('[data-window]').textContent,
    '2026-10-03T00:00:00.000Z - 2026-10-04T00:00:00.000Z · 105 events',
  );
  panel.querySelector('[data-window-create]').click();
  await settle(() => assigned.length === 1);
  assert.deepEqual(requests, [
    {
      framework: 'A',
      start_ts: '2026-10-03T00:00:00.000Z',
      end_ts: '2026-10-04T00:00:00.000Z',
      limit: '200',
    },
  ]);
  assert.deepEqual(assigned, ['/story.html?id=story-9&framework=A']);
});

test('a cleared Days field counts seven days, and the chart describes seven', async (t) => {
  const ui = await charts(t, { mode: 'histogram' });
  await settle(() => bars(ui).length === 4);
  ui.input('histDays', '3');
  assert.deepEqual(
    [ui.el('dateFrom').value, ui.el('dateTo').value],
    [isoDay(Date.now() - 2 * DAY), today],
  );
  ui.input('histDays', '');
  assert.deepEqual([ui.el('dateFrom').value, ui.el('dateTo').value], [weekAgo, today]);

  ui.window.dispatchEvent(new ui.window.Event('resize'));
  await pause();
  assert.match(
    ui.el('vizCaption').textContent,
    new RegExp(`Daily volume from ${weekAgo} to ${today} \\(7 days\\)\\.`),
  );
});

test('Days with a blank end date counts back from today', async (t) => {
  const ui = await charts(t, { mode: 'graph' });
  ui.el('dateTo').value = '';
  ui.input('histDays', '3');
  assert.deepEqual(
    [ui.el('dateFrom').value, ui.el('dateTo').value, ui.el('dateToDmy').value],
    [isoDay(Date.now() - 2 * DAY), today, today],
  );
});

test('only Shift turns the brush on, and Zoom mode away from the histogram says nothing', async (t) => {
  const ui = await charts(t, { mode: 'histogram' });
  await settle(() => bars(ui).length === 4);
  const overlay = () =>
    ui.document.querySelector('#viz .hist-main-brush .overlay').style.pointerEvents;
  const key = (type, k) => ui.window.dispatchEvent(new ui.window.KeyboardEvent(type, { key: k }));
  assert.equal(overlay(), 'none');
  key('keydown', 'a');
  assert.equal(overlay(), 'none');
  key('keydown', 'Shift');
  assert.equal(overlay(), 'all');
  key('keyup', 'a');
  assert.equal(overlay(), 'all');
  key('keyup', 'Shift');
  assert.equal(overlay(), 'none');

  ui.choose('graph');
  await settle(() => ui.document.querySelectorAll('#viz g.node-source').length === 2);
  ui.el('histZoomMode').click();
  assert.equal(ui.el('histZoomMode').textContent, 'Zoom mode: on');
  assert.ok(ui.el('histZoomMode').classList.contains('active'));
  assert.equal(ui.el('status').textContent, 'Loaded.');
});

test('Back with no zoom left goes nowhere', async (t) => {
  const start = '2026-10-02T00:00:00.000Z';
  const end = '2026-10-02T02:00:00.000Z';
  const ui = await charts(t, {
    mode: 'histogram',
    query: `&hstart=${start}&hend=${end}&hint=hour`,
    series: (u) =>
      u.searchParams.get('interval') === 'hour'
        ? {
            interval: 'hour',
            start_ts: start,
            end_ts: end,
            items: ['00', '01'].map((h) => ({
              bucket: `2026-10-02T${h}:00:00Z`,
              total: 1,
              mapped: 1,
              unmapped: 0,
              by_source: { github: 1 },
            })),
            sources: github,
          }
        : { interval: 'day', items: days(4, '2026-10-01'), sources: github },
  });
  await settle(() => bars(ui).length === 2);
  ui.el('histBack').click();
  await settle(() => bars(ui).length === 4);
  assert.ok(ui.el('histBack').classList.contains('d-none'));
  const requests = ui.series.length;
  const shown = bars(ui)[0];
  ui.el('histBack').click();
  await pause();
  assert.equal(ui.series.length, requests);
  // The chart is left as drawn.
  assert.ok(shown.isConnected);
  assert.equal(bars(ui).length, 4);
});

test('a shared zoom with an unknown or missing interval opens at days', async (t) => {
  const start = '2026-10-02T00:00:00Z';
  const end = '2026-10-04T00:00:00Z';
  for (const hint of ['&hint=week', '']) {
    const ui = await charts(t, {
      mode: 'histogram',
      query: `&hstart=${start}&hend=${end}${hint}`,
      series: { interval: 'day', items: days(2, '2026-10-02'), sources: github },
    });
    await settle(() => bars(ui).length === 2);
    assert.deepEqual(
      [ui.series[0].interval, ui.series[0].start_ts, ui.series[0].end_ts],
      ['day', start, end],
    );
  }
});
