// Selecting events on the Events page to start a story or add to one. The
// selection survives paging and filtering until it is cleared or used.
import { errorText, apiGet, apiPost, esc, toast, withFramework } from '/app.js';
import { featureEnabled } from '/pages/feature-availability.js';

export const MAX_SELECTED = 200;

export function createEventSelection({ me, framework, tbody, bar, menu, statusEl }) {
  const enabled = Boolean(
    featureEnabled(me, 'stories') && (me?.is_admin || me?.can_manage_stories),
  );
  const selected = new Map(); // event id -> summary
  const count = bar?.querySelector('[data-selection-count]');
  const titleInput = bar?.querySelector('[data-story-title]');
  const targetSelect = bar?.querySelector('[data-story-target]');
  let storiesLoaded = false;

  function storyUrl(id) {
    return withFramework(`/story.html?id=${encodeURIComponent(id)}`, framework);
  }

  function syncRows() {
    for (const box of tbody.querySelectorAll('[data-select-event]'))
      box.checked = selected.has(box.value);
    const all = document.getElementById('selectAll');
    if (all) {
      const boxes = [...tbody.querySelectorAll('[data-select-event]')];
      all.checked = boxes.length > 0 && boxes.every((box) => box.checked);
      all.indeterminate = !all.checked && boxes.some((box) => box.checked);
    }
  }

  function render() {
    if (!bar) return;
    bar.hidden = selected.size === 0;
    if (count) count.textContent = `${selected.size} selected`;
    if (titleInput && !titleInput.value && selected.size) {
      const [first] = selected.values();
      titleInput.placeholder = first ? `Story about “${first.slice(0, 60)}”` : 'Story title';
    }
    syncRows();
  }

  function toggle(id, summary, on) {
    if (on) {
      if (selected.size >= MAX_SELECTED && !selected.has(id)) {
        toast(
          statusEl,
          `A story holds up to ${MAX_SELECTED} events; the selection is full.`,
          'warning',
        );
        return;
      }
      selected.set(id, summary || '');
    } else selected.delete(id);
    render();
  }

  function clear() {
    selected.clear();
    render();
  }

  function items() {
    return [...selected.keys()].map((id) => ({ kind: 'event', entity_id: id }));
  }

  async function createStory() {
    if (!selected.size) return;
    const title = (titleInput?.value || titleInput?.placeholder || 'Story').trim();
    try {
      const story = await apiPost(`/api/v1/stories?framework=${encodeURIComponent(framework)}`, {
        title,
        items: items(),
      });
      clear();
      location.assign(storyUrl(story.id));
    } catch (error) {
      toast(statusEl, `Failed to create the story: ${errorText(error)}`, 'danger');
    }
  }

  async function loadStories() {
    if (!targetSelect || storiesLoaded) return;
    try {
      const data = await apiGet('/api/v1/stories?status=open&limit=200');
      const own = (data.items || []).filter((story) => story.can_edit);
      targetSelect.innerHTML =
        '<option value="">Choose a story</option>' +
        own
          .map((story) => `<option value="${esc(story.id)}">${esc(story.title)}</option>`)
          .join('');
      storiesLoaded = true;
    } catch (error) {
      toast(statusEl, `Failed to list stories: ${errorText(error)}`, 'danger');
    }
  }

  async function addToStory() {
    const id = targetSelect?.value;
    if (!id || !selected.size) return;
    try {
      const story = await apiGet(`/api/v1/stories/${encodeURIComponent(id)}`);
      const saved = await apiPost(
        `/api/v1/stories/${encodeURIComponent(id)}/items?framework=${encodeURIComponent(framework)}`,
        { items: items(), expected_version: story.version },
      );
      clear();
      toast(statusEl, `Added to the story “${saved.title}”.`, 'success');
      const link = document.createElement('a');
      link.href = storyUrl(saved.id);
      link.textContent = 'Open the story';
      link.className = 'ms-2';
      statusEl?.append(link);
    } catch (error) {
      toast(statusEl, `Failed to add to the story: ${errorText(error)}`, 'danger');
    }
  }

  function hideMenu() {
    if (menu) menu.hidden = true;
  }

  function showMenu(ev, row) {
    if (!menu) return;
    ev.preventDefault();
    const id = row.dataset.eventId;
    if (!selected.has(id)) toggle(id, row.querySelector('[data-event-summary]')?.textContent, true);
    menu.querySelector('[data-selection-count]').textContent = String(selected.size);
    menu.hidden = false;
    menu.style.left = `${ev.pageX}px`;
    menu.style.top = `${ev.pageY}px`;
    menu.querySelector('button')?.focus();
  }

  if (enabled) {
    tbody.addEventListener('change', (ev) => {
      const box = ev.target.closest('[data-select-event]');
      if (!box) return;
      const row = box.closest('tr[data-event-id]');
      toggle(box.value, row?.querySelector('[data-event-summary]')?.textContent, box.checked);
    });
    tbody.addEventListener('contextmenu', (ev) => {
      const row = ev.target.closest('tr[data-event-id]');
      if (!row || !tbody.contains(row)) return;
      showMenu(ev, row);
    });
    document.getElementById('selectAll')?.addEventListener('change', (ev) => {
      // Each toggle redraws the header box, so read its state once.
      const on = ev.target.checked;
      for (const box of tbody.querySelectorAll('[data-select-event]')) {
        const row = box.closest('tr[data-event-id]');
        toggle(box.value, row?.querySelector('[data-event-summary]')?.textContent, on);
      }
    });
    bar?.querySelector('[data-create-story]')?.addEventListener('click', createStory);
    bar?.querySelector('[data-clear-selection]')?.addEventListener('click', clear);
    bar?.querySelector('[data-add-to-story]')?.addEventListener('click', addToStory);
    targetSelect?.addEventListener('focus', loadStories);
    menu?.addEventListener('click', (ev) => {
      const action = ev.target.closest('[data-menu-action]')?.dataset.menuAction;
      if (!action) return;
      hideMenu();
      if (action === 'create') {
        bar?.scrollIntoView({ block: 'nearest' });
        titleInput?.focus();
      } else if (action === 'add') {
        loadStories();
        targetSelect?.focus();
      } else if (action === 'clear') clear();
    });
    document.addEventListener('click', (ev) => {
      if (menu && !menu.hidden && !menu.contains(ev.target)) hideMenu();
    });
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') hideMenu();
    });
  }
  const header = document.getElementById('selectHeader');
  if (header) header.hidden = !enabled;

  return {
    enabled,
    columns: enabled ? 1 : 0,
    cellHtml(event) {
      if (!enabled) return '';
      const summary = String(event.summary || '');
      return `<td class="story-select-cell"><input class="form-check-input" type="checkbox" data-select-event value="${esc(event.id)}" aria-label="Select ${esc(summary.slice(0, 80) || 'event')}"></td>`;
    },
    rendered: syncRows,
    get size() {
      return selected.size;
    },
    ids: () => [...selected.keys()],
    clear,
  };
}
