// The home page without a chosen framework, with catalogues of different
// shapes, an unreadable catalogue, singular counts, an empty search and a
// stale cached page that has no main element.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { page } from './support/page-fixture.mjs';

const overview = (extra = {}) => ({
  control_count: 2,
  clause_count: 0,
  events: { mapped: 8, unmapped: 0 },
  attention: [],
  ...extra,
});

// The home page with no framework chosen in the navigation and `catalog` as
// the framework list, or a failure when `catalog` is an Error.
async function home(t, { catalog, data = overview(), location } = {}) {
  const requests = [];
  const ui = await page(t, 'home', {
    htmlName: 'index',
    globals: location ? { location } : {},
    setup(window) {
      window.matchMedia = () => ({
        matches: true,
        addEventListener() {},
        removeEventListener() {},
      });
    },
    vendor: {
      getCurrentFramework: () => '',
      apiGet: async (raw) => {
        const url = new URL(raw, 'https://keen.example');
        requests.push(url);
        if (url.pathname === '/api/v1/frameworks') {
          if (catalog instanceof Error) throw catalog;
          return structuredClone(catalog);
        }
        if (url.pathname === '/api/v1/home') return structuredClone(data);
        if (url.pathname === '/api/v1/charts/event_volume_timeseries')
          return { items: [], sources: [] };
        throw new Error('Unexpected GET ' + raw);
      },
    },
  });
  const overviewRequest = requests.find((url) => url.pathname === '/api/v1/home');
  return { ui, requests, framework: overviewRequest.searchParams.get('framework') };
}

test('without a chosen framework the home page follows the catalogue to a framework', async (t) => {
  const listed = await home(t, {
    catalog: {
      items: [
        { slug: 'X', name: 'Ex', control_count: 4 },
        { slug: 'Y', name: 'Why', is_default: true },
      ],
    },
  });
  // A framework marked as the default wins over one with controls.
  assert.equal(listed.framework, 'Y');
  assert.equal(listed.ui.el('homeFrameworkName').textContent, 'Why');

  const controlled = await home(t, {
    catalog: {
      items: [
        { slug: 'X', name: 'Ex', control_count: 0 },
        { slug: 'Z', name: 'Zed', control_count: 3 },
      ],
    },
  });
  assert.equal(controlled.framework, 'Z');
  assert.equal(controlled.ui.el('homeFrameworkName').textContent, 'Zed');

  const first = await home(t, { catalog: { items: [{ slug: 'W', name: 'Dub' }, { slug: 'V' }] } });
  assert.equal(first.framework, 'W');
  assert.equal(first.ui.el('homeFrameworkName').textContent, 'Dub');
});

test('with no framework to choose the home page loads the overview without one', async (t) => {
  for (const catalog of [{ items: [] }, new Error('Offline')]) {
    const { ui, framework } = await home(t, { catalog });
    assert.equal(framework, null);
    assert.equal(ui.el('homeFrameworkName').textContent, 'No framework selected');
    // The workspace stays usable without a framework.
    assert.ok(ui.el('homeTasks').querySelector('a.home-task'));
    assert.match(ui.el('homeKpis').textContent, /Control evidence coverage/);
  }
});

test('a single unmapped event is counted in the singular', async (t) => {
  const { ui } = await home(t, {
    catalog: { items: [{ slug: 'A', name: 'Framework A' }] },
    data: overview({ events: { mapped: 8, unmapped: 1 } }),
  });
  const item = ui.el('homeAttention').querySelector('.home-attention-item strong');
  assert.equal(item.textContent, '1 unmapped event');
  assert.equal(item.parentElement.getAttribute('href'), '/events.html?unmapped=true&framework=A');
});

test('an empty search opens the event list for the framework without a query', async (t) => {
  const location = { origin: 'https://keen.example', href: 'https://keen.example/index.html' };
  const { ui } = await home(t, {
    catalog: { items: [{ slug: 'A', name: 'Framework A' }] },
    location,
  });
  assert.equal(ui.el('homeQ').value, '');
  ui.el('homeSearch').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  assert.equal(location.href, '/events.html?framework=A');
});

test('a busiest source with an empty name links to the events with an empty source', async (t) => {
  const ui = await page(t, 'home', {
    htmlName: 'index',
    setup(window) {
      window.matchMedia = () => ({
        matches: true,
        addEventListener() {},
        removeEventListener() {},
      });
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/home') return overview();
      if (u.pathname === '/api/v1/charts/event_volume_timeseries')
        return {
          items: [
            { bucket: '2026-10-01', total: 1, mapped: 0, unmapped: 1 },
            { bucket: '2026-10-02', total: 2, mapped: 1, unmapped: 1 },
          ],
          // The chart labels an event source recorded as empty "Unknown".
          sources: [{ source: '', label: 'Unknown', color: '#5b8def', total: 3 }],
        };
      throw new Error('Unexpected GET ' + u);
    },
  });
  const link = ui.el('homeSources').querySelector('a');
  assert.equal(link.getAttribute('href'), '/events.html?source=&framework=A');
  assert.equal(link.querySelector('.home-source-name').textContent, 'Unknown');
});

test('a stale cached page without a main element shows the version warning first in its body', async (t) => {
  const ui = await loadUi('pages/home.js', {
    html: '<body><p id="old">Cached home page</p></body>',
    url: 'https://keen.example/?__keen_home=20261026-home-5',
    // The page stops before it uses any of these.
    vendor: { withFramework: (url) => url, progressRing: () => '' },
  });
  t.after(ui.close);
  const first = ui.document.body.firstElementChild;
  assert.equal(first.getAttribute('role'), 'alert');
  assert.equal(first.className, 'alert alert-warning m-4');
  assert.match(first.textContent, /^The home page and its scripts are from different UI versions/);
  assert.equal(first.nextElementSibling.id, 'old');
});
