// Selecting sampled evidence on an audit page so several entries can be
// removed together. Selection survives reloads of the list until it is
// cleared or used, mirroring the event selection pattern on the Events page.
import { esc, toast } from '/app.js';

export const MAX_SELECTED_EVIDENCE = 200;

export function createEvidenceSelection({ container, bar, selectAllWrap, statusEl }) {
  const selected = new Set();
  const count = bar?.querySelector('[data-evidence-selection-count]');
  const selectAll = selectAllWrap?.querySelector('[data-select-evidence-all]');
  let lastIndex = -1;

  function boxes() {
    return [...container.querySelectorAll('[data-select-evidence]')];
  }

  function syncRows() {
    for (const box of boxes()) box.checked = selected.has(box.value);
    if (selectAll) {
      const rows = boxes();
      selectAll.checked = rows.length > 0 && rows.every((box) => box.checked);
      selectAll.indeterminate = !selectAll.checked && rows.some((box) => box.checked);
    }
  }

  function render() {
    if (!bar) return;
    bar.hidden = selected.size === 0;
    if (count) count.textContent = `${selected.size} selected`;
  }

  function setBox(box, on) {
    if (!box.value) return;
    if (on) {
      if (selected.size >= MAX_SELECTED_EVIDENCE && !selected.has(box.value)) {
        toast(
          statusEl,
          `You can remove up to ${MAX_SELECTED_EVIDENCE} evidence items at once; add the rest after this round.`,
          'warning',
        );
        return;
      }
      selected.add(box.value);
    } else selected.delete(box.value);
  }

  function applyRange(from, to, on) {
    const [a, b] = from <= to ? [from, to] : [to, from];
    for (let i = a; i <= b; i += 1) {
      const box = boxes()[i];
      if (box) setBox(box, on);
    }
    render();
    syncRows();
  }

  function toggle(box, { shiftKey } = {}) {
    const index = boxes().indexOf(box);
    if (index < 0) return;
    if (shiftKey && lastIndex >= 0) {
      applyRange(lastIndex, index, box.checked);
    } else {
      setBox(box, box.checked);
      lastIndex = index;
      render();
      syncRows();
    }
  }

  function selectAllRows(on) {
    applyRange(0, boxes().length - 1, on);
  }

  function clear() {
    selected.clear();
    lastIndex = -1;
    render();
    syncRows();
  }

  if (!bar || !selectAllWrap) {
    return { enabled: false, rendered() {}, clear() {}, ids: () => [] };
  }

  container.addEventListener('click', (ev) => {
    const box = ev.target.closest('[data-select-evidence]');
    if (!box) return;
    toggle(box, ev);
  });
  container.addEventListener('keydown', (ev) => {
    if (!ev.shiftKey || (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp')) return;
    const box = ev.target.closest('[data-select-evidence]');
    if (!box) return;
    const index = boxes().indexOf(box);
    const next = boxes()[ev.key === 'ArrowDown' ? index + 1 : index - 1];
    if (!next) return;
    ev.preventDefault();
    next.checked = box.checked;
    toggle(next, ev);
    next.focus();
  });
  selectAll.addEventListener('change', () => selectAllRows(selectAll.checked));
  bar.querySelector('[data-clear-evidence-selection]')?.addEventListener('click', clear);

  return {
    enabled: true,
    cellHtml(id) {
      return `<input class="form-check-input" type="checkbox" data-select-evidence value="${esc(id)}" aria-label="Select this sampled evidence">`;
    },
    rendered() {
      // Entries removed elsewhere (or by another edit) drop out of the selection.
      const present = new Set(boxes().map((box) => box.value));
      for (const id of [...selected]) if (!present.has(id)) selected.delete(id);
      syncRows();
      render();
    },
    count: () => selected.size,
    ids: () => [...selected],
    clear,
  };
}
