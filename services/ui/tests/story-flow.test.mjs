// The story flow chart canvas: boxes that drag, links drawn between them and a
// layout the page can save.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// Values made in the page realm are compared as plain copies.
const plain = (value) => JSON.parse(JSON.stringify(value));

const ITEMS = [
  {
    id: 'i1',
    kind: 'event',
    kind_label: 'Event',
    title: 'Sign-in',
    label: '',
    x: 40,
    y: 40,
    occurred_at: '2026-05-01T09:30:00',
    withheld: false,
  },
  {
    id: 'i2',
    kind: 'risk',
    kind_label: 'Risk',
    title: 'Export',
    label: 'Realised <b>risk</b>',
    x: 280,
    y: 40,
    withheld: false,
  },
  {
    id: 'i3',
    kind: 'risk',
    kind_label: 'Risk',
    title: 'Risk you cannot view',
    label: '',
    x: 520,
    y: 40,
    withheld: true,
  },
];
const LINKS = [{ from_item_id: 'i1', to_item_id: 'i2', label: 'led to' }];

async function canvas(t, { editable = true } = {}) {
  const changes = [];
  const ui = await loadUi('pages/story-flow.js', { html: '<div id="flow"></div>' });
  t.after(ui.close);
  const host = ui.document.getElementById('flow');
  const flow = ui.api.createFlowCanvas({ host, editable, onChange: (c) => changes.push(c.type) });
  flow.load(ITEMS, LINKS);
  const pointer = (type, target, x, y) =>
    target.dispatchEvent(
      new ui.window.MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 }),
    );
  const node = (id) => host.querySelector(`[data-item-id="${id}"]`);
  return { ui, host, flow, changes, pointer, node };
}

test('link geometry stops at the box edges and positions stay inside the canvas', async (t) => {
  const ui = await loadUi('pages/story-flow.js', { html: '<div></div>' });
  t.after(ui.close);
  const { linkPath, clampCoordinate, gridPositions, NODE_WIDTH } = ui.api;
  const side = linkPath({ x: 0, y: 0 }, { x: 400, y: 0 });
  assert.deepEqual([side.x1, side.x2], [NODE_WIDTH, 400]);
  assert.equal(side.y1, side.y2);
  const down = linkPath({ x: 0, y: 0 }, { x: 0, y: 300 });
  assert.deepEqual([down.y1, down.y2], [90, 300]);
  assert.deepEqual(
    [clampCoordinate(-5), clampCoordinate(12.6), clampCoordinate(99999)],
    [0, 13, 20000],
  );
  assert.deepEqual(
    gridPositions([{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }, { id: 'e' }]).map((p) => [
      p.x,
      p.y,
    ]),
    [
      [40, 40],
      [280, 40],
      [520, 40],
      [760, 40],
      [40, 170],
    ],
  );
});

test('items render as boxes at their saved positions with links drawn between them', async (t) => {
  const { host, node } = await canvas(t);
  assert.deepEqual(
    [...host.querySelectorAll('[data-item-id]')].map((n) => [
      n.dataset.kind,
      n.style.left,
      n.style.top,
    ]),
    [
      ['event', '40px', '40px'],
      ['risk', '280px', '40px'],
      ['risk', '520px', '40px'],
    ],
  );
  // The label leads and the record's title follows it; text is escaped.
  assert.equal(node('i2').querySelector('.story-node-title').textContent, 'Realised <b>risk</b>');
  assert.equal(node('i2').querySelector('.story-node-subtitle').textContent, 'Export');
  assert.equal(node('i2').innerHTML.includes('<b>'), false);
  assert.equal(node('i3').classList.contains('story-node-withheld'), true);
  assert.equal(node('i3').querySelector('.story-node-title').textContent, 'Risk you cannot view');
  assert.equal(node('i1').querySelector('.story-node-time').textContent, '2026-05-01T09:30:00');
  const links = [...host.querySelectorAll('[data-link-from]')];
  assert.deepEqual(
    links.map((g) => [g.dataset.linkFrom, g.dataset.linkTo, g.querySelector('text').textContent]),
    [['i1', 'i2', 'led to']],
  );
  assert.equal(host.querySelector('svg').getAttribute('width'), '760');
});

