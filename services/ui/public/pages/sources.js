import {
  initNavbar,
  apiGet,
  esc,
  fmtTs,
  tsSortKey,
  toast,
  enableTableSorting,
  sourceBadgeHtml,
  getCurrentFramework,
  withFramework,
  showTableLoading,
} from '/app.js';
import {
  enhanceFilterPillMultiSelect,
  selectedFilterPillValues,
  setFilterPillOptions,
} from '/pages/filter-pill-multiselect.js';

await initNavbar();
const framework = getCurrentFramework();
enableTableSorting();

const status = document.getElementById('status');
const tbody = document.getElementById('rows');
const filterEl = document.getElementById('sourcesFilter');
const typePicker = document.getElementById('sourceTypes');
const searchInput = document.getElementById('sourcesSearch');
const coverageSelect = document.getElementById('sourcesCoverage');

function splitList(value) {
  return String(value || '')
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);
}

// Filters arrive from badges and links as ?source= or ?type=, one or several.
function readFilters() {
  const params = new URLSearchParams(location.search || '');
  const coverage = String(params.get('coverage') || '');
  return {
    types: splitList(params.get('source') || params.get('type') || ''),
    q: String(params.get('q') || '').trim(),
    coverage: ['unmapped', 'mapped', 'none'].includes(coverage) ? coverage : '',
  };
}

let filters = readFilters();

enhanceFilterPillMultiSelect(typePicker, {
  title: 'Choose source types',
  description: 'Select one or more source types to show. Clear the selection to show every source.',
  buttonLabel: 'Choose source types',
  singular: 'source type',
  plural: 'source types',
  emptyText: 'All source types',
  emptySelectedText: 'No source type filters selected. All source types are shown.',
  noMatchText: 'No source types match your filter.',
  searchPlaceholder: 'Filter source types…',
  options: [],
  selectedValues: filters.types,
});
searchInput.value = filters.q;
coverageSelect.value = filters.coverage;

let sourceMeta = {};
let allItems = [];

function sourceHref(src, overrides = {}) {
  const p = new URLSearchParams();
  if (framework) p.set('framework', framework);
  if (src) p.set('source', src);
  if (overrides.tab) p.set('tab', overrides.tab);
  if (overrides.unmapped) p.set('unmapped', 'true');
  return `/source.html?${p.toString()}`;
}

function describeFilters() {
  const parts = [];
  if (filters.types.length)
    parts.push(
      filters.types.length === 1
        ? sourceMeta[filters.types[0]]?.label || filters.types[0]
        : `${filters.types.length} source types`,
    );
  if (filters.q) parts.push(`name contains “${filters.q}”`);
  if (filters.coverage === 'unmapped') parts.push('with unmapped events');
  if (filters.coverage === 'mapped') parts.push('with mapped events');
  if (filters.coverage === 'none') parts.push('fully mapped');
  return parts;
}

function renderFilterSummary() {
  const parts = describeFilters();
  if (!parts.length) {
    filterEl.replaceChildren();
    return;
  }
  filterEl.innerHTML = `
    <span class="badge badge-soft me-2">Filtered: ${esc(parts.join('; '))}</span>
    <a class="small-muted" href="${withFramework('/sources.html', framework)}">Clear filter</a>
  `;
}

function matches(s) {
  const src = String(s.source || '').toLowerCase();
  if (filters.types.length && !filters.types.some((t) => t.toLowerCase() === src)) return false;
  if (filters.q) {
    const label = String(sourceMeta[s.source]?.label || '').toLowerCase();
    const needle = filters.q.toLowerCase();
    if (!src.includes(needle) && !label.includes(needle)) return false;
  }
  if (filters.coverage === 'unmapped' && !(Number(s.unmapped_events) > 0)) return false;
  if (filters.coverage === 'mapped' && !(Number(s.mapped_events) > 0)) return false;
  if (filters.coverage === 'none' && Number(s.unmapped_events) > 0) return false;
  return true;
}

function render(items) {
  tbody.innerHTML = '';
  for (const s of items) {
    const src = s.source || 'unknown';
    const openHref = sourceHref(src);
    const unmappedHref = sourceHref(src, { unmapped: true });
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
    const parts = describeFilters();
    const msg = parts.length
      ? `No sources match "${esc(parts.join('; '))}".`
      : 'No sources found. Ingest some data first.';
    tr.innerHTML = `<td colspan="6" class="small-muted p-4">${msg}</td>`;
    tbody.appendChild(tr);
  }
}

function syncUrl() {
  const url = new URL(String(location.href), document.baseURI);
  for (const [key, value] of [
    ['source', filters.types.join(',')],
    ['q', filters.q],
    ['coverage', filters.coverage],
  ]) {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  }
  url.searchParams.delete('type');
  history.replaceState({}, '', url.pathname + url.search + url.hash);
}

function applyFilters() {
  filters = {
    types: selectedFilterPillValues(typePicker),
    q: searchInput.value.trim(),
    coverage: coverageSelect.value,
  };
  syncUrl();
  renderFilterSummary();
  render(allItems.filter(matches));
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
    allItems = data.items || [];
    // A type chosen from a link stays chosen even when it has no events yet.
    const known = new Set(Object.keys(sourceMeta));
    setFilterPillOptions(
      typePicker,
      [
        ...filters.types.filter((t) => !known.has(t)).map((t) => ({ value: t, label: t })),
        ...Object.values(sourceMeta).map((m) => ({
          value: m.source,
          label: m.label !== m.source ? `${m.label} (${m.source})` : m.source,
        })),
      ],
      { selectedValues: filters.types, pruneSelected: false },
    );
    renderFilterSummary();
    render(allItems.filter(matches));
  } catch (e) {
    toast(status, `Failed to load sources: ${String(e)}`, 'danger');
    tbody.innerHTML =
      '<tr><td colspan="6" class="small-muted p-4">Failed to load sources.</td></tr>';
  }
}

function activateInitialTab() {
  const tab = String(new URLSearchParams(location.search || '').get('tab') || '').toLowerCase();
  const target =
    tab === 'visualisations' || tab === 'visualizations'
      ? document.getElementById('sourcesVisualisationsTab')
      : null;
  if (target && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(target).show();
}

for (const btn of Array.from(document.querySelectorAll('#sourcesTabs [data-bs-toggle="tab"]'))) {
  btn.addEventListener('shown.bs.tab', () => {
    const name = String(btn.id || '')
      .replace(/^sources/, '')
      .replace(/Tab$/, '')
      .toLowerCase();
    const url = new URL(window.location.href);
    if (name && name !== 'register') url.searchParams.set('tab', name);
    else url.searchParams.delete('tab');
    window.history.replaceState({}, '', url.toString());
  });
}

typePicker.addEventListener('change', applyFilters);
coverageSelect.addEventListener('change', applyFilters);
searchInput.addEventListener('input', applyFilters);
document.getElementById('sourcesFilters').addEventListener('submit', (ev) => {
  ev.preventDefault();
  applyFilters();
});
document.getElementById('reload').addEventListener('click', async () => {
  await load().then(activateInitialTab);
});
load().then(activateInitialTab);
