import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const controls = [
  {
    id: 'c1',
    ref: 'A.1',
    title: '<b>Access</b>',
    justification: 'Best Practice',
    evidence_count: 4,
    last_evidence: '2026-05-01T00:00:00Z',
    in_scope: true,
  },
  { id: 'c2', ref: 'A.2', title: 'Backup', evidence_count: 0, in_scope: true },
  { id: 'c3', ref: 'A.3', title: 'Crypto', evidence_count: null, in_scope: true },
  { id: 'c4', ref: 'A.4', title: 'Out of scope', evidence_count: 1, in_scope: false },
  { id: 'k1', ref: '4', title: 'Clause row', type: 'clause', in_scope: true },
];

async function controlsPage(t, options = {}) {
  const posted = [];
  const ui = await page(t, 'controls', {
    query: options.query || '',
    globals: options.globals,
    get: async (u) => {
      if (u.pathname === '/api/v1/stats/controls') {
        if (options.fail) throw new Error('Stats offline');
        return { items: options.items ?? controls };
      }
      if (u.pathname === '/api/v1/stats/summary') {
        if (options.summaryFail) throw new Error('Summary offline');
        return options.summary ?? { events: { mapped: 10, unmapped: 2 } };
      }
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      posted.push({ path: u.pathname, body: JSON.parse(JSON.stringify(b)) });
      if (options.postFail) throw new Error('Saved searches offline');
      return { ok: true };
    },
    vendor: options.vendor,
  });
  await settle(() => ui.idle() && !ui.el('rows').querySelector('.table-loading-row'));
  return { ui, posted };
}

test('a saved search that cannot be stored reports why', async (t) => {
  const { ui, posted } = await controlsPage(t, {
    postFail: true,
    globals: { prompt: () => 'Gaps' },
  });
  ui.el('saveSearch').click();
  await settle(() => posted.length === 1);
  await settle(
    () => ui.el('status').textContent === 'Failed to save: Error: Saved searches offline',
  );
  assert.equal(ui.el('status').dataset.alert, 'danger');
});

test('the front search opens the events list, or a saved search by its name', async (t) => {
  const visited = [];
  const { ui } = await controlsPage(t, {
    globals: {
      location: {
        origin: 'https://keen.example',
        pathname: '/controls.html',
        search: '?framework=A',
        get href() {
          return 'https://keen.example/controls.html?framework=A';
        },
        set href(value) {
          visited.push(value);
        },
      },
    },
    vendor: {
      resolveSavedSearchUrl: (name) =>
        name === 'Weekly review' ? '/events.html?source=diary&q=review' : null,
    },
  });
  const submit = () =>
    ui.el('frontSearch').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  ui.el('frontQ').value = '  deploy <b>  ';
  submit();
  ui.el('frontQ').value = '';
  submit();
  ui.el('frontQ').value = 'Weekly review';
  submit();
  assert.deepEqual(visited, [
    '/events.html?q=deploy+%3Cb%3E&framework=A',
    '/events.html?framework=A',
    '/events.html?source=diary&q=review&framework=A',
  ]);
});

const refs = (ui) =>
  [...ui.el('rows').querySelectorAll('tr td:first-child a')].map((a) => a.textContent);

test('controls register shows in-scope controls with evidence counts and KPIs', async (t) => {
  const { ui } = await controlsPage(t);
  assert.deepEqual(refs(ui), ['A.1', 'A.2', 'A.3']);
  assert.equal(ui.el('rows').querySelector('b'), null);
  const rows = [...ui.el('rows').querySelectorAll('tr')];
  // The evidence count opens the control's evidence list.
  assert.match(
    rows[0].querySelector('a.badge').getAttribute('href'),
    /control\.html\?id=c1&tab=evidence&framework=A/,
  );
  assert.match(rows[1].textContent, /0/);
  // Counts withheld from accounts without event access render as a dash.
  assert.equal(rows[2].cells[3].textContent.trim(), '-');
  assert.equal(ui.el('kpiEventsMapped').textContent, '10');
  assert.equal(ui.el('kpiEventsUnmapped').textContent, '2');
  assert.equal(ui.el('kpiControlsWith').textContent, '1');
  assert.equal(ui.el('kpiControlsWithout').textContent, '2');
});

test('control filters update the bookmarkable URL and can be reset', async (t) => {
  const { ui } = await controlsPage(t);
  ui.el('onlyInScope').checked = false;
  ui.el('onlyInScope').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(refs(ui), ['A.1', 'A.2', 'A.3', 'A.4']);
  assert.match(ui.window.location.search, /in_scope=0/);
  ui.el('onlyGaps').checked = true;
  ui.el('onlyGaps').dispatchEvent(new ui.window.Event('change'));
  // Gaps exclude both evidenced and withheld counts.
  assert.deepEqual(refs(ui), ['A.2']);
  ui.el('filterText').value = 'best practice';
  ui.el('filterText').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('rows').textContent, /No controls match your filters/);
  ui.el('resetFilters').click();
  assert.deepEqual(refs(ui), ['A.1', 'A.2', 'A.3']);
  assert.doesNotMatch(ui.window.location.search, /gaps|in_scope/);
});

