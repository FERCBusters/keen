import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  esc,
  toast,
  debounce,
  enableTableSorting,
  getCurrentFramework,
  withFramework,
  showTableLoading,
  shorten,
  initCollapsibleFilterSections,
  canSampleIntoAudit,
  openAuditSampleModal,
} from '/app.js';
import {
  enhanceFilterPillMultiSelect,
  selectedFilterPillValues,
  setFilterPillOptions,
} from '/pages/filter-pill-multiselect.js';

const me = await initNavbar();
initCollapsibleFilterSections();
const framework = getCurrentFramework();
enableTableSorting();

const canViewPestle = !!(me?.is_admin || me?.can_view_pestle || me?.can_manage_pestle || me?.can_view_risks || me?.can_manage_risks);
const canManagePestle = !!(me?.is_admin || me?.can_manage_pestle || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me);

const params = new URLSearchParams(location.search);
const requestedItemId = params.get('id') || '';
const requestedTab = params.get('tab') || '';
if (requestedItemId) {
  const targetTab = requestedTab === 'editor' ? 'details' : requestedTab;
  const target = `/pestle_item.html?id=${encodeURIComponent(requestedItemId)}${targetTab ? `&tab=${encodeURIComponent(targetTab)}` : ''}`;
  location.replace(withFramework(target, framework));
}
let visualisationsLoaded = false;

const status = document.getElementById('status');
const newPestleItem = document.getElementById('newPestleItem');
const pestleItemsTab = document.getElementById('pestleItemsTab');
const businessProcessesTab = document.getElementById('businessProcessesTab');
const pestleVisualisationsTab = document.getElementById('pestleVisualisationsTab');

const pestleQ = document.getElementById('pestleQ');
const pestleTypeFilter = document.getElementById('pestleTypeFilter');
const pestleLensFilter = document.getElementById('pestleLensFilter');
const pestleRelevanceFilter = document.getElementById('pestleRelevanceFilter');
const reloadPestle = document.getElementById('reloadPestle');
const pestleRows = document.getElementById('pestleRows');
const pestleItemsMeta = document.getElementById('pestleItemsMeta');
const pestleLensTypeViz = document.getElementById('pestleLensTypeViz');
const pestleRelevanceVenn = document.getElementById('pestleRelevanceVenn');
const pestleClauseNetwork = document.getElementById('pestleClauseNetwork');

const businessProcessEditorCard = document.getElementById('businessProcessEditorCard');
const businessProcessName = document.getElementById('businessProcessName');
const addBusinessProcess = document.getElementById('addBusinessProcess');
const businessProcessList = document.getElementById('businessProcessList');

let meta = {types: [], lenses: [], relevance_levels: []};
let items = [];
let businessProcesses = [];

function filterValues(el) {
  return selectedFilterPillValues(el);
}

function setCsvParam(url, key, values) {
  const cleaned = Array.from(new Set((values || []).map((v) => String(v || '').trim()).filter(Boolean)));
  if (cleaned.length) url.searchParams.set(key, cleaned.join(','));
}

function setupPestleFilterPickers() {
  enhanceFilterPillMultiSelect(pestleTypeFilter, {
    title: 'Choose PESTLE(E) types',
    description: 'Select one or more PESTLE(E) types to include. Clear the selection to include all types.',
    buttonLabel: 'Choose types',
    singular: 'type',
    plural: 'types',
    emptyText: 'All types',
    emptySelectedText: 'No type filters selected. All types are included.',
    noMatchText: 'No PESTLE(E) types match your filter.',
    searchPlaceholder: 'Filter types…',
  });
  enhanceFilterPillMultiSelect(pestleLensFilter, {
    title: 'Choose PESTLE(E) lenses',
    description: 'Select one or more lenses to include. Clear the selection to include every lens.',
    buttonLabel: 'Choose lenses',
    singular: 'lens',
    plural: 'lenses',
    emptyText: 'All lenses',
    emptySelectedText: 'No lens filters selected. All lenses are included.',
    noMatchText: 'No lenses match your filter.',
    searchPlaceholder: 'Filter lenses…',
  });
  enhanceFilterPillMultiSelect(pestleRelevanceFilter, {
    title: 'Choose relevance levels',
    description: 'Select one or more overall relevance levels to include. Clear the selection to include all relevance levels.',
    buttonLabel: 'Choose relevance',
    singular: 'relevance level',
    plural: 'relevance levels',
    emptyText: 'All relevance',
    emptySelectedText: 'No relevance filters selected. All relevance levels are included.',
    noMatchText: 'No relevance levels match your filter.',
    searchPlaceholder: 'Filter relevance…',
  });
}


function setTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (!tabName || tabName === 'items') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}${p.toString() ? `?${p.toString()}` : ''}`);
}

function activateTab(tabName) {
  const tab = {
    items: pestleItemsTab,
    processes: businessProcessesTab,
    visualisations: pestleVisualisationsTab,
  }[tabName || 'items'] || pestleItemsTab;
  if (!tab || !window.bootstrap?.Tab) return;
  window.bootstrap.Tab.getOrCreateInstance(tab).show();
}

function wireTabs() {
  pestleItemsTab?.addEventListener('shown.bs.tab', () => setTabQuery('items'));
  businessProcessesTab?.addEventListener('shown.bs.tab', () => setTabQuery('processes'));
  pestleVisualisationsTab?.addEventListener('shown.bs.tab', () => {
    setTabQuery('visualisations');
    renderPestleVisualisations().catch((e) => toast(status, `Failed to load visualisations: ${String(e)}`, 'danger'));
  });
  activateTab(requestedTab === 'editor' ? 'items' : requestedTab || 'items');
}

function relevanceLabel(rowOrCode) {
  if (typeof rowOrCode === 'object' && rowOrCode) return rowOrCode.label || 'N/A';
  const code = String(rowOrCode || 'na');
  return meta.relevance_levels.find((x) => x.code === code)?.label || 'N/A';
}

function relevanceOptions(selectedCodeOrId = '') {
  const selected = selectedCodeOrId === null || selectedCodeOrId === undefined ? '' : String(selectedCodeOrId);
  return (meta.relevance_levels || []).map((r) => {
    const isSelected = selected !== '' && (selected === String(r.id) || selected === String(r.code));
    return `<option value="${esc(r.code)}" ${isSelected ? 'selected' : ''}>${esc(r.label)}</option>`;
  }).join('');
}

function fillSelects() {
  setFilterPillOptions(
    pestleTypeFilter,
    (meta.types || []).map((x) => ({value: String(x), label: String(x)})),
    {selectedValues: filterValues(pestleTypeFilter), dispatchChange: false}
  );
  setFilterPillOptions(
    pestleLensFilter,
    (meta.lenses || []).map((x) => ({value: String(x), label: String(x)})),
    {selectedValues: filterValues(pestleLensFilter), dispatchChange: false}
  );
  setFilterPillOptions(
    pestleRelevanceFilter,
    (meta.relevance_levels || []).map((r) => ({value: String(r.code), label: String(r.label || r.code), title: String(r.label || r.code)})),
    {selectedValues: filterValues(pestleRelevanceFilter), dispatchChange: false}
  );
}

function pestleItemHref(id, tab = 'details') {
  const path = `/pestle_item.html?id=${encodeURIComponent(id || '')}${tab ? `&tab=${encodeURIComponent(tab)}` : ''}`;
  return withFramework(path, framework);
}

function badgeForRelevance(rel) {
  const code = rel?.code || rel || 'na';
  let cls = 'text-bg-light';
  if (code === 'high') cls = 'text-bg-danger';
  else if (code === 'medium') cls = 'text-bg-warning';
  else if (code === 'low') cls = 'text-bg-success';
  return `<span class="badge ${cls}">${esc(relevanceLabel(rel))}</span>`;
}

function renderItems() {
  if (!pestleRows) return;
  if (!canViewPestle) {
    pestleRows.innerHTML = '<tr><td colspan="8" class="p-4 small-muted">You do not have permission to view PESTLE(E) assessments.</td></tr>';
    return;
  }
  if (!items.length) {
    pestleRows.innerHTML = '<tr><td colspan="8" class="p-4 text-center small-muted">No PESTLE(E) items match.</td></tr>';
    return;
  }
  pestleRows.innerHTML = items.map((it) => {
    const href = pestleItemHref(it.id);
    return `<tr>
      <td>${esc(it.type || '')}</td>
      <td>${esc(it.lens || '')}</td>
      <td><a class="fw-semibold" href="${esc(href)}">${esc(shorten(it.item || '', 160))}</a></td>
      <td>${badgeForRelevance(it.overall_relevance)}</td>
      <td class="wrap small-muted">${esc(shorten(it.rationale || '', 220) || '—')}</td>
      <td><span class="badge text-bg-light">${esc(String(it.business_process_count || 0))} process${Number(it.business_process_count || 0) === 1 ? '' : 'es'}</span></td>
      <td><span class="badge text-bg-light">${esc(String(it.clause_count || 0))} clause${Number(it.clause_count || 0) === 1 ? '' : 's'}</span></td>
      <td class="no-print"><div class="d-flex flex-wrap gap-1"><a class="btn btn-sm btn-outline-primary" href="${esc(href)}">${canManagePestle ? 'Edit' : 'View'}</a>${canSampleAudits ? `<button class="btn btn-sm btn-outline-success" type="button" data-sample-pestle="${esc(it.id)}"><i class="bi bi-clipboard2-plus" aria-hidden="true"></i><span class="visually-hidden">Sample into audit</span></button>` : ''}</div></td>
    </tr>`;
  }).join('');

  pestleRows.querySelectorAll('[data-sample-pestle]').forEach((btn) => btn.addEventListener('click', (ev) => {
    ev.preventDefault();
    const id = btn.getAttribute('data-sample-pestle') || '';
    const item = (items || []).find((x) => String(x.id) === String(id));
    openAuditSampleModal({
      me,
      entityType: 'pestle_item',
      entityId: id,
      title: item?.item || 'PESTLE(E) item',
      framework: item?.framework || framework,
      statusEl: status,
    });
  }));
}

function renderBusinessProcesses() {  if (businessProcessEditorCard) businessProcessEditorCard.style.display = canViewPestle ? '' : 'none';
  if (addBusinessProcess) addBusinessProcess.style.display = canManagePestle ? '' : 'none';
  if (businessProcessName) businessProcessName.disabled = !canManagePestle;
  if (!businessProcessList) return;
  if (!businessProcesses.length) {
    businessProcessList.innerHTML = '<div class="border rounded p-3 bg-light small-muted">No business processes have been created yet.</div>';
    return;
  }
  businessProcessList.innerHTML = `<div class="table-responsive"><table class="table table-sm align-middle mb-0">
    <thead><tr><th>Name</th><th style="width:150px;" class="no-print">Actions</th></tr></thead>
    <tbody>${businessProcesses.map((bp) => `<tr>
      <td class="fw-semibold">${esc(bp.name || '')}</td>
      <td class="no-print">${canManagePestle ? `<button class="btn btn-sm btn-outline-danger" type="button" data-delete-process="${esc(bp.id)}">Delete</button>` : '<span class="small-muted">—</span>'}</td>
    </tr>`).join('')}</tbody>
  </table></div>`;
  businessProcessList.querySelectorAll('[data-delete-process]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!canManagePestle) return;
    const id = btn.dataset.deleteProcess || '';
    const bp = businessProcesses.find((x) => String(x.id) === String(id));
    if (!confirm(`Delete business process “${bp?.name || 'this process'}”? Relevance links for it will also be removed.`)) return;
    try {
      await apiDelete(`/api/v1/pestle/business-processes/${encodeURIComponent(id)}`);
      await Promise.all([loadBusinessProcesses(), loadItems()]);
      visualisationsLoaded = false;
      toast(status, 'Business process deleted', 'success');
    } catch (e) {
      toast(status, `Failed to delete business process: ${String(e)}`, 'danger');
    }
  }));
}

function _clearViz(el, msg = '') {
  if (!el) return;
  el.innerHTML = msg || '';
}

function _svgFor(el, height = 320) {
  if (!el || !window.d3) return null;
  el.innerHTML = '';
  const width = Math.max(360, el.clientWidth || 640);
  return window.d3.select(el).append('svg')
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('width', '100%')
    .attr('height', height)
    .attr('role', 'img');
}

const PESTLE_PALETTE = ['#2563eb', '#7c3aed', '#db2777', '#ea580c', '#16a34a', '#0891b2', '#9333ea', '#f59e0b', '#0f766e', '#dc2626'];
const PESTLE_RELEVANCE_COLOURS = {
  high: '#dc2626',
  medium: '#f59e0b',
  low: '#16a34a',
  na: '#64748b',
};

function pestleColourScale(values) {
  return window.d3.scaleOrdinal()
    .domain((values || []).map((v) => String(v)))
    .range(PESTLE_PALETTE);
}

function pestleRelevanceColour(code) {
  return PESTLE_RELEVANCE_COLOURS[String(code || 'na')] || '#64748b';
}

function pestleParseRgbColour(value) {
  const raw = String(value || '').trim();
  let match = raw.match(/^rgba?\(([^)]+)\)$/i);
  if (match) {
    const parts = match[1].split(',').map((part) => part.trim());
    const r = Number.parseFloat(parts[0]);
    const g = Number.parseFloat(parts[1]);
    const b = Number.parseFloat(parts[2]);
    const a = parts.length >= 4 ? Number.parseFloat(parts[3]) : 1;
    if ([r, g, b, a].every(Number.isFinite)) return {r, g, b, a};
  }
  match = raw.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!match) return null;
  let hex = match[1];
  if (hex.length === 3) hex = hex.split('').map((ch) => ch + ch).join('');
  return {
    r: Number.parseInt(hex.slice(0, 2), 16),
    g: Number.parseInt(hex.slice(2, 4), 16),
    b: Number.parseInt(hex.slice(4, 6), 16),
    a: 1,
  };
}

function pestleEffectiveBackgroundColour(node) {
  let el = node;
  while (el && el !== document.documentElement) {
    if (el.nodeType === Node.ELEMENT_NODE) {
      const bg = pestleParseRgbColour(window.getComputedStyle(el).backgroundColor);
      if (bg && bg.a > 0.05) return bg;
    }
    el = el.parentElement;
  }
  return pestleParseRgbColour(window.getComputedStyle(document.body).backgroundColor) || {r: 255, g: 255, b: 255, a: 1};
}

function pestleRelativeLuminance({r, g, b}) {
  const linear = [r, g, b].map((channel) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
}

function pestleReadableTextColours(selection) {
  const node = selection.node();
  const host = node?.ownerSVGElement?.parentElement || node?.parentElement || document.body;
  const bg = pestleEffectiveBackgroundColour(host);
  const isLightBackground = pestleRelativeLuminance(bg) >= 0.45;
  return {
    fill: isLightBackground ? '#111827' : '#f8fafc',
    halo: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`,
  };
}

