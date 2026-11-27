// The options a control or clause picker offers: catalogue rows as text, the
// chosen ones selected, and chosen records the catalogue lacks kept.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

async function options(t) {
  const ui = await loadUi('pages/catalogue-options.js', {
    html: '<main><select id="controls" multiple><option value="old">Old</option></select></main>',
    url: 'https://keen.example/story.html',
  });
  t.after(ui.close);
  const select = ui.document.getElementById('controls');
  const listed = () =>
    [...select.options].map((o) => [
      o.value,
      o.textContent,
      o.title,
      o.dataset.badge || '',
      o.selected,
    ]);
  return { ...ui.api, select, listed };
}

test('catalogue rows become text options with the chosen ones selected', async (t) => {
  const { fillCatalogueSelect, chosenIds, select, listed } = await options(t);
  fillCatalogueSelect(
    select,
    [
      { id: 1, ref: 'A.5.1', title: 'Policies <b>for</b> security', description: 'Direction' },
      { id: 'c2', ref: 'A.5.15', title: 'Access control', in_scope: false },
      { id: 'k1', ref: '4.1', title: null, type: 'clause' },
    ],
    ['1', 'k1', 'gone'],
  );
  assert.deepEqual(listed(), [
    ['1', 'A.5.1 Policies <b>for</b> security', 'Direction', '', true],
    ['c2', 'A.5.15 Access control', '', 'out of scope', false],
    ['k1', 'Clause 4.1', '', '', true],
  ]);
  assert.equal(select.querySelector('b'), null, 'titles are text, never markup');
  // Numeric ids are matched as text; an id the catalogue lacks chooses nothing.
  assert.deepEqual([...chosenIds(select)], ['1', 'k1']);
  // Filling again replaces the list; nothing chosen by default.
  fillCatalogueSelect(select, [{ id: 'z', ref: 'Z.1' }]);
  assert.deepEqual(listed(), [['z', 'Z.1', '', '', false]]);
  assert.deepEqual([...chosenIds(select)], []);
});

test('chosen records the catalogue does not hold stay listed and chosen', async (t) => {
  const { fillCatalogueSelect, chosenIds, select, listed } = await options(t);
  const kept = [
    { id: 'c1', ref: 'A.1', title: 'Listed' },
    { id: 'x9', ref: 'B.9', title: 'Other framework' },
  ];
  fillCatalogueSelect(
    select,
    [
      { id: 'c1', ref: 'A.1', title: 'Listed' },
      { id: 'c2', ref: 'A.2' },
    ],
    ['c1'],
    kept,
  );
  assert.deepEqual(listed(), [
    ['c1', 'A.1 Listed', '', '', true],
    ['c2', 'A.2', '', '', false],
    ['x9', 'B.9 Other framework', '', '', true],
  ]);
  assert.deepEqual([...chosenIds(select)], ['c1', 'x9']);
  // With no catalogue at all, what was chosen is still there to save.
  fillCatalogueSelect(select, [], [], kept);
  assert.deepEqual([...chosenIds(select)], ['c1', 'x9']);
});

test('catalogueLabel names a clause as one and leaves out a missing title', async (t) => {
  const { catalogueLabel } = await options(t);
  assert.equal(catalogueLabel({ ref: 'A.1', title: 'Access' }), 'A.1 Access');
  assert.equal(
    catalogueLabel({ ref: 'A.1', type: 'clause', title: 'Access' }),
    'Clause A.1 Access',
  );
  assert.equal(catalogueLabel({ ref: 'A.1', title: '' }), 'A.1');
});
