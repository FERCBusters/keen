// Sunburst edge cases: the outer ring of the Other wedge, a chart whose every
// source is a sliver, zooming inside an Other drill-down, saved zooms that no
// longer match a wedge, a breakdown without dates, and labels on thin wedges.
import test from 'node:test';
import assert from 'node:assert/strict';
import { arc, centre, charts, graph, range, settle } from './support/visualisation-charts.mjs';

// Six visible sources and seven single-event ones that merge into "Other".
const manySources = graph([
  { key: 'github', label: 'GitHub', controls: { A1: 300, A2: 200 } },
  { key: 'okta', label: 'Okta', controls: { A3: 300 } },
  ...['aws', 'gcp', 'jira', 'loki'].map((key) => ({
    key,
    label: key.toUpperCase(),
    controls: { A4: 50 },
  })),
  ...Array.from({ length: 7 }, (_, i) => ({
    key: 'tiny' + i,
    label: 'Tiny ' + i,
    controls: { A5: 1 },
  })),
]);

// Two large controls and three single-event ones that merge into "Other".
const tinyControls = graph([
  { key: 'github', label: 'GitHub', controls: { A1: 500, C1: 1, C2: 1 } },
  { key: 'okta', label: 'Okta', controls: { A2: 500, C3: 1 } },
]);

// Saved control zooms name controls by their identifiers.
const CONTROL_IDS = {
  A1: '0b7a2f4e-2c1d-4c58-9b0e-5d8f6a1c2e01',
  C1: '0b7a2f4e-2c1d-4c58-9b0e-5d8f6a1c2e02',
};
for (const n of [...manySources.nodes, ...tinyControls.nodes])
  if (CONTROL_IDS[n.label]) n.control_id = CONTROL_IDS[n.label];

const wedges = (ui) =>
  [...ui.document.querySelectorAll('#viz path.sunburst-arc > title')]
    .map((n) => n.textContent.split('\n')[0])
    .filter((name) => !name.includes('→'));
const panel = (ui) => ui.el('sunburstOtherPanel');
const panelLinks = (ui) =>
  [...panel(ui).querySelectorAll('tbody a')].map((a) => [a.textContent, a.getAttribute('href')]);

test('the outer ring of Other zooms into it and lists the merged sources with their events', async (t) => {
  const ui = await charts(t, { mode: 'sunburst_source', graph: manySources });
  ui.click(arc(ui, 'Other → Other\nMerged: 7 segments\nMapped events: 7'), { shiftKey: true });

  assert.deepEqual(centre(ui).slice(0, 2), ['Other', 'All › Other']);
  assert.equal(panel(ui).style.display, '');
  assert.match(panel(ui).textContent, /Sources merged into "Other"/);
  assert.match(panel(ui).textContent, /Scope: Other - click an item to open filtered events\./);
  assert.equal(panel(ui).querySelector('thead th').textContent, 'Source');
  const links = panelLinks(ui);
  assert.equal(links.length, 7);
  assert.deepEqual(links[0], ['Tiny 0', `/events.html?framework=A&source=tiny0&${range}`]);

  // Other is no source of its own, so the zoom is not saved.
  ui.el('saveDefaultViz').click();
  await settle(() => ui.patches.length === 1);
  assert.deepEqual(ui.patches[0].default_visualisation, {
    mode: 'sunburst_source',
    date_range: { days: 7 },
  });
});

test('on the control sunburst the outer ring of Other lists the merged controls', async (t) => {
  const ui = await charts(t, { mode: 'sunburst_control', graph: tinyControls });
  ui.click(arc(ui, 'Other → Other\nMerged: 3 segments\nMapped events: 3'), { shiftKey: true });

  assert.equal(centre(ui)[0], 'Other');
  assert.match(panel(ui).textContent, /Controls merged into "Other"/);
  assert.equal(panel(ui).querySelector('thead th').textContent, 'Control');
  assert.deepEqual(
    panelLinks(ui).sort(),
    ['C1', 'C2', 'C3'].map((c) => [c, `/events.html?framework=A&control=${c}&${range}`]),
  );
  ui.el('saveDefaultViz').click();
  await settle(() => ui.patches.length === 1);
  assert.equal('sunburst_focus' in ui.patches[0].default_visualisation, false);
});

