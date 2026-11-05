import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3src = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');

// The metadata and visualisation shapes services/api/app/services/pestle.py sends.
const meta = {
  types: [
    'Political',
    'Economical',
    'Social',
    'Technological',
    'Legal',
    'Environmental',
    'Ethical',
  ],
  lenses: ['Internal', 'External'],
  relevance_levels: [
    { id: 'r0', code: 'na', label: 'N/A', sort_order: 0 },
    { id: 'r1', code: 'high', label: '1 High', sort_order: 1 },
    { id: 'r2', code: 'medium', label: '2 Medium', sort_order: 2 },
    { id: 'r3', code: 'low', label: '3 Low', sort_order: 3 },
  ],
};

function link(
  itemId,
  item,
  clauseId,
  ref,
  title,
  code,
  { type = 'Legal', lens = 'External' } = {},
) {
  const label = { high: '1 High', medium: '2 Medium', low: '3 Low' }[code];
  return {
    item_id: itemId,
    item,
    item_type: type,
    type,
    lens,
    clause_id: clauseId,
    clause_ref: ref,
    clause_title: title,
    relevance_code: code,
    relevance_label: label,
    relevance: { id: 'r', code, label, sort_order: 1 },
  };
}

function visualisations(clauseLinks, counts = { Internal: { Legal: 1 }, External: {} }) {
  return {
    framework: 'A',
    types: meta.types,
    lenses: meta.lenses,
    items: [],
    lens_type_counts: counts,
    relevance_counts: meta.relevance_levels.map((r) => ({
      code: r.code,
      label: r.label,
      count: 0,
    })),
    clause_links: clauseLinks,
    process_links: [],
  };
}

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

