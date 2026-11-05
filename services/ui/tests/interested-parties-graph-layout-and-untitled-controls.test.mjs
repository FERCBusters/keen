import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3src = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');

// Graphs in the shape services/api/app/services/interested_parties.py builds:
// parties on the left, controls or communication rules on the right.
const party = (id, name, nature) => ({
  id,
  label: `${name} - ${nature}`,
  subtitle: nature,
  note: '',
  kind: 'party',
  count: 1,
});
const control = (id, ref, title) => ({
  id,
  label: `${ref} ${title || ''}`.trim(),
  subtitle: title || '',
  kind: 'control',
  ref,
  count: 1,
});
const rule = (event, when, withWhom, methods) => ({
  id: ['communication', event, when, withWhom, methods].join('|'),
  label: `${event} × ${withWhom}`,
  subtitle: `${when} · ${methods}`,
  kind: 'communication',
  count: 1,
});
function graph(key, left, right) {
  return {
    key,
    title:
      key === 'party_controls'
        ? 'Interested parties × controls'
        : 'Interested parties × communications',
    left_label: 'Interested parties',
    right_label: key === 'party_controls' ? 'Controls' : 'Communication rules',
    empty: 'No data yet.',
    nodes: { left, right },
    links: left.map((l, i) => ({ source: l.id, target: right[i].id, count: 1, details: [] })),
    stats: { left_count: left.length, right_count: right.length, link_count: left.length },
  };
}

async function partiesPage(t, { query = '', width, graphs } = {}) {
  let partyQueries = 0;
  const ui = await page(t, 'interested_parties', {
    query,
    scripts: [d3src, 'window.d3 = d3;'],
    setup(window) {
      // jsdom lays nothing out, so the graph's measured width is set as a browser would.
      if (width)
        Object.defineProperty(
          window.document.getElementById('partyCommunicationsBipartite'),
          'clientWidth',
          { value: width },
        );
    },
    get: async (u) => {
      switch (u.pathname) {
        case '/api/v1/interested-parties/meta':
          return {
            framework: 'A',
            can_view: true,
            can_manage: true,
            events: [],
            when: [],
            with_whom: [],
            methods: [],
          };
        case '/api/v1/interested-parties/names':
        case '/api/v1/interested-parties/natures':
          return { items: [] };
        case '/api/v1/interested-parties':
          partyQueries += 1;
          return { framework: 'A', total: 0, limit: 5000, offset: 0, items: [] };
        case '/api/v1/interested-parties/visualisations':
          return { framework: 'A', items: [], bipartite_graphs: structuredClone(graphs) };
        default:
          throw new Error('Unexpected GET ' + u);
      }
    },
  });
  await settle(() => partyQueries || ui.el('status').textContent);
  if (graphs)
    await settle(
      () => ui.el('partyCommunicationsBipartite').textContent !== 'Loading visualisation',
    );
  return ui;
}

const commsGraph = (left, right) => ({
  party_controls: graph(
    'party_controls',
    [party('p0', 'Board', 'Owner')],
    [control('c0', 'A.5', 'Policies')],
  ),
  party_communications: graph('party_communications', left, right),
});
const headingXs = (svg) =>
  [...svg.querySelectorAll('.interested-party-bipartite-scene > text')]
    .slice(0, 2)
    .map((x) => [x.textContent, Number(x.getAttribute('x'))]);
const nodeLines = (node) =>
  [...node.querySelectorAll('text')].map((text) =>
    [...text.querySelectorAll('tspan')].map((s) => s.textContent),
  );
const sceneTransform = (svg) =>
  svg.querySelector('.interested-party-bipartite-scene').getAttribute('transform');

