// Selecting events on the Events page to create a story or add to one.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const plain = (value) => JSON.parse(JSON.stringify(value));
const EVENTS = [
  { id: 'e1', source: 'ci', summary: 'Deploy <v2>', timestamp: '2026-01-01', controls: [] },
  { id: 'e2', source: 'ci', summary: 'Rollback', timestamp: '2026-01-02', controls: [] },
];

async function events(t, { me, post, globals, stories = [] } = {}) {
  const calls = { get: [], post: [] };
  const ui = await page(t, 'events', {
    me: { can_view_events: true, ...me },
    globals,
    get: async (u) => {
      calls.get.push(u.pathname + u.search);
      if (u.pathname === '/api/v1/sources/meta') return { items: [] };
      if (u.pathname === '/api/v1/controls') return { items: [] };
      if (u.pathname === '/api/v1/events/facets') return { sources: [], controls: [] };
      if (u.pathname === '/api/v1/events') return { total: 2, items: EVENTS };
      if (u.pathname === '/api/v1/stories') {
        if (stories === null) throw new Error('Stories offline');
        return { items: stories };
      }
      if (u.pathname === '/api/v1/stories/s1') return { id: 's1', version: 7 };
      throw new Error('Unexpected GET ' + u);
    },
    post:
      post ||
      (async (u, body) => {
        calls.post.push([u.pathname + u.search, body]);
        return { id: 'new', title: 'Created' };
      }),
  });
  await settle(() => ui.document.querySelectorAll('#rows tr[data-event-id]').length === 2);
  const boxes = () => [...ui.document.querySelectorAll('#rows [data-select-event]')];
  const check = (id, on = true) => {
    const box = boxes().find((b) => b.value === id);
    box.checked = on;
    box.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  };
  return { ui, calls, boxes, check };
}

test('readers without story rights see no selection column', async (t) => {
  const { ui, boxes } = await events(t);
  assert.equal(boxes().length, 0);
  assert.equal(ui.el('selectHeader').hidden, true);
  assert.equal(ui.el('selectionBar').hidden, true);
  // Colspans fit the table without the extra column.
  assert.equal(ui.document.querySelector('#rows tr td').getAttribute('colspan'), null);
});

test('a story author selects events and creates a story from them', async (t) => {
  const assigned = [];
  const { ui, calls, boxes, check } = await events(t, {
    me: { can_manage_stories: true },
    globals: {
      location: {
        origin: 'https://keen.example',
        href: 'https://keen.example/events.html?framework=A',
        search: '?framework=A',
        assign: (u) => assigned.push(u),
      },
    },
  });
  assert.equal(ui.el('selectHeader').hidden, false);
  assert.deepEqual(
    boxes().map((b) => b.getAttribute('aria-label')),
    ['Select Deploy <v2>', 'Select Rollback'],
  );
  check('e1');
  assert.equal(ui.el('selectionBar').hidden, false);
  assert.equal(
    ui.el('selectionBar').querySelector('[data-selection-count]').textContent,
    '1 selected',
  );
  const title = ui.el('selectionBar').querySelector('[data-story-title]');
  assert.equal(title.placeholder, 'Story about “Deploy <v2>”');
  // Selecting every row on the page, then dropping one.
  ui.el('selectAll').checked = true;
  ui.el('selectAll').dispatchEvent(new ui.window.Event('change'));
  assert.equal(
    ui.el('selectionBar').querySelector('[data-selection-count]').textContent,
    '2 selected',
  );
  check('e2', false);
  assert.equal(ui.el('selectAll').indeterminate, true);
  // A selection survives the list being drawn again.
  ui.el('filters').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => calls.get.filter((u) => u.startsWith('/api/v1/events?')).length === 2);
  await settle(() => boxes().length === 2);
  assert.deepEqual(
    boxes().map((b) => b.checked),
    [true, false],
  );
  // A click on the checkbox cell does not open the side panel.
  boxes()[1].click();
  assert.equal(ui.el('eventPanel').classList.contains('show'), false);
  assert.equal(
    ui.el('selectionBar').querySelector('[data-selection-count]').textContent,
    '2 selected',
  );

  title.value = 'Release incident';
  ui.el('selectionBar').querySelector('[data-create-story]').click();
  await settle(() => assigned.length === 1);
  assert.deepEqual(plain(calls.post), [
    [
      '/api/v1/stories?framework=A',
      {
        title: 'Release incident',
        items: [
          { kind: 'event', entity_id: 'e1' },
          { kind: 'event', entity_id: 'e2' },
        ],
      },
    ],
  ]);
  assert.deepEqual(assigned, ['/story.html?id=new&framework=A']);
  assert.equal(ui.el('selectionBar').hidden, true);
});