function makePestleTextReadable(selection, {halo = false, opacity = 1} = {}) {
  const colours = pestleReadableTextColours(selection);
  selection
    .attr('fill', colours.fill)
    .attr('opacity', opacity);
  if (halo) {
    selection
      .attr('stroke', colours.halo)
      .attr('stroke-width', 4)
      .attr('stroke-linejoin', 'round')
      .style('paint-order', 'stroke');
  }
  return selection;
}

function drawLensTypeViz(data) {
  const el = pestleLensTypeViz;
  const d3 = window.d3;
  const types = data?.types || meta.types || [];
  const lenses = data?.lenses || meta.lenses || [];
  const counts = data?.lens_type_counts || {};
  if (!el || !d3) return;
  if (!types.length || !lenses.length) return _clearViz(el, '<div class="small-muted">No PESTLE(E) items to visualise yet.</div>');
  const width = Math.max(420, el.clientWidth || 640);
  const height = 320;
  const margin = {top: 34, right: 16, bottom: 78, left: 96};
  const svg = _svgFor(el, height);
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const x = d3.scaleBand().domain(types).range([margin.left, margin.left + innerW]).padding(0.08);
  const y = d3.scaleBand().domain(lenses).range([margin.top, margin.top + innerH]).padding(0.12);
  const max = d3.max(lenses.flatMap((lens) => types.map((type) => Number(counts?.[lens]?.[type] || 0)))) || 1;
  const opacity = d3.scaleLinear().domain([0, max]).range([0.18, 0.9]);
  const lensColour = pestleColourScale(lenses);
  svg.append('g').selectAll('text').data(types).join('text')
    .attr('x', (d) => x(d) + x.bandwidth() / 2)
    .attr('y', margin.top + innerH + 22)
    .attr('text-anchor', 'end')
    .attr('transform', (d) => `rotate(-35 ${x(d) + x.bandwidth() / 2} ${margin.top + innerH + 22})`)
    .attr('font-size', 11)
    .text((d) => d)
    .call((sel) => makePestleTextReadable(sel, {halo: true}));
  svg.append('g').selectAll('text').data(lenses).join('text')
    .attr('x', margin.left - 10).attr('y', (d) => y(d) + y.bandwidth() / 2 + 4)
    .attr('text-anchor', 'end').attr('font-size', 12).attr('font-weight', 700)
    .text((d) => d)
    .call((sel) => makePestleTextReadable(sel, {halo: true}));
  const cells = [];
  lenses.forEach((lens) => types.forEach((type) => cells.push({lens, type, count: Number(counts?.[lens]?.[type] || 0)})));
  const g = svg.append('g');
  g.selectAll('rect').data(cells).join('rect')
    .attr('x', (d) => x(d.type)).attr('y', (d) => y(d.lens))
    .attr('width', x.bandwidth()).attr('height', y.bandwidth())
    .attr('rx', 8)
    .attr('fill', (d) => d.count ? lensColour(d.lens) : '#e2e8f0')
    .attr('opacity', (d) => d.count ? opacity(d.count) : 0.45)
    .attr('stroke', '#fff')
    .attr('stroke-width', 1.5);
  g.selectAll('text').data(cells).join('text')
    .attr('x', (d) => x(d.type) + x.bandwidth() / 2)
    .attr('y', (d) => y(d.lens) + y.bandwidth() / 2 + 5)
    .attr('text-anchor', 'middle').attr('font-size', 13).attr('font-weight', 700)
    .text((d) => d.count ? d.count : '')
    .call((sel) => makePestleTextReadable(sel, {halo: true}));
}

