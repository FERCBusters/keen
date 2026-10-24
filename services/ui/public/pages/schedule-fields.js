// Schedule dates, shared by scheduled audits and quizzes: the date rules,
// months and recurrences on offer, the first day each fuzzy rule picks and a
// schedule's summary. The API keeps the same rules in app/services/schedules.py.
import { esc, isoDateToDisplay } from '/app.js';

// Each fuzzy rule: its value, its words in labels and previews, and the
// weekday it looks for (0 is Sunday; null takes the first Monday to Friday).
const FUZZY_RULES = [
  ['first_weekday_of_month', 'first weekday', null],
  ['first_monday_of_month', 'first Monday', 1],
  ['first_tuesday_of_month', 'first Tuesday', 2],
  ['first_wednesday_of_month', 'first Wednesday', 3],
  ['first_thursday_of_month', 'first Thursday', 4],
  ['first_friday_of_month', 'first Friday', 5],
];
const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];
const RECURRENCES = [
  ['once', 'Once'],
  ['weekly', 'Weekly'],
  ['monthly', 'Monthly'],
  ['quarterly', 'Quarterly'],
  ['yearly', 'Yearly'],
];

const capitalise = (text) => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;

function optionsHtml(choices) {
  return choices
    .map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`)
    .join('');
}

// Unknown rules read as the first weekday.
function fuzzyRule(rule) {
  return FUZZY_RULES.find(([value]) => value === String(rule || '')) || FUZZY_RULES[0];
}

export function isFuzzyDateRule(rule) {
  return String(rule || 'exact') !== 'exact';
}

export function fuzzyRuleLabel(rule) {
  return fuzzyRule(rule)[1];
}

export function monthName(number) {
  return MONTHS[Number(number) - 1] || '';
}

export function recurrenceName(value) {
  return RECURRENCES.find(([choice]) => choice === value)?.[1] || value;
}

// How often a schedule runs, from its schedule_* fields, such as
// "Monthly × 2 · first Monday of Jan".
export function scheduleSummary(schedule) {
  const recur = String(schedule?.schedule_recurrence || 'once');
  const interval = Number(schedule?.schedule_interval || 1);
  const base = recurrenceName(recur);
  const recurText = interval > 1 && recur !== 'once' ? `${base} × ${interval}` : base;
  const dateRule = String(schedule?.schedule_date_rule || 'exact');
  if (isFuzzyDateRule(dateRule)) {
    const month = monthName(schedule?.schedule_anchor_month).slice(0, 3) || 'month';
    return `${recurText} · ${fuzzyRuleLabel(dateRule)} of ${month}`;
  }
  return recurText;
}

// The year and month of an ISO date, or this year and month.
export function isoYearMonth(iso) {
  const match = String(iso || '').match(/^(\d{4})-(\d{2})-/);
  if (match) return { year: Number(match[1]), month: Number(match[2]) };
  const today = new Date();
  return { year: today.getFullYear(), month: today.getMonth() + 1 };
}

// The ISO date of the rule's first matching day in a month.
function firstRuleDateIso(year, month, rule) {
  const day = new Date(Date.UTC(year, Math.max(1, Math.min(12, month)) - 1, 1));
  const weekday = fuzzyRule(rule)[2];
  const matches = (d) => (weekday === null ? d !== 0 && d !== 6 : d === weekday);
  while (!matches(day.getUTCDay())) day.setUTCDate(day.getUTCDate() + 1);
  return day.toISOString().slice(0, 10);
}

// Fills a schedule form's menus and keeps its date fields in step with the
// date rule. The rule menu keeps the page's own exact option first. An exact
// rule shows the date picker. A fuzzy rule shows a month and start year, sets
// the start date to the rule's first day in that month and names it in the
// preview, which starts with `lead`. Fuzzy rules cannot recur weekly, and with
// `yearlyWhenFuzzy` a one-off schedule becomes yearly too.
export function scheduleDateFields(fields, { lead, yearlyWhenFuzzy = false }) {
  const { rule, recurrence, exactFields, fuzzyFields, month, monthLabel, year } = fields;
  const { start, startText, preview } = fields;
  rule.insertAdjacentHTML(
    'beforeend',
    optionsHtml(FUZZY_RULES.map(([value, label]) => [value, `${capitalise(label)} of month`])),
  );
  month.innerHTML = optionsHtml(MONTHS.map((name, idx) => [String(idx + 1), name]));
  recurrence.innerHTML = optionsHtml(RECURRENCES);
  const fuzzy = () => isFuzzyDateRule(rule.value);

  function update() {
    const isFuzzy = fuzzy();
    exactFields.style.display = isFuzzy ? 'none' : '';
    fuzzyFields.style.display = isFuzzy ? '' : 'none';
    recurrence.querySelector('option[value="weekly"]').disabled = isFuzzy;
    const oneOff = yearlyWhenFuzzy && recurrence.value === 'once';
    if (isFuzzy && (recurrence.value === 'weekly' || oneOff)) recurrence.value = 'yearly';
    if (!isFuzzy) {
      preview.textContent = '';
      return;
    }
    const fallback = isoYearMonth(start.value);
    const iso = firstRuleDateIso(
      Number(year.value) || fallback.year,
      Number(month.value) || fallback.month,
      rule.value,
    );
    const label = fuzzyRuleLabel(rule.value);
    monthLabel.textContent = `${capitalise(label)} of`;
    start.value = iso;
    startText.value = isoDateToDisplay(iso);
    preview.textContent = `${lead} will be ${isoDateToDisplay(iso)}; future runs keep the ${label} rule.`;
  }

  rule.addEventListener('change', update);
  month.addEventListener('change', update);
  year.addEventListener('input', update);
  recurrence.addEventListener('change', update);
  return {
    update,
    // The start date to send: the fuzzy rule's day, or the typed exact date
    // ('' when it does not parse).
    startForSubmit(wire) {
      update();
      if (!fuzzy() && wire?.syncHiddenFromText?.() === false) return '';
      return start.value || '';
    },
    // The month a fuzzy rule recurs in; exact dates have none.
    anchorMonth: () => (fuzzy() ? Number(month.value || 1) : null),
  };
}
