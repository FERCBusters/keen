import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3src = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');
const plain = (v) => JSON.parse(JSON.stringify(v));
const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';

const parties = [
  {
    id: 'p1',
    name: { id: 'n1', name: HOSTILE + 'Regulator' },
    nature: { id: 't1', name: 'Statutory' },
    note: 'Notify within 72 hours',
    controls: [{ id: 'c1', ref: 'A.5.1' }, { id: 'c2', title: 'Untitled ref' }, { id: 'c3' }],
    communications: [
      { event: 'Breach', with_whom: 'Commissioner' },
      { event: 'Audit', with_whom: 'Board' },
      { event: 'Annual report', with_whom: 'Public' },
      { event: 'Fourth', with_whom: 'Hidden' },
    ],
  },
  {
    id: 'p2',
    name: { name: 'Customers' },
    nature: { name: 'Contractual' },
    note: '  ',
    controls: [],
    communications: [],
  },
];

const partyNode = (id, label, subtitle, note = '') => ({
  id,
  label,
  subtitle,
  note,
  kind: 'party',
});
const controlNode = (id, ref, title) => ({
  id,
  label: `${ref} ${title}`,
  subtitle: title,
  ref,
  kind: 'control',
});
const longParty =
  'An interested party with a remarkably long descriptive name that will need wrapping';
const graphs = {
  party_controls: {
    title: 'Interested parties × controls',
    left_label: 'Interested parties',
    right_label: 'Controls',
    nodes: {
      left: [
        partyNode('p1', HOSTILE + 'Regulator', 'Statutory', 'Notify within 72 hours'),
        partyNode('p2', 'Customers', 'Contractual'),
      ],
      right: [
        controlNode('c1', 'A.5.1', 'Policies'),
        controlNode('c2', 'A.8.2', 'Privileged access'),
      ],
    },
    links: [
      { source: 'p1', target: 'c1', count: 1 },
      { source: 'p2', target: 'c2', count: 1 },
    ],
    stats: { left_count: 2, right_count: 2, link_count: 2 },
  },
  party_communications: {
    title: 'Interested parties × communications',
    left_label: 'Interested parties',
    right_label: 'Communication rules',
    nodes: {
      left: [
        { ...partyNode('p1', longParty, 'Statutory'), count: 2 },
        partyNode('p2', 'Customers', ''),
      ],
      right: [
        {
          id: 'communication|Breach|72h|Commissioner|Email',
          label: 'Breach × Commissioner',
          subtitle: '72h · Email',
          kind: 'communication',
          count: 2,
        },
        { id: 'c9', kind: 'control', ref: 'A.9', label: 'A.9 Access control' },
      ],
    },
    links: [
      { source: 'p1', target: 'communication|Breach|72h|Commissioner|Email', count: 2 },
      { source: 'p2', target: 'c9', count: 1 },
      { source: 'p2', target: 'missing' },
    ],
    stats: { left_count: 2, right_count: 2, link_count: 3 },
  },
};

async function partiesPage(t, options = {}) {
  const state = {
    names: structuredClone(options.names ?? [{ id: 'n1', name: 'Regulator' }]),
    natures: structuredClone(options.natures ?? [{ id: 't1', name: 'Statutory' }]),
    posted: [],
    removed: [],
    partyQueries: 0,
  };
  const ui = await page(t, 'interested_parties', {
    query: options.query || '',
    me: options.me || { is_admin: true },
    scripts: options.noD3 ? [] : [d3src, 'window.d3 = d3;'],
    setup(window) {
      if (options.tabs) window.bootstrap = options.tabs;
    },
    get: async (u) => {
      switch (u.pathname) {
        case '/api/v1/interested-parties/meta':
          return { events: [], when: [], with_whom: [], methods: [] };
        case '/api/v1/interested-parties/names':
          return { items: structuredClone(state.names) };
        case '/api/v1/interested-parties/natures':
          return { items: structuredClone(state.natures) };
        case '/api/v1/interested-parties':
          state.partyQueries += 1;
          return { items: structuredClone(options.parties ?? parties), total: 7 };
        case '/api/v1/interested-parties/visualisations':
          assert.equal(u.searchParams.get('framework'), 'A');
          if (options.vizFail) throw new Error('Viz offline');
          return { bipartite_graphs: structuredClone(options.graphs ?? graphs) };
        case '/api/v1/audits':
          return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
        default:
          throw new Error('Unexpected GET ' + u);
      }
    },
    post: async (u, body) => {
      state.posted.push({ path: u.pathname, body: plain(body) });
      if (options.postFail) throw new Error('Duplicate name');
      const list = u.pathname.endsWith('/natures') ? state.natures : state.names;
      list.push({ id: 'x' + list.length, name: body.name });
      return {};
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.removeFail) throw new Error('Nature is used by an interested party');
      const id = decodeURIComponent(u.pathname.split('/').pop());
      state.names = state.names.filter((x) => x.id !== id);
      state.natures = state.natures.filter((x) => x.id !== id);
      return {};
    },
  });
  await settle(() => state.partyQueries || ui.el('status').textContent);
  await new Promise((r) => setTimeout(r, 10));
  return { ui, state };
}

