import { apiGet, esc, withFramework, getCurrentFramework } from '/app.js';

export async function mountSwotRelated(type, id, container = null) {
  if (!id) return;
  const framework = getCurrentFramework();
  const host = container || document.querySelector('main .tab-content');
  if (!host) return;
  const section = document.createElement('section');
  section.id = 'swotRelatedPane';
  // A host supplies its own frame; without one the list becomes a page tab.
  if (!container) {
    const tabs = document.querySelector('main .nav-tabs');
    if (!tabs) return;
    section.className = 'tab-pane fade';
    section.setAttribute('role', 'tabpanel');
    const li = document.createElement('li');
    li.className = 'nav-item';
    li.innerHTML =
      '<button class="nav-link" type="button" role="tab" data-bs-toggle="tab" data-bs-target="#swotRelatedPane">SWOT</button>';
    tabs.append(li);
  }
  host.append(section);
  try {
    const data = await apiGet(
      `/api/v1/isms/swot?target_type=${encodeURIComponent(type)}&target_id=${encodeURIComponent(id)}&limit=100`,
    );
    section.innerHTML = `<h2 class="h5">Related SWOT items</h2>${data.items.map((row) => `<p><a href="${esc(withFramework(`/swot.html?id=${encodeURIComponent(row.id)}&tab=related`, framework))}">${esc(row.title)}</a> <span class="small-muted">${esc(row.kind)} · ${row.significance}/5</span></p>`).join('') || '<p class="small-muted">No SWOT items linked.</p>'}<a href="${esc(withFramework(`/swot.html?target_type=${encodeURIComponent(type)}&target_id=${encodeURIComponent(id)}`, framework))}">Explore in SWOT analysis (${data.total})</a>`;
  } catch {
    section.textContent = 'Related SWOT items are unavailable with your current access.';
  }
}
