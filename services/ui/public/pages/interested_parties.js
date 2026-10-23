import {
  initNavbar,
  apiGet,
  apiPost,
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
import {
  addHeadings,
  addNodeLabel,
  addZoom,
  curvedLink,
  splitLabel,
} from '/pages/bipartite-graph.js';

const me = await initNavbar();
initCollapsibleFilterSections();
const framework = getCurrentFramework();
enableTableSorting();
const canView = !!(
  me?.is_admin ||
  me?.can_view_interested_parties ||
  me?.can_manage_interested_parties ||
  me?.can_view_risks ||
  me?.can_manage_risks
);
const canManage = !!(me?.is_admin || me?.can_manage_interested_parties || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me, 'interested_party');
const params = new URLSearchParams(location.search);
const requestedTab = params.get('tab') || 'items';

const status = document.getElementById('status');
const newInterestedParty = document.getElementById('newInterestedParty');
const partyRows = document.getElementById('partyRows');
const partyItemsMeta = document.getElementById('partyItemsMeta');
const partyQ = document.getElementById('partyQ');
const partyNameFilter = document.getElementById('partyNameFilter');
const partyNatureFilter = document.getElementById('partyNatureFilter');
const reloadParties = document.getElementById('reloadParties');
const partyNameList = document.getElementById('partyNameList');
const partyNatureList = document.getElementById('partyNatureList');
const newPartyName = document.getElementById('newPartyName');
const newPartyNature = document.getElementById('newPartyNature');
const addPartyName = document.getElementById('addPartyName');
const addPartyNature = document.getElementById('addPartyNature');
const tabs = {
  items: document.getElementById('interestedPartiesTab'),
  names: document.getElementById('interestedPartyNamesTab'),
  natures: document.getElementById('interestedPartyNaturesTab'),
  visualisations: document.getElementById('interestedPartyVisualisationsTab'),
};
const controlsMatrixTarget = document.getElementById('partyControlsMatrix');
const bipartiteTargets = {
  party_communications: document.getElementById('partyCommunicationsBipartite'),
};

let meta = { events: [], when: [], with_whom: [], methods: [] };
let names = [];
let natures = [];
let parties = [];
let visualisationsLoaded = false;

function filterValues(el) {
  return selectedFilterPillValues(el);
}

function setCsvParam(urlSearchParams, key, values) {
  const cleaned = Array.from(
    new Set((values || []).map((v) => String(v || '').trim()).filter(Boolean)),
  );
  if (cleaned.length) urlSearchParams.set(key, cleaned.join(','));
}

function setupPartyFilterPickers() {
  enhanceFilterPillMultiSelect(partyNameFilter, {
    title: 'Choose interested party names',
    description:
      'Select one or more interested party names to include. Clear the selection to include all names.',
    buttonLabel: 'Choose names',
    singular: 'name',
    plural: 'names',
    emptyText: 'All names',
    emptySelectedText: 'No name filters selected. All names are included.',
    noMatchText: 'No names match your filter.',
    searchPlaceholder: 'Filter names',
  });
  enhanceFilterPillMultiSelect(partyNatureFilter, {
    title: 'Choose nature of interest values',
    description:
      'Select one or more natures of interest to include. Clear the selection to include all natures.',
    buttonLabel: 'Choose natures',
    singular: 'nature',
    plural: 'natures',
    emptyText: 'All natures',
    emptySelectedText: 'No nature filters selected. All natures are included.',
    noMatchText: 'No natures match your filter.',
    searchPlaceholder: 'Filter natures',
  });
}

function setTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (!tabName || tabName === 'items') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}${p.toString() ? `?${p.toString()}` : ''}`);
}
function activateTab(tabName) {
  // Own tab names only; inherited names such as "constructor" select Items.
  const tab = (Object.hasOwn(tabs, tabName) && tabs[tabName]) || tabs.items;
  if (tab && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(tab).show();
}
function wireTabs() {
  tabs.items?.addEventListener('shown.bs.tab', () => setTabQuery('items'));
  tabs.names?.addEventListener('shown.bs.tab', () => setTabQuery('names'));
  tabs.natures?.addEventListener('shown.bs.tab', () => setTabQuery('natures'));
  tabs.visualisations?.addEventListener('shown.bs.tab', () => {
    setTabQuery('visualisations');
    renderVisualisations().catch((e) =>
      toast(status, `Failed to load visualisations: ${String(e)}`, 'danger'),
    );
  });
  activateTab(requestedTab || 'items');
}
function partyHref(id) {
  return withFramework(`/interested_party.html?id=${encodeURIComponent(id || '')}`, framework);
}
function controlHref(id) {
  return withFramework(
    `/control.html?id=${encodeURIComponent(id || '')}&tab=interested-parties`,
    framework,
  );
}
function fillFilters() {
  setFilterPillOptions(
    partyNameFilter,
    names.map((x) => ({ value: String(x.id), label: x.name || '', title: x.name || '' })),
    { selectedValues: filterValues(partyNameFilter), dispatchChange: false },
  );
  setFilterPillOptions(
    partyNatureFilter,
    natures.map((x) => ({ value: String(x.id), label: x.name || '', title: x.name || '' })),
    { selectedValues: filterValues(partyNatureFilter), dispatchChange: false },
  );
}
function renderParties() {
  if (!partyRows) return;
  if (!canView) {
    partyRows.innerHTML =
      '<tr><td colspan="6" class="p-4 small-muted">You do not have permission to view interested parties.</td></tr>';
    return;
  }
  if (!parties.length) {
    partyRows.innerHTML =
      '<tr><td colspan="6" class="p-4 text-center small-muted">No interested parties match.</td></tr>';
    return;
  }
  partyRows.innerHTML = parties
    .map((it) => {
      const controls =
        (it.controls || [])
          .map(
            (c) =>
              `<a class="badge text-bg-light text-decoration-none me-1 mb-1" href="${esc(controlHref(c.id))}">${esc(c.ref || c.title || 'Control')}</a>`,
          )
          .join('') || '<span class="small-muted">-</span>';
      const comms =
        (it.communications || [])
          .map((c) => `${esc(c.event || '')} / ${esc(c.with_whom || '')}`)
          .slice(0, 3)
          .join('<br>') || '<span class="small-muted">-</span>';
      const note = (it.note || '').trim()
        ? esc(shorten(it.note, 110))
        : '<span class="small-muted">-</span>';
      return `<tr><td><a class="fw-semibold" href="${esc(partyHref(it.id))}">${esc(it.name?.name || '')}</a></td><td>${esc(it.nature?.name || '')}</td><td class="small text-wrap" style="min-width:220px;">${note}</td><td>${controls}</td><td class="small">${comms}</td><td class="no-print"><div class="d-flex flex-wrap gap-1"><a class="btn btn-sm btn-outline-primary" href="${esc(partyHref(it.id))}">${canManage ? 'Edit' : 'View'}</a>${canSampleAudits ? `<button class="btn btn-sm btn-outline-success" type="button" data-sample-party="${esc(it.id)}"><i class="bi bi-clipboard2-plus" aria-hidden="true"></i><span class="visually-hidden">Sample into audit</span></button>` : ''}</div></td></tr>`;
    })
    .join('');

  partyRows.querySelectorAll('[data-sample-party]').forEach((btn) =>
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      const id = btn.getAttribute('data-sample-party') || '';
      const party = (parties || []).find((x) => String(x.id) === String(id));
      openAuditSampleModal({
        me,
        entityType: 'interested_party',
        entityId: id,
        title: party
          ? `${party.name?.name || ''} - ${party.nature?.name || ''}`
          : 'Interested party',
        framework,
        statusEl: status,
      });
    }),
  );
}
function renderLookups() {
  const renderList = (rows, target, type) => {
    if (!target) return;
    if (!rows.length) {
      target.innerHTML =
        '<div class="border rounded p-3 bg-light small-muted">No entries yet.</div>';
      return;
    }
    target.innerHTML = `<div class="table-responsive"><table class="table table-sm align-middle mb-0"><tbody>${rows.map((row) => `<tr><td class="fw-semibold">${esc(row.name || '')}</td><td class="text-end no-print" style="width:100px;">${canManage ? `<button class="btn btn-sm btn-outline-danger" data-delete-${type}="${esc(row.id)}" type="button">Delete</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
  };
  renderList(names, partyNameList, 'name');
  renderList(natures, partyNatureList, 'nature');
}
async function loadLookups() {
  const [nameData, natureData] = await Promise.all([
    apiGet('/api/v1/interested-parties/names'),
    apiGet('/api/v1/interested-parties/natures'),
  ]);
  names = nameData.items || [];
  natures = natureData.items || [];
  fillFilters();
  renderLookups();
}
async function loadParties() {
  if (partyRows) showTableLoading(partyRows, 6, 'Loading interested parties');
  const qs = new URLSearchParams({ framework, limit: '5000' });
  if (partyQ?.value.trim()) qs.set('q', partyQ.value.trim());
  setCsvParam(qs, 'name_id', filterValues(partyNameFilter));
  setCsvParam(qs, 'nature_id', filterValues(partyNatureFilter));
  const data = await apiGet(`/api/v1/interested-parties?${qs.toString()}`);
  parties = data.items || [];
  if (partyItemsMeta)
    partyItemsMeta.textContent = `${parties.length} of ${data.total ?? parties.length} interested parties`;
  renderParties();
}
function nodeHref(node) {
  if (!node) return '';
  if (node.kind === 'party') return partyHref(node.id);
  if (node.kind === 'control') return controlHref(node.id);
  return '';
}
function relationshipSummary(graph) {
  const stats = graph?.stats || {};
  const leftLabel = (graph?.left_label || 'left items').toLowerCase();
  const rightLabel = (graph?.right_label || 'right items').toLowerCase();
  return `${stats.left_count || 0} ${leftLabel}, ${stats.right_count || 0} ${rightLabel}, ${stats.link_count || 0} relationship(s)`;
}
function estimatedLabelChars(nodes, fallback = 30) {
  const lengths = (nodes || []).map(
    (node) =>
      String(primaryNodeLabel(node) || '').length +
      (String(node?.subtitle || node?.ref || '').length ? 8 : 0),
  );
  return Math.max(fallback, Math.min(58, lengths.length ? Math.max(...lengths) : fallback));
}
function primaryNodeLabel(node) {
  if (!node) return '-';
  if (node.kind === 'control' && node.ref) return node.ref;
  return node.label || '-';
}
function secondaryNodeLabel(node) {
  if (!node) return '';
  if (node.kind === 'control') {
    const ref = String(node.ref || '').trim();
    const label = String(node.label || '').trim();
    const title = String(node.subtitle || '').trim();
    if (title) return title;
    return ref && label.startsWith(ref) ? label.slice(ref.length).trim() : label;
  }
  return node.subtitle || node.ref || '';
}
function renderControlsMatrix(el, graph) {
  if (!el) return;
  const rows = graph?.nodes?.left || [];
  const cols = graph?.nodes?.right || [];
  const links = graph?.links || [];
  if (!rows.length || !cols.length || !links.length) {
    el.textContent = graph?.empty || 'No control mapping data yet.';
    return;
  }
  const linkMap = new Map(links.map((link) => [`${link.source}::${link.target}`, link]));
  el.classList.add('interested-party-control-matrix');
  el.innerHTML = `
    <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3 no-print">
      <div class="small-muted">${esc(relationshipSummary(graph))}. Scroll horizontally to review all linked controls.</div>
      <input class="form-control form-control-sm" data-control-matrix-filter type="search" placeholder="Filter parties or controls" style="max-width:320px;">
    </div>
    <div data-control-matrix-host></div>
  `;
  const input = el.querySelector('[data-control-matrix-filter]');
  const host = el.querySelector('[data-control-matrix-host]');
  const renderTable = () => {
    const q = (input?.value || '').trim().toLowerCase();
    const rowMatches = new Set();
    const colMatches = new Set();
    if (q) {
      for (const row of rows) {
        const haystack = `${row.label || ''} ${row.subtitle || ''} ${row.note || ''}`.toLowerCase();
        if (haystack.includes(q)) rowMatches.add(row.id);
      }
      for (const col of cols) {
        const haystack = `${col.ref || ''} ${col.label || ''} ${col.subtitle || ''}`.toLowerCase();
        if (haystack.includes(q)) colMatches.add(col.id);
      }
    }
    const visibleCols = !q || rowMatches.size ? cols : cols.filter((col) => colMatches.has(col.id));
    const visibleColIds = new Set(visibleCols.map((col) => col.id));
    const visibleRows = !q
      ? rows
      : rowMatches.size
        ? rows.filter((row) => rowMatches.has(row.id))
        : rows.filter((row) =>
            links.some((link) => link.source === row.id && visibleColIds.has(link.target)),
          );
    if (!visibleRows.length || !visibleCols.length) {
      host.innerHTML =
        '<div class="border rounded p-3 bg-light small-muted">No parties or controls match that filter.</div>';
      return;
    }
    host.innerHTML = `
      <div class="interested-party-control-matrix-wrap">
        <table class="table table-sm table-bordered align-middle mb-0 interested-party-control-matrix-table">
          <thead>
            <tr>
              <th class="matrix-sticky-col matrix-corner">Interested party</th>
              ${visibleCols.map((col) => `<th class="matrix-control-header" title="${esc(col.label || col.ref || 'Control')}${col.subtitle ? ` - ${esc(col.subtitle)}` : ''}"><a href="${esc(controlHref(col.id))}" class="matrix-control-link">${esc(col.ref || col.label || 'Control')}</a></th>`).join('')}
            </tr>
          </thead>
          <tbody>
            ${visibleRows
              .map((row) => {
                const linkedCount = visibleCols.reduce(
                  (total, col) => total + (linkMap.has(`${row.id}::${col.id}`) ? 1 : 0),
                  0,
                );
                const note = (row.note || '').trim();
                return `<tr>
                <th class="matrix-sticky-col matrix-party-label">
                  <a class="fw-semibold" href="${esc(partyHref(row.id))}">${esc(row.label || 'Interested party')}</a>
                  <div class="small-muted">${esc(row.subtitle || '')}${linkedCount ? ` · ${linkedCount} shown control(s)` : ''}</div>
                  ${note ? `<div class="small-muted matrix-party-note" title="${esc(note)}">${esc(shorten(note, 90))}</div>` : ''}
                </th>
                ${visibleCols
                  .map((col) => {
                    const link = linkMap.get(`${row.id}::${col.id}`);
                    if (!link)
                      return '<td class="matrix-cell matrix-cell-empty" aria-label="Not linked"></td>';
                    const title = `${row.label || 'Interested party'} × ${col.label || col.ref || 'Control'}`;
                    return `<td class="matrix-cell matrix-cell-linked" title="${esc(title)}"><a href="${esc(controlHref(col.id))}" aria-label="${esc(title)}"><i class="bi bi-check-lg" aria-hidden="true"></i><span class="visually-hidden">Linked</span></a></td>`;
                  })
                  .join('')}
              </tr>`;
              })
              .join('')}
          </tbody>
        </table>
      </div>
    `;
  };
  input?.addEventListener('input', debounce(renderTable, 120));
  renderTable();
}

