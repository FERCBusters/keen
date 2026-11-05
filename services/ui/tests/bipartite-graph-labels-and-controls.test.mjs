// The drawing helpers shared by the bipartite relationship graphs: labels
// with no readable text, curves for links whose node has no position, and the
// zoom controls when a click lands between their buttons.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';

const script = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');

// The graphs read the page's vendored D3 global, loaded here as the page loads it.
async function helpers(t) {
  const ui = await loadUi('pages/bipartite-graph.js', {
    html: '<div id="graph"></div>',
    // The test draws with the same D3 the page code uses.
    scripts: [script, 'window.d3 = d3;'],
    // D3's transitions run on timers.
    globals: { setInterval, clearInterval },
  });
  t.after(ui.close);
  return { ...ui, d3: ui.window.d3 };
}

test('a node label with no readable text is drawn as a dash', async (t) => {
  const ui = await helpers(t);
  const { d3 } = ui;
  assert.deepEqual([...ui.api.splitLabel('')], ['-']);
  assert.deepEqual([...ui.api.splitLabel(' \n\t ')], ['-']);
  assert.deepEqual([...ui.api.splitLabel('  Access   policy ')], ['Access policy']);
  const svg = d3.select(ui.document.getElementById('graph')).append('svg');
  ui.api.addNodeLabel(svg.append('g'), {
    left: true,
    primaryLines: ui.api.splitLabel(''),
    href: '',
  });
  assert.equal(svg.select('text').text(), '-');
  assert.equal(svg.select('text').attr('text-anchor'), 'end');
});

test('a link to a node without a position is drawn from the top of its column', async (t) => {
  const ui = await helpers(t);
  const { d3 } = ui;
  const leftY = d3.scalePoint().domain(['risk-1']).range([100, 200]);
  const rightY = d3.scalePoint().domain(['A1']).range([100, 200]);
  const path = ui.api.curvedLink(10, 90, leftY, rightY, 70);
  assert.equal(path({ source: 'risk-1', target: 'A1' }), 'M10,150 C50,150 50,150 90,150');
  assert.equal(path({ source: 'risk-gone', target: 'A1' }), 'M10,70 C50,70 50,150 90,150');
  assert.equal(path({ source: 'risk-1', target: 'A9' }), 'M10,150 C50,150 50,70 90,70');
});

test('a click between the zoom buttons leaves the view, and a button still zooms', async (t) => {
  const ui = await helpers(t);
  const { d3 } = ui;
  const host = ui.document.getElementById('graph');
  const svg = d3.select(host).append('svg');
  // jsdom lays nothing out; give the SVG the size a browser would report.
  for (const [name, value] of [
    ['width', 820],
    ['height', 620],
  ])
    Object.defineProperty(svg.node(), name, { value: { baseVal: { value } } });
  const scene = svg.append('g');
  ui.api.addZoom(host, svg, scene, {
    contentWidth: 400,
    contentHeight: 300,
    viewportWidth: 820,
    viewportHeight: 620,
  });
  const fitted = scene.attr('transform');
  assert.equal(fitted, 'translate(210,160) scale(1)');
  const controls = host.querySelector('.viz-zoom-controls');
  controls.click();
  await new Promise((resolve) => setTimeout(resolve, 250));
  assert.equal(scene.attr('transform'), fitted);
  controls.querySelector('[data-zoom="in"]').click();
  await new Promise((resolve) => setTimeout(resolve, 250));
  assert.match(scene.attr('transform'), /scale\(1\.25\)$/);
});

test('a node label that links to a record opens it when clicked', async (t) => {
  const ui = await helpers(t);
  const { d3 } = ui;
  const svg = d3.select(ui.document.getElementById('graph')).append('svg');
  const linked = svg.append('g');
  ui.api.addNodeLabel(linked, {
    left: false,
    primaryLines: ['Access control'],
    secondaryLines: ['Control'],
    secondaryY: 20,
    href: '/events.html?framework=F1#control-a1',
  });
  const plain = svg.append('g');
  ui.api.addNodeLabel(plain, { left: true, primaryLines: ['Unlinked risk'] });
  assert.equal(linked.style('cursor'), 'pointer');
  assert.equal(linked.select('text').style('text-decoration'), 'underline');
  plain.node().dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
  assert.equal(ui.window.location.hash, '');
  linked.node().dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
  assert.equal(ui.window.location.hash, '#control-a1');
  assert.equal(plain.style('cursor'), '');
});