test('leaving a tab on a page opened without a query string leaves a bare address', async (t) => {
  const ui = await partiesPage(t);
  ui.window.history.replaceState({}, '', '/interested_parties.html?tab=names');
  ui.el('interestedPartiesTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.window.location.href, 'https://keen.example/interested_parties.html');
});

test('a control without a title is headed by its reference alone in the matrix', async (t) => {
  const ui = await partiesPage(t, {
    query: '&tab=visualisations',
    graphs: {
      party_controls: graph(
        'party_controls',
        [party('p1', 'Regulator', 'Statutory'), party('p2', 'Customers', 'Contractual')],
        [control('c1', 'A.9', null), control('c2', 'A.5.1', 'Policies <b>')],
      ),
      party_communications: graph('party_communications', [], []),
    },
  });
  const headers = [...ui.el('partyControlsMatrix').querySelectorAll('th.matrix-control-header')];
  assert.deepEqual(
    headers.map((h) => [h.textContent, h.getAttribute('title')]),
    [
      ['A.9', 'A.9'],
      ['A.5.1', 'A.5.1 Policies <b> - Policies <b>'],
    ],
  );
  const cells = [...ui.el('partyControlsMatrix').querySelectorAll('.matrix-cell-linked')];
  assert.deepEqual(
    cells.map((c) => c.getAttribute('title')),
    ['Regulator - Statutory × A.9', 'Customers - Contractual × A.5.1 Policies <b>'],
  );
  assert.equal(ui.el('partyControlsMatrix').querySelector('b'), null);
});

test('a narrow communications graph uses the compact layout and wraps labels to its shorter lines', async (t) => {
  const ui = await partiesPage(t, {
    query: '&tab=visualisations',
    width: 500,
    graphs: commsGraph(
      [
        party(
          'p1',
          'Information Commissioner’s Office enforcement team',
          'Statutory regulator for data',
        ),
      ],
      [
        rule(
          'Notifiable Event',
          'Within 72 hours',
          'Senior Supplier Point of Contact',
          'Email, Telephone, Fastest means possible',
        ),
      ],
    ),
  });
  const svg = ui.el('partyCommunicationsBipartite').querySelector('svg');
  assert.equal(svg.getAttribute('viewBox'), '0 0 500 500');
  // Long labels cap the left column at 260 and the right at 300; the middle is 260.
  assert.deepEqual(headingXs(svg), [
    ['Interested parties', 262],
    ['Communication rules', 554],
  ]);
  assert.equal(svg.querySelector('path').getAttribute('d'), 'M278,196 C408,196 408,196 538,196');
  assert.equal(sceneTransform(svg), 'translate(10,159.58904109589042) scale(0.547945205479452)');
  const [left, right] = svg.querySelectorAll('.interested-party-bipartite-node');
  assert.deepEqual(nodeLines(left), [
    ['Information', 'Commissioner’s Office…'],
    ['Statutory regulator…'],
  ]);
  assert.deepEqual(nodeLines(right), [
    ['Notifiable Event × Senior', 'Supplier Point of Contact'],
    ['Within 72 hours · Email,…'],
  ]);
});

test('compact graph columns keep their minimum widths for short labels and grow with medium ones', async (t) => {
  const short = await partiesPage(t, {
    query: '&tab=visualisations',
    width: 500,
    graphs: commsGraph([party('p1', 'Board', 'Owner')], [rule('Alert', 'Routine', 'ICO', 'Email')]),
  });
  const shortSvg = short.el('partyCommunicationsBipartite').querySelector('svg');
  assert.deepEqual(headingXs(shortSvg), [
    ['Interested parties', 152],
    ['Communication rules', 444],
  ]);
  assert.equal(
    sceneTransform(shortSvg),
    'translate(10,127.39938080495355) scale(0.7430340557275542)',
  );

  // Labels between the minimum and the cap widen each column by 6.2 units per character.
  const medium = await partiesPage(t, {
    query: '&tab=visualisations',
    width: 500,
    graphs: commsGraph(
      [party('p1', 'Customers', 'Contractual')],
      [rule('Business as Usual', 'Routine', 'Individual member', 'Email')],
    ),
  });
  const mediumSvg = medium.el('partyCommunicationsBipartite').querySelector('svg');
  assert.deepEqual(headingXs(mediumSvg), [
    ['Interested parties', 194],
    ['Communication rules', 486],
  ]);
  assert.equal(
    sceneTransform(mediumSvg),
    'translate(10,149.492385786802) scale(0.6091370558375635)',
  );

  // A wide graph keeps the full layout.
  const wide = await partiesPage(t, {
    query: '&tab=visualisations',
    width: 1200,
    graphs: commsGraph([party('p1', 'Board', 'Owner')], [rule('Alert', 'Routine', 'ICO', 'Email')]),
  });
  const wideSvg = wide.el('partyCommunicationsBipartite').querySelector('svg');
  assert.equal(wideSvg.getAttribute('viewBox'), '0 0 1200 500');
  assert.deepEqual(headingXs(wideSvg), [
    ['Interested parties', 240],
    ['Communication rules', 896],
  ]);
});