function drawRelevanceVenn(data) {
  const el = pestleRelevanceVenn;
  const d3 = window.d3;
  if (!el || !d3) return;
  const rows = data?.relevance_counts || [];
  const order = ['high', 'medium', 'low', 'na'];
  const byCode = new Map(rows.map((r) => [String(r.code), r]));
  const max = d3.max(order.map((code) => Number(byCode.get(code)?.count || 0))) || 1;
  const width = Math.max(420, el.clientWidth || 640);
  const height = 320;
  const svg = _svgFor(el, height);
  const radius = d3.scaleSqrt().domain([0, max]).range([28, 86]);
  const positions = [
    {code: 'high', x: width * 0.38, y: 122},
    {code: 'medium', x: width * 0.53, y: 122},
    {code: 'low', x: width * 0.455, y: 212},
    {code: 'na', x: width * 0.74, y: 212},
  ];
  const g = svg.append('g');
  g.selectAll('circle').data(positions).join('circle')
    .attr('cx', (d) => d.x).attr('cy', (d) => d.y)
    .attr('r', (d) => radius(Number(byCode.get(d.code)?.count || 0)))
    .attr('fill', (d) => pestleRelevanceColour(d.code)).attr('opacity', 0.24)
    .attr('stroke', (d) => pestleRelevanceColour(d.code)).attr('stroke-width', 3).attr('stroke-opacity', 0.78);
  g.selectAll('text.count').data(positions).join('text')
    .attr('class', 'count').attr('x', (d) => d.x).attr('y', (d) => d.y + 5)
    .attr('text-anchor', 'middle').attr('font-size', 20).attr('font-weight', 800)
    .text((d) => Number(byCode.get(d.code)?.count || 0))
    .call((sel) => makePestleTextReadable(sel, {halo: true}));
  g.selectAll('text.label').data(positions).join('text')
    .attr('class', 'label').attr('x', (d) => d.x).attr('y', (d) => d.y + radius(Number(byCode.get(d.code)?.count || 0)) + 20)
    .attr('text-anchor', 'middle').attr('font-size', 12)
    .text((d) => byCode.get(d.code)?.label || relevanceLabel(d.code))
    .call((sel) => makePestleTextReadable(sel, {halo: true}));
}