function renderBipartiteGraph(el, graph) {
  if (!el) return;
  if (!window.d3) {
    el.textContent = 'D3 is not available.';
    return;
  }
  const leftNodes = graph?.nodes?.left || [];
  const rightNodes = graph?.nodes?.right || [];
  const links = graph?.links || [];
  if (!leftNodes.length || !rightNodes.length || !links.length) {
    el.textContent = graph?.empty || 'No relationship data yet.';
    return;
  }
  el.innerHTML = '';
  el.classList.add('interested-party-bipartite-viz');

  const compact = el.clientWidth && el.clientWidth < 720;
  const viewportWidth = Math.max(compact ? 420 : 620, Math.floor(el.clientWidth || 900));
  const viewportHeight = Math.max(
    el.classList.contains('pestle-viz-box-lg') ? 500 : 340,
    Math.floor(el.clientHeight || 0),
  );
  const rowCount = Math.max(leftNodes.length, rightNodes.length, 4);
  const rowGap = compact ? 56 : 58;
  const topPad = 70;
  const bottomPad = 36;
  const contentHeight = Math.max(280, rowCount * rowGap + topPad + bottomPad);
  const leftLabelChars = estimatedLabelChars(leftNodes, compact ? 24 : 34);
  const rightLabelChars = estimatedLabelChars(rightNodes, compact ? 28 : 38);
  const leftColumnWidth = Math.max(
    compact ? 150 : 230,
    Math.min(compact ? 260 : 380, leftLabelChars * (compact ? 6.2 : 7.0)),
  );
  const rightColumnWidth = Math.max(
    compact ? 180 : 260,
    Math.min(compact ? 300 : 430, rightLabelChars * (compact ? 6.2 : 7.0)),
  );
  const middleWidth = Math.max(compact ? 230 : 360, Math.min(640, viewportWidth * 0.52));
  const contentWidth = Math.ceil(leftColumnWidth + middleWidth + rightColumnWidth + 56);
  const leftX = Math.round(leftColumnWidth + 18);
  const rightX = Math.round(leftX + middleWidth);
  const leftMap = new Map(leftNodes.map((node) => [node.id, node]));
  const rightMap = new Map(rightNodes.map((node) => [node.id, node]));
  const leftY = d3
    .scalePoint()
    .domain(leftNodes.map((node) => node.id))
    .range([topPad + 28, contentHeight - bottomPad])
    .padding(0.6);
  const rightY = d3
    .scalePoint()
    .domain(rightNodes.map((node) => node.id))
    .range([topPad + 28, contentHeight - bottomPad])
    .padding(0.6);
  const maxCount = d3.max(links, (link) => link.count || 1) || 1;
  const strokeWidth = d3.scaleLinear().domain([1, maxCount]).range([1.3, 5.5]);

  const svg = d3
    .select(el)
    .append('svg')
    .attr('width', '100%')
    .attr('height', viewportHeight)
    .attr('viewBox', `0 0 ${viewportWidth} ${viewportHeight}`)
    .attr('preserveAspectRatio', 'xMidYMid meet')
    .attr('role', 'img')
    .attr('aria-label', graph?.title || 'Interested party bipartite graph');
  const scene = svg.append('g').attr('class', 'interested-party-bipartite-scene');
  addZoom(el, svg, scene, { contentWidth, contentHeight, viewportWidth, viewportHeight });
  addHeadings(scene, {
    leftX,
    rightX,
    titles: [graph?.left_label || 'Interested parties', graph?.right_label || 'Related items'],
    summary: relationshipSummary(graph),
    y: 22,
    summaryY: 43,
  });
  scene
    .append('g')
    .attr('fill', 'none')
    .selectAll('path')
    .data(links)
    .join('path')
    .attr('d', curvedLink(leftX, rightX, leftY, rightY, topPad))
    .attr('stroke', 'currentColor')
    .attr('stroke-opacity', 0.23)
    .attr('stroke-width', (link) => strokeWidth(link.count || 1))
    .append('title')
    .text((link) => {
      const left = leftMap.get(link.source)?.label || 'Interested party';
      const right = rightMap.get(link.target)?.label || 'Related item';
      return `${left} × ${right}${link.count > 1 ? ` (${link.count})` : ''}`;
    });

  const renderNode = (selection, side) => {
    const isLeft = side === 'left';
    const x = isLeft ? leftX : rightX;
    const yScale = isLeft ? leftY : rightY;
    const maxPrimaryChars = isLeft ? (compact ? 24 : 36) : compact ? 28 : 42;
    const maxSecondaryChars = isLeft ? (compact ? 22 : 34) : compact ? 30 : 48;
    selection
      .attr('class', 'interested-party-bipartite-node')
      .attr('transform', (node) => `translate(${x},${yScale(node.id) || topPad})`);
    selection
      .append('circle')
      .attr('r', (node) => (node.kind === 'party' ? 7 : 6))
      .attr('fill', 'currentColor')
      .attr('opacity', (node) => (node.kind === 'party' ? 0.82 : 0.58));
    selection.each(function (node) {
      const primary = `${primaryNodeLabel(node)}${node.count > 1 ? ` (${node.count})` : ''}`;
      const secondary = secondaryNodeLabel(node);
      addNodeLabel(d3.select(this), {
        left: isLeft,
        primaryLines: splitLabel(primary, maxPrimaryChars, 2),
        secondaryLines: secondary ? splitLabel(secondary, maxSecondaryChars, 1) : [],
        secondaryY: 18,
        href: nodeHref(node),
      });
    });
    selection
      .append('title')
      .text(
        (node) =>
          `${node.label || ''}${node.subtitle ? `\n${node.subtitle}` : ''}${node.count ? `\nRelationships: ${node.count}` : ''}`,
      );
  };
  scene.append('g').selectAll('g').data(leftNodes).join('g').call(renderNode, 'left');
  scene.append('g').selectAll('g').data(rightNodes).join('g').call(renderNode, 'right');
}

