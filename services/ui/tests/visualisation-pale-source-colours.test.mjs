// Pale source colours, such as a yellow badge or a light grey one, stay legible
// in every chart: graph tiles and sunburst wedges are darkened further under
// white labels, and heatmap shading keeps more of the colour at both ends.
import test from 'node:test';
import assert from 'node:assert/strict';
import { charts, graph } from './support/visualisation-charts.mjs';

// Yellow (#ffff00) has a relative luminance of 0.93; light grey (#e0e0e0) 0.75.
const pale = graph([
  { key: 'pollen', label: 'Pollen', color: '#ffff00', controls: { A1: 100, A2: 1 } },
  { key: 'fog', label: 'Fog', color: '#e0e0e0', controls: { A1: 100, A2: 1 } },
]);

test('graph tiles darken a yellow badge by half and a light grey one by two fifths', async (t) => {
  const ui = await charts(t, { mode: 'graph', graph: pale });
  const fills = Object.fromEntries(
    [...ui.document.querySelectorAll('#viz g.node-source')].map((g) => [
      g.querySelector(':scope > title').textContent.split('\n')[0],
      g.querySelector('rect').style.fill,
    ]),
  );
  // 255 × 0.48 = 122 (#7a); 224 × 0.6 = 134 (#86).
  assert.deepEqual(fills, { Pollen: '#7a7a00', Fog: '#868686' });
});

test('heatmap cells of pale sources keep their colour at the faintest and strongest', async (t) => {
  const ui = await charts(t, { mode: 'heatmap', graph: pale });
  const fills = Object.fromEntries(
    [...ui.document.querySelectorAll('#viz rect.heat-cell')].map((cell) => [
      cell.querySelector(':scope > title').textContent.split('\n')[0],
      cell.getAttribute('fill'),
    ]),
  );
  assert.deepEqual(fills, {
    // The busiest cell mixes yellow 78% towards black and grey 72%.
    'Pollen × A1': 'rgb(56, 56, 0)',
    'Fog × A1': 'rgb(63, 63, 63)',
    // A single event starts from yellow mixed 30% towards white and grey 38%.
    'Pollen × A2': 'rgb(255, 255, 40)',
    'Fog × A2': 'rgb(230, 230, 230)',
  });
});

test('sunburst wedges darken a yellow badge by 38% and a light grey one by 28%', async (t) => {
  const ui = await charts(t, { mode: 'sunburst_source', graph: pale });
  const fills = Object.fromEntries(
    [...ui.document.querySelectorAll('#viz path.sunburst-arc')]
      .map((wedge) => [
        wedge.querySelector(':scope > title').textContent.split('\n')[0],
        wedge.getAttribute('fill'),
      ])
      .filter(([name]) => !name.includes('→')),
  );
  // 255 × 0.62 = 158 (#9e); 224 × 0.72 = 161 (#a1).
  assert.deepEqual(fills, { Pollen: '#9e9e00', Fog: '#a1a1a1' });
});
