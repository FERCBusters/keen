// The audit scope is chosen in three multiple selects (documents, clauses,
// controls), which the design system presents as its picker. Page tests run
// against a stand-in for the design system, so they choose as the picker
// would: by selecting options and reporting the change on the select.

export const scopeList = (ui, kind) => ui.document.getElementById(`${kind}sList`);

// One entry of a scope list, with the `checked` and `dispatchEvent` a tick box
// had, or null when the list does not offer it.
export function scopeBox(ui, kind, id) {
  const select = scopeList(ui, kind);
  const option = [...select.options].find((o) => o.value === id);
  if (!option) return null;
  return {
    option,
    select,
    get checked() {
      return option.selected;
    },
    set checked(value) {
      option.selected = value;
    },
    get disabled() {
      return select.disabled;
    },
    set disabled(value) {
      select.disabled = value;
    },
    dispatchEvent() {
      return select.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
    },
  };
}

// The ids chosen in a scope list, in list order.
export const scopeChosen = (ui, kind) =>
  [...scopeList(ui, kind).selectedOptions].map((option) => option.value);

// As the picker's "Deselect all": every option unchosen, one change reported.
export function deselectAll(ui, kind) {
  const select = scopeList(ui, kind);
  for (const option of select.options) option.selected = false;
  select.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
}
