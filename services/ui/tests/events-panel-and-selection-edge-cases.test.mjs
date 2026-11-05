// The Events page's side panel and story selection with sparse events, the
// design system's offcanvas, stale failures and menu use in other orders.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

// The design system's shorten(): white space collapses, so a summary of
// spaces shows as nothing.
const shorten = (s, limit = 160) => {
  const t = String(s ?? '')
    .replace(/\s+/g, ' ')
    .trim();
  return t.length <= limit ? t : t.slice(0, limit - 1) + '…';
};

async function events(t, options = {}) {
  const requests = [];
  const ui = await page(t, 'events', {
    me: options.me || { is_admin: false },
    globals: options.globals,
    // The design system's shorten(), and a badge it would draw for any source.
    vendor: { shorten, sourceBadgeHtml: (source) => `<span class="source-badge">${source}</span>` },
    get: async (u) => {
      requests.push(u.pathname + u.search);
      if (u.pathname === '/api/v1/sources/meta') return { items: [] };
      if (u.pathname === '/api/v1/controls') return { items: [] };
      if (u.pathname === '/api/v1/events/facets') return { sources: [], controls: [], rules: [] };
      if (u.pathname === '/api/v1/events')
        return { total: (options.items || []).length, items: options.items || [] };
      if (u.pathname === '/api/v1/stories')
        return { items: [{ id: 's1', title: 'Q1', can_edit: true }] };
      const m = u.pathname.match(/^\/api\/v1\/events\/([^/]+)$/);
      if (m && options.detail) return options.detail(m[1]);
      throw new Error('Unexpected GET ' + u);
    },
    post: options.post,
  });
  await settle(() => requests.some((r) => r.startsWith('/api/v1/events?')) && ui.idle());
  const panel = ui.el('eventPanel');
  const open = (id) =>
    ui
      .el('rows')
      .querySelector(`tr[data-event-id="${id}"] td:not(.story-select-cell)`)
      .dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true, cancelable: true }));
  return { ...ui, requests, panel, open };
}

const ROW = { id: 'e1', source: 'ci', summary: 'Deploy', timestamp: '2026-01-01', controls: [] };

test('the panel leaves out empty details and names an event without a summary or source', async (t) => {
  const ui = await events(t, {
    items: [ROW],
    detail: () => ({
      id: 'e1',
      source: '',
      summary: '',
      timestamp: '2026-01-01T10:00:00Z',
      system: null,
      actor: '',
      action: '   ',
      outcome: undefined,
      severity: 0,
      connection_id: 'conn-<7>',
      connection_name: null,
      artifacts: [],
      normalized_payload: 'not an object',
      controls: [],
    }),
  });
  ui.open('e1');
  await settle(() => ui.panel.querySelector('[data-panel-title]').textContent === '(no summary)');
  const details = [...ui.panel.querySelectorAll('[data-panel-details] dt')].map((dt) => [
    dt.textContent,
    dt.nextElementSibling.textContent,
  ]);
  assert.deepEqual(details, [
    ['Severity', '0'],
    ['Connection', 'conn-<7>'],
  ]);
  const body = ui.panel.querySelector('[data-panel-body]');
  assert.equal(body.querySelector('.source-badge'), null);
  assert.match(body.querySelector('[data-panel-fields]').textContent, /No structured fields\./);
});

test('the design system offcanvas shows and hides the panel when it is present', async (t) => {
  const calls = [];
  const Offcanvas = {
    getOrCreateInstance: (el) => ({
      show: () => calls.push(['show', el.id]),
      hide: () => calls.push(['hide', el.id]),
    }),
  };
  const ui = await events(t, {
    items: [ROW],
    globals: { bootstrap: { Offcanvas } },
    detail: () => ({ ...ROW, artifacts: [], normalized_payload: {} }),
  });
  ui.open('e1');
  await settle(() => ui.panel.querySelector('[data-panel-title]').textContent === 'Deploy');
  ui.panel.querySelector('[data-panel-close]').click();
  assert.deepEqual(calls, [
    ['show', 'eventPanel'],
    ['hide', 'eventPanel'],
  ]);
  // The offcanvas script owns the panel's visibility.
  assert.equal(ui.panel.classList.contains('show'), false);
  assert.equal(ui.panel.getAttribute('role'), null);
});