test('the right-click menu selects the row and offers the story actions', async (t) => {
  const { ui, calls, check } = await events(t, {
    me: { can_manage_stories: true },
    stories: [
      { id: 's1', title: 'Mine <open>', can_edit: true },
      { id: 's2', title: "Someone else's", can_edit: false },
    ],
  });
  const row = ui.document.querySelector('#rows tr[data-event-id="e2"]');
  const menuEvent = new ui.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true });
  row.dispatchEvent(menuEvent);
  assert.equal(menuEvent.defaultPrevented, true);
  assert.equal(ui.el('rowMenu').hidden, false);
  assert.equal(ui.el('rowMenu').querySelector('[data-selection-count]').textContent, '1');
  assert.equal(ui.el('selectionBar').hidden, false);
  // "Add to a story" lists the stories the account can edit.
  ui.el('rowMenu').querySelector('[data-menu-action="add"]').click();
  assert.equal(ui.el('rowMenu').hidden, true);
  const target = ui.el('selectionBar').querySelector('[data-story-target]');
  await settle(() => target.options.length === 2);
  assert.deepEqual(
    [...target.options].map((o) => [o.value, o.textContent]),
    [
      ['', 'Choose a story'],
      ['s1', 'Mine <open>'],
    ],
  );
  check('e1');
  target.value = 's1';
  ui.el('selectionBar').querySelector('[data-add-to-story]').click();
  await settle(() => calls.post.length === 1);
  assert.deepEqual(plain(calls.post[0]), [
    '/api/v1/stories/s1/items?framework=A',
    {
      items: [
        { kind: 'event', entity_id: 'e2' },
        { kind: 'event', entity_id: 'e1' },
      ],
      expected_version: 7,
    },
  ]);
  await settle(() => ui.el('status').textContent.startsWith('Added to the story'));
  assert.equal(
    ui.el('status').querySelector('a').getAttribute('href'),
    '/story.html?id=new&framework=A',
  );
  assert.equal(ui.el('selectionBar').hidden, true);
  // Escape closes the menu; clearing empties the selection.
  row.dispatchEvent(new ui.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
  ui.document.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Escape' }));
  assert.equal(ui.el('rowMenu').hidden, true);
  ui.el('selectionBar').querySelector('[data-clear-selection]').click();
  assert.equal(ui.el('selectionBar').hidden, true);
  assert.equal(ui.document.querySelector('#rows [data-select-event]:checked'), null);
});

test('the selection stops at the number of events a story holds', async (t) => {
  const many = Array.from({ length: 201 }, (_, i) => ({
    id: `e${i}`,
    source: 'ci',
    summary: `Event ${i}`,
    timestamp: '2026-01-01',
    controls: [],
  }));
  const ui = await page(t, 'events', {
    me: { can_view_events: true, can_manage_stories: true },
    get: async (u) => {
      if (u.pathname === '/api/v1/sources/meta') return { items: [] };
      if (u.pathname === '/api/v1/controls') return { items: [] };
      if (u.pathname === '/api/v1/events/facets') return { sources: [], controls: [] };
      if (u.pathname === '/api/v1/events') return { total: 201, items: many };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.document.querySelectorAll('#rows [data-select-event]').length === 201);
  ui.el('selectAll').checked = true;
  ui.el('selectAll').dispatchEvent(new ui.window.Event('change'));
  assert.equal(
    ui.el('selectionBar').querySelector('[data-selection-count]').textContent,
    '200 selected',
  );
  assert.equal(
    ui.el('status').textContent,
    'A story holds up to 200 events; the selection is full.',
  );
  assert.equal(ui.document.querySelectorAll('#rows [data-select-event]:checked').length, 200);
});

test('story failures are reported and the selection is kept for another try', async (t) => {
  const { ui, check } = await events(t, {
    me: { can_manage_stories: true },
    stories: null,
    post: async () => {
      throw new Error('Stories offline');
    },
  });
  const row = ui.document.querySelector('#rows tr[data-event-id="e1"]');
  row.dispatchEvent(new ui.window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
  // "Create a story" moves the viewer to the title field.
  ui.el('rowMenu').querySelector('[data-menu-action="create"]').click();
  assert.equal(ui.el('rowMenu').hidden, true);
  const title = ui.el('selectionBar').querySelector('[data-story-title]');
  assert.equal(ui.document.activeElement, title);

  ui.el('selectionBar').querySelector('[data-create-story]').click();
  await settle(() => ui.el('status').textContent.startsWith('Failed to create the story'));
  assert.equal(ui.el('status').textContent, 'Failed to create the story: Stories offline');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(ui.el('selectionBar').hidden, false);
  assert.equal(ui.document.querySelectorAll('#rows [data-select-event]:checked').length, 1);

  const target = ui.el('selectionBar').querySelector('[data-story-target]');
  target.dispatchEvent(new ui.window.Event('focus'));
  await settle(() => ui.el('status').textContent.startsWith('Failed to list stories'));
  assert.equal(target.options.length, 1);

  check('e2');
  target.add(new ui.window.Option('Mine', 's1'));
  target.value = 's1';
  ui.el('selectionBar').querySelector('[data-add-to-story]').click();
  await settle(() => ui.el('status').textContent.startsWith('Failed to add to the story'));
  assert.equal(ui.el('status').textContent, 'Failed to add to the story: Stories offline');
  assert.equal(
    ui.el('selectionBar').querySelector('[data-selection-count]').textContent,
    '2 selected',
  );
});

test('a hidden selection bar stays hidden despite its Bootstrap display class', async () => {
  const html = await readFile(new URL('../public/events.html', import.meta.url), 'utf8');
  assert.match(html, /id="selectionBar" class="[^"]*d-flex[^"]*"[^>]*hidden/);
  const css = await readFile(new URL('../public/styles.css', import.meta.url), 'utf8');
  assert.match(css, /\[hidden\] \{\s*display: none !important;/);
});
