import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));
const meta = {
  types: ['Political', 'Legal'],
  lenses: ['External', 'Internal'],
  relevance_levels: [
    { id: 1, code: 'na', label: 'N/A' },
    { id: 2, code: 'low', label: '3 Low' },
    { id: 3, code: 'medium', label: '2 Medium' },
    { id: 4, code: 'high', label: '1 High' },
  ],
};

function item(overrides = {}) {
  return {
    id: 'p1',
    framework: 'A',
    type: 'Legal',
    lens: 'Internal',
    item: '<img src=x onerror=alert(1)> New privacy law',
    rationale: 'Regulator <b>notice</b>',
    overall_relevance: { code: 'high', label: '1 High' },
    updated_at: '2026-03-01T00:00:00Z',
    related_controls: [
      { id: 'c1', ref: 'A1', title: '<i>Privacy</i>', in_scope: true },
      { id: 'c2', ref: 'A2', title: 'Retention', in_scope: false },
    ],
    ...overrides,
  };
}

async function pestleItem(t, options = {}) {
  const state = {
    item: options.item === undefined ? item() : options.item,
    patched: [],
    posted: [],
    removed: [],
    threads: options.threads || [
      {
        id: 't1',
        status: 'reviewing',
        created_by_user_id: 'u-erin',
        created_by_username: 'erin',
        created_at: '2026-03-02T00:00:00Z',
        posts: [
          {
            author_username: 'erin',
            body: 'Line 1\n<script>x</script>',
            created_at: '2026-03-02T00:00:00Z',
          },
        ],
      },
      { id: 't2', status: 'answered', created_by_username: 'frank', posts: [] },
    ],
  };
  const fail = options.fail || {};
  const ui = await page(t, 'pestle_item', {
    query: options.query ?? '&id=p1',
    me: options.me || { is_admin: true, user: 'root' },
    globals: options.globals,
    setup: options.setup,
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/pestle/meta') {
        if (fail.meta) throw new Error('Meta offline');
        return meta;
      }
      if (p === '/api/v1/pestle/items/p1') return structuredClone(state.item);
      if (p === '/api/v1/pestle/items/p1/business-processes')
        return {
          items: [
            { business_process: { id: 'b1', name: 'Payroll <b>' }, relevance: { code: 'medium' } },
            { business_process: { id: 'b2', name: 'Sales' } },
          ],
        };
      if (p === '/api/v1/pestle/items/p1/clauses')
        return {
          items: [
            { clause: { id: 'k1', ref: '4.1', title: 'Context' }, relevance: { code: 'low' } },
            { clause: { id: 'k2', ref: '6.1', title: 'Risks and opportunities' } },
          ],
        };
      if (p === '/api/v1/pestle/items/p1/changelog') {
        if (fail.changelog) throw new Error('Log offline');
        return { items: [{ summary: 'Item created', changed_by_username: 'root', changes: [] }] };
      }
      if (p === '/api/v1/questions/entities/pestle_item/p1') {
        if (fail.questions) throw new Error('<b>Questions offline</b>');
        return { threads: state.threads };
      }
      if (p === '/api/v1/audits')
        return {
          items: [
            { id: 'au1', title: 'Open <b>audit</b>', status: 'open', start_date: '2026-01-01' },
            { id: 'au2', title: 'Done', status: 'completed' },
            { id: 'au3', title: 'Running', status: 'in_progress' },
          ],
        };
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (options.patchFail) throw new Error('Rejected');
      if (u.pathname === '/api/v1/pestle/items/p1') {
        const level = meta.relevance_levels.find((r) => r.code === b.overall_relevance_code);
        state.item = {
          ...state.item,
          ...b,
          overall_relevance: { code: level.code, label: level.label },
        };
        return structuredClone(state.item);
      }
      return { items: [] };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail) throw new Error('Rejected');
      if (u.pathname === '/api/v1/pestle/items') {
        state.item = { ...item(), ...b, id: 'p1', related_controls: [] };
        return structuredClone(state.item);
      }
      return { ok: true };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.removeFail) throw new Error('Rejected');
      return { ok: true };
    },
  });
  await settle(() => ui.el('pestleItemTitle').textContent !== '' || ui.el('status').textContent);
  await new Promise((r) => setTimeout(r, 10));
  return { ui, state };
}