function pestleClauseHref(id) {
  return withFramework(`/clause.html?id=${encodeURIComponent(id || '')}&tab=pestle`, framework);
}

function splitPestleVizLabel(value, maxChars = 34, maxLines = 2) {
  const text = String(value || '').trim() || '—';
  if (text.length <= maxChars) return [text];
  const words = text.split(/\s+/);
  const lines = [];
  let current = '';
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length > maxChars && current) {
      lines.push(current);
      current = word;
      if (lines.length >= maxLines) break;
    } else {
      current = next;
    }
  }
  if (lines.length < maxLines && current) lines.push(current);
  if (!lines.length) lines.push(text.slice(0, maxChars));
  if (lines.join(' ').length < text.length) {
    const last = lines.length - 1;
    lines[last] = `${lines[last].slice(0, Math.max(8, maxChars - 1)).replace(/\s+$/, '')}…`;
  }
  return lines.slice(0, maxLines);
}

function addPestleBipartiteText(selection, lines, {x = 0, y = 0, anchor = 'start', fontSize = 12, weight = 600, lineHeight = 13, opacity = 1, underline = false} = {}) {
  const text = selection.append('text')
    .attr('x', x)
    .attr('y', y)
    .attr('font-size', fontSize)
    .attr('font-weight', weight)
    .attr('text-anchor', anchor)
    .attr('fill', 'currentColor')
    .attr('opacity', opacity);
  if (underline) text.style('text-decoration', 'underline');
  (lines || ['—']).forEach((line, idx) => {
    text.append('tspan')
      .attr('x', x)
      .attr('dy', idx === 0 ? 0 : lineHeight)
      .text(line);
  });
  return text;
}

function pestleVizButton(label, action, title) {
  return `<button class="viz-zoom-btn" type="button" data-zoom="${esc(action)}" title="${esc(title)}" aria-label="${esc(title)}">${esc(label)}</button>`;
}

function estimatedPestleLabelChars(nodes, fallback = 36) {
  const max = Math.max(...(nodes || []).map((node) => String(node.label || node.ref || '').length), fallback);
  return Math.min(Math.max(max, fallback), 58);
}

function clauseRelationshipSummary({clauses, items: graphItems, links}) {
  return `${clauses.length} clause${clauses.length === 1 ? '' : 's'}, ${graphItems.length} PESTLE(E) item${graphItems.length === 1 ? '' : 's'}, ${links.length} relevance mapping${links.length === 1 ? '' : 's'}`;
}

function relevanceWeight(code) {
  const c = String(code || '').toLowerCase();
  if (c === 'high') return 3;
  if (c === 'medium') return 2;
  if (c === 'low') return 1;
  return 1;
}

