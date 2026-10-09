import {esc, withFramework} from '/app.js';

// Local browsing changes only presentation, never evidence inheritance.
export function createRelatedControlsBrowser(root, {isAdmin = false} = {}) {
  const el = id => root.getElementById(id);
  const browser = el('crossFrameworkBrowser');
  const select = el('crossFrameworkView');
  const summary = el('crossFrameworkSummary');
  const list = el('crossFrameworkLinks');
  const pager = el('crossFrameworkPager');
  const previous = el('crossFrameworkPrevious');
  const next = el('crossFrameworkNext');
  const pageLabel = el('crossFrameworkPage');
  const pageSize = 10;
  let items = [];
  let page = 0;

  function hidePager() {
    pager.hidden = true;
    pager.classList.remove('d-flex');
  }

  function render() {
    hidePager();
    if (!items.length) {
      summary.textContent = '';
      list.textContent = 'No related controls in other frameworks.';
      return;
    }
    if (!select.value) {
      summary.textContent = `${items.length} related control${items.length === 1 ? '' : 's'} across ${select.options.length - 1} framework${select.options.length === 2 ? '' : 's'}.`;
      list.textContent = 'Choose a framework to view its related controls.';
      return;
    }
    const matches = items.filter(link => link.source_framework === select.value);
    page = Math.min(page, Math.max(0, Math.ceil(matches.length / pageSize) - 1));
    const start = page * pageSize;
    summary.textContent = `Showing ${start + 1}–${Math.min(start + pageSize, matches.length)} of ${matches.length} related control${matches.length === 1 ? '' : 's'}.`;
    list.innerHTML = matches.slice(start, start + pageSize).map(link => `
      <div class="border-bottom py-2">
        <div class="d-flex gap-2 align-items-start">
          <a class="flex-grow-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(link.source_control_id)}`, link.source_framework))}">${esc(link.source_ref)} ${esc(link.source_title || '')}</a>
          ${isAdmin && !link.derived ? `<button class="btn btn-sm btn-outline-danger" type="button" data-remove-cross-link="${esc(link.id)}" aria-label="${esc(`Remove link to ${link.source_ref}`)}">Remove</button>` : ''}
        </div>
        ${link.rationale ? `<details class="small-muted mt-1"><summary>Why related?</summary><div class="mt-1">${esc(link.rationale)}</div></details>` : ''}
      </div>`).join('');
    if (matches.length > pageSize) {
      pager.hidden = false;
      pager.classList.add('d-flex');
      previous.disabled = page === 0;
      next.disabled = start + pageSize >= matches.length;
      pageLabel.textContent = `Page ${page + 1} of ${Math.ceil(matches.length / pageSize)}`;
    }
  }

  select.addEventListener('change', () => { page = 0; render(); });
  previous.addEventListener('click', () => { page = Math.max(0, page - 1); render(); });
  next.addEventListener('click', () => { page += 1; render(); });

  return {
    update(links, frameworks = []) {
      items = links;
      const selected = select.value;
      const names = new Map(frameworks.map(f => [f.slug, f.name || f.slug]));
      const counts = new Map();
      items.forEach(link => counts.set(link.source_framework, (counts.get(link.source_framework) || 0) + 1));
      const groups = [...counts].sort(([a], [b]) => (names.get(a) || a).localeCompare(names.get(b) || b));
      select.innerHTML = '<option value="">Choose framework…</option>' + groups.map(([slug, count]) => `<option value="${esc(slug)}">${esc(names.get(slug) || slug)} (${count})</option>`).join('');
      select.value = counts.has(selected) ? selected : '';
      browser.hidden = !items.length;
      render();
    },
    showError(message) {
      items = [];
      browser.hidden = true;
      hidePager();
      summary.textContent = '';
      list.textContent = message;
    },
  };
}
