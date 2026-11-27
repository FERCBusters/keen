// Fills a multiple select with controls or clauses of a framework. The design
// system presents every such select as the same picker: a search box, a short
// list to browse and the chosen entries as chips. Options are built as
// elements, so catalogue text is never parsed as HTML.
import { node } from '/app.js';

export function catalogueLabel(record) {
  const ref = `${record.type === 'clause' ? 'Clause ' : ''}${record.ref}`;
  return record.title ? `${ref} ${record.title}` : ref;
}

/**
 * Replace the select's options with `records`, choosing those named in
 * `selectedIds`. `kept` lists chosen records the catalogue may not hold, such
 * as a control of another framework: each stays listed and chosen, so saving
 * never drops it.
 */
export function fillCatalogueSelect(select, records, selectedIds = [], kept = []) {
  const chosen = new Set(selectedIds.map(String));
  const listed = new Set(records.map((record) => String(record.id)));
  const extra = kept.filter((record) => !listed.has(String(record.id)));
  select.replaceChildren(
    ...[...records, ...extra].map((record) => {
      const option = node(
        'option',
        { value: record.id, title: record.description || '' },
        catalogueLabel(record),
      );
      if (record.in_scope === false) option.dataset.badge = 'out of scope';
      option.selected = chosen.has(String(record.id)) || extra.includes(record);
      return option;
    }),
  );
}

// The ids chosen in a select, in list order.
export function chosenIds(select) {
  return Array.from(select.selectedOptions, (option) => option.value);
}