function drawClauseNetwork(data) {
  const el = pestleClauseNetwork;
  const d3 = window.d3;
  if (!el || !d3) return;
  const linksRaw = (data?.clause_links || [])
    .filter((link) => link?.item_id && link?.clause_id)
    .sort((a, b) => (relevanceWeight(b.relevance_code) - relevanceWeight(a.relevance_code)) || String(a.clause_ref || '').localeCompare(String(b.clause_ref || ''), undefined, {numeric: true}) || String(a.item || '').localeCompare(String(b.item || '')))
    .slice(0, 320);
  if (!linksRaw.length) return _clearViz(el, '<div class="small-muted">No non-N/A clause relevance mappings exist yet.</div>');

  el.innerHTML = '';
  el.classList.add('interested-party-bipartite-viz');

  const clauseMap = new Map();
  const itemMap = new Map();
  const links = [];
  for (const row of linksRaw) {
    const clauseId = String(row.clause_id || '');
    const itemId = String(row.item_id || '');
    if (!clauseId || !itemId) continue;
    if (!clauseMap.has(clauseId)) {
      clauseMap.set(clauseId, {
        id: clauseId,
        kind: 'clause',
        ref: row.clause_ref || '',
        label: `${row.clause_ref || ''} ${row.clause_title || ''}`.trim() || 'Clause',
        subtitle: row.clause_title || '',
        count: 0,
      });
    }
    if (!itemMap.has(itemId)) {
      itemMap.set(itemId, {
        id: itemId,
        kind: 'pestle_item',
        label: row.item || 'PESTLE(E) item',
        subtitle: [row.type || row.item_type, row.lens].filter(Boolean).join(' / '),
        type: row.type || row.item_type || 'PESTLE(E)',
        lens: row.lens || '',
        count: 0,
      });
    }
    clauseMap.get(clauseId).count += 1;
    itemMap.get(itemId).count += 1;
    links.push({
      source: clauseId,
      target: itemId,
      relevance: row.relevance_code || 'low',
      relevanceLabel: row.relevance_label || relevanceLabel(row.relevance_code),
      count: relevanceWeight(row.relevance_code),
    });
  }

  const clauses = Array.from(clauseMap.values()).sort((a, b) => String(a.ref || a.label || '').localeCompare(String(b.ref || b.label || ''), undefined, {numeric: true}));
  const graphItems = Array.from(itemMap.values()).sort((a, b) => (Number(b.count || 0) - Number(a.count || 0)) || String(a.label || '').localeCompare(String(b.label || '')));
  if (!clauses.length || !graphItems.length || !links.length) return _clearViz(el, '<div class="small-muted">No non-N/A clause relevance mappings exist yet.</div>');

  const compact = el.clientWidth && el.clientWidth < 720;
  const viewportWidth = Math.max(compact ? 460 : 720, Math.floor(el.clientWidth || 980));
  const viewportHeight = Math.max(520, Math.floor(el.clientHeight || 0));
  const rowCount = Math.max(clauses.length, graphItems.length, 4);
  const rowGap = compact ? 58 : 62;
  const topPad = 76;
  const bottomPad = 40;
  const contentHeight = Math.max(340, rowCount * rowGap + topPad + bottomPad);
  const leftLabelChars = estimatedPestleLabelChars(clauses, compact ? 26 : 36);
  const rightLabelChars = estimatedPestleLabelChars(graphItems, compact ? 28 : 42);
  const leftColumnWidth = Math.max(compact ? 170 : 260, Math.min(compact ? 300 : 430, leftLabelChars * (compact ? 6.3 : 7.0)));
  const rightColumnWidth = Math.max(compact ? 190 : 280, Math.min(compact ? 320 : 460, rightLabelChars * (compact ? 6.3 : 7.0)));
  const middleWidth = Math.max(compact ? 230 : 360, Math.min(620, viewportWidth * 0.46));
  const contentWidth = Math.ceil(leftColumnWidth + middleWidth + rightColumnWidth + 56);
  const leftX = Math.round(leftColumnWidth + 18);
  const rightX = Math.round(leftX + middleWidth);
  const clauseY = d3.scalePoint().domain(clauses.map((node) => node.id)).range([topPad + 30, contentHeight - bottomPad]).padding(0.6);
  const itemY = d3.scalePoint().domain(graphItems.map((node) => node.id)).range([topPad + 30, contentHeight - bottomPad]).padding(0.6);
  const strokeWidth = d3.scaleLinear().domain([1, 3]).range([1.4, 5.4]);
  const itemColour = pestleColourScale([...new Set(graphItems.map((node) => node.type || 'PESTLE(E)'))]);

  const controls = document.createElement('div');
  controls.className = 'viz-zoom-controls no-print';
  controls.innerHTML = [
    pestleVizButton('+', 'in', 'Zoom in'),
    pestleVizButton('−', 'out', 'Zoom out'),
    pestleVizButton('Reset', 'reset', 'Reset zoom to fit'),
  ].join('');
  el.appendChild(controls);

  const svg = d3.select(el).append('svg')
    .attr('width', '100%')
    .attr('height', viewportHeight)
    .attr('viewBox', `0 0 ${viewportWidth} ${viewportHeight}`)
    .attr('preserveAspectRatio', 'xMidYMid meet')
    .attr('role', 'img')
    .attr('aria-label', 'Clauses to PESTLE(E) items bipartite graph');
  const scene = svg.append('g').attr('class', 'pestle-clause-bipartite-scene');
  const fitScale = Math.min(1, (viewportWidth - 20) / contentWidth, (viewportHeight - 20) / contentHeight);
  const fitX = Math.max(10, (viewportWidth - contentWidth * fitScale) / 2);
  const fitY = Math.max(10, (viewportHeight - contentHeight * fitScale) / 2);
  const fitTransform = d3.zoomIdentity.translate(fitX, fitY).scale(fitScale);
  const zoom = d3.zoom()
    .scaleExtent([Math.min(0.15, fitScale), 4])
    .translateExtent([[-contentWidth, -contentHeight], [contentWidth * 2, contentHeight * 2]])
    .on('zoom', (ev) => scene.attr('transform', ev.transform));
  svg.call(zoom).on('wheel.zoom', null);
  svg.call(zoom.transform, fitTransform);
  controls.addEventListener('click', (ev) => {
    const btn = ev.target?.closest?.('[data-zoom]');
    if (!btn) return;
    const action = btn.getAttribute('data-zoom');
    if (action === 'in') svg.transition().duration(160).call(zoom.scaleBy, 1.25);
    else if (action === 'out') svg.transition().duration(160).call(zoom.scaleBy, 0.8);
    else svg.transition().duration(160).call(zoom.transform, fitTransform);
  });

  addPestleBipartiteText(scene, ['Clauses'], {x: leftX - 16, y: 24, anchor: 'end', fontSize: 13, weight: 700});
  addPestleBipartiteText(scene, ['PESTLE(E) items'], {x: rightX + 16, y: 24, anchor: 'start', fontSize: 13, weight: 700});
  scene.append('text')
    .attr('x', leftX - 16)
    .attr('y', 46)
    .attr('font-size', 12)
    .attr('fill', 'currentColor')
    .attr('opacity', 0.72)
    .attr('text-anchor', 'end')
    .text(clauseRelationshipSummary({clauses, items: graphItems, links}));

  const path = (link) => {
    const y1 = clauseY(link.source) || topPad;
    const y2 = itemY(link.target) || topPad;
    const mid = (leftX + rightX) / 2;
    return `M${leftX},${y1} C${mid},${y1} ${mid},${y2} ${rightX},${y2}`;
  };
  const linkGroup = scene.append('g').attr('fill', 'none');
  linkGroup.selectAll('path').data(links).join('path')
    .attr('d', path)
    .attr('stroke', (link) => pestleRelevanceColour(link.relevance))
    .attr('stroke-opacity', 0.34)
    .attr('stroke-width', (link) => strokeWidth(link.count || 1))
    .append('title')
    .text((link) => {
      const clause = clauseMap.get(link.source)?.label || 'Clause';
      const item = itemMap.get(link.target)?.label || 'PESTLE(E) item';
      return `${clause} → ${item}\nRelevance: ${link.relevanceLabel}`;
    });

  const renderNode = (selection, side) => {
    const isClause = side === 'clause';
    const x = isClause ? leftX : rightX;
    const yScale = isClause ? clauseY : itemY;
    const anchor = isClause ? 'end' : 'start';
    const labelX = isClause ? -14 : 14;
    const maxPrimaryChars = isClause ? (compact ? 26 : 38) : (compact ? 28 : 44);
    const maxSecondaryChars = isClause ? (compact ? 28 : 42) : (compact ? 26 : 38);
    selection.attr('class', 'interested-party-bipartite-node')
      .attr('transform', (node) => `translate(${x},${yScale(node.id) || topPad})`);
    selection.append('circle')
      .attr('r', isClause ? 6 : 7)
      .attr('fill', (node) => isClause ? '#475569' : itemColour(node.type || 'PESTLE(E)'))
      .attr('opacity', isClause ? 0.62 : 0.86);
    selection.each(function(node) {
      const group = d3.select(this);
      const href = isClause ? pestleClauseHref(node.id) : pestleItemHref(node.id);
      const primary = `${isClause ? (node.ref || node.label) : node.label}${node.count ? ` (${node.count})` : ''}`;
      const primaryLines = splitPestleVizLabel(primary, maxPrimaryChars, isClause ? 2 : 3);
      addPestleBipartiteText(group, primaryLines, {x: labelX, y: primaryLines.length > 1 ? -10 : -3, anchor, fontSize: 12, weight: 650, lineHeight: 12.5, underline: Boolean(href)});
      const secondary = isClause ? node.subtitle : node.subtitle;
      if (secondary) {
        addPestleBipartiteText(group, splitPestleVizLabel(secondary, maxSecondaryChars, 1), {x: labelX, y: primaryLines.length > 1 ? 20 : 13, anchor, fontSize: 10.5, weight: 400, opacity: 0.68});
      }
      if (href) group.style('cursor', 'pointer').on('click', () => { window.location.href = href; });
    });
    selection.append('title').text((node) => `${node.label || ''}${node.subtitle ? `\n${node.subtitle}` : ''}${node.count ? `\nMappings: ${node.count}` : ''}`);
  };
  scene.append('g').selectAll('g').data(clauses).join('g').call(renderNode, 'clause');
  scene.append('g').selectAll('g').data(graphItems).join('g').call(renderNode, 'item');
}

