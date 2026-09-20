import { esc } from '/app.js';

const instances = new Map();

function targetId(target) {
  if (!target) return '';
  if (typeof target === 'string') return target.replace(/^#/, '');
  return target.id || '';
}

function targetEl(target) {
  if (!target) return null;
  if (typeof target === 'string') return document.getElementById(target.replace(/^#/, ''));
  return target;
}

function splitValues(value) {
  return String(value || '')
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);
}

function uniqueValues(values) {
  return Array.from(new Set((values || []).map((v) => String(v || '').trim()).filter(Boolean)));
}

function optionFromDom(opt) {
  return {
    value: String(opt?.value || '').trim(),
    label: String(opt?.textContent || opt?.value || '').trim(),
    title: String(opt?.title || opt?.textContent || opt?.value || '').trim(),
  };
}

function readElementValues(el) {
  if (!el) return [];
  if (el.dataset.filterPillValue !== undefined) return splitValues(el.dataset.filterPillValue);
  if (el.tagName === 'SELECT' && el.multiple) return Array.from(el.selectedOptions || []).map((opt) => String(opt.value || '').trim()).filter(Boolean);
  return splitValues(el.value || '');
}

function writeElementValues(el, values) {
  if (!el) return;
  const selected = uniqueValues(values);
  const serialised = selected.join(',');
  el.dataset.filterPillValue = serialised;
  if (el.tagName !== 'SELECT') {
    el.value = serialised;
    return;
  }
  const optionValues = new Set(Array.from(el.options || []).map((opt) => String(opt.value || '')));
  if (!selected.length) {
    el.value = '';
  } else if (selected.length === 1 && optionValues.has(selected[0])) {
    el.value = selected[0];
  } else {
    el.value = '';
  }
}

function normaliseOptions(options) {
  return (options || [])
    .map((opt) => {
      if (typeof opt === 'string') return {value: opt, label: opt, title: opt};
      const value = String(opt?.value || '').trim();
      const label = String(opt?.label || opt?.name || opt?.text || value).trim() || value;
      return {value, label, title: String(opt?.title || label || value).trim() || label || value};
    })
    .filter((opt) => opt.value);
}

function optionsFromElement(el) {
  if (!el || el.tagName !== 'SELECT') return [];
  return Array.from(el.options || [])
    .map(optionFromDom)
    .filter((opt) => opt.value);
}

function labelForCount(count, singular, plural) {
  return count === 1 ? singular : (plural || `${singular}s`);
}

function sortOptions(options, selectedSet) {
  return options.slice().sort((a, b) => {
    const aSel = selectedSet.has(a.value) ? 0 : 1;
    const bSel = selectedSet.has(b.value) ? 0 : 1;
    if (aSel !== bSel) return aSel - bSel;
    return String(a.label || a.value).localeCompare(String(b.label || b.value), undefined, {numeric: true, sensitivity: 'base'});
  });
}

function createModal(id, config) {
  const modalId = `${id}FilterPillModal`;
  let modal = document.getElementById(modalId);
  if (modal) return modal;
  modal = document.createElement('div');
  modal.className = 'modal fade keen-filter-pill-modal';
  modal.id = modalId;
  modal.tabIndex = -1;
  modal.setAttribute('aria-hidden', 'true');
  modal.innerHTML = `
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <div>
            <h5 class="modal-title mb-1">${esc(config.title || 'Choose filter values')}</h5>
            <div class="small text-secondary">${esc(config.description || 'Select one or more values. Clear the selection to include all values.')}</div>
          </div>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <div class="keen-filter-pill-toolbar mb-3">
            <div class="input-group input-group-sm">
              <span class="input-group-text"><i class="bi bi-search" aria-hidden="true"></i></span>
              <input type="search" class="form-control keen-filter-pill-search" placeholder="${esc(config.searchPlaceholder || 'Filter values…')}">
            </div>
            <div class="d-flex flex-wrap align-items-center gap-2">
              <span class="small text-secondary keen-filter-pill-modal-summary">—</span>
              <button type="button" class="btn btn-sm btn-outline-secondary keen-filter-pill-modal-clear">Clear all</button>
            </div>
          </div>
          <div class="keen-filter-pill-selected mb-3"></div>
          <div class="keen-filter-pill-grid"></div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Done</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  return modal;
}

function chipHtml(option, {selected = false, removable = false} = {}) {
  const value = String(option?.value || '').trim();
  const label = String(option?.label || value).trim() || value;
  const title = String(option?.title || label).trim() || label;
  const tag = removable ? 'span' : 'button';
  const attrs = removable ? '' : ' type="button"';
  return `<${tag}${attrs} class="keen-filter-pill-chip${selected ? ' selected' : ''}${removable ? ' removable' : ''}" data-value="${esc(value)}" title="${esc(title)}">
    <span class="keen-filter-pill-chip-text">${esc(label)}</span>
    ${removable ? `<button type="button" class="keen-filter-pill-remove" data-remove-value="${esc(value)}" aria-label="Remove ${esc(label)}"><i class="bi bi-x" aria-hidden="true"></i></button>` : ''}
  </${tag}>`;
}

function buildInstance(el, config = {}) {
  if (!el?.id) return null;
  if (instances.has(el.id)) return instances.get(el.id);

  const options = normaliseOptions(config.options || optionsFromElement(el));
  const optionMap = new Map(options.map((opt) => [opt.value, opt]));
  const state = {
    el,
    id: el.id,
    options,
    optionMap,
    selected: uniqueValues(config.selectedValues || readElementValues(el)),
    query: '',
    config: {
      title: 'Choose filter values',
      description: 'Select one or more values. Clear the selection to include all values.',
      buttonLabel: 'Choose values',
      singular: 'value',
      plural: 'values',
      emptyText: 'All values',
      emptySelectedText: 'No filters selected. All values are included.',
      noMatchText: 'No values match your filter.',
      searchPlaceholder: 'Filter values…',
      ...config,
    },
  };

  el.classList.add('d-none');
  el.setAttribute('aria-hidden', 'true');
  el.setAttribute('autocomplete', 'off');
  writeElementValues(el, state.selected);

  const picker = document.createElement('div');
  picker.className = 'keen-filter-pill-picker';
  picker.dataset.filterPillFor = el.id;
  picker.innerHTML = `
    <div class="keen-filter-pill-picker-toolbar">
      <button type="button" class="btn btn-sm btn-outline-secondary keen-filter-pill-open">
        <i class="bi bi-grid-3x3-gap me-1" aria-hidden="true"></i>${esc(state.config.buttonLabel)}
      </button>
      <div class="keen-filter-pill-summary small text-secondary">${esc(state.config.emptyText)}</div>
      <button type="button" class="btn btn-sm btn-link text-decoration-none keen-filter-pill-clear d-none">Clear</button>
    </div>
    <div class="keen-filter-pill-inline"></div>`;
  el.insertAdjacentElement('afterend', picker);

  const modal = createModal(el.id, state.config);
  const modalInstance = globalThis.bootstrap?.Modal ? globalThis.bootstrap.Modal.getOrCreateInstance(modal) : null;
  const openBtn = picker.querySelector('.keen-filter-pill-open');
  const clearBtn = picker.querySelector('.keen-filter-pill-clear');
  const summary = picker.querySelector('.keen-filter-pill-summary');
  const inline = picker.querySelector('.keen-filter-pill-inline');
  const search = modal.querySelector('.keen-filter-pill-search');
  const modalSummary = modal.querySelector('.keen-filter-pill-modal-summary');
  const modalClear = modal.querySelector('.keen-filter-pill-modal-clear');
  const selectedHost = modal.querySelector('.keen-filter-pill-selected');
  const grid = modal.querySelector('.keen-filter-pill-grid');

  function selectedSet() { return new Set(state.selected); }
  function selectedOptions() {
    return state.selected.map((value) => state.optionMap.get(value) || {value, label: value, title: value});
  }
  function commit({dispatchChange = true} = {}) {
    const before = el.dataset.filterPillValue || '';
    writeElementValues(el, state.selected);
    render();
    const after = el.dataset.filterPillValue || '';
    if (dispatchChange && before !== after) el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  function setSelected(values, opts = {}) {
    const allowed = new Set(state.options.map((opt) => opt.value));
    const prune = opts.pruneSelected !== false;
    state.selected = uniqueValues(values).filter((value) => !prune || allowed.has(value));
    commit({dispatchChange: !!opts.dispatchChange});
  }
  function setOptions(optionsArg, opts = {}) {
    state.options = normaliseOptions(optionsArg);
    state.optionMap = new Map(state.options.map((opt) => [opt.value, opt]));
    const nextSelected = Object.prototype.hasOwnProperty.call(opts, 'selectedValues') ? uniqueValues(opts.selectedValues) : state.selected;
    setSelected(nextSelected, {pruneSelected: opts.pruneSelected !== false, dispatchChange: !!opts.dispatchChange});
  }
  function toggle(value) {
    const v = String(value || '').trim();
    if (!v) return;
    const s = selectedSet();
    if (s.has(v)) s.delete(v); else s.add(v);
    state.selected = Array.from(s);
    commit({dispatchChange: true});
  }
  function remove(value) {
    const v = String(value || '').trim();
    state.selected = state.selected.filter((item) => item !== v);
    commit({dispatchChange: true});
  }
  function clear({dispatchChange = true} = {}) {
    state.selected = [];
    commit({dispatchChange});
  }
  function summaryText() {
    const count = state.selected.length;
    if (!count) return state.config.emptyText;
    return `${count} ${labelForCount(count, state.config.singular, state.config.plural)} selected`;
  }
  function renderInline() {
    const selected = selectedOptions();
    summary.textContent = summaryText();
    clearBtn.classList.toggle('d-none', !selected.length);
    if (!selected.length) {
      inline.innerHTML = `<div class="small text-secondary">${esc(state.config.emptySelectedText || state.config.emptyText)}</div>`;
      return;
    }
    inline.innerHTML = selected
      .sort((a, b) => String(a.label || a.value).localeCompare(String(b.label || b.value), undefined, {numeric: true, sensitivity: 'base'}))
      .map((opt) => chipHtml(opt, {selected: true, removable: true}))
      .join('');
  }
  function renderModal() {
    const s = selectedSet();
    modalSummary.textContent = summaryText();
    modalClear.classList.toggle('d-none', !state.selected.length);
    const selected = selectedOptions();
    selectedHost.innerHTML = selected.length
      ? selected.map((opt) => chipHtml(opt, {selected: true, removable: true})).join('')
      : `<div class="small text-secondary">${esc(state.config.emptySelectedText)}</div>`;
    const q = String(state.query || '').toLowerCase();
    const filtered = sortOptions(state.options.filter((opt) => {
      if (!q) return true;
      return `${opt.value} ${opt.label} ${opt.title}`.toLowerCase().includes(q);
    }), s);
    grid.innerHTML = filtered.length
      ? filtered.map((opt) => chipHtml(opt, {selected: s.has(opt.value), removable: false})).join('')
      : `<div class="small text-secondary p-2">${esc(state.config.noMatchText)}</div>`;
  }
  function render() {
    renderInline();
    renderModal();
  }

  openBtn?.addEventListener('click', () => {
    state.query = '';
    if (search) search.value = '';
    render();
    modalInstance?.show();
  });
  clearBtn?.addEventListener('click', () => clear({dispatchChange: true}));
  modalClear?.addEventListener('click', () => clear({dispatchChange: true}));
  search?.addEventListener('input', () => {
    state.query = String(search.value || '');
    renderModal();
  });
  inline?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-remove-value]');
    if (btn) remove(btn.getAttribute('data-remove-value'));
  });
  selectedHost?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-remove-value]');
    if (btn) remove(btn.getAttribute('data-remove-value'));
  });
  grid?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-value]');
    if (btn) toggle(btn.getAttribute('data-value'));
  });
  modal.addEventListener('shown.bs.modal', () => search?.focus());

  const instance = {
    getValues: () => state.selected.slice(),
    setSelected,
    setOptions,
    render,
  };
  instances.set(el.id, instance);
  render();
  return instance;
}

export function enhanceFilterPillMultiSelect(target, config = {}) {
  const el = targetEl(target);
  if (!el) return null;
  return buildInstance(el, config);
}

export function selectedFilterPillValues(target) {
  const el = targetEl(target);
  if (!el) return [];
  const inst = instances.get(el.id);
  if (inst) return inst.getValues();
  return readElementValues(el);
}

export function setFilterPillOptions(target, options, opts = {}) {
  const el = targetEl(target);
  if (!el) return null;
  const inst = instances.get(el.id) || buildInstance(el, opts.config || {});
  inst?.setOptions(options, opts);
  return inst;
}

export function setFilterPillSelected(target, values, opts = {}) {
  const el = targetEl(target);
  if (!el) return null;
  const inst = instances.get(el.id) || buildInstance(el, opts.config || {});
  inst?.setSelected(values, opts);
  return inst;
}
