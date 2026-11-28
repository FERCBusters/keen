// A mapping rule's controls and clauses are chosen in a multiple select, which
// the design system presents as its picker. Page tests run against a stand-in
// for the design system, so they choose as the picker would: by selecting the
// option and reporting the change on the select.
export function chooseTarget(select, ref, kind = 'control', on = true) {
  const option = [...select.options].find((o) => o.value === `${kind}|${ref}`);
  if (!option) throw new Error(`The picker does not list ${kind} ${ref}`);
  option.selected = on;
  select.dispatchEvent(new select.ownerDocument.defaultView.Event('change', { bubbles: true }));
}

// The rows chosen in the picker, as "kind|reference".
export const chosenTargets = (select) => [...select.selectedOptions].map((o) => o.value);
