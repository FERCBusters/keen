// The planning views shared by SWOT and ISMS objectives: objectives named by
// their requirement, quadrant trees that stay complete and bounded, timelines
// with nothing dated, nested hierarchies and graph nodes without titles.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as d3 from 'd3';
import { loadUi } from './support/load-ui.mjs';

async function views(t, { withD3 = false } = {}) {
  const ui = await loadUi('pages/planning-views.js', {
    vendor: { withFramework: (url) => url },
    html: '<main id="host"></main>',
    setup(window) {
      if (withD3) window.d3 = d3;
    },
  });
  t.after(ui.close);
  return { ui, host: ui.document.getElementById('host') };
}
const item = (id, parent_id = null, extra = {}) => ({
  id,
  title: `Item ${id}`,
  kind: 'threat',
  significance: 3,
  status: 'planned',
  parent_id,
  ...extra,
});
// A chain of `length` items, each the parent of the next.
const chain = (length) => Array.from({ length }, (_, i) => item(`c${i}`, i ? `c${i - 1}` : null));
const depth = (element, selector) => {
  let levels = 0;
  for (let node = element; node; node = node.parentElement?.closest(selector)) levels++;
  return levels;
};

test('an objective without a goal is named by its requirement', async (t) => {
  const { ui } = await views(t);
  const row = ui.api.normalise(
    { id: 'o1', goal: '', requirement: 'Train every operator', status: 'not_started' },
    true,
  );
  assert.equal(row.title, 'Train every operator');
  assert.equal(row.status, 'defined');
  assert.equal(ui.api.href(row, 'F1'), '/isms-objective.html?id=o1');
});

test('a quadrant shows every item once, items in a parent loop included', async (t) => {
  const { host, ui } = await views(t);
  ui.api.renderBoard(host, [item('a', 'b'), item('b', 'a'), item('root')], { view: 'quadrant' });
  const titles = [...host.querySelectorAll('[data-column="threat"] .planning-compact-title')].map(
    (a) => a.textContent,
  );
  assert.deepEqual(titles, ['Item root', 'Item a', 'Item b']);
  assert.match(host.querySelector('[data-column="threat"] .badge').textContent, /^3$/);
});

test('a quadrant nests twelve levels and lists deeper items after them', async (t) => {
  const { host, ui } = await views(t);
  ui.api.renderBoard(host, chain(14), { view: 'quadrant' });
  const cards = [...host.querySelectorAll('.planning-compact-title')];
  assert.equal(cards.length, 14);
  const levels = cards.map((card) => depth(card.closest('li'), 'li'));
  assert.deepEqual(levels, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 1]);
  assert.equal(cards[13].textContent, 'Item c13');
});

test('a timeline with nothing dated says so and draws no chart', async (t) => {
  const { host, ui } = await views(t, { withD3: true });
  ui.api.renderTimeline(host, [item('a'), item('b', null, { target_date: 'soon' })], 'F1');
  assert.equal(
    host.textContent,
    '0 items with target dates are plotted; 2 without a target date are not plotted. Bars show planned start to target completion; diamonds show target completion only.',
  );
  assert.equal(host.querySelector('svg'), null);
});

test('the hierarchy nests children under their parents, to a bounded depth', async (t) => {
  const { host, ui } = await views(t);
  ui.api.renderHierarchy(host, [item('root'), item('child', 'root'), item('leaf', 'child')], 'F1');
  const links = [...host.querySelectorAll('a')];
  assert.deepEqual(
    links.map((a) => [a.textContent, depth(a.closest('ul'), 'ul')]),
    [
      ['Item root · Threat · 3/5', 1],
      ['Item child · Threat · 3/5', 2],
      ['Item leaf · Threat · 3/5', 3],
    ],
  );
  assert.equal(links[1].getAttribute('href'), '/swot.html?id=child');

  ui.api.renderHierarchy(host, chain(15), 'F1');
  assert.equal(host.querySelectorAll('a').length, 13);
  assert.equal(host.querySelectorAll('a')[12].textContent, 'Item c12 · Threat · 3/5');

  ui.api.renderHierarchy(host, [], 'F1');
  assert.equal(host.querySelectorAll('ul.planning-tree').length, 1);
  assert.equal(host.querySelector('li'), null);
});

test('a graph node for a control without a title is named by its reference', async (t) => {
  const { host, ui } = await views(t, { withD3: true });
  ui.api.renderGraph(
    host,
    {
      title: 'Supplier outage',
      significance: 2,
      controls: [{ id: 'c1', ref: 'A.5.30', title: null }],
    },
    'F1',
  );
  const node = host.querySelector('a[aria-label^="Control"]');
  assert.equal(node.getAttribute('aria-label'), 'Control: A.5.30 ');
  assert.deepEqual(
    [...node.querySelectorAll('tspan')].map((line) => line.textContent),
    ['Control: A.5.30'],
  );
  assert.equal(node.getAttribute('href'), '/control.html?id=c1');
});
