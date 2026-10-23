import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle, pickFilter, filterChips, chosenChips } from './support/page-fixture.mjs';

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
      if (u.pathname === '/api/v1/sources/meta') {
        if (options.sourcesFail) throw new Error('Sources unavailable');
        return { items: [{ source: 'ci', label: 'CI' }] };
      }
      if (u.pathname === '/api/v1/controls') {
        if (options.controlsFail) throw new Error('Controls unavailable');
        return { items: [{ ref: 'A.1', title: 'Access', type: 'control' }] };
      }
      if (u.pathname === '/api/v1/events/facets') {
        requests.facets.push(u.searchParams);
        if (options.facetsFail) throw new Error('Facets unavailable');
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

test('exports download the filters on screen, including an unapplied text filter', async (t) => {
  const ui = await events(t, {
    query:
      '&source=ci&control=A.1&clause=4.1&unmapped=true&start_date=2026-01-01&end_date=2026-01-31&offset=50',
  });
  // The page has no free-text search box; text filters cover every field.
  assert.equal(ui.el('q'), null);
  ui.el('addCondition').click();
  fillCondition(ui, 0, 'any', 'contains', '  deploy  ');

  ui.el('exportCsv').click();

  assert.equal(ui.downloads.length, 1);
  const url = new URL(ui.downloads[0].href, 'https://keen.example');
  assert.equal(url.pathname, '/api/v1/events/export');
  assert.deepEqual(params(url.searchParams), {
    framework: 'A',
    source: 'ci',
    control: 'A.1',
    clause: '4.1',
    f: 'any:contains:  deploy  ',
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
  assert.equal(shown.get('f'), 'any:contains:  deploy  ');
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
      '&q=deploy&source=ci&control=A.1&clause=4.1&rule=deploys&f=actor:contains:x&start_date=2026-01-01&end_date=2026-01-31&start_ts=2026-01-02T00:00:00Z&end_ts=2026-01-03T00:00:00Z&limit=100&offset=50',
  });
  ui.el('unmapped').checked = true;
  const before = ui.requests.facets.length;
  assert.equal(chosenChips(ui, 'rule')[0][0], 'deploys');

  ui.el('reset').click();

  await settle(() => ui.requests.events.length === 2);
  assert.deepEqual(params(ui.lastEvents()), { framework: 'A', limit: '50', offset: '0' });
  for (const id of ['startDateDmy', 'endDateDmy']) assert.equal(ui.el(id).value, '', id);
  assert.equal(ui.document.querySelectorAll('[data-condition-row]').length, 0);
  for (const id of ['source', 'control', 'rule']) assert.deepEqual(chosenChips(ui, id), [], id);
  assert.equal(ui.el('unmapped').checked, false);
  assert.equal(ui.el('limit').value, '50');
  const shown = new URL(ui.window.location.href).searchParams;
  for (const key of ['q', 'source', 'control', 'clause', 'rule', 'f', 'start_ts', 'end_ts'])
    assert.equal(shown.has(key), false, key);
  // The option lists are refreshed for the unfiltered results.
  await settle(() => ui.requests.facets.length === before + 1);
  assert.deepEqual(params(ui.requests.facets.at(-1)), { framework: 'A' });
  assert.equal(ui.el('pageTitle').textContent, 'Events');
});

test('search text in a link becomes a text filter over any field', async (t) => {
  // The navbar and Home search send their text as q; the page shows it as the
  // first text filter and drops q from the address.
  const ui = await events(t, { query: '&q=  deploy  &f=actor:contains:alice&offset=50' });
  const rows = () =>
    [...ui.document.querySelectorAll('[data-condition-row]')].map((row) => [
      row.querySelector('[data-condition-field]').value,
      row.querySelector('[data-condition-op]').value,
      row.querySelector('[data-condition-value]').value,
    ]);
  assert.deepEqual(rows(), [
    ['any', 'contains', 'deploy'],
    ['actor', 'contains', 'alice'],
  ]);
  assert.deepEqual(ui.lastEvents().getAll('f'), ['any:contains:deploy', 'actor:contains:alice']);
  assert.equal(ui.lastEvents().has('q'), false);
  const shown = new URL(ui.window.location.href).searchParams;
  assert.equal(shown.has('q'), false);
  assert.deepEqual(shown.getAll('f'), ['any:contains:deploy', 'actor:contains:alice']);
  // The filter actions sit together at the end of the filter card.
  const actions = ui.el('filterActions');
  assert.ok(actions.classList.contains('justify-content-end'));
  assert.deepEqual(
    [...actions.querySelectorAll('input, button')].map((el) => el.id || el.textContent),
    ['unmapped', 'Apply', 'reset', 'saveSearch'],
  );
  assert.equal(ui.el('filters').lastElementChild, actions);
});

test('a saved search keeps every filter on screen, named by its text filters', async (t) => {
  const names = [];
  const ui = await events(t, {
    query: '&source=ci&rule=deploys&f=any:contains:deploy&f=actor:not_contains:robot&offset=50',
    globals: {
      prompt: (message, suggested) => {
        names.push(suggested);
        return suggested;
      },
    },
  });
  ui.el('unmapped').checked = true;

  ui.el('saveSearch').click();

  await settle(() => ui.calls.some((c) => c.method === 'POST'));
  const saved = ui.calls.find((c) => c.method === 'POST');
  assert.equal(saved.url, '/api/v1/me/saved-searches');
  assert.equal(
    saved.body.url,
    '/events.html?source=ci&rule=deploys&f=any%3Acontains%3Adeploy&f=actor%3Anot_contains%3Arobot&unmapped=true&limit=50',
  );
  assert.equal(
    names[0],
    'Events (source=ci, rule=deploys, Any field contains “deploy”, Actor does not contain “robot”, unmapped)',
  );
  assert.equal(saved.body.name, names[0]);
});

test('with automatic filtering, changes apply without pressing Apply', async (t) => {
  const ui = await events(t, {
    me: { is_admin: true, preferences: { auto_apply_filters: true } },
    query: '&offset=50',
  });

  // Choosing a filter applies it at once, from the first page.
  pickFilter(ui, 'source', 'ci');
  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().get('source'), 'ci');
  assert.equal(ui.lastEvents().get('offset'), '0');
  ui.el('unmapped').checked = true;
  ui.el('unmapped').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.requests.events.length === 3);
  assert.equal(ui.lastEvents().get('unmapped'), 'true');
  ui.el('startDateDmy').value = '01/01/2026';
  ui.el('startDate').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.requests.events.length === 4);
  assert.equal(ui.lastEvents().get('start_date'), '2026-01-01');
});

