import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

// A SWOT item's page: actions that wait on a pending request, draft relationships,
// deep links and ISMS meta without its optional catalogues.
const copy = (value) => JSON.parse(JSON.stringify(value ?? null));

function swot(overrides = {}) {
  return {
    id: 's1',
    title: 'Flood exposure',
    description: 'Ground floor server room',
    response: 'Relocate',
    kind: 'threat',
    significance: 4,
    status: 'planned',
    parent_id: null,
    owner_user_id: null,
    start_date: null,
    target_date: null,
    version: 2,
    created_at: '2026-09-01T00:00:00',
    updated_at: '2026-09-02T00:00:00',
    controls: [],
    clauses: [],
    links: [],
    ...overrides,
  };
}
const fullMeta = { framework: 'A', users: [], controls: [], clauses: [] };
// With the ISMS sections that list people, controls and clauses switched off,
// isms_meta leaves those keys out.
const sparseMeta = { framework: 'A', section: 'all', can_view: true, can_manage: true };
const objective = (i) => ({
  target_type: 'objective',
  target_id: 'o' + i,
  title: 'Objective <i>' + i + '</i>',
  url: '/isms-objective.html?id=o' + i,
});

async function swotPage(
  t,
  {
    query = '&id=s1',
    item = swot(),
    me = { is_admin: true },
    meta = fullMeta,
    choices,
    patch,
    ...options
  } = {},
) {
  const state = { patches: [], posts: [], deletes: [] };
  const ui = await page(t, 'swot', {
    query,
    me,
    ...options,
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/swot/s1') return copy(item);
      if (u.pathname === '/api/v1/isms/meta') return copy(meta);
      if (u.pathname === '/api/v1/isms/swot') return { items: [], total: 0, limit: 200, offset: 0 };
      if (u.pathname.startsWith('/api/v1/isms/swot-choices/'))
        return choices ? choices(u) : { items: [] };
      if (u.pathname === '/api/v1/isms/swot/s1/changelog') return { items: [] };
      if (u.pathname === '/api/v1/questions/entities/isms_swot/s1') return { threads: [] };
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, body) => {
      state.patches.push({ path: u.pathname, body: copy(body) });
      return patch ? patch(u, body) : copy(item);
    },
    post: async (u, body) => {
      state.posts.push({ path: u.pathname, body: copy(body) });
      return { ok: true };
    },
    remove: async (u) => {
      state.deletes.push(u.pathname);
      return { ok: true };
    },
  });
  await settle(
    () =>
      (ui.el('pageTitle').textContent && ui.el('linkType').options.length > 0 && ui.idle()) ||
      ui.el('status').textContent,
  );
  return { ...ui, state };
}

const linkButton = (ui) => ui.el('linkForm').querySelector('[type="submit"]');
const pause = () => new Promise((r) => setTimeout(r, 20));

test('without people, controls or clauses in the meta the editor offers none of them', async (t) => {
  const ui = await swotPage(t, { meta: sparseMeta });
  assert.equal(ui.el('pageTitle').textContent, 'Flood exposure');
  assert.equal(ui.el('status').textContent, '');
  assert.deepEqual(
    [...ui.el('owner').options].map((o) => o.textContent),
    ['Unassigned'],
  );
  assert.equal(ui.el('controls').options.length, 0);
  assert.equal(ui.el('clauses').options.length, 0);

  const draft = await swotPage(t, { query: '&new=1', meta: sparseMeta });
  assert.equal(draft.el('pageTitle').textContent, 'New SWOT item');
  assert.equal(draft.el('status').textContent, '');
  assert.equal(draft.el('controls').options.length, 0);
  assert.match(draft.el('relatedList').textContent, /No linked items\./);
});

test('readers see an item’s relationships without unlink buttons', async (t) => {
  const ui = await swotPage(t, {
    me: { can_view_isms: true },
    item: swot({ links: [objective(1)] }),
  });
  const list = ui.el('relatedList');
  assert.equal(list.querySelector('a').textContent, 'Objective <i>1</i>');
  assert.equal(list.querySelector('i'), null);
  assert.equal(list.querySelector('[data-unlink-id]'), null);
  assert.equal(ui.el('linkForm').hidden, true);
});

test('saving twice before the first save returns sends one update', async (t) => {
  let finish;
  const ui = await swotPage(t, {
    patch: () => new Promise((resolve) => (finish = () => resolve(copy(swot())))),
  });
  ui.el('title').value = 'Flood exposure (revised)';
  const save = ui.el('itemForm').querySelector('[type="submit"]');
  save.click();
  save.click();
  await pause();
  assert.equal(ui.state.patches.length, 1);
  assert.equal(ui.state.patches[0].body.title, 'Flood exposure (revised)');
  finish();
  await settle(() => ui.el('status').textContent === 'SWOT item saved.');
  // Once the save has finished, a further save goes ahead.
  save.click();
  await settle(() => ui.state.patches.length === 2);
});

