import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

// Events page workflows: exports, resets, saved searches, automatic filtering
// and filters whose values no longer match anything.
const EVENT = { id: 'e1', source: 'ci', summary: 'Deploy', timestamp: '2026-01-01', controls: [] };

async function events(t, options = {}) {
  const requests = { events: [], facets: [] };
  const downloads = [];
  const ui = await page(t, 'events', {
    ...options,
    setup(window) {
      // A download is an anchor click; record what it would fetch.
      window.HTMLAnchorElement.prototype.click = function () {
        downloads.push({
          href: this.getAttribute('href'),
          download: this.getAttribute('download'),
          target: this.target,
        });
      };
      options.setup?.(window);
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/sources/meta') return { items: [{ source: 'ci', label: 'CI' }] };
      if (u.pathname === '/api/v1/controls') {
        if (options.controlsFail) throw new Error('Controls unavailable');
        return { items: [{ ref: 'A.1', title: 'Access', type: 'control' }] };
      }
      if (u.pathname === '/api/v1/events/facets') {
        requests.facets.push(u.searchParams);
        return (
          options.facets ?? {
            sources: [{ source: 'ci', count: 3 }],
            controls: [{ ref: 'A.1', count: 2 }],
          }
        );
      }
      if (u.pathname === '/api/v1/events') {
        requests.events.push(u.searchParams);
        return { total: 1, items: [EVENT] };
      }
      throw new Error('Unexpected GET ' + u);
    },
  });
  return { ...ui, requests, downloads, lastEvents: () => requests.events.at(-1) };
}

const params = (searchParams) => Object.fromEntries(searchParams);

test('exports download the filters on screen, including unapplied search text', async (t) => {
  const ui = await events(t, {
    query:
      '&source=ci&control=A.1&clause=4.1&unmapped=true&start_date=2026-01-01&end_date=2026-01-31&offset=50',
  });
  ui.el('q').value = '  deploy  ';

  ui.el('exportCsv').click();

  assert.equal(ui.downloads.length, 1);
  const url = new URL(ui.downloads[0].href, 'https://keen.example');
  assert.equal(url.pathname, '/api/v1/events/export');
  assert.deepEqual(params(url.searchParams), {
    q: 'deploy',
    framework: 'A',
    source: 'ci',
    control: 'A.1',
    clause: '4.1',
    unmapped: 'true',
    start_date: '2026-01-01',
    end_date: '2026-01-31',
    format: 'csv',
  });
  // The browser saves the file in place of opening another tab.
  assert.equal(ui.downloads[0].download, '');
  assert.equal(ui.downloads[0].target, '');
  // The temporary link is gone, and the page keeps its place in the results.
  assert.equal(ui.document.querySelectorAll('a[download]').length, 0);
  const shown = new URL(ui.window.location.href).searchParams;
  assert.equal(shown.get('q'), 'deploy');
  assert.equal(shown.get('offset'), '50');

  // A second click straight away is the same request; it downloads once.
  ui.el('exportJson').click();
  assert.equal(ui.downloads.length, 1);
  await new Promise((resolve) => setTimeout(resolve, 650));
  ui.el('exportNdjson').click();
  assert.equal(
    new URL(ui.downloads.at(-1).href, 'https://keen.example').searchParams.get('format'),
    'ndjson',
  );
  assert.equal(ui.downloads.length, 2);
});

test('Reset clears every filter and the time window and starts from the first page', async (t) => {
  const ui = await events(t, {
    query:
      '&q=deploy&source=ci&control=A.1&clause=4.1&start_date=2026-01-01&end_date=2026-01-31&start_ts=2026-01-02T00:00:00Z&end_ts=2026-01-03T00:00:00Z&limit=100&offset=50',
  });
  ui.el('unmapped').checked = true;
  const before = ui.requests.facets.length;

  ui.el('reset').click();

  await settle(() => ui.requests.events.length === 2);
  assert.deepEqual(params(ui.lastEvents()), { framework: 'A', limit: '50', offset: '0' });
  for (const id of ['q', 'source', 'control', 'startDateDmy', 'endDateDmy'])
    assert.equal(ui.el(id).value, '', id);
  assert.equal(ui.el('unmapped').checked, false);
  assert.equal(ui.el('limit').value, '50');
  const shown = new URL(ui.window.location.href).searchParams;
  for (const key of ['q', 'source', 'control', 'clause', 'start_ts', 'end_ts'])
    assert.equal(shown.has(key), false, key);
  // The option lists are refreshed for the unfiltered results.
  await settle(() => ui.requests.facets.length === before + 1);
  assert.deepEqual(params(ui.requests.facets.at(-1)), { framework: 'A' });
  assert.equal(ui.el('pageTitle').textContent, 'Events');
});

