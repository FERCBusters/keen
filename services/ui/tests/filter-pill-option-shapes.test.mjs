// The shared filter-pill picker given option lists and selections in the
// shapes pages pass them: records without labels or titles, blank values,
// missing lists, text fields and elements the page did not find.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

async function picker(t, html = '<select id="filter"></select>') {
  const ui = await loadUi('pages/filter-pill-multiselect.js', { html });
  t.after(() => ui.close());
  return ui;
}

const chips = (ui, id = 'filter') =>
  [
    ...ui.document.querySelectorAll(`#${id}FilterPillModal .keen-filter-pill-grid [data-value]`),
  ].map((chip) => [
    chip.dataset.value,
    chip.querySelector('.keen-filter-pill-chip-text').textContent,
    chip.title,
  ]);

test('option records are labelled by label, name or text, and fall back to their value', async (t) => {
  const ui = await picker(t);
  ui.api.setFilterPillOptions('filter', [
    { value: 'n', name: 'Named' },
    { value: 't', text: 'Texted' },
    { value: 'v' },
    { value: 'w', label: '   ' },
    { value: 'x', label: 'Ex', title: '   ' },
    { value: 'y', label: 'Why', title: 'Why <b>this</b>' },
    { label: 'No value' },
    { value: '', label: 'Blank' },
  ]);
  assert.deepEqual(chips(ui), [
    ['x', 'Ex', 'Ex'],
    ['n', 'Named', 'Named'],
    ['t', 'Texted', 'Texted'],
    ['v', 'v', 'v'],
    ['w', 'w', 'w'],
    ['y', 'Why', 'Why <b>this</b>'],
  ]);
  assert.equal(ui.document.querySelector('.keen-filter-pill-grid b'), null);
});

test('select options without text are labelled by their value', async (t) => {
  const ui = await picker(
    t,
    '<select id="filter"><option value="ossec"></option><option value="journal">Journal</option></select>',
  );
  // The select's current value, its first option, starts selected and is listed first.
  ui.api.enhanceFilterPillMultiSelect(ui.document.getElementById('filter'));
  assert.deepEqual(chips(ui), [
    ['ossec', 'ossec', 'ossec'],
    ['journal', 'Journal', 'Journal'],
  ]);
});

test('blank and missing values never become selections', async (t) => {
  const ui = await picker(t);
  const select = ui.document.getElementById('filter');
  const instance = ui.api.setFilterPillOptions(select, ['a', 'b'], {
    selectedValues: [null, ' a ', undefined, 0, '', 'b', 'a'],
  });
  assert.deepEqual([...instance.getValues()], ['a', 'b']);
  assert.equal(select.dataset.filterPillValue, 'a,b');
  ui.api.setFilterPillOptions(select, ['a', 'b'], { selectedValues: null });
  assert.deepEqual([...instance.getValues()], []);
  instance.setSelected(['b']);
  instance.setSelected(undefined);
  assert.deepEqual([...instance.getValues()], []);
  // Kept selections outside the options are cleaned the same way.
  instance.setSelected([null, ' retired ', undefined, 0], { pruneSelected: false });
  assert.deepEqual([...instance.getValues()], ['retired']);
});

test('a missing option list leaves a picker with nothing to offer', async (t) => {
  const ui = await picker(t);
  const instance = ui.api.setFilterPillOptions('filter', ['a'], { selectedValues: ['a'] });
  ui.api.setFilterPillOptions('filter', null);
  assert.deepEqual(chips(ui), []);
  assert.deepEqual([...instance.getValues()], []);
  assert.match(
    ui.document.querySelector('.keen-filter-pill-grid').textContent,
    /No values match your filter\./,
  );
});

test('a text field holds the picker values, and starts with none to offer', async (t) => {
  const ui = await picker(t, '<input id="tags" value=" alpha, ,beta ">');
  const field = ui.document.getElementById('tags');
  const instance = ui.api.enhanceFilterPillMultiSelect(field);
  assert.deepEqual([...instance.getValues()], ['alpha', 'beta']);
  assert.equal(field.value, 'alpha,beta');
  assert.deepEqual(chips(ui, 'tags'), []);
  instance.setOptions(['alpha', 'beta', 'gamma']);
  ui.document.querySelector('#tagsFilterPillModal [data-value="gamma"]').click();
  assert.equal(field.value, 'alpha,beta,gamma');
});

test('blank picker wording falls back to the generic wording', async (t) => {
  const ui = await picker(t);
  ui.api.enhanceFilterPillMultiSelect('filter', {
    title: '',
    description: '',
    searchPlaceholder: '',
    emptySelectedText: '',
    emptyText: 'Every source',
    singular: 'tag',
    plural: '',
    options: ['a', 'b'],
  });
  const modal = ui.document.getElementById('filterFilterPillModal');
  assert.equal(modal.querySelector('.modal-title').textContent, 'Choose filter values');
  assert.equal(
    modal.querySelector('.modal-header .small').textContent,
    'Select one or more values. Clear the selection to include all values.',
  );
  assert.equal(modal.querySelector('.keen-filter-pill-search').placeholder, 'Filter values');
  const inline = ui.document.querySelector('.keen-filter-pill-inline');
  assert.equal(inline.textContent, 'Every source');
  modal.querySelector('[data-value="a"]').click();
  modal.querySelector('[data-value="b"]').click();
  assert.equal(
    ui.document.querySelector('.keen-filter-pill-summary').textContent,
    '2 tags selected',
  );
});

test('elements the page did not find, or without an id, get no picker', async (t) => {
  const ui = await picker(t, '<select id="filter"></select><select class="unnamed"></select>');
  assert.equal(ui.api.enhanceFilterPillMultiSelect(null), null);
  assert.equal(ui.api.enhanceFilterPillMultiSelect('#missing'), null);
  assert.equal(ui.api.setFilterPillOptions(null, ['a']), null);
  assert.equal(ui.api.setFilterPillOptions('missing', ['a']), null);
  assert.deepEqual([...ui.api.selectedFilterPillValues(null)], []);
  assert.equal(
    ui.api.enhanceFilterPillMultiSelect(ui.document.querySelector('.unnamed'), { options: ['a'] }),
    null,
  );
  assert.equal(ui.document.querySelectorAll('.keen-filter-pill-picker, .modal').length, 0);
});

test('setting options on a plain select builds its picker with the page wording', async (t) => {
  const ui = await picker(t, '<select id="filter"></select><select id="other"></select>');
  const instance = ui.api.setFilterPillOptions('#filter', ['a', 'b'], {
    selectedValues: ['b'],
    config: { title: 'Choose sources', emptyText: 'All sources' },
  });
  assert.deepEqual([...instance.getValues()], ['b']);
  assert.equal(
    ui.document.querySelector('#filterFilterPillModal .modal-title').textContent,
    'Choose sources',
  );
  assert.equal(ui.api.setFilterPillOptions('#filter', ['a']), instance);
  const other = ui.api.setFilterPillOptions('other', ['c']);
  assert.deepEqual(
    chips(ui, 'other').map(([value]) => value),
    ['c'],
  );
  assert.equal(
    ui.document.querySelector('#otherFilterPillModal .modal-title').textContent,
    'Choose filter values',
  );
  assert.notEqual(other, instance);
});