test('linking a record while the item is saving waits for the save', async (t) => {
  let finish;
  const ui = await swotPage(t, {
    choices: () => ({ items: [objective(1)] }),
    patch: () => new Promise((resolve) => (finish = () => resolve(copy(swot())))),
  });
  await settle(() => ui.el('linkItem').options.length === 2);
  ui.el('itemForm').querySelector('[type="submit"]').click();
  ui.el('linkItem').value = 'o1';
  linkButton(ui).click();
  await pause();
  assert.deepEqual(ui.state.posts, []);
  finish();
  await settle(() => ui.el('status').textContent === 'SWOT item saved.');
});

test('a draft adds nothing when its relationship type has no records to choose', async (t) => {
  const ui = await swotPage(t, { query: '&new=1' });
  // The record list is empty and disabled, so the form submits with no choice.
  assert.equal(ui.el('linkItem').disabled, true);
  linkButton(ui).click();
  await pause();
  assert.equal(ui.el('relatedList').querySelector('[data-unlink-id]'), null);
  assert.equal(ui.el('status').textContent, '');
});

test('a draft adds nothing when Link item is chosen before its records have loaded', async (t) => {
  let win;
  const clicked = [];
  const ui = await page(t, 'swot', {
    query: '&new=1',
    setup(window) {
      win = window;
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/isms/meta') return copy(fullMeta);
      if (u.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (u.pathname === '/api/v1/isms/swot-choices/objective') {
        // The form is in view while the first records are still on their way.
        const form = win.document.getElementById('linkForm');
        clicked.push(form.hidden);
        form.querySelector('[type="submit"]').click();
        await pause();
        return { items: [objective(1)] };
      }
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('linkItem').options.length === 2);
  assert.deepEqual(clicked, [false]);
  assert.equal(ui.el('relatedList').querySelector('[data-unlink-id]'), null);
  assert.equal(ui.el('status').textContent, '');
});

test('a draft keeps at most 200 relationships', async (t) => {
  const records = Array.from({ length: 201 }, (_, i) => objective(i));
  const ui = await swotPage(t, { query: '&new=1', choices: () => ({ items: records }) });
  await settle(() => ui.el('linkItem').options.length === 202);
  for (let i = 0; i < 200; i++) {
    ui.el('linkItem').value = 'o' + i;
    linkButton(ui).click();
  }
  assert.equal(ui.el('relatedList').querySelectorAll('[data-unlink-id]').length, 200);
  assert.equal(ui.el('status').dataset.alert, 'info');
  ui.el('linkItem').value = 'o200';
  linkButton(ui).click();
  assert.equal(ui.el('status').textContent, 'A SWOT item supports up to 200 relationships.');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('relatedList').querySelectorAll('[data-unlink-id]').length, 200);
  assert.equal(ui.el('relatedList').querySelector('[data-unlink-id="o200"]'), null);
  // A record already in the draft is still accepted without growing it.
  ui.el('linkItem').value = 'o0';
  linkButton(ui).click();
  assert.equal(ui.el('status').dataset.alert, 'info');
  assert.equal(ui.el('relatedList').querySelectorAll('[data-unlink-id]').length, 200);
});

test('clicking a relationship card outside its unlink button changes nothing', async (t) => {
  const ui = await swotPage(t, { item: swot({ links: [objective(1)] }) });
  ui.el('relatedList').querySelector('h3').click();
  await pause();
  assert.deepEqual(ui.state.deletes, []);
  assert.equal(ui.el('status').textContent, '');
  assert.ok(ui.el('relatedList').querySelector('[data-unlink-id="o1"]'));
});

test('a deep link opens the named tab of the item', async (t) => {
  const shown = [];
  await swotPage(t, {
    query: '&id=s1&tab=related',
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  assert.deepEqual(shown, ['relatedTab']);

  const unknown = [];
  await swotPage(t, {
    query: '&id=s1&tab=constructor',
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => unknown.push(el.id) }) },
      };
    },
  });
  assert.deepEqual(unknown, ['detailsTab']);
});

test('a draft opened at its Questions tab asks for the item to be saved first', async (t) => {
  const ui = await swotPage(t, {
    query: '&new=1&tab=questions',
    me: { is_admin: true },
    setup(window) {
      window.bootstrap = { Tab: { getOrCreateInstance: () => ({ show() {} }) } };
    },
  });
  const panel = ui.el('questionPanel');
  assert.equal(panel.querySelector('[data-question-list]').textContent, 'Save the record first.');
  assert.equal(panel.querySelector('[data-question-ask]').hidden, true);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/questions/')));
});
