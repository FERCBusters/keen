// Who and what a risk or a PESTLE(E) factor affects: the people, vendors /
// third parties and assets it names. The risk wizard, the risk and PESTLE(E)
// pages, the registers and the Statement of Applicability show and pick them
// the same way.
import { apiGet, localPath, withFramework } from '/app.js';

export const AFFECTED_KINDS = {
  person: { label: 'Person', icon: 'bi-person' },
  vendor: { label: 'Vendor / 3rd party', icon: 'bi-building' },
  asset: { label: 'Asset', icon: 'bi-hdd-stack' },
};
// Records the picker can add by name, and the choice that names one.
const NEW_KINDS = { new_person: 'person', new_vendor: 'vendor' };

// Badges linking to each affected record; a dash when there are none.
export function affectedBadges(items, framework) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) return [node('span', { class: 'small-muted' }, '-')];
  return list.map((item) => {
    const kind = AFFECTED_KINDS[item.kind];
    const href = localPath(item.href) && localPath(withFramework(item.href, framework));
    const attrs = { class: `badge badge-soft me-1${href ? ' text-decoration-none' : ''}` };
    if (href) attrs.href = href;
    if (item.detail) attrs.title = item.detail;
    return node(
      href ? 'a' : 'span',
      attrs,
      icon(`${kind.icon} me-1`),
      node('span', { class: 'visually-hidden' }, `${kind.label}: `),
      item.label,
    );
  });
}

// The same badges as markup, for tables written as text.
export function affectedBadgesHtml(items, framework) {
  return affectedBadges(items, framework)
    .map((badge) => badge.outerHTML)
    .join('');
}

// The people, vendors and assets the signed-in account may name, and whether
// it may add a person or vendor by name.
export function loadAffectedOptions() {
  return apiGet('/api/v1/affected/options');
}

function choiceKey(choice) {
  return `${choice.kind}:${choice.id || choice.label.toLowerCase()}`;
}

// What the API takes for the chosen records.
export function affectedPayload(chosen) {
  return chosen.map((choice) =>
    NEW_KINDS[choice.kind] ? { [choice.kind]: choice.label } : { [`${choice.kind}_id`]: choice.id },
  );
}

// Chosen records from what the API returns for a risk, an item or a draft.
export function affectedChoices(items) {
  return (Array.isArray(items) ? items : [])
    .filter((item) => AFFECTED_KINDS[item?.kind] && item.id)
    .map((item) => ({
      kind: item.kind,
      id: String(item.id),
      label: String(item.label ?? ''),
      detail: String(item.detail ?? ''),
    }));
}

function node(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === 'class') el.className = value;
    else if (name === 'hidden') el.hidden = value;
    else el.setAttribute(name, value);
  }
  el.append(...children.filter((child) => child !== null));
  return el;
}

function icon(name) {
  return node('i', { class: `bi ${name}`, 'aria-hidden': 'true' });
}