test('the register shows controls, up to three communications and notes as text', async (t) => {
  const { ui } = await partiesPage(t);
  const rows = [...ui.el('partyRows').querySelectorAll('tr')];
  assert.equal(ui.el('partyRows').querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
  assert.equal(rows[0].querySelector('a').textContent, HOSTILE + 'Regulator');
  const controlLinks = [...rows[0].querySelectorAll('a.badge')];
  assert.deepEqual(
    controlLinks.map((a) => [a.textContent, a.getAttribute('href')]),
    [
      ['A.5.1', '/control.html?id=c1&tab=governance&framework=A'],
      ['Untitled ref', '/control.html?id=c2&tab=governance&framework=A'],
      ['Control', '/control.html?id=c3&tab=governance&framework=A'],
    ],
  );
  const comms = rows[0].querySelectorAll('td')[4];
  assert.equal(comms.querySelectorAll('br').length, 2);
  assert.doesNotMatch(comms.textContent, /Fourth/);
  assert.match(rows[0].textContent, /Notify within 72 hours/);
  // A blank note, no controls and no communications each show a dash.
  assert.equal(rows[1].querySelectorAll('.small-muted').length, 3);
  assert.equal(ui.el('partyItemsMeta').textContent, '2 of 7 interested parties');
});

test('a party can be sampled into an audit from the register', async (t) => {
  const { ui, state } = await partiesPage(t);
  ui.el('partyRows').querySelector('[data-sample-party="p1"]').click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  assert.equal(
    ui.document.getElementById('entityAuditSampleTitle').textContent,
    `Sample “${HOSTILE}Regulator - Statutory” into audit`,
  );
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.entity_type, 'interested_party');
  assert.equal(state.posted[0].body.entity_id, 'p1');
});

test('natures can be added and deleted, and lookup failures are reported', async (t) => {
  const { ui, state } = await partiesPage(t, { natures: [] });
  assert.match(ui.el('partyNatureList').textContent, /No entries yet/);
  ui.el('newPartyNature').value = '   ';
  ui.el('addPartyNature').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.posted.length, 0);
  ui.el('newPartyNature').value = '  Regulatory  ';
  ui.el('addPartyNature').click();
  await settle(() => ui.el('status').textContent === 'Nature added.');
  assert.deepEqual(state.posted, [
    { path: '/api/v1/interested-parties/natures', body: { name: 'Regulatory' } },
  ]);
  assert.equal(ui.el('newPartyNature').value, '');
  // The new nature is offered as a register filter.
  assert.ok(ui.document.querySelector('.keen-filter-pill-grid [data-value="x0"]'));
  ui.document.querySelector('[data-delete-nature="x0"]').click();
  await settle(() => ui.el('status').textContent === 'Nature deleted.');
  assert.deepEqual(state.removed, ['/api/v1/interested-parties/natures/x0']);
  assert.match(ui.el('partyNatureList').textContent, /No entries yet/);

  const failing = await partiesPage(t, { postFail: true, removeFail: true });
  failing.ui.el('newPartyName').value = 'Regulator';
  failing.ui.el('addPartyName').click();
  await settle(() => failing.ui.el('status').textContent === 'Duplicate name');
  assert.equal(failing.ui.el('newPartyName').value, 'Regulator');
  failing.ui.document.querySelector('[data-delete-nature="t1"]').click();
  await settle(
    () => failing.ui.el('status').textContent === 'Nature is used by an interested party',
  );
});

test('nature filters and reload send the selected IDs', async (t) => {
  const { ui } = await partiesPage(t);
  const queries = () =>
    ui.calls
      .filter((c) => c.url.startsWith('/api/v1/interested-parties?'))
      .map((c) => new URL(c.url, 'https://keen.example').searchParams);
  ui.document.querySelector('.keen-filter-pill-grid [data-value="t1"]').click();
  await settle(() => queries().some((q) => q.get('nature_id') === 't1'));
  ui.el('reloadParties').click();
  await settle(() => queries().length === 3);
  assert.equal(queries().at(-1).get('nature_id'), 't1');
  assert.equal(queries().at(-1).get('limit'), '5000');
});