test('bookmarked filters are restored and saved searches use the current URL', async (t) => {
  let suggested;
  const { ui, posted } = await controlsPage(t, {
    query: '&q=backup&gaps=true&in_scope=false',
    globals: { prompt: (_, def) => ((suggested = def), ' Backup gaps ') },
  });
  assert.equal(ui.el('filterText').value, 'backup');
  assert.equal(ui.el('onlyGaps').checked, true);
  assert.equal(ui.el('onlyInScope').checked, false);
  assert.deepEqual(refs(ui), ['A.2']);
  ui.el('saveSearch').click();
  await settle(() => posted.length);
  assert.equal(suggested, 'Controls (q=backup, gaps, all-scope)');
  assert.equal(posted[0].body.name, 'Backup gaps');
  assert.match(posted[0].body.url, /^\/controls\.html\?.*q=backup.*gaps=1.*in_scope=0/);
});

test('controls handle empty catalogues, counter failures and load failures', async (t) => {
  const empty = await controlsPage(t, { items: [] });
  assert.match(empty.ui.el('rows').textContent, /No controls loaded yet/);
  const counters = await controlsPage(t, { summaryFail: true });
  await settle(() => /failed to load counters/.test(counters.ui.el('status').textContent));
  assert.equal(counters.ui.el('kpiEventsMapped').textContent, '-');
  const withheld = await controlsPage(t, { summary: { events: null } });
  assert.equal(withheld.ui.el('kpiControlsWith').textContent, '-');
  const failed = await controlsPage(t, { fail: true });
  assert.match(failed.ui.el('status').textContent, /Failed to load controls: Error: Stats offline/);
  failed.ui.el('reload').click();
  await settle(
    () => failed.ui.calls.filter((c) => c.url.startsWith('/api/v1/stats/controls')).length === 2,
  );
});

const clauseTree = {
  tree: [
    {
      id: 'k4',
      ref: '4',
      title: 'Context',
      control_link_count: 9,
      upstream_url: 'https://iso.example/4',
      children: [
        {
          id: 'k41',
          ref: '4.1',
          title: '<i>Organisation</i>',
          parent_id: 'k4',
          control_link_count: 3,
          evidence_mappings: [
            { title: 'Charter', url: 'https://docs.example/charter' },
            { url: 'javascript:alert(1)' },
          ],
          children: [],
        },
      ],
    },
    {
      id: 'k5',
      ref: '5',
      title: 'Leadership',
      child_count: 2,
      evidence_mappings: [{ title: '', url: 'https://docs.example/l' }],
      upstream_url: 'javascript:alert(1)',
    },
  ],
};

async function clausesPage(t, options = {}) {
  const ui = await page(t, 'clauses', {
    query: options.query || '',
    get: async (u) => {
      if (u.pathname === '/api/v1/clauses') {
        if (options.fail) throw new Error('Clauses offline');
        return clauseTree;
      }
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('meta').textContent || ui.el('status').textContent);
  return ui;
}

test('clauses register flattens the hierarchy and hides counts on parent clauses', async (t) => {
  const ui = await clausesPage(t);
  assert.equal(ui.el('meta').textContent, '3 of 3 clauses');
  const rows = [...ui.el('rows').querySelectorAll('tr')];
  assert.deepEqual(
    rows.map((r) => r.cells[0].textContent),
    ['4', '4.1', '5'],
  );
  assert.equal(rows[1].cells[0].getAttribute('style'), 'padding-left:1.25rem');
  // Titles are text; the only <i> elements are link icons.
  assert.equal(rows[1].cells[1].textContent, '<i>Organisation</i>');
  assert.equal(ui.el('rows').querySelector('i:not(.bi)'), null);
  // Parent clauses (children present or a child count) show a dash in the count column.
  assert.equal(rows[0].cells[2].textContent, '-');
  assert.equal(rows[1].cells[2].textContent, '3');
  assert.equal(rows[2].cells[2].textContent, '-');
  // Only safe evidence and guidance links are rendered.
  assert.deepEqual(
    [...rows[1].cells[3].querySelectorAll('a')].map((a) => a.getAttribute('href')),
    ['https://docs.example/charter'],
  );
  assert.ok(rows[0].cells[4].querySelector('a[href="https://iso.example/4"]'));
  assert.equal(rows[2].cells[4].querySelector('a'), null);
  assert.equal(rows[2].cells[3].querySelector('a').getAttribute('href'), 'https://docs.example/l');
});

test('clause search filters, persists and clears', async (t) => {
  const ui = await clausesPage(t, { query: '&q=lead' });
  assert.equal(ui.el('q').value, 'lead');
  assert.equal(ui.el('meta').textContent, '1 of 3 clauses');
  ui.el('q').value = 'nothing';
  ui.el('q').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('rows').textContent, /No clauses match/);
  assert.match(ui.window.location.search, /q=nothing/);
  ui.el('clearFilters').click();
  assert.equal(ui.el('meta').textContent, '3 of 3 clauses');
  assert.doesNotMatch(ui.window.location.search, /q=/);
});

test('clause load failures are reported', async (t) => {
  const ui = await clausesPage(t, { fail: true });
  assert.match(ui.el('status').textContent, /Failed to load clauses: Error: Clauses offline/);
  assert.match(ui.el('rows').textContent, /Failed to load clauses/);
});
