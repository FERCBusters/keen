// Selecting sampled evidence on an audit page (pages/evidence-selection.js on
// the audit page's own selection bar): the 200-item limit, ranges chosen
// upwards, Shift+Arrow from the keyboard, and entries removed elsewhere.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';

const html = await readFile(new URL('../public/audit.html', import.meta.url), 'utf8');

async function selection(t, count) {
  const notices = [];
  const ui = await loadUi('pages/evidence-selection.js', {
    html,
    vendor: { toast: (element, message, kind) => notices.push([message, kind]) },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  const chooser = ui.api.createEvidenceSelection({
    container: el('evidence'),
    bar: el('evidenceSelectionBar'),
    selectAllWrap: el('evidenceSelectAllWrap'),
    statusEl: el('status'),
  });
  const draw = (ids) => {
    el('evidence').innerHTML = ids
      .map(
        (id) =>
          `<div data-evidence-card="${id}">${chooser.cellHtml(id)}<button type="button">Remove</button></div>`,
      )
      .join('');
    chooser.rendered();
  };
  draw(Array.from({ length: count }, (_, i) => `ev-${i}`));
  const boxes = () => [...el('evidence').querySelectorAll('[data-select-evidence]')];
  const click = (box, shiftKey = false) =>
    box.dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true, shiftKey }));
  const key = (target, key, shiftKey = true) => {
    const event = new ui.window.KeyboardEvent('keydown', {
      key,
      shiftKey,
      bubbles: true,
      cancelable: true,
    });
    target.dispatchEvent(event);
    return event;
  };
  const shown = () =>
    el('evidenceSelectionBar').querySelector('[data-evidence-selection-count]').textContent;
  return { ui, el, chooser, draw, boxes, click, key, shown, notices };
}

test('selecting everything stops at 200 items and says why', async (t) => {
  const { ui, el, chooser, boxes, shown, notices } = await selection(t, 201);
  const all = el('evidenceSelectAllWrap').querySelector('[data-select-evidence-all]');
  all.checked = true;
  all.dispatchEvent(new ui.window.Event('change'));
  assert.equal([...chooser.ids()].length, 200);
  assert.equal(shown(), '200 selected');
  assert.equal(boxes()[200].checked, false);
  assert.equal(all.indeterminate, true);
  assert.deepEqual(notices, [
    ['You can remove up to 200 evidence items at once; add the rest after this round.', 'warning'],
  ]);
});

test('a shift-click above the last choice selects the range between them', async (t) => {
  const { boxes, click, chooser, shown } = await selection(t, 6);
  // A click ticks a box, as it does in the browser.
  click(boxes()[4]);
  click(boxes()[1], true);
  assert.deepEqual([...chooser.ids()], ['ev-4', 'ev-1', 'ev-2', 'ev-3']);
  assert.deepEqual(
    boxes().map((box) => box.checked),
    [false, true, true, true, true, false],
  );
  assert.equal(shown(), '4 selected');
});

test('Shift and the arrow keys extend the selection from the focused entry', async (t) => {
  const { ui, el, boxes, click, key, chooser } = await selection(t, 3);
  click(boxes()[0]);
  // Arrows without Shift, other keys and keys outside a checkbox change nothing.
  assert.equal(key(boxes()[0], 'ArrowDown', false).defaultPrevented, false);
  assert.equal(key(boxes()[0], 'Tab').defaultPrevented, false);
  assert.equal(key(el('evidence').querySelector('button'), 'ArrowDown').defaultPrevented, false);
  assert.deepEqual([...chooser.ids()], ['ev-0']);

  assert.equal(key(boxes()[0], 'ArrowDown').defaultPrevented, true);
  assert.deepEqual([...chooser.ids()], ['ev-0', 'ev-1']);
  assert.equal(ui.document.activeElement, boxes()[1]);
  key(boxes()[1], 'ArrowDown');
  assert.deepEqual([...chooser.ids()], ['ev-0', 'ev-1', 'ev-2']);
  // The last entry has nothing below it.
  assert.equal(key(boxes()[2], 'ArrowDown').defaultPrevented, false);

  click(boxes()[2]);
  assert.equal(boxes()[2].checked, false);
  assert.equal(key(boxes()[2], 'ArrowUp').defaultPrevented, true);
  assert.deepEqual([...chooser.ids()], ['ev-0']);
  assert.equal(ui.document.activeElement, boxes()[1]);
  assert.equal(boxes()[1].checked, false);
});

test('entries removed elsewhere leave the selection when the list is drawn again', async (t) => {
  const { draw, boxes, click, chooser, shown, el } = await selection(t, 3);
  for (const box of boxes().slice(0, 2)) click(box);
  assert.equal(shown(), '2 selected');
  draw(['ev-1', 'ev-2']);
  assert.deepEqual([...chooser.ids()], ['ev-1']);
  assert.equal(shown(), '1 selected');
  assert.deepEqual(
    boxes().map((box) => box.checked),
    [true, false],
  );
  el('evidenceSelectionBar').querySelector('[data-clear-evidence-selection]').click();
  assert.deepEqual([...chooser.ids()], []);
  assert.equal(el('evidenceSelectionBar').hidden, true);
});