async function renderPestleVisualisations(force = false) {
  if (!canViewPestle || (!force && visualisationsLoaded)) return;
  if (!window.d3) {
    for (const el of [pestleLensTypeViz, pestleRelevanceVenn, pestleClauseNetwork]) _clearViz(el, '<div class="text-danger">D3 is not available.</div>');
    return;
  }
  const u = new URL('/api/v1/pestle/visualisations', location.origin);
  if (framework) u.searchParams.set('framework', framework);
  const data = await apiGet(u.pathname + u.search);
  drawLensTypeViz(data);
  drawRelevanceVenn(data);
  drawClauseNetwork(data);
  visualisationsLoaded = true;
}

async function loadMeta() {
  meta = await apiGet('/api/v1/pestle/meta');
  fillSelects();
}

async function loadItems() {
  if (!canViewPestle) return;
  showTableLoading(pestleRows, 8, 'Loading PESTLE(E) items…');
  const u = new URL('/api/v1/pestle/items', location.origin);
  if (framework) u.searchParams.set('framework', framework);
  if (pestleQ?.value) u.searchParams.set('q', pestleQ.value.trim());
  setCsvParam(u, 'type', filterValues(pestleTypeFilter));
  setCsvParam(u, 'lens', filterValues(pestleLensFilter));
  setCsvParam(u, 'relevance_code', filterValues(pestleRelevanceFilter));
  u.searchParams.set('limit', '5000');
  const data = await apiGet(u.pathname + u.search);
  items = data?.items || [];
  renderItems();
  if (pestleItemsMeta) {
    const total = Number(data?.total || items.length);
    pestleItemsMeta.textContent = `${total} PESTLE(E) item${total === 1 ? '' : 's'} for ${data?.framework || framework || 'the selected framework'}.`;
  }
}

