import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const FORM = `<form>
  <select id="rule"><option value="exact">Exact first run date</option></select>
  <div id="exact"></div>
  <div id="fuzzy" style="display:none">
    <label id="monthLabel">First weekday of</label>
    <select id="month"></select>
    <input id="year" type="number" />
    <div id="preview"></div>
  </div>
  <select id="recurrence"></select>
  <input id="start" type="date" />
  <input id="startText" />
</form>`;

async function fixture(t, options = { lead: 'First run' }) {
  const ui = await loadUi('pages/schedule-fields.js', {
    html: FORM,
    vendor: { isoDateToDisplay: (iso) => `display ${iso}` },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  const dates = ui.api.scheduleDateFields(
    {
      rule: el('rule'),
      recurrence: el('recurrence'),
      exactFields: el('exact'),
      fuzzyFields: el('fuzzy'),
      month: el('month'),
      monthLabel: el('monthLabel'),
      year: el('year'),
      start: el('start'),
      startText: el('startText'),
      preview: el('preview'),
    },
    options,
  );
  const choose = (id, value, type = 'change') => {
    el(id).value = value;
    el(id).dispatchEvent(new ui.window.Event(type));
  };
  return { ui, el, dates, choose };
}

const values = (select) => [...select.options].map((o) => o.value);

test('the menus list the shared rules, months and recurrences after the page option', async (t) => {
  const { el } = await fixture(t);
  assert.deepEqual(values(el('rule')), [
    'exact',
    'first_weekday_of_month',
    'first_monday_of_month',
    'first_tuesday_of_month',
    'first_wednesday_of_month',
    'first_thursday_of_month',
    'first_friday_of_month',
  ]);
  assert.equal(el('rule').options[0].textContent, 'Exact first run date');
  assert.equal(el('rule').options[2].textContent, 'First Monday of month');
  assert.deepEqual(values(el('month')), [
    '1',
    '2',
    '3',
    '4',
    '5',
    '6',
    '7',
    '8',
    '9',
    '10',
    '11',
    '12',
  ]);
  assert.equal(el('month').options[8].textContent, 'September');
  assert.deepEqual(values(el('recurrence')), ['once', 'weekly', 'monthly', 'quarterly', 'yearly']);
});

test('a fuzzy rule picks its first day from the start date when no year is given', async (t) => {
  const { el, choose } = await fixture(t);
  el('start').value = '2026-03-17';
  choose('recurrence', 'weekly');
  choose('rule', 'first_thursday_of_month');
  assert.equal(el('exact').style.display, 'none');
  assert.equal(el('fuzzy').style.display, '');
  // Weekly recurrence is unavailable for fuzzy rules.
  assert.equal(el('recurrence').value, 'yearly');
  assert.equal(el('recurrence').querySelector('option[value="weekly"]').disabled, true);
  // The month menu starts on January; the year comes from the start date.
  assert.equal(el('start').value, '2026-01-01');
  assert.equal(el('monthLabel').textContent, 'First Thursday of');
  assert.equal(
    el('preview').textContent,
    'First run will be display 2026-01-01; future runs keep the first Thursday rule.',
  );
  choose('year', '2027', 'input');
  choose('month', '2');
  // 1 February 2027 is a Monday, so the first Thursday is 4 February.
  assert.equal(el('start').value, '2027-02-04');
  assert.equal(el('startText').value, 'display 2027-02-04');
});

test('one-off fuzzy schedules stay one-off unless the form asks for yearly', async (t) => {
  const kept = await fixture(t, { lead: 'Next run' });
  kept.choose('rule', 'first_weekday_of_month');
  assert.equal(kept.el('recurrence').value, 'once');
  assert.match(kept.el('preview').textContent, /^Next run will be /);

  const moved = await fixture(t, { lead: 'First run', yearlyWhenFuzzy: true });
  moved.choose('rule', 'first_weekday_of_month');
  assert.equal(moved.el('recurrence').value, 'yearly');
});

test('unknown rules read as the first weekday and a blank start uses this year', async (t) => {
  const { ui, el, dates } = await fixture(t);
  const option = ui.document.createElement('option');
  option.value = 'last_day_of_month';
  el('rule').append(option);
  el('rule').value = 'last_day_of_month';
  el('month').value = '8';
  dates.update();
  const year = new Date().getFullYear();
  assert.match(el('start').value, new RegExp(`^${year}-08-0[1-3]$`));
  assert.ok(![0, 6].includes(new Date(`${el('start').value}T00:00:00Z`).getUTCDay()));
  assert.equal(el('monthLabel').textContent, 'First weekday of');
  assert.equal(ui.api.fuzzyRuleLabel('last_day_of_month'), 'first weekday');
});

test('submitting sends the fuzzy day or the parsed exact date with its anchor month', async (t) => {
  const { el, dates, choose } = await fixture(t);
  el('start').value = '2026-05-20';
  assert.equal(dates.startForSubmit({ syncHiddenFromText: () => true }), '2026-05-20');
  assert.equal(dates.startForSubmit({ syncHiddenFromText: () => false }), '');
  assert.equal(dates.startForSubmit(null), '2026-05-20');
  assert.equal(dates.anchorMonth(), null);
  el('start').value = '';
  assert.equal(dates.startForSubmit(null), '');

  choose('year', '2026', 'input');
  choose('month', '11');
  choose('rule', 'first_monday_of_month');
  // An unparsed exact date no longer matters once a fuzzy rule sets the day.
  assert.equal(dates.startForSubmit({ syncHiddenFromText: () => false }), '2026-11-02');
  assert.equal(dates.anchorMonth(), 11);

  choose('rule', 'exact');
  assert.equal(el('preview').textContent, '');
  assert.equal(el('exact').style.display, '');
  assert.equal(el('recurrence').querySelector('option[value="weekly"]').disabled, false);
});

test('month, recurrence and rule names for schedule lists', async (t) => {
  const { ui } = await fixture(t);
  assert.equal(ui.api.monthName(3), 'March');
  assert.equal(ui.api.monthName(0), '');
  assert.equal(ui.api.monthName('x'), '');
  assert.equal(ui.api.recurrenceName('quarterly'), 'Quarterly');
  assert.equal(ui.api.recurrenceName('fortnightly'), 'fortnightly');
  assert.equal(ui.api.isFuzzyDateRule(''), false);
  assert.equal(ui.api.isFuzzyDateRule('first_friday_of_month'), true);
  assert.deepEqual({ ...ui.api.isoYearMonth('2025-12-31') }, { year: 2025, month: 12 });
});