const tab = (ui, id) => ui.el(id).dispatchEvent(new ui.window.Event('shown.bs.tab'));

test('PESTLE item details render safely with relevance badge and related controls', async (t) => {
  const { ui } = await pestleItem(t);
  assert.equal(ui.el('pestleItemTitle').textContent, item().item);
  assert.equal(
    ui.el('pestleItemMeta').textContent,
    'A · Legal / Internal · updated 2026-03-01T00:00:00Z',
  );
  assert.equal(ui.el('pestleEditorTitle').textContent, 'Edit PESTLE(E) item');
  assert.match(ui.el('pestleBadges').innerHTML, /text-bg-danger">1 High/);
  assert.equal(ui.el('pestleType').value, 'Legal');
  assert.equal(ui.el('pestleLens').value, 'Internal');
  assert.equal(ui.el('pestleOverallRelevance').value, 'high');
  assert.equal(ui.el('pestleRationale').value, 'Regulator <b>notice</b>');
  assert.equal(ui.el('relatedControlsCount').textContent, '2');
  assert.equal(ui.el('relatedControls').querySelector('i'), null);
  assert.match(ui.el('relatedControls').textContent, /In scope[\s\S]*Out of scope/);
  assert.match(
    ui.el('relatedControls').querySelector('a').getAttribute('href'),
    /control\.html\?id=c1&framework=A/,
  );
  assert.match(ui.el('pestleBreadcrumb').getAttribute('href'), /pestle\.html\?framework=A/);
  assert.match(ui.el('pestleVisualisations').getAttribute('href'), /tab=visualisations/);
  assert.equal(ui.el('deletePestleItem').style.display, '');
  assert.equal(ui.el('samplePestleAudit').style.display, '');
});

test('read-only PESTLE viewers see disabled fields and no mutation controls', async (t) => {
  const { ui, state } = await pestleItem(t, {
    me: { can_view_pestle: true },
    item: item({ related_controls: [] }),
  });
  assert.equal(ui.el('pestleEditorTitle').textContent, 'View PESTLE(E) item');
  for (const id of [
    'pestleType',
    'pestleLens',
    'pestleOverallRelevance',
    'pestleItemText',
    'pestleRationale',
  ])
    assert.equal(ui.el(id).disabled, true, id);
  for (const id of [
    'savePestleItem',
    'deletePestleItem',
    'samplePestleAudit',
    'saveBusinessProcessRelevance',
    'saveClauseRelevance',
  ])
    assert.equal(ui.el(id).style.display, 'none', id);
  assert.equal(ui.el('pestleQuestions').querySelector('[data-question-ask]').hidden, true);
  assert.match(ui.el('relatedControls').textContent, /No controls are related/);
  tab(ui, 'pestleRelevanceTab');
  await settle(() => ui.el('businessProcessRelevanceList').querySelector('select'));
  assert.ok(
    [...ui.document.querySelectorAll('.process-relevance, .clause-relevance')].every(
      (s) => s.disabled,
    ),
  );
  ui.el('pestleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  ui.el('saveClauseRelevance').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.patched.length, 0);
});

test('users without PESTLE permission are told so and no item is fetched', async (t) => {
  const { ui } = await pestleItem(t, { me: {} });
  assert.match(ui.el('status').textContent, /do not have permission to view PESTLE/);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/pestle/items/')));
});

test('a viewer opening the page without an id is told the id is missing', async (t) => {
  const { ui } = await pestleItem(t, { me: { can_view_pestle: true }, query: '' });
  assert.equal(ui.el('status').textContent, 'Missing PESTLE(E) item id.');
});

test('new PESTLE items require text and are created before relevance can be edited', async (t) => {
  const { ui, state } = await pestleItem(t, { query: '', item: null });
  assert.equal(ui.el('pestleItemTitle').textContent, 'New PESTLE(E) item');
  assert.equal(ui.el('pestleItemMeta').textContent, 'A · new item');
  assert.match(ui.el('pestleBadges').textContent, /unsaved/);
  assert.match(ui.el('relatedControls').textContent, /Save the item and map clauses/);
  assert.match(ui.el('businessProcessRelevanceList').textContent, /Save the item first/);
  assert.match(ui.el('pestleChangelog').textContent, /Save the item first/);
  assert.equal(ui.el('deletePestleItem').style.display, 'none');
  // Questions wait until the item exists.
  const ask = ui.el('pestleQuestions').querySelector('[data-question-ask]');
  assert.equal(ask.hidden, true);
  assert.equal(
    ui.el('pestleQuestions').querySelector('[data-question-list]').textContent,
    'Save the item first.',
  );

  ui.el('pestleItemText').value = '   ';
  ui.el('pestleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Enter a PESTLE\(E\) item/.test(ui.el('status').textContent));
  assert.equal(state.posted.length, 0);

  ui.el('pestleType').value = 'Political';
  ui.el('pestleLens').value = 'External';
  ui.el('pestleOverallRelevance').value = 'medium';
  ui.el('pestleItemText').value = '  Election  ';
  ui.el('pestleRationale').value = 'Policy change';
  ui.el('pestleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'PESTLE(E) item saved');
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/pestle/items',
    body: {
      framework: 'A',
      type: 'Political',
      lens: 'External',
      item: 'Election',
      overall_relevance_code: 'medium',
      rationale: 'Policy change',
    },
  });
  assert.match(ui.window.location.search, /id=p1/);
  assert.ok(ui.el('businessProcessRelevanceList').querySelector('select'));
  assert.equal(ui.el('deletePestleItem').style.display, '');
  await settle(() => ui.el('thread-t1'));
  assert.equal(ask.hidden, false);
});

test('existing PESTLE items are updated with PATCH', async (t) => {
  const { ui, state } = await pestleItem(t);
  ui.el('pestleItemText').value = 'Updated law';
  ui.el('pestleOverallRelevance').value = 'low';
  ui.el('savePestleItem')
    .closest('form')
    .dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'PESTLE(E) item saved');
  assert.equal(state.patched[0].path, '/api/v1/pestle/items/p1');
  assert.equal(state.patched[0].body.item, 'Updated law');
  assert.equal(state.patched[0].body.overall_relevance_code, 'low');
  assert.match(ui.el('pestleBadges').innerHTML, /text-bg-success">3 Low/);
});

test('failed saves report the error and re-enable the save button', async (t) => {
  const { ui } = await pestleItem(t, { patchFail: true });
  ui.el('pestleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Failed to save PESTLE\(E\) item: Rejected/.test(ui.el('status').textContent));
  assert.equal(ui.el('savePestleItem').disabled, false);
  tab(ui, 'pestleRelevanceTab');
  await settle(() => ui.el('businessProcessRelevanceList').querySelector('select'));
  ui.el('saveBusinessProcessRelevance').click();
  await settle(() => /Failed to save process relevance/.test(ui.el('status').textContent));
  ui.el('saveClauseRelevance').click();
  await settle(() => /Failed to save clause relevance/.test(ui.el('status').textContent));
});

test('business process and clause relevance are edited and saved, including filtered clauses', async (t) => {
  const { ui, state } = await pestleItem(t);
  tab(ui, 'pestleRelevanceTab');
  await settle(() => ui.el('clauseRelevanceList').querySelector('select'));
  assert.match(ui.window.location.search, /tab=relevance/);
  assert.equal(ui.el('businessProcessRelevanceList').querySelector('b'), null);
  const proc = ui.document.querySelector('[data-process-id="b1"]');
  assert.equal(proc.value, 'medium');
  assert.equal(ui.document.querySelector('[data-process-id="b2"]').value, 'na');
  proc.value = 'high';
  ui.el('saveBusinessProcessRelevance').click();
  await settle(() => ui.el('status').textContent === 'Business process relevance saved');
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/pestle/items/p1/business-processes',
    body: {
      items: [
        { business_process_id: 'b1', relevance_code: 'high' },
        { business_process_id: 'b2', relevance_code: 'na' },
      ],
    },
  });
  assert.match(
    ui.el('businessProcessRelevanceList').textContent,
    /No business processes exist yet/,
  );

  // Filtering hides a clause; the hidden clause keeps its existing relevance in the payload.
  ui.el('clauseRelevanceFilter').value = 'risks';
  ui.el('clauseRelevanceFilter').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(
    [...ui.document.querySelectorAll('.clause-relevance')].map((s) => s.dataset.clauseId),
    ['k2'],
  );
  ui.document.querySelector('[data-clause-id="k2"]').value = 'high';
  ui.el('saveClauseRelevance').click();
  await settle(() => ui.el('status').textContent === 'Clause relevance saved');
  assert.deepEqual(state.patched[1].body, {
    items: [
      { clause_id: 'k1', relevance_code: 'low' },
      { clause_id: 'k2', relevance_code: 'high' },
    ],
  });
  assert.match(ui.el('clauseRelevanceList').textContent, /No clauses match/);
});

// The shared question panel is tested through the event page; these tests
// cover what the PESTLE(E) item page supplies.
test('PESTLE(E) item questions are asked at the item and read in its Questions tab', async (t) => {
  const { ui, state } = await pestleItem(t);
  tab(ui, 'pestleQuestionsTab');
  await settle(() => ui.el('pestleQuestions').querySelector('.question-thread'));
  assert.match(ui.window.location.search, /tab=questions/);
  assert.equal(ui.el('thread-t1').querySelector('script'), null);
  assert.equal(ui.el('questionBody').placeholder, 'Write a question about this PESTLE(E) item');
  ui.el('questionBody').value = '  Who owns this?  ';
  ui.el('pestleQuestions')
    .querySelector('[data-question-ask]')
    .dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Question submitted');
  assert.deepEqual(state.posted.at(-1), {
    path: '/api/v1/questions/entities/pestle_item/p1',
    body: { body: 'Who owns this?' },
  });
});

test('a link to a PESTLE(E) question thread opens it and marks the replies seen', async (t) => {
  const { ui, state } = await pestleItem(t, {
    me: { can_view_pestle: true, can_create_questions: true, user: 'erin', user_id: 'u-erin' },
    query: '&id=p1&tab=questions&thread=t1',
  });
  await settle(() =>
    state.posted.some((p) => p.path === '/api/v1/questions/entities/pestle_item/p1/mark-seen'),
  );
  assert.ok(ui.el('thread-t1').classList.contains('question-thread-focus'));
  assert.ok(ui.el('questionReply-t1'));
});

test('question and changelog failures are reported inside their panes', async (t) => {
  const { ui } = await pestleItem(t, { fail: { questions: true, changelog: true } });
  tab(ui, 'pestleQuestionsTab');
  await settle(() => /Failed to load questions/.test(ui.el('pestleQuestions').textContent));
  assert.equal(ui.el('pestleQuestions').querySelector('b'), null);
  tab(ui, 'pestleChangelogTab');
  await settle(() => /Failed to load changelog/.test(ui.el('pestleChangelog').textContent));
  assert.match(ui.window.location.search, /tab=changelog/);
  tab(ui, 'pestleDetailsTab');
  assert.doesNotMatch(ui.window.location.search, /tab=/);
});

test('deep links load relevance and changelog panes with the item', async (t) => {
  const rel = await pestleItem(t, { query: '&id=p1&tab=relevance' });
  await settle(() => rel.ui.el('clauseRelevanceList').querySelector('select'));
  const log = await pestleItem(t, { query: '&id=p1&tab=changelog' });
  await settle(() => /Item created/.test(log.ui.el('pestleChangelog').textContent));
});

test('a metadata failure is reported as a load failure', async (t) => {
  const { ui } = await pestleItem(t, { fail: { meta: true } });
  assert.match(ui.el('status').textContent, /Failed to load PESTLE\(E\) item: Meta offline/);
});

test('deleting a PESTLE item requires confirmation', async (t) => {
  const cancelled = await pestleItem(t, { globals: { confirm: () => false } });
  cancelled.ui.el('deletePestleItem').click();
  assert.deepEqual(cancelled.state.removed, []);
  const { ui, state } = await pestleItem(t, { globals: { confirm: () => true } });
  ui.el('deletePestleItem').click();
  await settle(() => ui.el('status').textContent === 'PESTLE(E) item deleted');
  assert.deepEqual(state.removed, ['/api/v1/pestle/items/p1']);
});

test('sampling into an audit offers only active audits the user can edit', async (t) => {
  const { ui, state } = await pestleItem(t);
  ui.el('samplePestleAudit').click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.options.length === 2);
  const select = ui.document.getElementById('entityAuditSampleSelect');
  assert.deepEqual(
    [...select.options].map((o) => o.value),
    ['au1', 'au3'],
  );
  assert.equal(select.options[0].textContent, 'Open <b>audit</b> · 2026-01-01 · Open');
  assert.equal(
    ui.document.getElementById('entityAuditSampleTitle').textContent,
    `Sample “${item().item}” into audit`,
  );
  ui.document.getElementById('entityAuditSampleNotes').value = 'Checked';
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/audits/au1/evidence',
    body: {
      entity_type: 'pestle_item',
      entity_id: 'p1',
      evidence_url: '/pestle_item.html?framework=A&id=p1',
      title: item().item,
      notes: 'Checked',
    },
  });
  await settle(() => ui.el('status').textContent === 'Item sampled into audit');
});