test('a failure for an earlier event does not replace the event opened last', async (t) => {
  let fail;
  const ui = await events(t, {
    items: [ROW, { ...ROW, id: 'e2', summary: 'Rollback' }],
    detail: (id) =>
      id === 'e1'
        ? new Promise((resolve, reject) => {
            fail = () => reject(new Error('Event offline'));
          })
        : { ...ROW, id: 'e2', summary: 'Rollback', artifacts: [], normalized_payload: {} },
  });
  ui.open('e1');
  ui.open('e2');
  await settle(() => ui.panel.querySelector('[data-panel-title]').textContent === 'Rollback');
  fail();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(ui.panel.querySelector('[data-panel-title]').textContent, 'Rollback');
  assert.doesNotMatch(ui.panel.textContent, /Failed to load/);
});

const AUTHOR = { can_manage_stories: true, can_view_events: true };

async function selecting(t, items, options = {}) {
  const ui = await events(t, { me: AUTHOR, items, ...options });
  const bar = ui.el('selectionBar');
  const menu = ui.el('rowMenu');
  const box = (id) => ui.el('rows').querySelector(`[data-select-event][value="${id}"]`);
  const check = (id) => {
    box(id).checked = true;
    box(id).dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  };
  return { ...ui, bar, menu, box, check };
}

test('an event without a summary is offered for selection as an event, and titles the story generically', async (t) => {
  const ui = await selecting(t, [
    { ...ROW, id: 'e1', summary: '' },
    { ...ROW, id: 'e2', summary: '   ' },
  ]);
  assert.equal(ui.box('e1').getAttribute('aria-label'), 'Select event');
  ui.check('e2');
  assert.equal(ui.bar.querySelector('[data-story-title]').placeholder, 'Story title');
});

test('the story list is read once however often the chooser is focused', async (t) => {
  const ui = await selecting(t, [ROW]);
  ui.check('e1');
  const target = ui.bar.querySelector('[data-story-target]');
  target.dispatchEvent(new ui.window.Event('focus'));
  await settle(() => target.options.length === 2);
  target.dispatchEvent(new ui.window.Event('focus'));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(ui.requests.filter((r) => r.startsWith('/api/v1/stories?')).length, 1);
});

test('adding to a story without choosing one sends nothing', async (t) => {
  const posts = [];
  const ui = await selecting(t, [ROW], {
    post: async (u) => {
      posts.push(u.pathname);
      return {};
    },
  });
  ui.check('e1');
  ui.bar.querySelector('[data-add-to-story]').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(posts, []);
  assert.equal(ui.requests.filter((r) => r.startsWith('/api/v1/stories/')).length, 0);
  assert.equal(ui.bar.hidden, false);
});

test('a right-click on the empty results message opens no selection menu', async (t) => {
  const ui = await selecting(t, []);
  const context = new ui.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true });
  ui.el('emptyState').querySelector('td').dispatchEvent(context);
  assert.equal(context.defaultPrevented, false);
  assert.equal(ui.menu.hidden, true);
});

test('the selection menu stays open on a click between its actions, and Clear empties the selection', async (t) => {
  const ui = await selecting(t, [ROW, { ...ROW, id: 'e2', summary: 'Rollback' }]);
  ui.check('e1');
  const row = ui.el('rows').querySelector('tr[data-event-id="e2"] td:nth-child(3)');
  row.dispatchEvent(
    new ui.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 5 }),
  );
  assert.equal(ui.menu.hidden, false);
  assert.equal(ui.menu.querySelector('[data-selection-count]').textContent, '2');
  ui.menu.dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
  assert.equal(ui.menu.hidden, false);
  ui.menu.querySelector('[data-menu-action="clear"]').click();
  assert.equal(ui.menu.hidden, true);
  assert.equal(ui.bar.hidden, true);
  assert.equal(ui.box('e1').checked, false);
  assert.equal(ui.box('e2').checked, false);
});
