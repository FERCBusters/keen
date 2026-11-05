// The organisation chart drawn by d3 on the ISMS register: its zoom buttons and
// the labels of nodes whose names do not fit.
import test from 'node:test';
import assert from 'node:assert/strict';
import { orgRecord, register, settle, user } from './support/isms-register-fixture.mjs';

const scaleOf = (g) => Number(/scale\(([^)]+)\)/.exec(g.getAttribute('transform') || '')?.[1]);

test('the chart zooms in and out from its buttons, and Reset returns to the first view', async (t) => {
  const ui = await register(t, {
    tab: 'org',
    records: { org_nodes: [orgRecord('n1', 'Board')] },
  });
  await settle(() => ui.el('orgChartViz').querySelector('svg > g'));
  const chart = ui.el('orgChartViz').querySelector('svg > g');
  const initial = chart.getAttribute('transform');
  assert.equal(scaleOf(chart), 1);

  ui.el('orgChartZoomIn').click();
  await settle(() => Math.abs(scaleOf(chart) - 1.2) < 1e-9);
  ui.el('orgChartZoomIn').click();
  await settle(() => Math.abs(scaleOf(chart) - 1.44) < 1e-9);
  ui.el('orgChartZoomOut').click();
  await settle(() => Math.abs(scaleOf(chart) - 1.2) < 1e-9);
  ui.el('orgChartZoomReset').click();
  await settle(() => chart.getAttribute('transform') === initial);
  assert.equal(ui.el('status').textContent, '');
});

test('a node label that cannot fit is cut to the line and ends with an ellipsis', async (t) => {
  const ui = await register(t, {
    tab: 'org',
    records: {
      org_nodes: [
        orgRecord('n1', 'Alpha Supercalifragilisticexpialidocious Gamma', {
          node_type: 'department',
          people: [
            { relationship_type: 'member', user: user('u1', 'alexandra.longname') },
            { relationship_type: 'member', user: user('u2', 'bartholomew.longname') },
          ],
        }),
      ],
    },
  });
  await settle(() => ui.el('orgChartViz').querySelectorAll('.isms-org-node-title').length > 1);
  const node = [...ui.el('orgChartViz').querySelectorAll('svg g g g')].find((g) =>
    g.querySelector('title')?.textContent.startsWith('Alpha'),
  );
  assert.deepEqual(
    [...node.querySelectorAll('.isms-org-node-title')].map((line) => line.textContent),
    ['Alpha', 'Supercalifragilisticexpiali…'],
  );
  assert.equal(
    node.querySelector('.isms-org-node-subtitle').textContent,
    'department · alexandra.longname,…',
  );
  assert.equal(
    node.querySelector('title').textContent,
    'Alpha Supercalifragilisticexpialidocious Gamma (alexandra.longname, bartholomew.longname)',
  );
});
