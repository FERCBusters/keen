// Audit scope selection and the scope graph beyond the common path covered in
// audit-page.test.mjs: ticking controls, filters, parent clauses, node order
// and large scopes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { settle } from './support/page-fixture.mjs';
import { apiAudit, auditPage, change, reloaded } from './support/audit-pages.mjs';

const tick = (ui, attr, id, checked = true) =>
  change(ui, ui.document.querySelector(`[${attr}="${id}"]`), { checked });

test('ticking a control adds it to the scope that is saved', async (t) => {
  const { ui, state } = await auditPage(t, {
    audit: apiAudit({ scoped_controls: [{ id: 'c1', ref: 'A1', title: 'Access', type: 'x' }] }),
  });
  tick(ui, 'data-control-id', 'c2');
  assert.equal(ui.el('controlScopeCount').textContent, '2 selected');
  ui.el('saveScope').click();
  await settle(() => state.put);
  assert.deepEqual(state.put.body.controls, ['c1', 'c2']);
  await reloaded(ui);
});

test('scope filters say when nothing matches, and an untitled control does not match "null"', async (t) => {
  const { ui } = await auditPage(t, {
    responses: {
      '/api/v1/controls': {
        items: [
          { id: 'c1', ref: 'A1', title: 'Access', type: 'annex_control' },
          { id: 'c9', ref: 'A9', title: null, type: 'annex_control' },
        ],
      },
    },
  });
  const listed = () =>
    [...ui.document.querySelectorAll('[data-control-id]')].map((x) => x.dataset.controlId);
  assert.deepEqual(listed(), ['c1', 'c9']);
  ui.el('controlFilter').value = 'nul';
  ui.el('controlFilter').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(listed(), []);
  assert.equal(ui.el('controlsList').textContent, 'No controls match.');
  ui.el('controlFilter').value = 'a9';
  ui.el('controlFilter').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(listed(), ['c9']);

  ui.el('clauseFilter').value = 'zzz';
  ui.el('clauseFilter').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('clausesList').textContent, 'No clauses match.');
});

test('choosing a parent clause reports the child clauses and controls it added', async (t) => {
  const { ui } = await auditPage(t, {
    responses: {
      '/api/v1/clauses': {
        items: [
          { id: 'cl4', ref: '4', title: 'Context', parent_id: null },
          { id: 'cl41', ref: '4.1', title: 'Organisation', parent_id: 'cl4' },
          { id: 'cl5', ref: '5', title: 'Leadership', parent_id: null },
          { id: 'cl51', ref: '5.1', title: 'Commitment', parent_id: 'cl5' },
        ],
      },
      '/api/v1/graph/clause_control': {
        nodes: [],
        links: [
          { source: 'clause:cl41', target: 'control:c1' },
          { source: 'clause:cl51', target: 'control:c2' },
        ],
      },
    },
  });
  const status = () => ui.el('status').textContent;
  // One child clause and its one control are named in the singular.
  tick(ui, 'data-clause-id', 'cl5');
  assert.equal(status(), 'Selected 1 child clause and 1 linked control.');
  // A parent whose children are all chosen still brings back their controls.
  tick(ui, 'data-clause-id', 'cl4');
  ui.el('deselectControls').click();
  tick(ui, 'data-clause-id', 'cl4');
  assert.equal(status(), 'Selected 0 child clauses and 1 linked control.');
  assert.equal(ui.document.querySelector('[data-control-id="c1"]').checked, true);
  assert.equal(ui.document.querySelector('[data-control-id="c2"]').checked, false);
  // With nothing left to add, nothing is reported.
  ui.el('status').textContent = '';
  tick(ui, 'data-clause-id', 'cl4');
  assert.equal(status(), '');
});

