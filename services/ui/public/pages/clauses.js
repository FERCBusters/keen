import {
  initNavbar,
  apiGet,
  esc,
  qs,
  setQuery,
  toast,
  debounce,
  enableTableSorting,
  getCurrentFramework,
  withFramework,
  upstreamLinkHtml,
  titledLinksHtml,
  showTableLoading,
} from '/app.js';

await initNavbar();
const framework = getCurrentFramework();
enableTableSorting();

const status = document.getElementById('status');
const rowsEl = document.getElementById('rows');
const metaEl = document.getElementById('meta');
const qEl = document.getElementById('q');

let clauses = [];
let state = {q: qs('q', '')};
if (qEl) qEl.value = state.q;

function hasChildClauses(c) {
  const id = String(c?.id || '');
  if (!id) return false;
  if (Number(c?.child_count || 0) > 0) return true;
  return clauses.some((candidate) => String(candidate?.parent_id || '') === id);
}

function flatten(nodes, depth = 0, out = []) {
  for (const n of nodes || []) {
    out.push({...n, depth});
    flatten(n.children || [], depth + 1, out);
  }
  return out;
}

function render() {
  const q = (state.q || '').trim().toLowerCase();
  const view = clauses.filter((c) => {
    if (!q) return true;
    return `${c.ref || ''} ${c.title || ''}`.toLowerCase().includes(q);
  });
  if (metaEl) metaEl.textContent = `${view.length} of ${clauses.length} clause${clauses.length === 1 ? '' : 's'}`;
  if (!view.length) {
    rowsEl.innerHTML = '<tr><td colspan="5" class="small-muted p-4">No clauses match.</td></tr>';
    return;
  }
  rowsEl.innerHTML = view.map((c) => {
    const href = withFramework(`/clause.html?id=${encodeURIComponent(c.id)}`, c.framework || framework);
    const depth = Math.max(0, Number(c.depth || 0));
    const indent = depth ? `style="padding-left:${Math.min(depth * 1.25, 4)}rem"` : '';
    const cls = depth === 0 ? 'fw-bold' : '';
    const upstreamHtml = upstreamLinkHtml(c.upstream_url, {title: 'Open upstream guidance'});
    const evidenceMappings = Array.isArray(c.evidence_mappings)
      ? c.evidence_mappings
      : (Array.isArray(c.evidence_urls) ? c.evidence_urls.map((url) => ({title: '', url})) : []);
    const evidenceHtml = titledLinksHtml(evidenceMappings, {title: 'Open evidence mapping', empty: '<span class="small-muted">—</span>', itemClass: 'd-inline-flex align-items-center gap-1 mb-1'});
    const parentClause = hasChildClauses(c);
    const linkedControlsHtml = parentClause
      ? '<span class="small-muted" title="Parent clause; controls are linked to child clauses">—</span>'
      : esc(String(c.control_link_count || 0));
    const linkedControlsSort = parentClause ? -1 : Number(c.control_link_count || 0);
    return `<tr>
      <td class="${cls}" ${indent}><a href="${esc(href)}">${esc(c.ref || '')}</a></td>
      <td class="wrap ${cls}">${esc(c.title || '')}</td>
      <td data-sort="${linkedControlsSort}">${linkedControlsHtml}</td>
      <td data-sort="${Number(evidenceMappings.length || 0)}">${evidenceHtml}</td>
      <td>${upstreamHtml}</td>
    </tr>`;
  }).join('');
}

async function load() {
  showTableLoading(rowsEl, 5, 'Loading clauses…');
  try {
    const data = await apiGet(`/api/v1/clauses?framework=${encodeURIComponent(framework)}`);
    clauses = flatten(data?.tree || []);
    render();
  } catch (e) {
    toast(status, `Failed to load clauses: ${String(e)}`, 'danger');
    rowsEl.innerHTML = '<tr><td colspan="5" class="small-muted p-4">Failed to load clauses.</td></tr>';
  }
}

const debRender = debounce(() => {
  state.q = (qEl?.value || '').trim();
  setQuery({framework: framework || null, q: state.q || null});
  render();
}, 200);
qEl?.addEventListener('input', debRender);
document.getElementById('clearFilters')?.addEventListener('click', () => {
  if (qEl) qEl.value = '';
  state.q = '';
  setQuery({framework: framework || null, q: null});
  render();
});

load();
