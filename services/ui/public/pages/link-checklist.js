// A filter box over a scrollable list of tick boxes, for choosing the controls
// or clauses a record is linked to. Typing a reference or part of a title
// narrows the list; what is ticked stays ticked while it is filtered out of
// view. The list scrolls inside its own box, so a framework with hundreds of
// controls does not lengthen the page. Everything shown is built as elements.
import { node } from '/app.js';

// Rows drawn at once; a longer catalogue asks for a filter.
export const MAX_ROWS = 400;

export function matchesLink(record, filter) {
  return (
    !filter ||
    `${record.ref} ${record.title || ''} ${record.description || ''}`.toLowerCase().includes(filter)
  );
}

/**
 * Mount a checklist in `host`.
 *
 * `plural` names what is listed ("controls"), `label` heads it. Returns
 * `load(records, selectedIds)`, `selected()` (the ticked ids, in list order)
 * and `setDisabled(flag)`.
 */
export function createLinkChecklist(host, { label, plural }) {
  const id = `linkChecklist-${plural}`;
  const filter = node('input', {
    id,
    type: 'search',
    class: 'form-control form-control-sm mb-2',
    maxlength: '128',
    placeholder: `Filter ${plural} by reference or title`,
  });
  const list = node('div', {
    class: 'border rounded p-2 link-checklist-scroll',
    role: 'group',
    'aria-label': label,
  });
  const count = node('span', {}, '0');
  host.replaceChildren(
    node('label', { class: 'form-label', for: id }, label),
    filter,
    list,
    node('div', { class: 'small-muted mt-2', 'aria-live': 'polite' }, count, ' selected.'),
  );
  let records = [];
  let disabled = false;
  const chosen = new Set();

  function render() {
    const needle = filter.value.trim().toLowerCase();
    const matching = records.filter((record) => matchesLink(record, needle));
    const rows = matching.slice(0, MAX_ROWS).map((record) => {
      const box = node('input', {
        class: 'form-check-input mt-1 flex-shrink-0',
        type: 'checkbox',
        value: record.id,
      });
      box.checked = chosen.has(record.id);
      box.disabled = disabled;
      return node(
        'label',
        { class: 'd-flex gap-2 align-items-start py-1 mb-0' },
        box,
        node(
          'span',
          {},
          node('span', { class: 'fw-semibold' }, record.ref),
          record.title ? ` ${record.title}` : '',
          record.in_scope === false
            ? node('span', { class: 'badge text-bg-secondary ms-1' }, 'out of scope')
            : null,
        ),
      );
    });
    if (matching.length > MAX_ROWS)
      rows.push(
        node(
          'div',
          { class: 'small-muted pt-1' },
          `Showing the first ${MAX_ROWS} of ${matching.length}. Type in the filter to narrow the list.`,
        ),
      );
    list.replaceChildren(
      ...(rows.length ? rows : [node('div', { class: 'small-muted' }, `No ${plural} match.`)]),
    );
    count.textContent = String(chosen.size);
  }

  filter.addEventListener('input', render);
  // Enter in the filter narrows the list; it does not submit the form around it.
  filter.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') event.preventDefault();
  });
  list.addEventListener('change', (event) => {
    if (event.target.checked) chosen.add(event.target.value);
    else chosen.delete(event.target.value);
    count.textContent = String(chosen.size);
  });

  return {
    load(next, selectedIds) {
      records = next.map((record) => ({ ...record, id: String(record.id) }));
      chosen.clear();
      // Only what the catalogue still holds can be ticked.
      const known = new Set(records.map((record) => record.id));
      for (const selectedId of selectedIds)
        if (known.has(String(selectedId))) chosen.add(String(selectedId));
      filter.value = '';
      render();
    },
    selected: () => records.filter((record) => chosen.has(record.id)).map((record) => record.id),
    setDisabled(flag) {
      disabled = flag;
      render();
    },
  };
}