test('the scope graph lists linked items first, then by reference, and greys unlinked controls', async (t) => {
  const { ui } = await auditPage(t, {
    responses: {
      '/api/v1/audits/a1/scope-map': {
        clauses: [
          { id: 'k10', ref: '10', title: 'Improvement' },
          { id: 'k2', ref: '2', title: 'References' },
          { id: 'k5', ref: '5', title: 'Leadership' },
        ],
        controls: [
          { id: 'c9', ref: 'A9', title: null, type: 'annex_control' },
          { id: 'c3', ref: 'A3', title: 'Crypto', type: 'annex_control' },
          { id: 'c1', ref: 'A1', title: 'Access', type: 'annex_control' },
        ],
        links: [{ clause_id: 'k5', control_id: 'c9', applicability: 'applicable' }],
        summary: {
          clause_count: 3,
          control_count: 3,
          relationship_count: 1,
          controls_in_scope_without_selected_clause_link: 2,
          clauses_without_selected_control_link: 2,
        },
      },
    },
  });
  const svg = ui.el('scopeHeatmap').querySelector('svg');
  const labels = (sel) =>
    [...svg.querySelectorAll(`${sel} text`)].map((x) =>
      [...x.querySelectorAll('tspan')].map((s) => s.textContent).join(' '),
    );
  assert.deepEqual(labels('.audit-scope-clause'), [
    '5 - Leadership',
    '2 - References',
    '10 - Improvement',
  ]);
  // An untitled control is labelled by its reference alone.
  assert.deepEqual(labels('.audit-scope-control'), ['A9', 'A1 - Access', 'A3 - Crypto']);
  const controls = [...svg.querySelectorAll('.audit-scope-control')];
  assert.deepEqual(
    controls.map((g) => g.querySelector('rect').style.fill),
    ['rgba(31,31,42,0.84)', 'rgba(108,117,125,0.72)', 'rgba(108,117,125,0.72)'],
  );
  assert.deepEqual(
    controls.map((g) => g.querySelector('title').textContent.split('\n')[1]),
    [
      '1 linked clause(s) in this audit scope',
      '0 linked clause(s) in this audit scope',
      '0 linked clause(s) in this audit scope',
    ],
  );
  assert.equal(
    ui.el('scopeHeatmapSummary').textContent,
    '3 clause(s), 3 control(s), 1 clause/control relationship(s) in this audit scope. 2 selected clause(s) have no selected control link; 2 selected control(s) have no selected clause link.',
  );
});

const many = (n, prefix, extra = {}) =>
  Array.from({ length: n }, (_, i) => ({
    id: `${prefix}${i}`,
    ref: `${prefix.toUpperCase()}${i}`,
    title: i === 0 ? 'Information security roles and responsibilities' : '',
    ...extra,
  }));

test('a scope of over fifty items draws smaller labels on tighter lines', async (t) => {
  const { ui } = await auditPage(t, {
    responses: {
      '/api/v1/audits/a1/scope-map': {
        clauses: many(55, 'k'),
        controls: many(55, 'c', { type: 'annex_control' }),
        links: [],
        summary: {},
      },
    },
  });
  const svg = ui.el('scopeHeatmap').querySelector('svg');
  assert.equal(svg.getAttribute('height'), String(55 * 42 + 96));
  for (const side of ['clause', 'control']) {
    const texts = [...svg.querySelectorAll(`.audit-scope-${side} text`)];
    assert.ok(
      texts.every((x) => x.style.fontSize === '10px'),
      side,
    );
    // The long first label wraps onto two lines 11px apart.
    const first = svg.querySelector(`.audit-scope-${side}`);
    const ys = [...first.querySelectorAll('tspan')].map((s) => Number(s.getAttribute('y')));
    assert.equal(ys.length, 2, side);
    assert.equal(ys[1] - ys[0], 11, side);
  }
});

test('a scope of over sixty items draws shorter rows', async (t) => {
  const { ui } = await auditPage(t, {
    responses: {
      '/api/v1/audits/a1/scope-map': {
        clauses: many(61, 'k'),
        controls: many(2, 'c', { type: 'annex_control' }),
        links: [],
        summary: {},
      },
    },
  });
  const svg = ui.el('scopeHeatmap').querySelector('svg');
  assert.equal(svg.getAttribute('height'), String(61 * 34 + 96));
  // Only the side with over fifty items uses the smaller labels.
  assert.equal(svg.querySelector('.audit-scope-control text').style.fontSize, '12px');
});

test('with documents switched off, the documents already in scope are listed and can be ticked', async (t) => {
  const scoped = {
    id: 'd1',
    title: 'Policy <b>one</b>',
    document_type: 'policy',
    external_url: null,
    filename: null,
  };
  const { ui, state } = await auditPage(t, {
    me: { is_admin: true, user_id: 'u1', features: { enabled: { documents: false } } },
    audit: apiAudit({ scoped_documents: [scoped] }),
  });
  assert.ok(!state.gets.some((path) => path.startsWith('/api/v1/isms/documents')));
  const row = ui.document.querySelector('[data-document-id="d1"]').closest('label');
  assert.match(row.textContent, /Policy <b>one<\/b>\s*policy\s*0 clauses, 0 controls/);
  assert.equal(row.querySelector('b'), null);
  tick(ui, 'data-document-id', 'd1', false);
  assert.equal(ui.el('documentScopeCount').textContent, '0 selected');
  tick(ui, 'data-document-id', 'd1');
  assert.equal(ui.el('documentScopeCount').textContent, '1 selected');
  // A document without links brings no clauses or controls with it.
  assert.equal(ui.el('clauseScopeCount').textContent, '0 selected');
  assert.equal(ui.el('controlScopeCount').textContent, '0 selected');
});
