// The story flow chart for boxes stacked on one another, records without a
// title, and a selected link cleared from the empty canvas.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const plain = (value) => JSON.parse(JSON.stringify(value));

async function canvas(t, items, links = []) {
  const changes = [];
  const ui = await loadUi('pages/story-flow.js', { html: '<div id="flow"></div>' });
  t.after(ui.close);
  const host = ui.document.getElementById('flow');
  const flow = ui.api.createFlowCanvas({
    host,
    editable: true,
    onChange: (c) => changes.push(plain(c)),
    linkFor: (item) => item.url || '',
  });
  flow.load(items, links);
  const click = (target) =>
    target.dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
  return { ui, host, flow, changes, click };
}

const event = (id, overrides = {}) => ({
  id,
  kind: 'event',
  kind_label: 'Event',
  position: 0,
  x: 40,
  y: 40,
  entity_id: 'e-' + id,
  label: '',
  note: '',
  occurred_at: null,
  title: 'Sign-in',
  url: `/event.html?id=e-${id}`,
  withheld: false,
  ...overrides,
});

test('a link between two boxes stacked in one place is drawn at their shared centre', async (t) => {
  const ui = await loadUi('pages/story-flow.js', { html: '<div></div>' });
  t.after(ui.close);
  assert.deepEqual(plain(ui.api.linkPath({ x: 40, y: 40 }, { x: 40, y: 40 })), {
    x1: 140,
    y1: 85,
    x2: 140,
    y2: 85,
    mx: 140,
    my: 85,
  });

  // Dragged on top of each other, the boxes still draw a finite line.
  const { host } = await canvas(
    t,
    [event('i1'), event('i2')],
    [{ from_item_id: 'i1', to_item_id: 'i2', label: 'then' }],
  );
  const line = host.querySelector('[data-link-from="i1"] line');
  assert.deepEqual(
    ['x1', 'y1', 'x2', 'y2'].map((name) => line.getAttribute(name)),
    ['140', '85', '140', '85'],
  );
  assert.equal(host.querySelector('[data-link-from="i1"] text').getAttribute('y'), '79');
});

test('an event recorded without a summary shows its kind as the box title', async (t) => {
  const { host } = await canvas(t, [event('i1', { title: '' })]);
  const node = host.querySelector('[data-item-id="i1"]');
  const link = node.querySelector('.story-node-title a');
  assert.equal(link.textContent, 'Event');
  assert.equal(link.getAttribute('aria-label'), 'Open Event: Event (opens in a new tab)');
  assert.equal(link.getAttribute('href'), '/event.html?id=e-i1');
  assert.equal(node.getAttribute('aria-label'), 'Event: ');
  assert.equal(node.querySelector('.story-node-subtitle'), null);
});

test('a selected link stays selected through clicks on boxes and clears from the empty canvas', async (t) => {
  const { host, flow, changes, click } = await canvas(
    t,
    [event('i1'), event('i2', { x: 400 })],
    [{ from_item_id: 'i1', to_item_id: 'i2', label: 'then' }],
  );
  click(host.querySelector('[data-link-from="i1"] .story-link-hit'));
  assert.equal(flow.selectedLink.label, 'then');
  assert.deepEqual(changes, [
    { type: 'select', link: { from_item_id: 'i1', to_item_id: 'i2', label: 'then' } },
  ]);
  click(host.querySelector('[data-item-id="i2"]'));
  assert.equal(flow.selectedLink.label, 'then');
  assert.equal(changes.length, 1);
  assert.ok(host.querySelector('[data-link-from="i1"]').classList.contains('selected'));

  click(host.querySelector('.story-flow-nodes'));
  assert.equal(flow.selectedLink, null);
  assert.deepEqual(changes.at(-1), { type: 'select', link: null });
  assert.equal(host.querySelector('[data-link-from="i1"]').classList.contains('selected'), false);
  // With nothing selected, another click on the canvas reports nothing.
  click(host.querySelector('.story-flow-nodes'));
  assert.equal(changes.length, 2);
});