test('the name of a saved search opens that search instead of looking for the text', async (t) => {
  const location = { origin: 'https://keen.example', href: 'https://keen.example/events.html' };
  const ui = await events(t, {
    globals: { location },
    vendor: {
      resolveSavedSearchUrl: (name) =>
        name === 'Weekly deploys' ? '/events.html?source=ci&q=deploy' : null,
    },
  });
  ui.el('q').value = 'Weekly deploys';

  ui.el('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));

  assert.equal(location.href, '/events.html?source=ci&q=deploy&framework=A');
  assert.equal(ui.requests.events.length, 1);

  // Any other text is searched for.
  ui.el('q').value = 'Weekly';
  ui.el('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().get('q'), 'Weekly');
});

function manualDebounce() {
  const pending = [];
  const debounce = (fn) => {
    const queued = () => pending.push(fn);
    queued.cancel = () => pending.splice(0);
    return queued;
  };
  return { debounce, pending, flush: () => pending.splice(0).forEach((fn) => fn()) };
}

test('with automatic filtering, changes apply without pressing Apply', async (t) => {
  const timer = manualDebounce();
  const location = { origin: 'https://keen.example', href: 'https://keen.example/events.html' };
  const ui = await events(t, {
    me: { is_admin: true, preferences: { auto_apply_filters: true } },
    query: '&offset=50',
    globals: { location },
    vendor: {
      debounce: timer.debounce,
      resolveSavedSearchUrl: (name) =>
        name === 'Weekly deploys' ? '/events.html?source=ci' : null,
    },
  });

  // Typing waits for a pause before searching, from the first page.
  ui.el('q').value = 'dep';
  ui.el('q').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.requests.events.length, 1);
  timer.flush();
  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().get('q'), 'dep');
  assert.equal(ui.lastEvents().get('offset'), '0');

  // Choosing a filter applies it at once.
  ui.el('source').value = 'ci';
  ui.el('source').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.requests.events.length === 3);
  assert.equal(ui.lastEvents().get('source'), 'ci');
  ui.el('unmapped').checked = true;
  ui.el('unmapped').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.requests.events.length === 4);
  assert.equal(ui.lastEvents().get('unmapped'), 'true');

  // Leaving the search box applies the text without waiting.
  ui.el('q').value = 'deploy';
  ui.el('q').dispatchEvent(new ui.window.Event('input'));
  ui.el('q').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.requests.events.length === 5);
  assert.equal(ui.lastEvents().get('q'), 'deploy');
  assert.equal(timer.pending.length, 0);

  // Picking a saved search from the suggestions opens it and abandons the
  // pending search for its name.
  ui.el('q').value = 'Weekly deploys';
  ui.el('q').dispatchEvent(new ui.window.Event('input'));
  ui.el('q').dispatchEvent(
    new ui.window.InputEvent('input', { inputType: 'insertReplacementText' }),
  );
  assert.equal(location.href, '/events.html?source=ci&framework=A');
  assert.equal(timer.pending.length, 0);
  assert.equal(ui.requests.events.length, 5);

  // A saved search name typed in full opens the search once the box is left.
  location.href = 'https://keen.example/events.html';
  ui.el('q').dispatchEvent(new ui.window.Event('change'));
  assert.equal(location.href, '/events.html?source=ci&framework=A');
  assert.equal(ui.requests.events.length, 5);
});