// A picker in `container`: search the registers, tick records, and add a
// person or vendor by name where the account may. `prefix` names its
// elements. `onChange` hears every change to the chosen records.
export function mountAffectedPicker(container, { prefix, options, chosen = [], onChange }) {
  const canAdd = options.can_add;
  const all = [...options.people, ...options.vendors, ...options.assets];
  let picked = [];
  // Chosen records the options leave out, such as one just added by name,
  // are listed with the rest.
  function choose(next) {
    picked = [...next];
    const keys = new Set(all.map(choiceKey));
    for (const choice of picked)
      if (AFFECTED_KINDS[choice.kind] && !keys.has(choiceKey(choice))) all.push(choice);
  }
  choose(chosen);
  const search = node('input', {
    id: `${prefix}Search`,
    class: 'form-control mb-2',
    type: 'search',
    autocomplete: 'off',
    placeholder: `Search people, vendors / 3rd parties and assets${canAdd.person || canAdd.vendor ? ', or type a new name' : ''}`,
    'aria-label': 'Search people, vendors / 3rd parties and assets',
  });
  const list = node('div', { id: `${prefix}List`, class: 'list-group mb-2' });
  const addName = node('span', { id: `${prefix}AddName` });
  const addButton = (kind, iconName, text) =>
    node(
      'button',
      { class: 'btn btn-sm btn-outline-primary', type: 'button', 'data-add-affected': kind },
      icon(`${iconName} me-1`),
      text,
    );
  const addPanel = node(
    'div',
    { id: `${prefix}AddPanel`, class: 'border rounded-3 p-3 mb-2', hidden: true },
    node('div', { class: 'fw-semibold mb-2' }, 'Add “', addName, '” as a new'),
    node(
      'div',
      { class: 'd-flex flex-wrap gap-2' },
      canAdd.person ? addButton('new_person', 'bi-person-plus', 'Person') : null,
      canAdd.vendor ? addButton('new_vendor', 'bi-building-add', 'Vendor / 3rd party') : null,
    ),
    node(
      'div',
      { class: 'small-muted mt-2' },
      'Keen records the name when you save; complete their details in People & vendors / 3rd parties.',
    ),
  );
  const chosenList = node('div', {
    id: `${prefix}Chosen`,
    class: 'd-flex flex-wrap gap-1',
    'aria-live': 'polite',
  });
  container.replaceChildren(search, list, addPanel, chosenList);

  function changed() {
    render();
    onChange?.([...picked]);
  }

  function renderChosen() {
    chosenList.replaceChildren(
      ...(picked.length
        ? picked.map((choice) => {
            const fresh = NEW_KINDS[choice.kind];
            const kind = AFFECTED_KINDS[fresh || choice.kind];
            return node(
              'span',
              { class: 'badge badge-soft d-inline-flex align-items-center gap-1' },
              icon(kind.icon),
              choice.label,
              fresh ? node('span', { class: 'small' }, `new ${kind.label.toLowerCase()}`) : null,
              node(
                'button',
                {
                  class: 'btn btn-sm btn-link p-0 ms-1',
                  type: 'button',
                  'data-remove-affected': choiceKey(choice),
                  'aria-label': `Remove ${choice.label}`,
                },
                icon('bi-x'),
              ),
            );
          })
        : [node('span', { class: 'small-muted' }, 'Nobody and nothing selected yet.')]),
    );
  }

  function optionRow(option, checked) {
    const box = node('input', {
      class: 'form-check-input mt-0',
      type: 'checkbox',
      'data-affected': choiceKey(option),
    });
    box.checked = checked;
    return node(
      'label',
      { class: 'list-group-item d-flex gap-2 align-items-center' },
      box,
      node(
        'span',
        { class: 'flex-grow-1' },
        node('span', { class: 'fw-semibold' }, option.label),
        option.detail ? node('span', { class: 'small-muted' }, ` - ${option.detail}`) : null,
      ),
      node('span', { class: 'badge text-bg-light border' }, AFFECTED_KINDS[option.kind].label),
    );
  }

  function render() {
    const query = search.value.trim();
    const q = query.toLowerCase();
    const keys = new Set(picked.map(choiceKey));
    const rows = all
      .filter((option) => !q || `${option.label} ${option.detail}`.toLowerCase().includes(q))
      .slice(0, 60)
      .map((option) => optionRow(option, keys.has(choiceKey(option))));
    list.replaceChildren(
      ...(rows.length
        ? rows
        : [
            node(
              'div',
              { class: 'list-group-item small-muted' },
              query
                ? 'Nobody and nothing by that name yet.'
                : 'No people, vendors / 3rd parties or assets recorded yet.',
            ),
          ]),
    );
    // A name already recorded, or already added, is not offered again.
    const known = all.some((option) => option.kind !== 'asset' && option.label.toLowerCase() === q);
    const added = picked.some(
      (choice) => NEW_KINDS[choice.kind] && choice.label.toLowerCase() === q,
    );
    addPanel.hidden = !query || known || added || !(canAdd.person || canAdd.vendor);
    addName.textContent = query;
    renderChosen();
  }

  let timer = 0;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(render, 120);
  });
  list.addEventListener('change', (ev) => {
    const key = ev.target.dataset.affected;
    picked = picked.filter((choice) => choiceKey(choice) !== key);
    if (ev.target.checked) {
      const option = all.find((item) => choiceKey(item) === key);
      picked.push({ kind: option.kind, id: option.id, label: option.label, detail: option.detail });
    }
    changed();
  });
  addPanel.addEventListener('click', (ev) => {
    const add = ev.target.closest('[data-add-affected]');
    if (!add) return;
    picked.push({ kind: add.dataset.addAffected, label: search.value.trim() });
    search.value = '';
    changed();
    search.focus();
  });
  chosenList.addEventListener('click', (ev) => {
    const remove = ev.target.closest('[data-remove-affected]');
    if (!remove) return;
    picked = picked.filter((choice) => choiceKey(choice) !== remove.dataset.removeAffected);
    changed();
  });
  render();
  return {
    value: () => [...picked],
    set(next) {
      choose(next);
      render();
    },
  };
}
