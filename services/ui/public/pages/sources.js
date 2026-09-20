import {initNavbar, apiGet, esc, fmtTs, tsSortKey, toast, enableTableSorting, sourceBadgeHtml, getCurrentFramework, withFramework, showTableLoading} from '/app.js';

await initNavbar();
const framework = getCurrentFramework();
enableTableSorting();

const status = document.getElementById('status');
const tbody = document.getElementById('rows');
const params = new URLSearchParams(location.search || '');
const filterSourceRaw = (params.get('source') || params.get('type') || '').trim();
const filterSource = filterSourceRaw ? filterSourceRaw.toLowerCase() : '';
const filterEl = document.getElementById('sourcesFilter');

if (filterEl && filterSource) {
  const pretty = filterSourceRaw;
  filterEl.innerHTML = `
    <span class="badge badge-soft me-2">Filtered: ${esc(pretty)}</span>
    <a class="small-muted" href="${withFramework('/sources.html', framework)}">Clear filter</a>
  `;
}

let sourceMeta = {};

function sourceHref(src, overrides = {}) {
  const p = new URLSearchParams();
  if (framework) p.set('framework', framework);
  if (src) p.set('source', src);
  if (overrides.tab) p.set('tab', overrides.tab);
  if (overrides.unmapped) p.set('unmapped', 'true');
  return `/source.html?${p.toString()}`;
}

function render(items) {
  tbody.innerHTML = '';
  for (const s of items) {
    const src = s.source || 'unknown';
    const openHref = sourceHref(src);
    const unmappedHref = sourceHref(src, {unmapped: true});
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td data-sort="${esc(src)}">${sourceBadgeHtml(src, sourceMeta, openHref)}</td>
      <td data-sort="${esc(s.total_events)}"><span class="badge badge-soft">${esc(s.total_events)}</span></td>
      <td data-sort="${esc(s.mapped_events)}"><span class="badge badge-soft">${esc(s.mapped_events)}</span></td>
      <td data-sort="${esc(s.unmapped_events)}"><span class="badge text-bg-light">${esc(s.unmapped_events)}</span></td>
      <td class="small-muted" data-sort="${esc(tsSortKey(s.last_seen))}">${esc(fmtTs(s.last_seen))}</td>
      <td class="no-print" data-nosort>
        <a class="btn btn-sm btn-outline-primary me-2" href="${esc(openHref)}">Open</a>
        <a class="btn btn-sm btn-outline-primary" href="${esc(unmappedHref)}">Unmapped</a>
      </td>
    `;
    tbody.appendChild(tr);
  }

  if (items.length === 0) {
    const tr = document.createElement('tr');
    const msg = filterSource ? `No sources match "${esc(filterSourceRaw)}".` : 'No sources found. Ingest some data first.';
    tr.innerHTML = `<td colspan="6" class="small-muted p-4">${msg}</td>`;
    tbody.appendChild(tr);
  }
}

async function load() {
  status.style.display = 'none';
  showTableLoading(tbody, 6, 'Loading sources…');
  try {
    const data = await apiGet(`/api/v1/sources?framework=${encodeURIComponent(framework)}`);
    sourceMeta = {};
    for (const x of data.items || []) {
      const src = String(x.source || '').trim();
      if (!src) continue;
      sourceMeta[src] = {
        source: src,
        label: String(x.label || src).trim() || src,
        color: String(x.color || '').trim(),
      };
    }
    let items = data.items || [];
    if (filterSource) {
      items = (items || []).filter((s) => String(s.source || '').toLowerCase() === filterSource);
    }
    render(items);
  } catch (e) {
    toast(status, `Failed to load sources: ${String(e)}`, 'danger');
    tbody.innerHTML = '<tr><td colspan="6" class="small-muted p-4">Failed to load sources.</td></tr>';
  }
}

function activateInitialTab() {
  const tab = String(new URLSearchParams(location.search || '').get('tab') || '').toLowerCase();
  const target = (tab === 'visualisations' || tab === 'visualizations') ? document.getElementById('sourcesVisualisationsTab') : null;
  if (target && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(target).show();
}

for (const btn of Array.from(document.querySelectorAll('#sourcesTabs [data-bs-toggle="tab"]'))) {
  btn.addEventListener('shown.bs.tab', () => {
    const name = String(btn.id || '').replace(/^sources/, '').replace(/Tab$/, '').toLowerCase();
    const url = new URL(window.location.href);
    if (name && name !== 'register') url.searchParams.set('tab', name);
    else url.searchParams.delete('tab');
    window.history.replaceState({}, '', url.toString());
  });
}

document.getElementById('reload').addEventListener('click', async () => { await load().then(activateInitialTab); });
load().then(activateInitialTab);