test('without automatic filtering, nothing reloads until Apply', async (t) => {
  const ui = await events(t, { me: { is_admin: true, preferences: {} } });
  ui.el('q').value = 'deploy';
  ui.el('q').dispatchEvent(new ui.window.Event('input'));
  ui.el('source').value = 'ci';
  ui.el('source').dispatchEvent(new ui.window.Event('change'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(ui.requests.events.length, 1);
  // The heading already follows the chosen source.
  assert.equal(ui.el('pageTitle').textContent, 'Events for CI');
});

test('a typed date that cannot be read is reported and the previous range is kept', async (t) => {
  const ui = await events(t, { query: '&start_date=2026-01-01&end_date=2026-01-31' });
  ui.el('startDateDmy').value = 'soon';
  ui.el('endDateDmy').value = 'later';

  ui.el('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));

  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().get('start_date'), '2026-01-01');
  assert.equal(ui.lastEvents().get('end_date'), '2026-01-31');
  assert.equal(ui.el('startDateDmy').value, '01/01/2026');
  assert.equal(ui.el('endDateDmy').value, '31/01/2026');
  assert.equal(ui.el('status').textContent, "Invalid 'To' date. Use dd/mm/yyyy.");
  assert.equal(ui.el('status').dataset.alert, 'warning');
});

test('filters that match nothing stay selected and show a zero count', async (t) => {
  const ui = await events(t, {
    query: '&source=retired&control=A.9',
    facets: { sources: [{ source: 'ci', count: 3 }], controls: [{ ref: 'A.1', count: 2 }] },
  });
  await settle(() => ui.el('source').options.length === 3);

  const source = ui.el('source');
  assert.equal(source.value, 'retired');
  assert.equal(source.selectedOptions[0].textContent, 'retired (0)');
  const control = ui.el('control');
  assert.equal(control.value, 'A.9');
  assert.equal(control.selectedOptions[0].textContent, 'Current filter: A.9 (0)');
  assert.equal(ui.lastEvents().get('control'), 'A.9');
});

test('without the control catalogue the page keeps the filter and names it from the results', async (t) => {
  const ui = await events(t, {
    controlsFail: true,
    query: '&control=A.1',
    facets: {
      sources: [{ source: 'ci', count: 3 }],
      controls: [{ ref: 'A.1', title: 'Access', count: 2 }],
    },
  });
  assert.equal(ui.el('control').value, 'A.1');
  assert.equal(ui.el('pageTitle').textContent, 'Events for control A.1');
  await settle(() => /Access/.test(ui.el('control').selectedOptions[0].textContent));

  // Choosing a source names the control from its result option.
  ui.el('source').value = 'ci';
  ui.el('source').dispatchEvent(new ui.window.Event('change'));

  assert.equal(ui.el('pageTitle').textContent, 'Events for CI and control A.1 — Access');
  assert.equal(ui.document.title, 'Keen – Events for CI and control A.1 — Access');
});

test('a filter missing from the catalogue stays available while the catalogue is unavailable', async (t) => {
  const ui = await events(t, {
    controlsFail: true,
    query: '&control=retired-control&unmapped=true',
  });
  const control = ui.el('control');
  assert.deepEqual(
    [...control.options].map((o) => [o.value, o.textContent]),
    [
      ['retired-control', 'Current filter: retired-control'],
      ['', 'All controls'],
    ],
  );
  assert.equal(control.value, 'retired-control');
  // Unmapped results skip the facet request, so these options remain.
  assert.equal(ui.requests.facets.length, 0);
});

test('changing the page size reloads from the first page', async (t) => {
  const ui = await events(t, { query: '&offset=50' });

  ui.el('limit').value = '100';
  ui.el('limit').dispatchEvent(new ui.window.Event('change'));

  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().get('limit'), '100');
  assert.equal(ui.lastEvents().get('offset'), '0');
  await settle(() => ui.requests.facets.length === 2);
});

test('a saved search that cannot be stored reports why', async (t) => {
  const ui = await events(t, {
    globals: { prompt: () => 'Deploys' },
    post: async () => {
      throw new Error('Name already used');
    },
  });

  ui.el('saveSearch').click();

  await settle(() => /Failed to save/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Failed to save: Error: Name already used');
  assert.equal(ui.el('status').dataset.alert, 'danger');
});

test('dismissing the name prompt saves nothing', async (t) => {
  const ui = await events(t, { globals: { prompt: () => '   ' } });
  ui.el('saveSearch').click();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(ui.calls.filter((c) => c.method === 'POST').length, 0);
});