test('audit managers without event access can sample a PESTLE(E) item', async (t) => {
  const { ui } = await pestleItem(t, {
    me: {
      can_view_pestle: true,
      can_manage_audits: true,
      features: { enabled: { event_sampling: false } },
    },
  });
  await settle(() => ui.el('samplePestleAudit').style.display === '');
});

test('a new item opens its deep-linked tab and every pane asks for a save first', async (t) => {
  const shown = [];
  const { ui } = await pestleItem(t, {
    query: '&tab=questions',
    item: null,
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  assert.deepEqual(shown, ['pestleQuestionsTab']);
  assert.equal(
    ui.el('pestleQuestions').querySelector('[data-question-list]').textContent,
    'Save the item first.',
  );
  tab(ui, 'pestleChangelogTab');
  assert.equal(ui.el('pestleChangelog').textContent.trim(), 'Save the item first.');
  tab(ui, 'pestleRelevanceTab');
  assert.match(ui.el('businessProcessRelevanceList').textContent, /Save the item first/);
  assert.match(ui.el('clauseRelevanceList').textContent, /Save the item first/);
  // The address keeps the tab and never names an item that does not exist yet.
  const search = new URL(ui.window.location.href).searchParams;
  assert.equal(search.get('tab'), 'relevance');
  assert.equal(search.has('id'), false);
  assert.equal(
    ui.calls.some((c) => c.url.startsWith('/api/v1/pestle/items/')),
    false,
  );
});

test('a refused deletion is reported and the button is usable again', async (t) => {
  const { ui, state } = await pestleItem(t, { globals: { confirm: () => true }, removeFail: true });
  ui.el('deletePestleItem').click();
  await settle(() => /Failed to delete PESTLE\(E\) item/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Failed to delete PESTLE(E) item: Rejected');
  assert.equal(ui.el('deletePestleItem').disabled, false);
  assert.deepEqual(state.removed, ['/api/v1/pestle/items/p1']);
  assert.equal(ui.el('pestleItemTitle').textContent, item().item);
});