test('dragging a box moves it and reports a layout change once the pointer lifts', async (t) => {
  const { host, flow, changes, pointer, node } = await canvas(t);
  pointer('pointerdown', node('i1'), 100, 100);
  pointer('pointermove', host, 130, 155);
  assert.deepEqual([node('i1').style.left, node('i1').style.top], ['70px', '95px']);
  assert.deepEqual(changes, []);
  pointer('pointermove', host, 10, 10);
  pointer('pointerup', host, 10, 10);
  assert.deepEqual([node('i1').style.left, node('i1').style.top], ['0px', '0px']);
  assert.deepEqual(changes, ['positions']);
  assert.deepEqual(plain(flow.layout().positions[0]), { item_id: 'i1', x: 0, y: 0 });
  // The link follows the moved box.
  assert.notEqual(host.querySelector('[data-link-from] line').getAttribute('x1'), '240');
  // A press without movement is not a change.
  pointer('pointerdown', node('i2'), 5, 5);
  pointer('pointerup', host, 5, 5);
  assert.deepEqual(changes, ['positions']);
});

test('link mode joins two boxes, labels the link and removes it again', async (t) => {
  const { ui, host, flow, changes, pointer, node } = await canvas(t);
  flow.setLinkMode(true);
  assert.equal(host.classList.contains('story-flow-link-mode'), true);
  pointer('pointerdown', node('i2'), 0, 0);
  assert.equal(node('i2').classList.contains('story-node-linking'), true);
  pointer('pointerdown', node('i3'), 0, 0);
  assert.deepEqual(changes, ['links', 'select']);
  assert.deepEqual(plain(flow.selectedLink), { from_item_id: 'i2', to_item_id: 'i3', label: '' });
  flow.labelSelectedLink('then');
  assert.equal(host.querySelector('[data-link-from="i2"] text').textContent, 'then');
  assert.equal(host.querySelector('[data-link-from="i2"]').classList.contains('selected'), true);
  // The same pair again is one link; a box to itself is nothing.
  pointer('pointerdown', node('i2'), 0, 0);
  pointer('pointerdown', node('i3'), 0, 0);
  pointer('pointerdown', node('i1'), 0, 0);
  pointer('pointerdown', node('i1'), 0, 0);
  assert.equal(flow.layout().links.length, 2);
  assert.deepEqual(plain(flow.layout().links[1]), {
    from_item_id: 'i2',
    to_item_id: 'i3',
    label: 'then',
  });
  // Clicking a drawn link selects it; empty canvas clears the selection.
  host
    .querySelector('[data-link-from="i1"] .story-link-hit')
    .dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
  assert.equal(flow.selectedLink.label, 'led to');
  flow.removeSelectedLink();
  assert.deepEqual(
    flow.layout().links.map((l) => l.from_item_id),
    ['i2'],
  );
  host
    .querySelector('.story-flow-nodes')
    .dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
  assert.equal(flow.selectedLink, null);
  flow.setLinkMode(false);
  assert.equal(host.classList.contains('story-flow-link-mode'), false);
});

test('arranging by order lays boxes out on the grid and a reader cannot drag', async (t) => {
  const { flow, changes, pointer, node, host } = await canvas(t, { editable: false });
  pointer('pointerdown', node('i1'), 100, 100);
  pointer('pointermove', host, 300, 300);
  pointer('pointerup', host, 300, 300);
  assert.deepEqual(changes, []);
  assert.equal(node('i1').style.left, '40px');
  flow.setEditable(true);
  pointer('pointerdown', node('i1'), 100, 100);
  pointer('pointermove', host, 110, 100);
  pointer('pointerup', host, 110, 100);
  assert.equal(node('i1').style.left, '50px');
  assert.deepEqual(changes, ['positions']);
  flow.load(
    ITEMS.map((item, index) => ({ ...item, x: 900 - index, y: 700 })),
    [],
  );
  flow.arrange();
  assert.deepEqual(
    flow.layout().positions.map((p) => [p.x, p.y]),
    [
      [40, 40],
      [280, 40],
      [520, 40],
    ],
  );
  assert.deepEqual(changes, ['positions', 'positions']);
  flow.setEditable(false);
  pointer('pointerdown', node('i1'), 0, 0);
  pointer('pointermove', host, 50, 50);
  pointer('pointerup', host, 50, 50);
  assert.deepEqual(changes, ['positions', 'positions']);
});