async function pestle(
  t,
  { me = { is_admin: true }, query = '', viz, width, backgrounds, processesFail = false } = {},
) {
  const itemQueries = [];
  const ui = await page(t, 'pestle', {
    me,
    query,
    scripts: [d3src, 'window.d3 = d3;'],
    setup(window) {
      window.bootstrap = fakeTabs();
      // jsdom lays nothing out, so the graph's measured width is set as a browser would.
      if (width)
        Object.defineProperty(
          window.document.getElementById('pestleClauseNetwork'),
          'clientWidth',
          {
            value: width,
          },
        );
      if (backgrounds) {
        const real = window.getComputedStyle.bind(window);
        window.getComputedStyle = (el, pseudo) => {
          const colour = backgrounds(el);
          return colour ? { backgroundColor: colour } : real(el, pseudo);
        };
      }
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/pestle/meta') return structuredClone(meta);
      if (u.pathname === '/api/v1/pestle/items') {
        itemQueries.push(Object.fromEntries(u.searchParams));
        return { framework: 'A', total: 0, limit: 5000, offset: 0, items: [] };
      }
      if (u.pathname === '/api/v1/pestle/business-processes') {
        if (processesFail) throw new Error('Processes offline');
        return { total: 0, limit: 5000, offset: 0, items: [] };
      }
      if (u.pathname === '/api/v1/pestle/visualisations') return structuredClone(viz);
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(
    () =>
      itemQueries.length ||
      /permission/.test(ui.el('pestleRows').textContent) ||
      ui.el('status').textContent,
  );
  if (viz) await settle(() => ui.el('pestleClauseNetwork').querySelector('svg'));
  return { ui, itemQueries };
}

const headingXs = (svg) =>
  [...svg.querySelectorAll('.pestle-clause-bipartite-scene > text')]
    .slice(0, 2)
    .map((x) => [x.textContent, Number(x.getAttribute('x'))]);
const nodeLines = (node) =>
  [...node.querySelectorAll('text')].map((text) =>
    [...text.querySelectorAll('tspan')].map((s) => s.textContent),
  );

test('leaving a tab on a page opened without a query string leaves a bare address', async (t) => {
  const { ui } = await pestle(t);
  ui.window.history.replaceState({}, '', '/pestle.html?tab=processes');
  ui.el('pestleItemsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.window.location.href, 'https://keen.example/pestle.html');
  ui.el('businessProcessesTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.equal(ui.window.location.href, 'https://keen.example/pestle.html?tab=processes');
});

test('users without PESTLE(E) access see no business-process editor and reloading sends nothing', async (t) => {
  const { ui, itemQueries } = await pestle(t, { me: { can_view_events: true } });
  assert.equal(ui.el('businessProcessEditorCard').style.display, 'none');
  assert.equal(ui.el('addBusinessProcess').style.display, 'none');
  assert.equal(ui.el('businessProcessName').disabled, true);
  ui.el('reloadPestle').click();
  ui.el('pestleQ').value = 'privacy';
  ui.el('pestleQ').dispatchEvent(new ui.window.Event('input'));
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(itemQueries.length, 0);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/pestle/items')));
  assert.match(ui.el('pestleRows').textContent, /do not have permission/);

  // Readers keep the editor card in view.
  const reader = await pestle(t, { me: { can_view_pestle: true } });
  assert.equal(reader.ui.el('businessProcessEditorCard').style.display, '');
  assert.equal(reader.ui.el('addBusinessProcess').style.display, 'none');
});

test('a reader whose business processes fail to load is offered no Add button', async (t) => {
  const { ui } = await pestle(t, { me: { can_view_pestle: true }, processesFail: true });
  await settle(() => /Processes offline/.test(ui.el('status').textContent));
  assert.equal(ui.el('businessProcessEditorCard').style.display, '');
  assert.equal(ui.el('addBusinessProcess').style.display, 'none');
  assert.equal(ui.el('businessProcessName').disabled, true);
});

test('graph labels read an opaque rgb background without alpha and skip colours they cannot parse', async (t) => {
  const { ui } = await pestle(t, {
    query: '&tab=visualisations',
    viz: visualisations([link('i1', 'Privacy law', 'c1', '4.1', 'Context', 'high')]),
    backgrounds: (el) =>
      ({
        pestleLensTypeViz: 'rgb(10, 10, 10)',
        // A colour space the parser does not read falls through to the card behind it.
        pestleRelevanceVenn: 'oklch(0.21 0.02 260)',
      })[el.id] ||
      // The card that holds the relevance circles.
      (el.classList.contains('card') && el.querySelector(':scope > #pestleRelevanceVenn')
        ? 'rgb(200, 220, 255)'
        : null),
  });
  const heatLabel = ui.el('pestleLensTypeViz').querySelector('svg g text');
  assert.equal(heatLabel.getAttribute('fill'), '#f8fafc');
  assert.equal(heatLabel.getAttribute('stroke'), 'rgb(10, 10, 10)');
  const vennLabel = ui.el('pestleRelevanceVenn').querySelector('svg text.count');
  assert.equal(vennLabel.getAttribute('fill'), '#111827');
  assert.equal(vennLabel.getAttribute('stroke'), 'rgb(200, 220, 255)');
});

test('a register with no PESTLE(E) items draws an empty heatmap without counts', async (t) => {
  const { ui } = await pestle(t, {
    query: '&tab=visualisations',
    viz: visualisations([link('i1', 'Privacy law', 'c1', '4.1', 'Context', 'high')], {
      Internal: {},
      External: {},
    }),
  });
  const cells = [...ui.el('pestleLensTypeViz').querySelectorAll('svg rect')];
  assert.equal(cells.length, 14);
  assert.ok(cells.every((c) => c.getAttribute('fill') === '#e2e8f0'));
  assert.ok(cells.every((c) => c.getAttribute('opacity') === '0.45'));
});

test('a single clause mapping is summarised in the singular', async (t) => {
  const { ui } = await pestle(t, {
    query: '&tab=visualisations',
    viz: visualisations([link('i1', 'Privacy law', 'c1', '4.1', 'Context', 'high')]),
  });
  const svg = ui.el('pestleClauseNetwork').querySelector('svg');
  assert.match(svg.textContent, /1 clause, 1 PESTLE\(E\) item, 1 relevance mapping(?!s)/);
});

test('equally relevant mappings sort by clause then item, and equally mapped items by name', async (t) => {
  const { ui } = await pestle(t, {
    query: '&tab=visualisations',
    viz: visualisations([
      link('i1', 'Zeta', 'c1', '4.1', 'Context', 'medium'),
      link('i4', 'Mike', 'c3', '4.3', 'Scope', 'low'),
      link('i2', 'Alpha', 'c2', '4.2', 'Needs', 'medium'),
      link('i3', 'Kilo', 'c3', '4.3', 'Scope', 'low'),
    ]),
  });
  const svg = ui.el('pestleClauseNetwork').querySelector('svg');
  assert.match(svg.textContent, /3 clauses, 4 PESTLE\(E\) items, 4 relevance mappings/);
  assert.deepEqual(
    [...svg.querySelectorAll('path > title')].map((x) => x.textContent),
    [
      '4.1 Context × Zeta\nRelevance: 2 Medium',
      '4.2 Needs × Alpha\nRelevance: 2 Medium',
      '4.3 Scope × Kilo\nRelevance: 3 Low',
      '4.3 Scope × Mike\nRelevance: 3 Low',
    ],
  );
  const nodes = [...svg.querySelectorAll('.interested-party-bipartite-node')];
  assert.deepEqual(
    nodes.slice(3).map((n) => n.querySelector('tspan').textContent),
    ['Alpha (1)', 'Kilo (1)', 'Mike (1)', 'Zeta (1)'],
  );
});

test('a narrow graph uses the compact layout and wraps labels to its shorter lines', async (t) => {
  const longRef = 'Annex A clause five point one policies for information security';
  const longItem =
    'Cross-border data transfer obligations under several overlapping privacy regimes';
  const { ui } = await pestle(t, {
    query: '&tab=visualisations',
    width: 500,
    viz: visualisations([
      link('i1', longItem, 'c1', longRef, 'Information security policies and topic rules', 'high'),
    ]),
  });
  const svg = ui.el('pestleClauseNetwork').querySelector('svg');
  assert.equal(svg.getAttribute('viewBox'), '0 0 500 520');
  // Long labels cap the left column at 300 and the right at 320; the middle is 230.
  assert.deepEqual(headingXs(svg), [
    ['Clauses', 302],
    ['PESTLE(E) items', 564],
  ]);
  assert.equal(svg.querySelector('path').getAttribute('d'), 'M318,207 C433,207 433,207 548,207');
  assert.equal(
    svg.querySelector('.pestle-clause-bipartite-scene').getAttribute('transform'),
    'translate(10,167.81456953642385) scale(0.5298013245033113)',
  );
  const [clause, item] = svg.querySelectorAll('.interested-party-bipartite-node');
  assert.deepEqual(nodeLines(clause), [
    ['Annex A clause five point', 'one policies for…'],
    ['Information security…'],
  ]);
  assert.deepEqual(nodeLines(item), [
    ['Cross-border data transfer', 'obligations under several', 'overlapping privacy regimes…'],
    ['Legal / External'],
  ]);
});

test('compact columns keep their minimum widths for short labels and grow with medium ones', async (t) => {
  const short = await pestle(t, {
    query: '&tab=visualisations',
    width: 500,
    viz: visualisations([link('i1', 'Tax', 'c1', '4.1', 'Context', 'high')]),
  });
  const shortSvg = short.ui.el('pestleClauseNetwork').querySelector('svg');
  assert.deepEqual(headingXs(shortSvg), [
    ['Clauses', 172],
    ['PESTLE(E) items', 434],
  ]);
  assert.equal(
    shortSvg.querySelector('.pestle-clause-bipartite-scene').getAttribute('transform'),
    'translate(10,130.71207430340556) scale(0.7430340557275542)',
  );

  // Labels between the minimum and the cap widen each column by 6.3 units per character.
  const medium = await pestle(t, {
    query: '&tab=visualisations',
    width: 500,
    viz: visualisations([
      link(
        'i1',
        'Supplier concentration in hosting market',
        'c1',
        '4.1',
        'Understanding the context ok',
        'high',
      ),
    ]),
  });
  const mediumSvg = medium.ui.el('pestleClauseNetwork').querySelector('svg');
  assert.deepEqual(headingXs(mediumSvg), [
    ['Clauses', 204],
    ['PESTLE(E) items', 466],
  ]);
  assert.equal(
    mediumSvg.querySelector('.pestle-clause-bipartite-scene').getAttribute('transform'),
    'translate(10,147.13513513513513) scale(0.6486486486486487)',
  );

  // A wide graph keeps the full layout.
  const wide = await pestle(t, {
    query: '&tab=visualisations',
    width: 1200,
    viz: visualisations([link('i1', 'Tax', 'c1', '4.1', 'Context', 'high')]),
  });
  const wideSvg = wide.ui.el('pestleClauseNetwork').querySelector('svg');
  assert.equal(wideSvg.getAttribute('viewBox'), '0 0 1200 520');
  assert.deepEqual(headingXs(wideSvg), [
    ['Clauses', 262],
    ['PESTLE(E) items', 846],
  ]);
});