test('without automatic filtering, nothing reloads until Apply', async (t) => {
  const ui = await events(t, { me: { is_admin: true, preferences: {} } });
  ui.el('addCondition').click();
  fillCondition(ui, 0, 'any', 'contains', 'deploy');
  condition(ui, 0)
    .querySelector('[data-condition-value]')
    .dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  pickFilter(ui, 'source', 'ci');
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
    query: '&source=retired&control=A.9&rule=old-rule',
    facets: {
      sources: [{ source: 'ci', count: 3 }],
      controls: [{ ref: 'A.1', count: 2 }],
      rules: [{ rule_id: 'deploys', count: 2, description: 'Deployments', targets: ['A:A.1'] }],
    },
  });
  await settle(() => filterChips(ui, 'source').length === 2);

  assert.deepEqual(chosenChips(ui, 'source'), [['retired', 'retired (0)']]);
  assert.deepEqual(chosenChips(ui, 'control'), [['A.9', 'Current filter: A.9 (0)']]);
  assert.deepEqual(chosenChips(ui, 'rule'), [['old-rule', 'Current filter: old-rule (0)']]);
  assert.deepEqual(filterChips(ui, 'rule'), [
    ['old-rule', 'Current filter: old-rule (0)'],
    ['deploys', 'deploys - Deployments (2)'],
  ]);
  assert.equal(ui.lastEvents().get('control'), 'A.9');
  assert.equal(ui.lastEvents().get('rule'), 'old-rule');
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
  assert.equal(ui.el('pageTitle').textContent, 'Events for control A.1');
  await settle(() => /Access/.test(chosenChips(ui, 'control')[0][1]));
  assert.deepEqual(chosenChips(ui, 'control'), [['A.1', 'A.1 - Access (2)']]);

  // Choosing a source names the control from its result option.
  pickFilter(ui, 'source', 'ci');

  assert.equal(ui.el('pageTitle').textContent, 'Events for CI and control A.1 - Access');
  assert.equal(ui.document.title, 'Keen - Events for CI and control A.1 - Access');
});