function fakeTabs() {
  const shown = [];
  return {
    shown,
    Tab: {
      getOrCreateInstance: (el) => ({
        show() {
          shown.push(el.id);
          el.dispatchEvent(new el.ownerDocument.defaultView.Event('shown.bs.tab'));
        },
      }),
    },
  };
}

test('tabs follow the tab query parameter and record the active tab', async (t) => {
  const tabs = fakeTabs();
  const { ui } = await partiesPage(t, { query: '&tab=natures', tabs });
  assert.deepEqual(tabs.shown, ['interestedPartyNaturesTab']);
  assert.match(ui.window.location.search, /tab=natures/);
  ui.el('interestedPartyNamesTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=names/);
  ui.el('interestedPartiesTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.doesNotMatch(ui.window.location.search, /tab=/);
  const unknown = fakeTabs();
  await partiesPage(t, { query: '&tab=unknown', tabs: unknown });
  assert.deepEqual(unknown.shown, ['interestedPartiesTab']);
});

test('inherited object names in the tab parameter select the Items tab', async (t) => {
  for (const name of ['constructor', '__proto__', 'toString', 'hasOwnProperty']) {
    const tabs = fakeTabs();
    await partiesPage(t, { query: `&tab=${name}`, tabs });
    assert.deepEqual(tabs.shown, ['interestedPartiesTab'], name);
  }
});

test('the party/control matrix links both sides and filters by party or control', async (t) => {
  const { ui } = await partiesPage(t, { query: '&tab=visualisations' });
  const matrix = ui.el('partyControlsMatrix');
  await settle(() => matrix.querySelector('table'));
  assert.equal(matrix.querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
  assert.match(
    matrix.textContent,
    /2 interested parties, 2 controls, 2 relationship\(s\)\. Scroll horizontally/,
  );
  const headers = [...matrix.querySelectorAll('th.matrix-control-header')];
  assert.deepEqual(
    headers.map((h) => h.textContent.trim()),
    ['A.5.1', 'A.8.2'],
  );
  assert.equal(headers[0].getAttribute('title'), 'A.5.1 Policies - Policies');
  const rows = [...matrix.querySelectorAll('tbody tr')];
  assert.equal(rows[0].querySelector('a').textContent, HOSTILE + 'Regulator');
  assert.equal(
    rows[0].querySelector('a').getAttribute('href'),
    '/interested_party.html?id=p1&framework=A',
  );
  assert.match(rows[0].textContent, /Statutory · 1 shown control\(s\)/);
  assert.equal(
    rows[0].querySelector('.matrix-party-note').getAttribute('title'),
    'Notify within 72 hours',
  );
  assert.equal(rows[1].querySelector('.matrix-party-note'), null);
  const linked = rows[0].querySelector('.matrix-cell-linked');
  assert.equal(linked.getAttribute('title'), `${HOSTILE}Regulator × A.5.1 Policies`);
  assert.equal(
    linked.querySelector('a').getAttribute('href'),
    '/control.html?id=c1&tab=governance&framework=A',
  );
  assert.equal(rows[0].querySelectorAll('.matrix-cell-empty').length, 1);

  const filter = matrix.querySelector('[data-control-matrix-filter]');
  const search = (value) => {
    filter.value = value;
    filter.dispatchEvent(new ui.window.Event('input'));
  };
  // A party match keeps every control column.
  search('72 HOURS');
  assert.equal(matrix.querySelectorAll('tbody tr').length, 1);
  assert.equal(matrix.querySelectorAll('th.matrix-control-header').length, 2);
  // A control match keeps only parties linked to that control.
  search('privileged');
  assert.deepEqual(
    [...matrix.querySelectorAll('th.matrix-control-header')].map((h) => h.textContent.trim()),
    ['A.8.2'],
  );
  assert.match(matrix.querySelector('tbody').textContent, /Customers/);
  assert.equal(matrix.querySelectorAll('tbody tr').length, 1);
  search('nothing matches this');
  assert.match(matrix.textContent, /No parties or controls match that filter/);
  search('');
  assert.equal(matrix.querySelectorAll('tbody tr').length, 2);
});

test('the communications graph draws linked parties and rules with full-label tooltips', async (t) => {
  const tabs = fakeTabs();
  const { ui } = await partiesPage(t, { query: '&tab=visualisations', tabs });
  const host = ui.el('partyCommunicationsBipartite');
  await settle(() => host.querySelector('svg'));
  // The direct render and the tab event share one request when it finishes first.
  assert.ok(
    ui.calls.filter((c) => c.url.startsWith('/api/v1/interested-parties/visualisations')).length <=
      2,
  );
  const svg = host.querySelector('svg');
  assert.equal(svg.getAttribute('aria-label'), 'Interested parties × communications');
  assert.match(svg.textContent, /2 interested parties, 2 communication rules, 3 relationship\(s\)/);
  const nodes = [...svg.querySelectorAll('.interested-party-bipartite-node')];
  assert.equal(nodes.length, 4);
  assert.equal(
    nodes[0].querySelector(':scope > title').textContent,
    `${longParty}\nStatutory\nRelationships: 2`,
  );
  assert.equal(nodes[0].querySelector('text').querySelectorAll('tspan').length, 2);
  assert.equal(nodes[0].style.cursor, 'pointer');
  // Communication rules have no page of their own; controls do.
  assert.equal(nodes[2].style.cursor, '');
  assert.match(nodes[2].textContent, /Breach × Commissioner \(2\)/);
  assert.match(nodes[2].textContent, /72h · Email/);
  assert.equal(nodes[3].style.cursor, 'pointer');
  assert.match(nodes[3].textContent, /^A\.9/);
  assert.match(nodes[3].textContent, /Access control/);
  const links = [...svg.querySelectorAll('path > title')].map((x) => x.textContent);
  assert.deepEqual(links, [
    `${longParty} × Breach × Commissioner (2)`,
    'Customers × A.9 Access control',
    'Customers × Related item',
  ]);
  host.querySelector('[data-zoom="in"]').click();
  host.querySelector('[data-zoom="out"]').click();
  host.querySelector('[data-zoom="reset"]').click();
  await new Promise((r) => setTimeout(r, 200));
  assert.match(
    svg.querySelector('.interested-party-bipartite-scene').getAttribute('transform'),
    /scale/,
  );
});

test('visualisations explain empty graphs, missing D3 and load failures', async (t) => {
  const empty = await partiesPage(t, {
    query: '&tab=visualisations',
    graphs: {
      party_controls: {
        nodes: { left: [], right: [] },
        links: [],
        empty: 'No interested parties have linked controls yet.',
      },
      party_communications: {
        nodes: { left: [partyNode('p1', 'Regulator', '')], right: [] },
        links: [],
      },
    },
  });
  await settle(() =>
    /No relationship data yet/.test(empty.ui.el('partyCommunicationsBipartite').textContent),
  );
  assert.equal(
    empty.ui.el('partyControlsMatrix').textContent,
    'No interested parties have linked controls yet.',
  );

  const missing = await partiesPage(t, { query: '&tab=visualisations', graphs: {} });
  await settle(() =>
    /No control mapping data yet/.test(missing.ui.el('partyControlsMatrix').textContent),
  );

  const noD3 = await partiesPage(t, { noD3: true, query: '&tab=visualisations' });
  await settle(
    () => noD3.ui.el('partyCommunicationsBipartite').textContent === 'D3 is not available.',
  );

  const failed = await partiesPage(t, { vizFail: true });
  failed.ui
    .el('interestedPartyVisualisationsTab')
    .dispatchEvent(new failed.ui.window.Event('shown.bs.tab'));
  await settle(
    () => failed.ui.el('status').textContent === 'Failed to load visualisations: Viz offline',
  );
});

test('party graph labels mark every truncation with an ellipsis', async (t) => {
  const label =
    'Regional health regulator for clinical records and data-protection-complaints-from-patients-and-carers';
  const graphs = {
    party_controls: { nodes: { left: [], right: [] }, links: [] },
    party_communications: {
      nodes: {
        left: [{ ...partyNode('p1', label, 'Statutory'), count: 2 }],
        right: [
          {
            id: 'comm',
            label: 'Breach × Regulator',
            subtitle: '72h · Email',
            kind: 'communication',
          },
        ],
      },
      links: [{ source: 'p1', target: 'comm', count: 2 }],
      stats: { left_count: 1, right_count: 1, link_count: 1 },
    },
  };
  const { ui } = await partiesPage(t, { query: '&tab=visualisations', graphs });
  const host = ui.el('partyCommunicationsBipartite');
  await settle(() => host.querySelector('svg'));
  const node = host.querySelector('.interested-party-bipartite-node');
  const lines = [...node.querySelector('text').querySelectorAll('tspan')].map((x) => x.textContent);
  assert.equal(lines.length, 2);
  assert.ok(lines.at(-1).endsWith('…'), lines.at(-1));
  assert.ok(node.querySelector(':scope > title').textContent.startsWith(label));
});

test('audit managers can sample parties without event access', async (t) => {
  const features = { enabled: { event_sampling: false } };
  const { ui } = await partiesPage(t, {
    me: { can_view_interested_parties: true, can_manage_audits: true, features },
  });
  assert.ok(ui.el('partyRows').querySelector('[data-sample-party="p1"]'));
  const reader = await partiesPage(t, { me: { can_view_interested_parties: true, features } });
  assert.equal(reader.ui.el('partyRows').querySelector('[data-sample-party]'), null);
});
