// Text filters on the Events page: one operator applied to one event field, or
// to any of them. Each row is kept as "field:operator:value", the form the API
// reads, so the URL, saved searches and exports carry the same filters.
import { esc } from '/app.js';

export const CONDITION_FIELDS = [
  ['any', 'Any field'],
  ['summary', 'Summary'],
  ['system', 'System'],
  ['actor', 'Actor'],
  ['action', 'Action'],
  ['outcome', 'Outcome'],
  ['external_id', 'Identifier'],
];

export const CONDITION_OPERATORS = [
  ['contains', 'contains'],
  ['not_contains', 'does not contain'],
  ['starts_with', 'begins with'],
  ['ends_with', 'ends with'],
  ['equals', 'equals'],
  ['not_equals', 'does not equal'],
  ['regex', 'matches regex'],
  ['not_regex', 'does not match regex'],
];

export const MAX_CONDITIONS = 10;

const fieldValues = new Set(CONDITION_FIELDS.map(([value]) => value));
const operatorValues = new Set(CONDITION_OPERATORS.map(([value]) => value));

// Reads "field:operator:value"; the value may contain colons.
export function parseCondition(raw) {
  const text = String(raw || '');
  const first = text.indexOf(':');
  const second = first < 0 ? -1 : text.indexOf(':', first + 1);
  if (second < 0) return null;
  const field = text.slice(0, first).trim().toLowerCase();
  const op = text
    .slice(first + 1, second)
    .trim()
    .toLowerCase();
  const value = text.slice(second + 1);
  if (!fieldValues.has(field) || !operatorValues.has(op) || !value.trim()) return null;
  return { field, op, value };
}

export function formatCondition({ field, op, value }) {
  return `${field}:${op}:${value}`;
}

export function describeCondition(raw) {
  const parsed = parseCondition(raw);
  if (!parsed) return '';
  const field = CONDITION_FIELDS.find(([value]) => value === parsed.field)?.[1] || parsed.field;
  const op = CONDITION_OPERATORS.find(([value]) => value === parsed.op)?.[1] || parsed.op;
  return `${field} ${op} “${parsed.value}”`;
}

function options(pairs, selected) {
  return pairs
    .map(
      ([value, label]) =>
        `<option value="${esc(value)}"${value === selected ? ' selected' : ''}>${esc(label)}</option>`,
    )
    .join('');
}

// Manages the rows under `host`; `onChange` runs after a row changes.
export function createConditionEditor({ host, addButton, onChange }) {
  function rows() {
    return [...host.querySelectorAll('[data-condition-row]')];
  }
  function updateAddButton() {
    if (addButton) addButton.disabled = rows().length >= MAX_CONDITIONS;
  }
  function rowHtml(condition) {
    return `
      <div class="d-flex flex-wrap gap-2 align-items-center" data-condition-row>
        <select class="form-select form-select-sm w-auto" data-condition-field aria-label="Field">${options(CONDITION_FIELDS, condition.field)}</select>
        <select class="form-select form-select-sm w-auto" data-condition-op aria-label="Operator">${options(CONDITION_OPERATORS, condition.op)}</select>
        <input class="form-control form-control-sm flex-grow-1" data-condition-value aria-label="Value" value="${esc(condition.value)}" placeholder="Text or pattern" maxlength="512" style="min-width:180px;">
        <button class="btn btn-sm btn-outline-secondary" type="button" data-condition-remove aria-label="Remove text filter"><i class="bi bi-x-lg" aria-hidden="true"></i></button>
      </div>`;
  }
  function add(condition = { field: 'any', op: 'contains', value: '' }, { focus = false } = {}) {
    if (rows().length >= MAX_CONDITIONS) return;
    host.insertAdjacentHTML('beforeend', rowHtml(condition));
    updateAddButton();
    if (focus) rows().at(-1).querySelector('[data-condition-value]').focus();
  }
  // Complete rows as the API reads them; a row with no value is left out.
  function values() {
    return rows()
      .map((row) =>
        formatCondition({
          field: row.querySelector('[data-condition-field]').value,
          op: row.querySelector('[data-condition-op]').value,
          value: row.querySelector('[data-condition-value]').value,
        }),
      )
      .filter((raw) => parseCondition(raw));
  }
  function set(list) {
    host.replaceChildren();
    for (const raw of list || []) {
      const parsed = parseCondition(raw);
      if (parsed) add(parsed);
    }
    updateAddButton();
  }

  addButton?.addEventListener('click', () => add(undefined, { focus: true }));
  host.addEventListener('click', (ev) => {
    const remove = ev.target.closest('[data-condition-remove]');
    if (!remove) return;
    remove.closest('[data-condition-row]').remove();
    updateAddButton();
    onChange?.();
  });
  host.addEventListener('change', () => onChange?.());
  updateAddButton();
  return { add, set, values };
}
