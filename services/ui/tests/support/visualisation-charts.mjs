// The visualisation page as the chart tests drive it: the real page module with
// real d3 and SVG on a host page's markup, an API fixture for the chart
// endpoints, and records of what the page opens, copies, downloads and saves.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './page-fixture.mjs';

export const d3 = await readFile(
  new URL('../node_modules/d3/dist/d3.min.js', import.meta.url),
  'utf8',
);
export const DAY = 864e5;
export const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);
export const today = isoDay(Date.now());
export const weekAgo = isoDay(Date.now() - 6 * DAY);
export const range = `start_date=${weekAgo}&end_date=${today}`;

// Sources with the controls their events map to, as the graph endpoints
// describe them: `controls` maps a control reference to its event count.
export function graph(sources) {
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
      mapped_events: s.mapped_events ?? total,
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
  return { nodes, links, graph_kind: 'source_control_events' };
}

export const twoSources = graph([
  { key: 'github', label: 'GitHub', color: '#24292f', controls: { A1: 6, A2: 2 } },
  { key: 'okta', label: 'Okta', color: '#007dc1', controls: { A1: 1 } },
]);

export const daySeries = {
  interval: 'day',
  start_ts: '2026-10-01T00:00:00Z',
  end_ts: '2026-10-03T00:00:00Z',
  items: [
    { bucket: '2026-10-01', total: 5, mapped: 3, unmapped: 2, by_source: { github: 3, okta: 2 } },
    { bucket: '2026-10-02', total: 4, mapped: 4, unmapped: 0, by_source: { github: 1, okta: 3 } },
  ],
  sources: [
    { source: 'okta', label: 'Okta', color: '#007dc1', total: 5 },
    { source: 'github', label: 'GitHub', color: '#24292f', total: 4 },
  ],
};

// A request the test answers when it chooses, to hold the page mid-load.
export function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// Options:
//   htmlName   host page whose markup the charts mount in (default sources)
//   mode       chart named in the address; query adds more address parameters
//   active     whether the charts pane is already shown (default true)
//   modes      the mount's data-viz-modes (default all); null keeps the page's
//   mount      further data-* values for the mount, such as { vizDefaultMode }
//   theme      whether the page has a dark background stylesheet (default true)
//   graph, series, stats, events   answers for the chart, histogram, controls
//              and events endpoints: a value, or a function of the request URL
//   get, post  answers for any other request
export async function charts(t, options = {}) {
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
  const answer = (value, u) => (typeof value === 'function' ? value(u) : value);
  let win;
  const ui = await page(t, 'visualisation', {
    htmlName: options.htmlName || 'sources',
    scripts: [d3],
    query: (options.mode ? '&mode=' + options.mode : '') + (options.query || ''),
    me: options.me || { is_admin: true },
    vendor: options.vendor,
    globals: {
      XMLSerializer: function XMLSerializer() {
        return new win.XMLSerializer();
      },
      ...(options.globals || {}),
    },
    setup(window) {
      win = window;
      const mount = window.document.querySelector('[data-keen-visualisation-mount]');
      if (options.active !== false) mount.closest('.tab-pane').classList.add('active');
      if (options.modes !== null) mount.dataset.vizModes = options.modes || 'all';
      Object.assign(mount.dataset, options.mount || {});
      if (options.theme !== false) {
        const theme = window.document.createElement('style');
        theme.textContent =
          'body { background-color: rgb(24, 24, 32); } svg text { fill: rgb(250, 250, 250); }';
        window.document.head.append(theme);
      }
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
      options.setup?.(window);
    },
    get: async (u) => {
      if (u.pathname.startsWith('/api/v1/graph/')) {
        if (options.graphFail) throw new Error('Graph offline');
        return answer(options.graph ?? twoSources, u);
      }
      if (u.pathname === '/api/v1/charts/event_volume_timeseries') {
        series.push(Object.fromEntries(u.searchParams));
        if (options.seriesFail) throw new Error('Series offline');
        return answer(options.series ?? daySeries, u);
      }
      if (u.pathname === '/api/v1/stats/controls' && options.stats) return answer(options.stats, u);
      if (u.pathname === '/api/v1/events' && options.events) return answer(options.events, u);
      if (options.get) return options.get(u);
      throw new Error('Unexpected GET ' + u);
    },
    post: options.post,
    patch: async (u, body) => {
      assert.equal(u.pathname, '/api/v1/me/preferences');
      // Bodies come from the page's realm; compare them as plain data.
      patches.push(JSON.parse(JSON.stringify(body)));
      return body;
    },
  });
  if (options.active !== false && options.wait !== false)
    await settle(() => /Loaded\.|Failed/.test(ui.el('status').textContent));
  const click = (el, init = {}) =>
    el.dispatchEvent(
      new ui.window.MouseEvent('click', { bubbles: true, cancelable: true, ...init }),
    );
  const titled = (selector, title) =>
    [...ui.document.querySelectorAll(selector)].find(
      (el) => el.querySelector(':scope > title')?.textContent === title,
    );
  const input = (id, value, type = 'input') => {
    ui.el(id).value = value;
    ui.el(id).dispatchEvent(new ui.window.Event(type));
  };
  const choose = (mode) => input('vizSelect', mode, 'change');
  const graphRequests = () => ui.calls.filter((c) => c.url.startsWith('/api/v1/graph/'));
  return {
    ...ui,
    opened,
    copied,
    files,
    series,
    patches,
    click,
    titled,
    input,
    choose,
    graphRequests,
  };
}

export const bars = (ui) => ui.document.querySelectorAll('#viz g.layer rect');
export const arc = (ui, title) => ui.titled('#viz path.sunburst-arc', title);
export const centre = (ui) =>
  [...ui.document.querySelectorAll('#viz > g > text.small-muted')].map((n) => n.textContent);
export const pause = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));
export { settle };