async function renderVisualisations() {
  if (visualisationsLoaded) return;
  const data = await apiGet(
    `/api/v1/interested-parties/visualisations?framework=${encodeURIComponent(framework)}`,
  );
  renderControlsMatrix(controlsMatrixTarget, data.bipartite_graphs?.party_controls);
  for (const [key, target] of Object.entries(bipartiteTargets))
    renderBipartiteGraph(target, data.bipartite_graphs?.[key]);
  visualisationsLoaded = true;
}
async function addLookup(type) {
  const input = type === 'name' ? newPartyName : newPartyNature;
  const value = (input?.value || '').trim();
  if (!value) return;
  await apiPost(`/api/v1/interested-parties/${type === 'name' ? 'names' : 'natures'}`, {
    name: value,
  });
  input.value = '';
  await loadLookups();
  toast(status, `${type === 'name' ? 'Name' : 'Nature'} added.`, 'success');
}
async function deleteLookup(type, id) {
  await apiDelete(
    `/api/v1/interested-parties/${type === 'name' ? 'names' : 'natures'}/${encodeURIComponent(id)}`,
  );
  await loadLookups();
  toast(status, `${type === 'name' ? 'Name' : 'Nature'} deleted.`, 'success');
}
function wireEvents() {
  if (newInterestedParty && !canManage) newInterestedParty.style.display = 'none';
  addPartyName?.addEventListener('click', () =>
    addLookup('name').catch((e) => toast(status, String(e), 'danger')),
  );
  addPartyNature?.addEventListener('click', () =>
    addLookup('nature').catch((e) => toast(status, String(e), 'danger')),
  );
  reloadParties?.addEventListener('click', () =>
    loadParties().catch((e) => toast(status, String(e), 'danger')),
  );
  const debounced = debounce(
    () => loadParties().catch((e) => toast(status, String(e), 'danger')),
    250,
  );
  partyQ?.addEventListener('input', debounced);
  partyNameFilter?.addEventListener('change', () =>
    loadParties().catch((e) => toast(status, String(e), 'danger')),
  );
  partyNatureFilter?.addEventListener('change', () =>
    loadParties().catch((e) => toast(status, String(e), 'danger')),
  );
  document.addEventListener('click', (ev) => {
    const n = ev.target.closest('[data-delete-name]');
    const t = ev.target.closest('[data-delete-nature]');
    if (n)
      deleteLookup('name', n.dataset.deleteName).catch((e) => toast(status, String(e), 'danger'));
    if (t)
      deleteLookup('nature', t.dataset.deleteNature).catch((e) =>
        toast(status, String(e), 'danger'),
      );
  });
}

try {
  meta = await apiGet(`/api/v1/interested-parties/meta?framework=${encodeURIComponent(framework)}`);
  setupPartyFilterPickers();
  wireTabs();
  wireEvents();
  await loadLookups();
  await loadParties();
  if (requestedTab === 'visualisations') await renderVisualisations();
} catch (e) {
  toast(status, `Failed to load interested parties: ${String(e)}`, 'danger');
}