test('when every source is a sliver, one Other wedge holds them all', async (t) => {
  const slivers = graph(
    Array.from({ length: 50 }, (_, i) => ({
      key: 's' + i,
      label: 'Sliver ' + i,
      controls: { A1: 1 },
    })),
  );
  const ui = await charts(t, { mode: 'sunburst_source', graph: slivers });
  assert.deepEqual(wedges(ui), ['Other']);
  ui.click(arc(ui, 'Other\nMerged: 50 segments\nMapped events: 50'));
  assert.equal(ui.el('vizTitle').textContent, 'Sunburst: by source - Other');
  assert.equal(wedges(ui).length, 50);
  assert.ok(wedges(ui).includes('Sliver 49'));
});

test('inside an Other drill-down, zooming out of a source leaves the centre ready to go back', async (t) => {
  const ui = await charts(t, { mode: 'sunburst_source', graph: manySources });
  ui.click(arc(ui, 'Other\nMerged: 7 segments\nMapped events: 7'));
  const target = ui.document.querySelector('#viz .sunburst-center-target');
  assert.equal(target.style.cursor, 'pointer');

  ui.click(arc(ui, 'Tiny 0\nMapped events: 1'));
  assert.deepEqual(centre(ui).slice(0, 2), ['Tiny 0', 'Other › Tiny 0']);
  ui.click(target);
  assert.equal(centre(ui)[0], 'Other');
  assert.equal(target.style.cursor, 'pointer');
  assert.equal(centre(ui).at(-1), 'Click center to go back');

  ui.click(target);
  assert.equal(ui.el('vizTitle').textContent, 'Sunburst: by source');
  assert.ok(arc(ui, 'Other\nMerged: 7 segments\nMapped events: 7'));
});

test('a saved zoom from the other sunburst opens the whole chart', async (t) => {
  const savedControl = await charts(t, {
    graph: manySources,
    me: {
      is_admin: true,
      preferences: {
        default_visualisation: {
          mode: 'sunburst_control',
          sunburst_focus: { control_id: CONTROL_IDS.A1 },
        },
      },
    },
  });
  assert.equal(centre(savedControl)[0], 'A1');
  savedControl.choose('sunburst_source');
  assert.equal(centre(savedControl)[0], 'All');
  assert.ok(arc(savedControl, 'Other\nMerged: 7 segments\nMapped events: 7'));

  const savedSource = await charts(t, {
    graph: tinyControls,
    me: {
      is_admin: true,
      preferences: {
        default_visualisation: { mode: 'sunburst_source', sunburst_focus: { source_key: 'okta' } },
      },
    },
  });
  assert.equal(centre(savedSource)[0], 'Okta');
  savedSource.choose('sunburst_control');
  assert.equal(centre(savedSource)[0], 'All');
  assert.ok(arc(savedSource, 'Other\nMerged: 3 segments\nMapped events: 3'));
});

test('a saved zoom on a source or control now merged into Other opens the whole chart', async (t) => {
  const source = await charts(t, {
    graph: manySources,
    me: {
      is_admin: true,
      preferences: {
        default_visualisation: {
          mode: 'sunburst_source',
          sunburst_focus: { source_key: 'tiny3' },
        },
      },
    },
  });
  assert.equal(centre(source)[0], 'All');

  const control = await charts(t, {
    graph: tinyControls,
    me: {
      is_admin: true,
      preferences: {
        default_visualisation: {
          mode: 'sunburst_control',
          sunburst_focus: { control_id: CONTROL_IDS.C1 },
        },
      },
    },
  });
  assert.equal(centre(control)[0], 'All');
});

test('with the dates cleared, the Other breakdown and its links leave the dates out', async (t) => {
  const ui = await charts(t, { mode: 'sunburst_source', graph: manySources });
  ui.el('dateFrom').value = '';
  ui.click(arc(ui, 'Other\nMerged: 7 segments\nMapped events: 7'), { shiftKey: true });
  const summary = panel(ui).querySelector('.d-flex > div:last-child').textContent;
  assert.equal(summary, '7 segments · 7 events');
  assert.deepEqual(panelLinks(ui)[0], ['Tiny 0', '/events.html?framework=A&source=tiny0']);
});

test('control references are labelled on thinner wedges than other names', async (t) => {
  // Each small control takes 2.35% of the circle: too thin for a name, wide
  // enough for a short reference such as A.5.1.
  const ui = await charts(t, {
    mode: 'sunburst_source',
    graph: graph([
      { key: 'github', label: 'GitHub', controls: { Big: 1906, 'A.5.1': 47, Backups: 47 } },
    ]),
  });
  const opacity = Object.fromEntries(
    [...ui.document.querySelectorAll('#viz .sunburst-label-depth2')].map((n) => [
      n.firstChild.textContent,
      n.style.opacity,
    ]),
  );
  assert.deepEqual(opacity, { Big: '1', 'A.5.1': '1', Backups: '0' });
});