async function loadBusinessProcesses() {
  if (!canViewPestle) return;
  const data = await apiGet('/api/v1/pestle/business-processes?limit=5000');
  businessProcesses = data?.items || [];
  renderBusinessProcesses();
}

addBusinessProcess?.addEventListener('click', async () => {
  if (!canManagePestle) return;
  const name = (businessProcessName?.value || '').trim();
  if (!name) return toast(status, 'Enter a business process name', 'warning');
  addBusinessProcess.disabled = true;
  try {
    await apiPost('/api/v1/pestle/business-processes', {name});
    if (businessProcessName) businessProcessName.value = '';
    await loadBusinessProcesses();
    visualisationsLoaded = false;
    toast(status, 'Business process added', 'success');
  } catch (e) {
    toast(status, `Failed to add business process: ${String(e)}`, 'danger');
  } finally {
    addBusinessProcess.disabled = false;
  }
});

reloadPestle?.addEventListener('click', () => loadItems().catch((e) => toast(status, `Failed to load PESTLE(E) items: ${String(e)}`, 'danger')));
pestleQ?.addEventListener('input', debounce(() => loadItems().catch(() => {}), 300));
pestleTypeFilter?.addEventListener('change', () => loadItems().catch(() => {}));
pestleLensFilter?.addEventListener('change', () => loadItems().catch(() => {}));
pestleRelevanceFilter?.addEventListener('change', () => loadItems().catch(() => {}));

if (newPestleItem) {
  newPestleItem.href = withFramework('/pestle_item.html', framework);
  newPestleItem.classList.toggle('d-none', !canManagePestle);
}

setupPestleFilterPickers();
wireTabs();

try {
  await loadMeta();
  if (!canViewPestle) {
    renderItems();
  } else {
    await Promise.all([loadItems(), loadBusinessProcesses()]);
    if (requestedTab === 'visualisations') await renderPestleVisualisations(true);
  }
} catch (e) {
  toast(status, `Failed to load PESTLE(E): ${String(e)}`, 'danger');
}