test('a filter missing from the catalogue stays available while the catalogue is unavailable', async (t) => {
  const ui = await events(t, {
    controlsFail: true,
    query: '&control=retired-control&unmapped=true',
  });
  assert.deepEqual(chosenChips(ui, 'control'), [
    ['retired-control', 'Current filter: retired-control'],
  ]);
  assert.deepEqual(filterChips(ui, 'control'), [
    ['retired-control', 'Current filter: retired-control'],
  ]);
  assert.equal(ui.lastEvents().get('control'), 'retired-control');
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

// --- Several values per filter, mapping rules and text filters ---------------

async function richEvents(t, options = {}) {
  return events(t, {
    ...options,
    facets: {
      sources: [
        { source: 'ci', count: 3 },
        { source: 'okta', count: 2 },
      ],
      controls: [
        { ref: 'A.1', title: 'Access', count: 2 },
        { ref: 'A.2', title: 'Backups', count: 1 },
      ],
      rules: [
        { rule_id: 'deploys', count: 2, description: 'Deployments', targets: ['A:A.1'] },
        { rule_id: 'backup-runs', count: 1, description: '', targets: [] },
      ],
    },
  });
}

test('several sources, controls and mapping rules combine into one request', async (t) => {
  const ui = await richEvents(t);
  await settle(() => filterChips(ui, 'rule').length === 2);

  pickFilter(ui, 'source', 'ci');
  pickFilter(ui, 'source', 'okta');
  pickFilter(ui, 'control', 'A.1');
  pickFilter(ui, 'control', 'A.2');
  pickFilter(ui, 'rule', 'deploys');
  ui.el('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));

  await settle(() => ui.requests.events.length === 2);
  assert.equal(ui.lastEvents().get('source'), 'ci,okta');
  assert.equal(ui.lastEvents().get('control'), 'A.1,A.2');
  assert.equal(ui.lastEvents().get('rule'), 'deploys');
  const shown = new URL(ui.window.location.href).searchParams;
  assert.equal(shown.get('source'), 'ci,okta');
  assert.equal(shown.get('rule'), 'deploys');
  assert.equal(
    ui.el('pageTitle').textContent,
    'Events for 2 sources and 2 controls and rule deploys',
  );
  // The rule chip carries the rule's description and targets.
  assert.deepEqual(filterChips(ui, 'rule'), [
    ['deploys', 'deploys - Deployments (2)'],
    ['backup-runs', 'backup-runs (1)'],
  ]);
  assert.equal(
    ui.document.querySelector('#ruleFilterPillModal [data-value="deploys"]').title,
    'deploys maps to A:A.1',
  );
  // The facet request carries the same filters.
  await settle(() => ui.requests.facets.length === 2);
  assert.equal(ui.requests.facets.at(-1).get('control'), 'A.1,A.2');
});

function condition(ui, index) {
  return ui.document.querySelectorAll('[data-condition-row]')[index];
}

function fillCondition(ui, index, field, op, value) {
  const row = condition(ui, index);
  row.querySelector('[data-condition-field]').value = field;
  row.querySelector('[data-condition-op]').value = op;
  row.querySelector('[data-condition-value]').value = value;
}

test('text filters apply one operator per field and travel through the URL and exports', async (t) => {
  const ui = await events(t);
  assert.equal(ui.document.querySelectorAll('[data-condition-row]').length, 0);

  ui.el('addCondition').click();
  ui.el('addCondition').click();
  ui.el('addCondition').click();
  assert.equal(ui.document.activeElement, condition(ui, 2).querySelector('[data-condition-value]'));
  // A new row starts on any field, as the search box used to.
  assert.equal(condition(ui, 0).querySelector('[data-condition-field]').value, 'any');
  assert.equal(
    condition(ui, 0).querySelector('[data-condition-field] option').textContent,
    'Any field',
  );
  fillCondition(ui, 0, 'actor', 'not_contains', 'robot');
  fillCondition(ui, 1, 'summary', 'regex', '^Deploy: v\\d+');
  // A row without a value is not a filter.
  fillCondition(ui, 2, 'system', 'equals', '  ');
  ui.el('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));

  await settle(() => ui.requests.events.length === 2);
  assert.deepEqual(ui.lastEvents().getAll('f'), [
    'actor:not_contains:robot',
    'summary:regex:^Deploy: v\\d+',
  ]);
  assert.deepEqual(new URL(ui.window.location.href).searchParams.getAll('f'), [
    'actor:not_contains:robot',
    'summary:regex:^Deploy: v\\d+',
  ]);
  ui.el('exportCsv').click();
  assert.deepEqual(new URL(ui.downloads[0].href, 'https://keen.example').searchParams.getAll('f'), [
    'actor:not_contains:robot',
    'summary:regex:^Deploy: v\\d+',
  ]);

  // Removing a row applies on the next Apply; Reset clears them all.
  condition(ui, 0).querySelector('[data-condition-remove]').click();
  ui.el('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.requests.events.length === 3);
  assert.deepEqual(ui.lastEvents().getAll('f'), ['summary:regex:^Deploy: v\\d+']);
  ui.el('reset').click();
  await settle(() => ui.requests.events.length === 4);
  assert.deepEqual(ui.lastEvents().getAll('f'), []);
  assert.equal(ui.document.querySelectorAll('[data-condition-row]').length, 0);
  assert.equal(new URL(ui.window.location.href).searchParams.has('f'), false);
});

test('text filters in the address are shown as rows, and at most ten can be added', async (t) => {
  const ui = await events(t, {
    query:
      '&f=actor:contains:alice&f=outcome:not_equals:ok&f=summary:contains:Error: disk full&f=colour:contains:x&f=summary:contains',
  });
  // Rows the API would refuse are left out; a value keeps its own colons.
  const rows = () =>
    [...ui.document.querySelectorAll('[data-condition-row]')].map((row) => [
      row.querySelector('[data-condition-field]').value,
      row.querySelector('[data-condition-op]').value,
      row.querySelector('[data-condition-value]').value,
    ]);
  assert.deepEqual(rows(), [
    ['actor', 'contains', 'alice'],
    ['outcome', 'not_equals', 'ok'],
    ['summary', 'contains', 'Error: disk full'],
  ]);
  assert.deepEqual(ui.lastEvents().getAll('f'), [
    'actor:contains:alice',
    'outcome:not_equals:ok',
    'summary:contains:Error: disk full',
    'colour:contains:x',
    'summary:contains',
  ]);
  // An address with more rows than the API accepts shows the first ten.
  const crowded = await events(t, {
    query: Array.from({ length: 12 }, (_, i) => `&f=actor:contains:a${i}`).join(''),
  });
  assert.equal(crowded.document.querySelectorAll('[data-condition-row]').length, 10);
  assert.equal(crowded.el('addCondition').disabled, true);

  for (let i = 0; i < 12; i++) ui.el('addCondition').click();
  assert.equal(ui.document.querySelectorAll('[data-condition-row]').length, 10);
  assert.equal(ui.el('addCondition').disabled, true);
  condition(ui, 9).querySelector('[data-condition-remove]').click();
  assert.equal(ui.el('addCondition').disabled, false);
});

test('with automatic filtering, a changed text filter applies at once', async (t) => {
  const ui = await events(t, { me: { is_admin: true, preferences: { auto_apply_filters: true } } });
  ui.el('addCondition').click();
  fillCondition(ui, 0, 'actor', 'starts_with', 'ali');

  condition(ui, 0)
    .querySelector('[data-condition-value]')
    .dispatchEvent(new ui.window.Event('change', { bubbles: true }));

  await settle(() => ui.requests.events.length === 2);
  assert.deepEqual(ui.lastEvents().getAll('f'), ['actor:starts_with:ali']);
  condition(ui, 0).querySelector('[data-condition-remove]').click();
  await settle(() => ui.requests.events.length === 3);
  assert.deepEqual(ui.lastEvents().getAll('f'), []);
});

test('without the source register or facets the chosen sources stay and the list loads', async (t) => {
  const ui = await events(t, { sourcesFail: true, facetsFail: true, query: '&source=ci,diary' });
  await settle(() => ui.requests.facets.length === 1);
  assert.deepEqual(chosenChips(ui, 'source'), [
    ['ci', 'ci'],
    ['diary', 'diary'],
  ]);
  assert.deepEqual(filterChips(ui, 'source'), [
    ['ci', 'ci'],
    ['diary', 'diary'],
  ]);
  assert.equal(ui.lastEvents().get('source'), 'ci,diary');
  assert.equal(ui.document.querySelectorAll('#rows tr[data-event-id]').length, 1);
  assert.equal(ui.el('status').dataset.alert, undefined);
});
