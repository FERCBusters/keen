// The filterable, scrollable list of tick boxes used to choose the controls
// or clauses a record is linked to.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const CONTROLS = [
  {
    id: 1,
    ref: 'A.5.1',
    title: 'Policies <b>for</b> security',
    description: 'Management direction',
  },
  { id: 2, ref: 'A.5.15', title: 'Access control', in_scope: false },
  { id: 3, ref: 'A.8.13', title: null, description: 'Information backup' },
];

async function checklist(t, records = CONTROLS, selected = []) {
  const ui = await loadUi('pages/link-checklist.js', {
    html: '<main><form id="form"><div id="host">old</div></form></main>',
    url: 'https://keen.example/story.html',
  });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  const list = ui.api.createLinkChecklist(host, { label: 'Controls here', plural: 'controls' });
  list.load(records, selected);
  const filter = host.querySelector('input[type=search]');
  const type = (value) => {
    filter.value = value;
    filter.dispatchEvent(new ui.window.Event('input'));
  };
  const rows = () => [...host.querySelectorAll('label.d-flex')].map((row) => row.textContent);
  const tick = (id, on = true) => {
    const box = host.querySelector(`input[type=checkbox][value="${id}"]`);
    box.checked = on;
    box.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  };
  return { ui, host, list, filter, type, rows, tick };
}

test('the checklist lists every record as text in a scrollable, labelled group', async (t) => {
  const { host, rows, filter, list } = await checklist(t, CONTROLS, [2, 'gone']);
  assert.equal(host.querySelector('label.form-label').textContent, 'Controls here');
  assert.equal(host.querySelector('label.form-label').htmlFor, filter.id);
  assert.equal(filter.placeholder, 'Filter controls by reference or title');
  const group = host.querySelector('[role=group]');
  assert.equal(group.getAttribute('aria-label'), 'Controls here');
  assert.ok(group.classList.contains('link-checklist-scroll'));
  assert.deepEqual(rows(), [
    'A.5.1 Policies <b>for</b> security',
    'A.5.15 Access controlout of scope',
    'A.8.13',
  ]);
  assert.equal(host.querySelector('b'), null, 'titles are text, never markup');
  assert.equal(group.querySelectorAll('.badge').length, 1);
  // Numeric ids are matched as text; an id the catalogue lacks is not kept.
  assert.deepEqual(
    [...host.querySelectorAll('input[type=checkbox]')].map((box) => [box.value, box.checked]),
    [
      ['1', false],
      ['2', true],
      ['3', false],
    ],
  );
  assert.deepEqual(list.selected(), ['2']);
  assert.match(host.textContent, /1 selected\.$/);
});

test('typing a reference, title or description narrows the list and keeps what is ticked', async (t) => {
  const { host, rows, type, tick, list } = await checklist(t);
  tick('3');
  type('  a.5.15 ');
  assert.deepEqual(rows(), ['A.5.15 Access controlout of scope']);
  type('POLICIES');
  assert.deepEqual(rows(), ['A.5.1 Policies <b>for</b> security']);
  type('backup');
  assert.deepEqual(rows(), ['A.8.13'], 'the description is searched too');
  assert.equal(host.querySelector('input[type=checkbox]').checked, true);
  // An untitled record is not found by the word "null".
  type('null');
  assert.deepEqual(rows(), []);
  assert.match(host.querySelector('[role=group]').textContent, /^No controls match\.$/);
  type('a.5');
  tick('1');
  tick('2');
  tick('2', false);
  type('');
  assert.equal(rows().length, 3);
  assert.deepEqual(list.selected(), ['1', '3'], 'in list order');
  assert.match(host.textContent, /2 selected\.$/);
});

test('Enter in the filter does not submit the form around it, and other keys pass', async (t) => {
  const { ui, filter } = await checklist(t);
  const press = (key) => {
    const event = new ui.window.KeyboardEvent('keydown', { key, cancelable: true, bubbles: true });
    filter.dispatchEvent(event);
    return event.defaultPrevented;
  };
  assert.equal(press('Enter'), true);
  assert.equal(press('a'), false);
});

test('a disabled checklist shows its ticks without taking changes, and loading replaces it', async (t) => {
  const { host, list, type } = await checklist(t, CONTROLS, ['1']);
  list.setDisabled(true);
  assert.ok([...host.querySelectorAll('input[type=checkbox]')].every((box) => box.disabled));
  assert.equal(host.querySelector('input[value="1"]').checked, true);
  list.setDisabled(false);
  assert.ok([...host.querySelectorAll('input[type=checkbox]')].every((box) => !box.disabled));
  type('backup');
  list.load([{ id: 'k1', ref: '4.1', title: 'Context' }], ['k1', '1']);
  assert.equal(host.querySelector('input[type=search]').value, '', 'a new list clears the filter');
  assert.deepEqual(list.selected(), ['k1']);
  list.load([], []);
  assert.match(host.querySelector('[role=group]').textContent, /^No controls match\.$/);
  assert.match(host.textContent, /0 selected\.$/);
});

test('a very long catalogue draws its first rows and asks for a filter', async (t) => {
  const many = Array.from({ length: 405 }, (_, i) => ({ id: `c${i}`, ref: `R${i}`, title: 'T' }));
  const { ui, host, rows, type, list } = await checklist(t, many, ['c404']);
  assert.equal(ui.api.MAX_ROWS, 400);
  assert.equal(rows().length, 400);
  assert.match(
    host.querySelector('[role=group]').lastElementChild.textContent,
    /^Showing the first 400 of 405\. Type in the filter to narrow the list\.$/,
  );
  assert.deepEqual(list.selected(), ['c404'], 'a tick beyond the rows drawn is kept');
  type('r404');
  assert.deepEqual(rows(), ['R404 T']);
  assert.equal(host.querySelector('input[type=checkbox]').checked, true);
});

test('matchesLink compares lower-cased text and accepts an empty filter', async (t) => {
  const { ui } = await checklist(t);
  const { matchesLink } = ui.api;
  assert.equal(matchesLink({ ref: 'A.1' }, ''), true);
  assert.equal(matchesLink({ ref: 'A.1', title: 'Backup' }, 'backup'), true);
  assert.equal(matchesLink({ ref: 'A.1', description: 'Restore' }, 'restore'), true);
  assert.equal(matchesLink({ ref: 'A.1' }, 'undefined'), false);
});
