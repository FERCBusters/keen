// The chart helpers on their own: label layout with missing text and default
// sizes, dates that are not dates, and mounts that name no chart, an unusable
// chart list, no title or no coverage gaps.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  truncateMiddleText,
  splitLongToken,
  wrapLabelLines,
} from '../public/pages/visualisation/labels.js';
import { utcMsFromIsoDate, isoAddDays } from '../public/pages/visualisation/dates.js';
import { loadUi } from './support/load-ui.mjs';
import { d3 } from './support/visualisation-charts.mjs';

test('missing label text shortens to nothing, and a tiny limit keeps the start', () => {
  assert.equal(truncateMiddleText(null, 10), '');
  assert.equal(truncateMiddleText(undefined), '');
  // Below five characters there is no room for an ellipsis between two ends.
  assert.equal(truncateMiddleText('Access review', 4), 'Acce');
  assert.equal(truncateMiddleText('Access review', 5), 'Ac…ew');
});

test('a missing token splits into nothing, and long tokens split every twelve characters', () => {
  assert.deepEqual(splitLongToken(undefined, 4), []);
  assert.deepEqual(splitLongToken(null), []);
  assert.deepEqual(splitLongToken('x'.repeat(30)), ['x'.repeat(12), 'x'.repeat(12), 'x'.repeat(6)]);
});

test('labels wrap at eighteen characters a line when no width is given', () => {
  assert.deepEqual(wrapLabelLines('Privileged access management review'), [
    'Privileged access',
    'management review',
  ]);
});

test('a node label without sizes is laid out as one line in a 160 by 28 tile', async (t) => {
  const ui = await loadUi('pages/visualisation/labels.js', {
    html: '<svg><text id="label"></text></svg>',
    scripts: [d3, 'window.d3 = d3;'],
  });
  t.after(ui.close);
  const text = ui.document.getElementById('label');
  ui.api.renderWrappedNodeLabel(ui.window.d3.select(text), 'Access control policy review');
  const lines = [...text.querySelectorAll('tspan')];
  // 28px holds one 14px line; 144px holds twenty 7px characters.
  assert.deepEqual(
    lines.map((n) => [n.textContent, n.getAttribute('x'), n.getAttribute('y')]),
    [['Access control…', '80', '14']],
  );
});

test('a month or day of zero is no date', () => {
  assert.ok(Number.isNaN(utcMsFromIsoDate('2026-00-10')));
  assert.ok(Number.isNaN(utcMsFromIsoDate('2026-10-00')));
  assert.ok(Number.isNaN(utcMsFromIsoDate('0000-10-10')));
  assert.equal(isoAddDays('2026-00-10', 1), null);
});

async function mounted(t, attributes) {
  const ui = await loadUi('pages/visualisation/embedded-markup.js', {
    html: `<div data-keen-visualisation-mount ${attributes}></div>`,
  });
  t.after(ui.close);
  ui.api.ensureEmbeddedVisualisationMarkup();
  const mount = ui.document.querySelector('[data-keen-visualisation-mount]');
  return {
    mount,
    modes: [...mount.querySelectorAll('#vizSelect option')].map((o) => o.value),
  };
}

const EVERY_MODE = [
  'graph',
  'heatmap',
  'sunburst_source',
  'sunburst_control',
  'graph_clause_events',
  'heatmap_clause_events',
  'graph_clause_control',
  'histogram',
];

test('a mount that names no usable chart offers every chart', async (t) => {
  assert.deepEqual((await mounted(t, '')).modes, EVERY_MODE);
  assert.deepEqual((await mounted(t, 'data-viz-modes="pie, bogus"')).modes, EVERY_MODE);
  // The first chart offered is the one selected.
  const { mount } = await mounted(t, 'data-viz-modes="pie"');
  assert.equal(mount.querySelector('#vizSelect').value, 'graph');
});

test('a mount without a title, or with a blank one, is titled Visualisations', async (t) => {
  assert.equal((await mounted(t, '')).mount.querySelector('h2').textContent, 'Visualisations');
  assert.equal(
    (await mounted(t, 'data-viz-title="   "')).mount.querySelector('h2').textContent,
    'Visualisations',
  );
});

test('a mount can keep the distribution and leave out the coverage gaps', async (t) => {
  const { mount } = await mounted(t, 'data-show-coverage-gaps="0"');
  assert.ok(mount.querySelector('#topSources'));
  assert.ok(mount.querySelector('#topControls'));
  assert.equal(mount.querySelector('#gaps'), null);
  assert.equal(mount.querySelector('#coverageGapsPanel'), null);
  assert.equal(mount.querySelector('#vizReloadGaps'), null);
});
