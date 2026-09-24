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
  userPillHtml,
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
const pageName = document.body?.dataset?.page || '';
const isMitigatorPage = pageName === 'mitigator';
enableTableSorting();

const canViewRisks = !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
const canManageRisks = !!(me?.is_admin || me?.can_manage_risks);
const canManagePestle = !!(me?.is_admin || me?.can_manage_pestle || me?.can_manage_risks);
const canManageInterestedParties = !!(me?.is_admin || me?.can_manage_interested_parties || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me);

const params = new URLSearchParams(location.search);
const requestedRiskId = params.get('id') || '';
const requestedEditRiskId = params.get('edit') || '';
const requestedTab = params.get('tab') || '';
const requestedTemplateId = params.get('template') || '';
if (!isMitigatorPage && requestedTab === 'mitigator') {
  const redirect = new URL('/mitigator.html', location.origin);
  if (framework) redirect.searchParams.set('framework', framework);
  location.replace(redirect.pathname + redirect.search);
}

const status = document.getElementById('status');
const rows = document.getElementById('rows');
const riskTable = document.getElementById('riskTable');
const resultMeta = document.getElementById('resultMeta');
const q = document.getElementById('q');
const categoryFilter = document.getElementById('categoryFilter');
const subcategoryFilter = document.getElementById('subcategoryFilter');
const typeFilter = document.getElementById('typeFilter');
const reloadBtn = document.getElementById('reload');
const newRiskBtn = document.getElementById('newRisk');

const RISK_TYPE_FILTER_OPTIONS = [
  {value: 'Confidentiality', label: 'Confidentiality'},
  {value: 'Integrity', label: 'Integrity'},
  {value: 'Availability', label: 'Availability'},
];

function filterValues(el) {
  return selectedFilterPillValues(el);
}

function setCsvParam(url, key, values) {
  const cleaned = Array.from(new Set((values || []).map((v) => String(v || '').trim()).filter(Boolean)));
  if (cleaned.length) url.searchParams.set(key, cleaned.join(','));
}

function setupRiskFilterPickers() {
  enhanceFilterPillMultiSelect(categoryFilter, {
    title: 'Choose risk categories',
    description: 'Select one or more asset categories to include. Clear the selection to include every category.',
    buttonLabel: 'Choose categories',
    singular: 'category',
    plural: 'categories',
    emptyText: 'All categories',
    emptySelectedText: 'No category filters selected. All categories are included.',
    noMatchText: 'No categories match your filter.',
    searchPlaceholder: 'Filter categories…',
  });
  enhanceFilterPillMultiSelect(subcategoryFilter, {
    title: 'Choose risk subcategories',
    description: 'Select one or more asset subcategories to include. Category selections narrow this list.',
    buttonLabel: 'Choose subcategories',
    singular: 'subcategory',
    plural: 'subcategories',
    emptyText: 'All subcategories',
    emptySelectedText: 'No subcategory filters selected. All subcategories are included.',
    noMatchText: 'No subcategories match your filter.',
    searchPlaceholder: 'Filter subcategories…',
  });
  enhanceFilterPillMultiSelect(typeFilter, {
    title: 'Choose CIA risk types',
    description: 'Select one or more CIA Triad risk types to include. Clear the selection to include all types.',
    buttonLabel: 'Choose risk types',
    singular: 'risk type',
    plural: 'risk types',
    emptyText: 'All risk types',
    emptySelectedText: 'No risk type filters selected. All risk types are included.',
    noMatchText: 'No risk types match your filter.',
    searchPlaceholder: 'Filter risk types…',
    options: RISK_TYPE_FILTER_OPTIONS,
  });
}


const editorCard = document.getElementById('editorCard');
const editorTitle = document.getElementById('editorTitle');
const riskForm = document.getElementById('riskForm');
const riskId = document.getElementById('riskId');
const assetId = document.getElementById('assetId');
const selectedAssetSummary = document.getElementById('selectedAssetSummary');
const asset = document.getElementById('asset');
const assetOptions = document.getElementById('assetOptions');
const category = document.getElementById('category');
const subcategory = document.getElementById('subcategory');
const owner = document.getElementById('owner');
const threatSummary = document.getElementById('threatSummary');
const threatScore = document.getElementById('threatScore');
const vulnerabilityScore = document.getElementById('vulnerabilityScore');
const impactScore = document.getElementById('impactScore');
const riskScorePreview = document.getElementById('riskScorePreview');
const residualVulnerabilityScore = document.getElementById('residualVulnerabilityScore');
const residualImpactScore = document.getElementById('residualImpactScore');
const residualScorePreview = document.getElementById('residualScorePreview');
const controls = document.getElementById('controls');
const note = document.getElementById('note');
const deleteRisk = document.getElementById('deleteRisk');
const saveRisk = document.getElementById('saveRisk');

const categoryCard = document.getElementById('categoryCard');
const categoryList = document.getElementById('categoryList');
const manageCategoryName = document.getElementById('manageCategoryName');
const manageAddCategory = document.getElementById('manageAddCategory');
const manageSubcategoryCategory = document.getElementById('manageSubcategoryCategory');
const manageSubcategoryName = document.getElementById('manageSubcategoryName');
const manageAddSubcategory = document.getElementById('manageAddSubcategory');

const risksListTab = document.getElementById('risksListTab');
const riskVisualisationsTab = document.getElementById('riskVisualisationsTab');
const riskVisualisationsTabItem = document.getElementById('riskVisualisationsTabItem');
const controlsWithoutRisksTab = document.getElementById('controlsWithoutRisksTab');
const controlsWithoutRisksTabItem = document.getElementById('controlsWithoutRisksTabItem');
const controlsWithoutRisksRows = document.getElementById('controlsWithoutRisksRows');
const controlsWithoutRisksMeta = document.getElementById('controlsWithoutRisksMeta');
const controlsWithoutRisksQ = document.getElementById('controlsWithoutRisksQ');
const reloadControlsWithoutRisks = document.getElementById('reloadControlsWithoutRisks');
const riskVizTopRisks = document.getElementById('riskVizTopRisks');
const riskVizTopControls = document.getElementById('riskVizTopControls');
const riskVizScoreModeInputs = [...document.querySelectorAll('input[name="riskVizScoreMode"]')];
const reloadRiskVisualisations = document.getElementById('reloadRiskVisualisations');
const riskControlsGraph = document.getElementById('riskControlsGraph');
const riskControlsHeatmap = document.getElementById('riskControlsHeatmap');
const riskControlsGraphMeta = document.getElementById('riskControlsGraphMeta');
const riskControlsHeatmapMeta = document.getElementById('riskControlsHeatmapMeta');
const newRiskScenarioTab = document.getElementById('newRiskScenarioTab');
const newRiskScenarioTabItem = document.getElementById('newRiskScenarioTabItem');
const riskCategoriesTabItem = document.getElementById('riskCategoriesTabItem');

const mitigatorForm = document.getElementById('mitigatorForm');
const mitigatorIssue = document.getElementById('mitigatorIssue');
const mitigatorAsset = document.getElementById('mitigatorAsset');
const mitigatorCategory = document.getElementById('mitigatorCategory');
const mitigatorSubcategory = document.getElementById('mitigatorSubcategory');
const mitigatorClear = document.getElementById('mitigatorClear');
const mitigatorAnalyse = document.getElementById('mitigatorAnalyse');
const mitigatorResultsCard = document.getElementById('mitigatorResultsCard');
const mitigatorResultsMeta = document.getElementById('mitigatorResultsMeta');
const mitigatorSignals = document.getElementById('mitigatorSignals');
const mitigatorSuggestions = document.getElementById('mitigatorSuggestions');
const mitigatorCreateRisk = document.getElementById('mitigatorCreateRisk');
const mitigatorCreatePestle = document.getElementById('mitigatorCreatePestle');
const mitigatorCreateInterested = document.getElementById('mitigatorCreateInterested');
const mitigatorWeightOutputs = {
  Confidentiality: document.getElementById('mitigatorWeightCValue'),
  Integrity: document.getElementById('mitigatorWeightIValue'),
  Availability: document.getElementById('mitigatorWeightAValue'),
};

let categories = [];
let assets = [];
let users = [];
let allControls = [];
let currentItems = [];
let controlsWithoutRisksLoaded = false;
let riskVisualisationsLoaded = false;
let riskControlGraphData = null;
let riskVizScoreMode = riskVizScoreModeInputs.find((input) => input.checked)?.value === 'inherent' ? 'inherent' : 'residual';
let lastMitigatorAnalysis = null;

function scoreClass(score) {
  const n = Number(score || 0);
  if (n < 5) return 'text-bg-success';
  if (n <= 12) return 'text-bg-warning';
  return 'text-bg-danger';
}

function scoreBadge(score) {
  const n = Number(score || 0);
  return `<span class="badge ${scoreClass(n)}">${esc(String(n))}</span>`;
}

function scorePreviewClass(score) {
  const n = Number(score || 0);
  if (n < 5) return 'score-pill score-low';
  if (n <= 12) return 'score-pill score-medium';
  return 'score-pill score-high';
}

function clampScore(value) {
  const raw = value === null || value === undefined || value === '' ? 1 : value;
  const n = Math.floor(Number(raw));
  if (!Number.isFinite(n)) return 1;
  return Math.max(0, Math.min(5, n));
}

function scoreFieldValue(value) {
  return String(value === null || value === undefined || value === '' ? 1 : clampScore(value));
}

function syncScorePreviews() {
  const t = clampScore(threatScore?.value);
  const v = clampScore(vulnerabilityScore?.value);
  const i = clampScore(impactScore?.value);
  const rv = clampScore(residualVulnerabilityScore?.value);
  const ri = clampScore(residualImpactScore?.value);
  const risk = t * v * i;
  const residual = rv * ri;
  if (riskScorePreview) {
    riskScorePreview.textContent = String(risk);
    riskScorePreview.className = scorePreviewClass(risk);
  }
  if (residualScorePreview) {
    residualScorePreview.textContent = String(residual);
    residualScorePreview.className = scorePreviewClass(residual);
  }
}

function selectedRiskTypes() {
  return [...document.querySelectorAll('.risk-type')]
      .filter((x) => x.checked)
      .map((x) => x.value);
}

function setRiskTypes(values) {
  const set = new Set((values || []).map(String));
  for (const cb of document.querySelectorAll('.risk-type')) cb.checked = set.has(cb.value);
}

function selectedControls() {
  return [...(controls?.selectedOptions || [])].map((opt) => opt.value).filter(Boolean);
}

function setSelectedControls(items) {
  const set = new Set((items || []).map((c) => String(c.id || c.control_item_id || c)));
  for (const opt of controls?.options || []) opt.selected = set.has(String(opt.value));
}

function riskHref(id) {
  return withFramework(`/risk.html?id=${encodeURIComponent(id || '')}`, framework);
}

function riskEditHref(id) {
  return withFramework(`/risks.html?edit=${encodeURIComponent(id || '')}`, framework);
}

function controlHref(id) {
  return withFramework(`/control.html?id=${encodeURIComponent(id || '')}`, framework);
}

function pestleHref(id, tab = 'details') {
  return withFramework(`/pestle_item.html?id=${encodeURIComponent(id || '')}${tab ? `&tab=${encodeURIComponent(tab)}` : ''}`, framework);
}

function interestedPartyHref(id) {
  return withFramework(`/interested_party.html?id=${encodeURIComponent(id || '')}`, framework);
}


function clampRiskVizLimit(input, fallback) {
  const n = Math.floor(Number(input?.value || fallback));
  if (!Number.isFinite(n)) return fallback;
  return Math.max(5, Math.min(200, n));
}

function normaliseRiskVizScoreMode(value) {
  return value === 'inherent' ? 'inherent' : 'residual';
}

function riskVizScoreLabel(mode = riskVizScoreMode) {
  return normaliseRiskVizScoreMode(mode) === 'inherent' ? 'inherent risk score' : 'residual risk score';
}

function riskVizScoreShortLabel(mode = riskVizScoreMode) {
  return normaliseRiskVizScoreMode(mode) === 'inherent' ? 'Risk score' : 'Residual score';
}

function riskVizScoreValue(item, mode = riskVizScoreMode) {
  const scoreMode = normaliseRiskVizScoreMode(mode);
  const field = scoreMode === 'inherent' ? 'risk_score' : 'residual_risk_score';
  const fallback = scoreMode === 'inherent' ? item?.value : item?.risk_score;
  const n = Number(item?.[field] ?? fallback ?? 0);
  return Number.isFinite(n) ? n : 0;
}

function riskVizNodeHref(node) {
  if (node?.node_kind === 'risk' && node.risk_id) return riskHref(node.risk_id);
  if (node?.node_kind === 'control' && node.control_id) return controlHref(node.control_id);
  return '';
}

function riskVizPrimaryLabel(node) {
  if (!node) return '—';
  if (node.node_kind === 'control') return node.ref || node.label || 'Control';
  return node.label || node.asset || 'Risk';
}

function riskVizSecondaryLabel(node) {
  if (!node) return '';
  if (node.node_kind === 'control') return node.title || '';
  const parts = [node.category, node.subcategory].filter(Boolean).join(' / ');
  const types = (node.risk_types || []).join(', ');
  return [parts, types].filter(Boolean).join(' • ');
}

function truncateRiskVizText(value, max = 72) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

function splitRiskVizLabel(value, maxChars = 34, maxLines = 2) {
  const text = String(value || '—').replace(/\s+/g, ' ').trim() || '—';
  if (text.length <= maxChars) return [text];
  const words = text.split(' ');
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
    lines[last] = truncateRiskVizText(lines[last], Math.max(8, maxChars - 1));
  }
  return lines.slice(0, maxLines);
}

function addRiskVizText(selection, lines, {x = 0, y = 0, anchor = 'start', fontSize = 12, weight = 600, lineHeight = 13, opacity = 1, underline = false} = {}) {
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

function riskVizButton(label, action, title) {
  return `<button class="viz-zoom-btn" type="button" data-zoom="${esc(action)}" title="${esc(title)}" aria-label="${esc(title)}">${esc(label)}</button>`;
}

function riskControlDataset() {
  const scoreMode = normaliseRiskVizScoreMode(riskVizScoreMode);
  const data = riskControlGraphData || {nodes: [], links: []};
  const nodeById = new Map((data.nodes || []).map((node) => [node.id, node]));
  const linkedRiskIds = new Set();
  const linkedControlIds = new Set();
  for (const link of data.links || []) {
    if (nodeById.get(link.source)?.node_kind === 'risk') linkedRiskIds.add(link.source);
    if (nodeById.get(link.target)?.node_kind === 'control') linkedControlIds.add(link.target);
  }
  const risks = (data.nodes || [])
    .filter((node) => node.node_kind === 'risk' && linkedRiskIds.has(node.id))
    .sort((a, b) => (Number(b.link_count || 0) - Number(a.link_count || 0)) || (riskVizScoreValue(b, scoreMode) - riskVizScoreValue(a, scoreMode)) || String(a.label || '').localeCompare(String(b.label || '')))
    .slice(0, clampRiskVizLimit(riskVizTopRisks, 30));
  const selectedRiskIds = new Set(risks.map((node) => node.id));
  const controlsForTopRisks = new Set((data.links || []).filter((link) => selectedRiskIds.has(link.source)).map((link) => link.target));
  const controls = (data.nodes || [])
    .filter((node) => node.node_kind === 'control' && linkedControlIds.has(node.id) && controlsForTopRisks.has(node.id))
    .sort((a, b) => (Number(b.link_count || 0) - Number(a.link_count || 0)) || String(a.ref || a.label || '').localeCompare(String(b.ref || b.label || '')))
    .slice(0, clampRiskVizLimit(riskVizTopControls, 50));
  const controlIds = new Set(controls.map((node) => node.id));
  const links = (data.links || []).filter((link) => selectedRiskIds.has(link.source) && controlIds.has(link.target));
  return {risks, controls, links};
}

function renderRiskControlsGraph() {
  const el = riskControlsGraph;
  if (!el) return;
  if (!window.d3) { el.textContent = 'D3 is not available.'; return; }
  const scoreMode = normaliseRiskVizScoreMode(riskVizScoreMode);
  const {risks, controls: controlNodes, links} = riskControlDataset();
  if (!risks.length || !controlNodes.length || !links.length) {
    el.textContent = 'No risk/control mappings are available for this framework yet.';
    return;
  }
  el.innerHTML = '';
  el.classList.add('interested-party-bipartite-viz');
  const riskMap = new Map(risks.map((node) => [node.id, node]));
  const controlMap = new Map(controlNodes.map((node) => [node.id, node]));
  const compact = el.clientWidth && el.clientWidth < 720;
  const viewportWidth = Math.max(compact ? 460 : 720, Math.floor(el.clientWidth || 980));
  const rowCount = Math.max(risks.length, controlNodes.length, 4);
  const rowGap = compact ? 62 : 64;
  const topPad = 76;
  const bottomPad = 40;
  const contentHeight = Math.max(340, rowCount * rowGap + topPad + bottomPad);
  const viewportHeight = Math.max(520, Math.min(920, contentHeight));
  const leftColumnWidth = Math.max(compact ? 180 : 280, Math.min(compact ? 300 : 430, viewportWidth * 0.34));
  const rightColumnWidth = Math.max(compact ? 180 : 250, Math.min(compact ? 300 : 380, viewportWidth * 0.30));
  const middleWidth = Math.max(compact ? 220 : 330, Math.min(560, viewportWidth * 0.42));
  const contentWidth = Math.ceil(leftColumnWidth + middleWidth + rightColumnWidth + 56);
  const leftX = Math.round(leftColumnWidth + 18);
  const rightX = Math.round(leftX + middleWidth);
  const riskY = d3.scalePoint().domain(risks.map((node) => node.id)).range([topPad + 32, contentHeight - bottomPad]).padding(0.6);
  const controlY = d3.scalePoint().domain(controlNodes.map((node) => node.id)).range([topPad + 32, contentHeight - bottomPad]).padding(0.6);
  const maxScore = d3.max(links, (link) => riskVizScoreValue(link, scoreMode)) || 1;
  const strokeWidth = d3.scaleLinear().domain([0, Math.max(1, maxScore)]).range([1.5, 6.5]);

  const zoomControls = document.createElement('div');
  zoomControls.className = 'viz-zoom-controls no-print';
  zoomControls.innerHTML = [
    riskVizButton('+', 'in', 'Zoom in'),
    riskVizButton('−', 'out', 'Zoom out'),
    riskVizButton('Reset', 'reset', 'Reset zoom to fit'),
  ].join('');
  el.appendChild(zoomControls);

  const svg = d3.select(el).append('svg')
    .attr('width', '100%')
    .attr('height', viewportHeight)
    .attr('viewBox', `0 0 ${viewportWidth} ${viewportHeight}`)
    .attr('preserveAspectRatio', 'xMidYMid meet')
    .attr('role', 'img')
    .attr('aria-label', 'Risks to controls graph');
  const scene = svg.append('g').attr('class', 'risk-controls-graph-scene');
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
  zoomControls.addEventListener('click', (ev) => {
    const btn = ev.target?.closest?.('[data-zoom]');
    if (!btn) return;
    const action = btn.getAttribute('data-zoom');
    if (action === 'in') svg.transition().duration(160).call(zoom.scaleBy, 1.25);
    else if (action === 'out') svg.transition().duration(160).call(zoom.scaleBy, 0.8);
    else svg.transition().duration(160).call(zoom.transform, fitTransform);
  });

  addRiskVizText(scene, ['Risks'], {x: leftX - 16, y: 24, anchor: 'end', fontSize: 13, weight: 700});
  addRiskVizText(scene, ['Controls'], {x: rightX + 16, y: 24, anchor: 'start', fontSize: 13, weight: 700});
  scene.append('text')
    .attr('x', leftX - 16)
    .attr('y', 46)
    .attr('font-size', 12)
    .attr('fill', 'currentColor')
    .attr('opacity', 0.72)
    .attr('text-anchor', 'end')
    .text(`${risks.length} risk(s), ${controlNodes.length} control(s), ${links.length} mapping(s)`);

  const path = (link) => {
    const y1 = riskY(link.source) || topPad;
    const y2 = controlY(link.target) || topPad;
    const mid = (leftX + rightX) / 2;
    return `M${leftX},${y1} C${mid},${y1} ${mid},${y2} ${rightX},${y2}`;
  };
  scene.append('g').attr('fill', 'none').selectAll('path').data(links).join('path')
    .attr('d', path)
    .attr('stroke', (link) => riskMap.get(link.source)?.color || 'currentColor')
    .attr('stroke-opacity', 0.28)
    .attr('stroke-width', (link) => strokeWidth(riskVizScoreValue(link, scoreMode)))
    .append('title')
    .text((link) => {
      const risk = riskMap.get(link.source);
      const control = controlMap.get(link.target);
      const selectedScore = riskVizScoreValue(link, scoreMode);
      return `${riskVizPrimaryLabel(risk)} → ${riskVizPrimaryLabel(control)}\n${riskVizScoreShortLabel(scoreMode)}: ${selectedScore}\nInherent risk score: ${link.risk_score ?? link.value ?? '—'}\nResidual risk score: ${link.residual_risk_score ?? '—'}`;
    });

  const renderNode = (selection, side) => {
    const isRisk = side === 'risk';
    const x = isRisk ? leftX : rightX;
    const yScale = isRisk ? riskY : controlY;
    const anchor = isRisk ? 'end' : 'start';
    const labelX = isRisk ? -14 : 14;
    const maxPrimary = isRisk ? (compact ? 28 : 44) : (compact ? 28 : 36);
    const maxSecondary = isRisk ? (compact ? 26 : 42) : (compact ? 28 : 48);
    selection.attr('class', 'interested-party-bipartite-node')
      .attr('transform', (node) => `translate(${x},${yScale(node.id) || topPad})`);
    selection.append('circle')
      .attr('r', isRisk ? 7 : 6)
      .attr('fill', (node) => node.color || 'currentColor')
      .attr('opacity', isRisk ? 0.85 : 0.62);
    selection.each(function(node) {
      const group = d3.select(this);
      const href = riskVizNodeHref(node);
      const primary = `${riskVizPrimaryLabel(node)}${node.link_count ? ` (${node.link_count})` : ''}`;
      const primaryLines = splitRiskVizLabel(primary, maxPrimary, isRisk ? 3 : 2);
      addRiskVizText(group, primaryLines, {x: labelX, y: primaryLines.length > 1 ? -10 : -3, anchor, fontSize: 12, weight: 650, lineHeight: 12.5, underline: Boolean(href)});
      const secondary = riskVizSecondaryLabel(node);
      if (secondary) addRiskVizText(group, splitRiskVizLabel(secondary, maxSecondary, 1), {x: labelX, y: primaryLines.length > 1 ? 22 : 13, anchor, fontSize: 10.5, weight: 400, opacity: 0.68});
      if (href) group.style('cursor', 'pointer').on('click', () => { window.location.href = href; });
    });
    selection.append('title').text((node) => `${riskVizPrimaryLabel(node)}${riskVizSecondaryLabel(node) ? `\n${riskVizSecondaryLabel(node)}` : ''}`);
  };
  scene.append('g').selectAll('g').data(risks).join('g').call(renderNode, 'risk');
  scene.append('g').selectAll('g').data(controlNodes).join('g').call(renderNode, 'control');
}

function renderRiskControlsHeatmap() {
  const el = riskControlsHeatmap;
  if (!el) return;
  const scoreMode = normaliseRiskVizScoreMode(riskVizScoreMode);
  const {risks, controls: controlNodes, links} = riskControlDataset();
  if (!risks.length || !controlNodes.length || !links.length) {
    el.textContent = 'No risk/control mappings are available for this framework yet.';
    return;
  }
  const linkMap = new Map(links.map((link) => [`${link.source}::${link.target}`, link]));
  const maxScore = Math.max(1, ...links.map((link) => riskVizScoreValue(link, scoreMode)));
  const scoreLabel = riskVizScoreLabel(scoreMode);
  el.classList.add('interested-party-control-matrix');
  el.innerHTML = `
    <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3 no-print">
      <div class="small-muted">${esc(risks.length)} risk(s), ${esc(controlNodes.length)} control(s), ${esc(links.length)} mapping(s). Darker cells have higher ${esc(scoreLabel)}.</div>
      <input class="form-control form-control-sm" data-risk-control-matrix-filter type="search" placeholder="Filter risks or controls…" style="max-width:320px;">
    </div>
    <div data-risk-control-matrix-host></div>
  `;
  const input = el.querySelector('[data-risk-control-matrix-filter]');
  const host = el.querySelector('[data-risk-control-matrix-host]');
  const renderTable = () => {
    const qText = (input?.value || '').trim().toLowerCase();
    const rowMatches = new Set();
    const colMatches = new Set();
    if (qText) {
      for (const risk of risks) {
        const haystack = `${risk.label || ''} ${risk.asset || ''} ${risk.category || ''} ${risk.subcategory || ''} ${(risk.risk_types || []).join(' ')} ${risk.threat_summary || ''}`.toLowerCase();
        if (haystack.includes(qText)) rowMatches.add(risk.id);
      }
      for (const control of controlNodes) {
        const haystack = `${control.ref || ''} ${control.label || ''} ${control.title || ''}`.toLowerCase();
        if (haystack.includes(qText)) colMatches.add(control.id);
      }
    }
    const visibleCols = !qText || rowMatches.size ? controlNodes : controlNodes.filter((node) => colMatches.has(node.id));
    const visibleColIds = new Set(visibleCols.map((node) => node.id));
    const visibleRows = !qText
      ? risks
      : rowMatches.size
        ? risks.filter((node) => rowMatches.has(node.id))
        : risks.filter((node) => links.some((link) => link.source === node.id && visibleColIds.has(link.target)));
    if (!visibleRows.length || !visibleCols.length) {
      host.innerHTML = '<div class="border rounded p-3 bg-light small-muted">No risks or controls match that filter.</div>';
      return;
    }
    host.innerHTML = `
      <div class="interested-party-control-matrix-wrap risk-control-heatmap-wrap">
        <table class="table table-sm table-bordered align-middle mb-0 interested-party-control-matrix-table risk-control-heatmap-table">
          <thead>
            <tr>
              <th class="matrix-sticky-col matrix-corner">Risk scenario</th>
              ${visibleCols.map((control) => `<th class="matrix-control-header" title="${esc(control.ref || control.label || 'Control')} — ${esc(control.title || '')}"><a href="${esc(controlHref(control.control_id || ''))}" class="matrix-control-link">${esc(control.ref || control.label || 'Control')}</a></th>`).join('')}
            </tr>
          </thead>
          <tbody>
            ${visibleRows.map((risk) => {
              const linkedCount = visibleCols.reduce((total, control) => total + (linkMap.has(`${risk.id}::${control.id}`) ? 1 : 0), 0);
              const subtitle = riskVizSecondaryLabel(risk);
              return `<tr>
                <th class="matrix-sticky-col matrix-party-label">
                  <a class="fw-semibold" href="${esc(riskHref(risk.risk_id || ''))}">${esc(truncateRiskVizText(riskVizPrimaryLabel(risk), 92))}</a>
                  <div class="small-muted">${esc(subtitle)}${linkedCount ? ` • ${linkedCount} shown control(s)` : ''}</div>
                  <div class="small-muted">Risk score: ${esc(String(risk.risk_score ?? '—'))} • Residual: ${esc(String(risk.residual_risk_score ?? '—'))}</div>
                </th>
                ${visibleCols.map((control) => {
                  const link = linkMap.get(`${risk.id}::${control.id}`);
                  if (!link) return '<td class="matrix-cell matrix-cell-empty" aria-label="Not linked"></td>';
                  const score = riskVizScoreValue(link, scoreMode);
                  const alpha = Math.max(0.18, Math.min(0.92, 0.16 + (score / maxScore) * 0.74));
                  const title = `${riskVizPrimaryLabel(risk)} → ${riskVizPrimaryLabel(control)} — ${riskVizScoreShortLabel(scoreMode).toLowerCase()} ${score}, inherent ${link.risk_score ?? link.value ?? '—'}, residual ${link.residual_risk_score ?? '—'}`;
                  return `<td class="matrix-cell risk-control-heatmap-cell" title="${esc(title)}" style="background:rgba(var(--bs-primary-rgb), ${alpha.toFixed(3)});"><a href="${esc(controlHref(control.control_id || ''))}" aria-label="${esc(title)}">${esc(String(score))}</a></td>`;
                }).join('')}
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;
  };
  input?.addEventListener('input', debounce(renderTable, 120));
  renderTable();
}

function renderRiskVisualisations() {
  renderRiskControlsGraph();
  renderRiskControlsHeatmap();
  const {risks, controls: controlNodes, links} = riskControlDataset();
  const totalRisks = (riskControlGraphData?.nodes || []).filter((node) => node.node_kind === 'risk').length;
  const totalControls = (riskControlGraphData?.nodes || []).filter((node) => node.node_kind === 'control').length;
  const meta = `${risks.length} of ${totalRisks} risks, ${controlNodes.length} of ${totalControls} controls, ${links.length} visible mappings for ${framework || 'the current framework'}.`;
  const scoreLabel = riskVizScoreLabel();
  if (riskControlsGraphMeta) riskControlsGraphMeta.textContent = `${meta} Link thickness follows ${scoreLabel}.`;
  if (riskControlsHeatmapMeta) riskControlsHeatmapMeta.textContent = `${meta} Darker cells have higher ${scoreLabel}.`;
}

async function loadRiskVisualisations(opts = {}) {
  if (!canViewRisks) return;
  if (!riskControlsGraph && !riskControlsHeatmap) return;
  if (!riskControlGraphData || opts.force) {
    if (riskControlsGraph) riskControlsGraph.innerHTML = '<div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>Loading risk/control graph…</span></div>';
    if (riskControlsHeatmap) riskControlsHeatmap.innerHTML = '<div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>Loading risk/control heatmap…</span></div>';
    riskControlGraphData = await apiGet(`/api/v1/graph/risk_control?framework=${encodeURIComponent(framework)}`);
  }
  renderRiskVisualisations();
  riskVisualisationsLoaded = true;
}

function controlTypeLabel(type) {
  const value = String(type || '').replace(/_/g, ' ').trim();
  return value ? value.replace(/\b\w/g, (ch) => ch.toUpperCase()) : 'Control';
}

function assetLabel(assetRow) {
  const parts = [assetRow?.category?.name, assetRow?.subcategory?.name].filter(Boolean).join(' / ');
  return parts ? `${assetRow?.name || 'Asset'} — ${parts}` : (assetRow?.name || 'Asset');
}

function normaliseAssetName(value) {
  return String(value || '').trim().toLowerCase();
}

function selectedAssetRow() {
  const id = assetId?.value || '';
  if (!id) return null;
  return assets.find((a) => String(a.id) === String(id)) || null;
}

function upsertAsset(assetRow) {
  if (!assetRow?.id) return;
  const id = String(assetRow.id);
  const idx = assets.findIndex((a) => String(a.id) === id);
  if (idx >= 0) assets[idx] = assetRow;
  else assets.push(assetRow);
}

function setAssetFieldsFromAsset(assetRow) {
  if (!assetRow) return;
  if (asset) asset.value = assetRow.name || '';
  const catId = assetRow.category?.id || '';
  const subId = assetRow.subcategory?.id || '';
  if (category && catId) category.value = catId;
  renderSubcategories(subId);
}

function selectExistingAsset(id) {
  const row = assets.find((a) => String(a.id) === String(id));
  if (!row) return;
  if (assetId) assetId.value = String(row.id);
  setAssetFieldsFromAsset(row);
  syncAssetEditorState();
}

function clearSelectedAsset({clearName = false} = {}) {
  if (assetId) assetId.value = '';
  if (clearName && asset) asset.value = '';
  if (category) category.value = categories[0]?.id || '';
  renderSubcategories();
  syncAssetEditorState();
  if (clearName) asset?.focus();
}

function matchingAssetsForInput() {
  const qv = normaliseAssetName(asset?.value || '');
  if (!qv) return [];
  return [...assets]
      .filter((a) => normaliseAssetName(assetLabel(a)).includes(qv) || normaliseAssetName(a.name).includes(qv))
      .sort((a, b) => assetLabel(a).localeCompare(assetLabel(b), undefined, {numeric: true}))
      .slice(0, 8);
}

function syncAssetSelectionFromText() {
  const selected = selectedAssetRow();
  const typed = normaliseAssetName(asset?.value || '');
  if (selected && typed === normaliseAssetName(selected.name)) {
    syncAssetEditorState();
    return;
  }
  if (assetId) assetId.value = '';
  if (!typed) {
    syncAssetEditorState();
    return;
  }
  const exact = assets.filter((a) => normaliseAssetName(a.name) === typed);
  if (exact.length === 1) {
    if (assetId) assetId.value = String(exact[0].id);
    setAssetFieldsFromAsset(exact[0]);
  }
  syncAssetEditorState();
}

function renderAssetSuggestionBox(selected) {
  if (!selectedAssetSummary) return;
  const box = selectedAssetSummary.querySelector('.alert') || selectedAssetSummary;
  const typed = (asset?.value || '').trim();
  if (selected) {
    box.innerHTML = `Using existing asset: <strong>${esc(assetLabel(selected))}</strong>. This will create or update a separate risk scenario for the same reusable asset.
      ${canManageRisks ? '<button class="btn btn-sm btn-outline-secondary ms-2" type="button" data-clear-asset="1">Use a new asset name instead</button>' : ''}`;
    selectedAssetSummary.style.display = '';
  } else if (canManageRisks && typed.length >= 2) {
    const matches = matchingAssetsForInput();
    if (matches.length) {
      box.innerHTML = `<div class="mb-2">Existing assets match “${esc(typed)}”. Select one to reuse it, or keep typing to create a new asset.</div>
        <div class="d-flex flex-wrap gap-2">${matches.map((a) => `<button class="btn btn-sm btn-outline-primary" type="button" data-use-asset="${esc(a.id)}">${esc(assetLabel(a))}</button>`).join('')}</div>`;
      selectedAssetSummary.style.display = '';
    } else {
      box.innerHTML = 'No existing asset matches this text. Saving will create a new reusable asset with the selected category and subcategory.';
      selectedAssetSummary.style.display = '';
    }
  } else if (canManageRisks) {
    box.innerHTML = 'Type an asset name. Matching existing assets will appear here so you can reuse them instead of creating duplicates.';
    selectedAssetSummary.style.display = '';
  } else {
    selectedAssetSummary.style.display = 'none';
  }
  box.querySelectorAll('[data-use-asset]').forEach((btn) => btn.addEventListener('click', () => selectExistingAsset(btn.dataset.useAsset || '')));
  box.querySelector('[data-clear-asset]')?.addEventListener('click', () => clearSelectedAsset({clearName: true}));
}

function renderAssets(targetValue = '') {
  const sorted = [...assets].sort((a, b) => assetLabel(a).localeCompare(assetLabel(b), undefined, {numeric: true}));
  if (assetOptions) {
    assetOptions.innerHTML = sorted
        .map((a) => `<option value="${esc(a.name || '')}" label="${esc(assetLabel(a))}"></option>`)
        .join('');
  }
  if (targetValue) {
    selectExistingAsset(targetValue);
  } else {
    syncAssetEditorState();
  }
}

function syncAssetEditorState({clearForNew = false} = {}) {
  const selected = selectedAssetRow();
  const readonly = !canManageRisks;
  if (selected) {
    setAssetFieldsFromAsset(selected);
  } else if (clearForNew) {
    if (assetId) assetId.value = '';
    if (asset) asset.value = '';
    if (category) category.value = categories[0]?.id || '';
    renderSubcategories();
  }

  const usingExisting = !!selected;
  if (asset) asset.disabled = readonly;
  for (const el of [category, subcategory]) {
    if (el) el.disabled = readonly || usingExisting;
  }
  renderAssetSuggestionBox(selected);
}

function subcategoriesForCategory(categoryId) {
  const cat = categories.find((c) => String(c.id) === String(categoryId));
  return cat?.subcategories || [];
}

function renderSubcategories(targetValue = '') {
  const selectedCategoryId = category?.value || categories[0]?.id || '';
  const subs = subcategoriesForCategory(selectedCategoryId);
  const opts = subs.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
  if (subcategory) {
    const cur = targetValue || subcategory.value || subs[0]?.id || '';
    subcategory.innerHTML = opts || '<option value="">No subcategories</option>';
    subcategory.value = subs.some((s) => String(s.id) === String(cur)) ? cur : (subs[0]?.id || '');
  }
}

function renderFilterSubcategories() {
  if (!subcategoryFilter) return;
  const categoryIds = new Set(filterValues(categoryFilter));
  const cur = filterValues(subcategoryFilter);
  const sourceCategories = categoryIds.size
    ? categories.filter((c) => categoryIds.has(String(c.id)))
    : categories;
  const subs = sourceCategories.flatMap((c) => (c.subcategories || []).map((s) => ({...s, categoryName: c.name})));
  const valid = new Set(subs.map((s) => String(s.id)));
  setFilterPillOptions(
    subcategoryFilter,
    subs.map((s) => ({value: String(s.id), label: s.categoryName ? `${s.categoryName} / ${s.name}` : s.name, title: s.name || ''})),
    {selectedValues: cur.filter((value) => valid.has(String(value))), dispatchChange: false}
  );
}

function resetForm() {
  if (riskId) riskId.value = '';
  if (assetId) assetId.value = '';
  if (asset) asset.value = '';
  if (category) category.value = categories[0]?.id || '';
  renderSubcategories();
  syncAssetEditorState();
  if (owner) owner.value = '';
  setRiskTypes(['Confidentiality']);
  if (threatSummary) threatSummary.value = '';
  if (threatScore) threatScore.value = '1';
  if (vulnerabilityScore) vulnerabilityScore.value = '1';
  if (impactScore) impactScore.value = '1';
  if (residualVulnerabilityScore) residualVulnerabilityScore.value = '1';
  if (residualImpactScore) residualImpactScore.value = '1';
  setSelectedControls([]);
  if (note) note.value = '';
  if (deleteRisk) deleteRisk.style.display = 'none';
  if (editorTitle) editorTitle.textContent = 'New risk scenario';
  syncScorePreviews();
}

function showEditor(risk = null) {
  if (!canViewRisks) return;
  resetForm();
  setFormReadonly(!canManageRisks);
  if (risk) {
    if (riskId) riskId.value = risk.id || '';
    if (risk.asset_entity?.id) {
      upsertAsset(risk.asset_entity);
      renderAssets(risk.asset_entity.id);
    } else {
      if (assetId) assetId.value = '';
      if (asset) asset.value = risk.asset || risk.asset_entity?.name || '';
      if (category) category.value = risk.category?.id || risk.asset_entity?.category?.id || '';
      renderSubcategories(risk.subcategory?.id || risk.asset_entity?.subcategory?.id || '');
      syncAssetEditorState();
    }
    ensureOwnerOption(risk);
    ensureControlOptions(risk.controls || []);
    if (owner) owner.value = risk.risk_owner?.id || '';
    setRiskTypes(risk.risk_types || []);
    if (threatSummary) threatSummary.value = risk.threat_summary || '';
    if (threatScore) threatScore.value = scoreFieldValue(risk.threat_score);
    if (vulnerabilityScore) vulnerabilityScore.value = scoreFieldValue(risk.vulnerability_score);
    if (impactScore) impactScore.value = scoreFieldValue(risk.impact_score);
    if (residualVulnerabilityScore) residualVulnerabilityScore.value = scoreFieldValue(risk.residual_vulnerability_score);
    if (residualImpactScore) residualImpactScore.value = scoreFieldValue(risk.residual_impact_score);
    if (note) note.value = risk.note || '';
    setSelectedControls(risk.controls || []);
    if (deleteRisk) deleteRisk.style.display = '';
    const threat = (risk.threat_summary || '').trim();
    const title = threat ? `${risk.asset || 'Asset'} — ${threat}` : (risk.asset || 'Risk');
    if (editorTitle) editorTitle.textContent = `${canManageRisks ? 'Edit' : 'View'} risk scenario: ${title}`;
  }
  syncScorePreviews();
  if (editorCard) editorCard.style.display = '';
  showTab(newRiskScenarioTab);
  editorCard?.scrollIntoView({behavior: 'smooth', block: 'start'});
  (risk ? threatSummary : asset)?.focus();
}

function hideEditor() {
  if (editorCard) editorCard.style.display = 'none';
  resetForm();
  showTab(risksListTab);
  try {
    const u = new URL(location.href);
    u.searchParams.delete('id');
    u.searchParams.delete('edit');
    history.replaceState({}, '', u.pathname + (u.search ? u.search : ''));
  } catch {}
}

function renderCategories() {
  const opts = categories.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  if (category) {
    const cur = category.value || categories[0]?.id || '';
    category.innerHTML = opts || '<option value="">No categories</option>';
    category.value = categories.some((c) => String(c.id) === String(cur)) ? cur : (categories[0]?.id || '');
  }
  renderSubcategories();
  if (categoryFilter) {
    const cur = filterValues(categoryFilter);
    setFilterPillOptions(
      categoryFilter,
      categories.map((c) => ({value: String(c.id), label: c.name || '', title: c.name || ''})),
      {selectedValues: cur, dispatchChange: false}
    );
  }
  if (manageSubcategoryCategory) {
    const cur = manageSubcategoryCategory.value || category?.value || categories[0]?.id || '';
    manageSubcategoryCategory.innerHTML = opts || '<option value="">Add a category first</option>';
    manageSubcategoryCategory.value = categories.some((c) => String(c.id) === String(cur)) ? cur : (categories[0]?.id || '');
    manageSubcategoryCategory.disabled = !categories.length;
  }
  if (manageAddSubcategory) manageAddSubcategory.disabled = !categories.length;
  if (manageSubcategoryName) manageSubcategoryName.disabled = !categories.length;
  renderFilterSubcategories();
  renderMitigatorCategories();
  syncAssetEditorState();
  if (!categoryList) return;
  if (!categories.length) {
    categoryList.innerHTML = '<span class="small-muted">No categories yet.</span>';
    return;
  }
  categoryList.innerHTML = `<div class="vstack gap-2">${categories.map((c) => `
    <div class="border rounded-3 p-2">
      <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between">
        <div class="fw-semibold">${esc(c.name)}</div>
        ${canManageRisks ? `<button class="btn btn-sm btn-link p-0 text-danger" type="button" title="Delete category" data-del-category="${esc(c.id)}">Delete</button>` : ''}
      </div>
      <div class="small-muted mt-1">Subcategories</div>
      <div class="d-flex flex-wrap gap-2 mt-1">${(c.subcategories || []).map((s) => `
        <span class="badge text-bg-light d-inline-flex align-items-center gap-2">
          <span>${esc(s.name)}</span>
          ${canManageRisks ? `<button class="btn btn-sm btn-link p-0 text-danger" type="button" title="Delete subcategory" data-del-subcategory="${esc(s.id)}">×</button>` : ''}
        </span>`).join('') || '<span class="small-muted">No subcategories</span>'}</div>
    </div>`).join('')}</div>`;
  categoryList.querySelectorAll('[data-del-category]').forEach((btn) => btn.addEventListener('click', async () => {
    const id = btn.dataset.delCategory || '';
    const cat = categories.find((c) => c.id === id);
    if (!id || !confirm(`Delete category “${cat?.name || id}”? Categories in use cannot be deleted.`)) return;
    try {
      await apiDelete(`/api/v1/risks/categories/${encodeURIComponent(id)}`);
      await loadCategories();
      toast(status, 'Category deleted', 'success');
    } catch (e) {
      toast(status, `Failed to delete category: ${String(e)}`, 'danger');
    }
  }));
  categoryList.querySelectorAll('[data-del-subcategory]').forEach((btn) => btn.addEventListener('click', async () => {
    const id = btn.dataset.delSubcategory || '';
    const sub = categories.flatMap((c) => c.subcategories || []).find((s) => s.id === id);
    if (!id || !confirm(`Delete subcategory “${sub?.name || id}”? Subcategories in use cannot be deleted.`)) return;
    try {
      await apiDelete(`/api/v1/risks/subcategories/${encodeURIComponent(id)}`);
      await loadCategories();
      toast(status, 'Subcategory deleted', 'success');
    } catch (e) {
      toast(status, `Failed to delete subcategory: ${String(e)}`, 'danger');
    }
  }));
}

function renderUsers() {
  if (!owner) return;
  const cur = owner.value || '';
  owner.innerHTML = `<option value="">No owner</option>${users.map((u) => `<option value="${esc(u.id)}">${esc(u.username)}</option>`).join('')}`;
  owner.value = cur;
}

function renderControlOptions() {
  if (!controls) return;
  controls.innerHTML = allControls
      .filter((c) => c.type !== 'clause')
      .sort((a, b) => String(a.ref || '').localeCompare(String(b.ref || ''), undefined, {numeric: true}))
      .map((c) => `<option value="${esc(c.id)}">${esc(c.ref || '')} — ${esc(c.title || '')}</option>`)
      .join('');
}

function ensureOwnerOption(risk) {
  const ro = risk?.risk_owner || null;
  if (!ro?.id || users.some((u) => String(u.id) === String(ro.id))) return;
  users = [...users, {id: String(ro.id), username: ro.username || 'Risk owner'}];
  renderUsers();
}

function ensureControlOptions(items) {
  const existing = new Set(allControls.map((c) => String(c.id)));
  let changed = false;
  for (const c of items || []) {
    const id = String(c.id || c.control_item_id || '');
    if (!id || existing.has(id)) continue;
    allControls.push({
      id,
      type: c.type || 'control',
      ref: c.ref || '',
      title: c.title || '',
    });
    existing.add(id);
    changed = true;
  }
  if (changed) renderControlOptions();
}

function setFormReadonly(readonly) {
  const disabled = !!readonly;
  for (const el of [owner, threatSummary, threatScore, vulnerabilityScore, impactScore, residualVulnerabilityScore, residualImpactScore, controls, note]) {
    if (el) el.disabled = disabled;
  }
  document.querySelectorAll('.risk-type').forEach((cb) => { cb.disabled = disabled; });
  syncAssetEditorState();
  if (saveRisk) saveRisk.style.display = disabled ? 'none' : '';
  if (deleteRisk) deleteRisk.style.display = disabled ? 'none' : (riskId?.value ? '' : 'none');
}

function defaultRiskSort(a, b) {
  const riskCmp = Number(b.risk_score || 0) - Number(a.risk_score || 0);
  if (riskCmp) return riskCmp;
  const assetCmp = String(a.asset || '').localeCompare(String(b.asset || ''), undefined, {numeric: true});
  if (assetCmp) return assetCmp;
  return String(a.threat_summary || '').localeCompare(String(b.threat_summary || ''), undefined, {numeric: true});
}

function markDefaultRiskSort() {
  if (!riskTable) return;
  const headers = [...riskTable.querySelectorAll('thead th')];
  headers.forEach((th) => {
    th.removeAttribute('data-sort-dir');
    th.classList.remove('sort-asc', 'sort-desc');
  });
  const riskHeader = headers[5];
  if (!riskHeader) return;
  riskHeader.setAttribute('data-sort-dir', 'desc');
  riskHeader.classList.add('sort-desc');
}

function renderRows(items) {
  if (!rows) return;
  if (!items.length) {
    rows.innerHTML = '<tr><td colspan="9" class="p-4 text-center small-muted">No risks match.</td></tr>';
    return;
  }
  rows.innerHTML = items.map((r) => {
    const types = (r.risk_types || []).map((x) => `<span class="badge badge-soft me-1">${esc(x)}</span>`).join('');
    const controlsText = Number(r.control_count || 0) === 1 ? '1 control' : `${Number(r.control_count || 0)} controls`;
    const threat = (r.threat_summary || '').trim();
    return `<tr>
      <td>
        <div class="fw-semibold"><a href="${esc(riskHref(r.id))}" data-open-risk="${esc(r.id)}">${esc(r.asset || '')}</a></div>
        <div class="small-muted text-truncate" style="max-width:420px;">${esc(threat || 'No threat summary')}</div>
      </td>
      <td>${esc(r.category?.name || '')}</td>
      <td>${esc(r.subcategory?.name || '')}</td>
      <td>${types || '<span class="small-muted">—</span>'}</td>
      <td>${userPillHtml(r.risk_owner)}</td>
      <td class="text-end" data-sort="${esc(Number(r.risk_score || 0))}">${scoreBadge(r.risk_score)}</td>
      <td class="text-end" data-sort="${esc(Number(r.residual_risk_score || 0))}">${scoreBadge(r.residual_risk_score)}</td>
      <td data-sort="${esc(Number(r.control_count || 0))}"><span class="badge text-bg-light">${esc(controlsText)}</span></td>
      <td class="no-print">
        <div class="d-flex flex-wrap gap-1">
          <a class="btn btn-sm btn-outline-secondary" href="${esc(riskHref(r.id))}">View</a>
          ${canSampleAudits ? `<button class="btn btn-sm btn-outline-success" type="button" data-sample-risk="${esc(r.id)}"><i class="bi bi-clipboard2-plus" aria-hidden="true"></i><span class="visually-hidden">Sample into audit</span></button>` : ''}
          ${canManageRisks ? `<button class="btn btn-sm btn-outline-primary" type="button" data-edit-risk="${esc(r.id)}">Edit</button>` : ''}
        </div>
      </td>
    </tr>`;
  }).join('');

  rows.querySelectorAll('[data-sample-risk]').forEach((el) => el.addEventListener('click', (ev) => {
    ev.preventDefault();
    const riskId = el.dataset.sampleRisk || '';
    const risk = (currentItems || []).find((x) => String(x.id) === String(riskId));
    openAuditSampleModal({
      me,
      entityType: 'risk',
      entityId: riskId,
      title: risk ? `${risk.asset || 'Risk'}${risk.threat_summary ? ` — ${risk.threat_summary}` : ''}` : 'CIA Triad risk',
      framework,
      statusEl: status,
    });
  }));
  rows.querySelectorAll('[data-edit-risk]').forEach((el) => el.addEventListener('click', async (ev) => {
    if (ev.metaKey || ev.ctrlKey) return;
    ev.preventDefault();
    const id = el.dataset.editRisk || '';
    await openRisk(id);
  }));
}

function renderControlsWithoutRisksRows(items) {
  if (!controlsWithoutRisksRows) return;
  if (!items.length) {
    controlsWithoutRisksRows.innerHTML = '<tr><td colspan="5" class="p-4 text-center small-muted">All controls currently have at least one associated risk scenario.</td></tr>';
    return;
  }
  controlsWithoutRisksRows.innerHTML = items.map((c) => {
    const scoped = c.in_scope === false ? '<span class="badge text-bg-secondary">Out of scope</span>' : '<span class="badge text-bg-success">In scope</span>';
    return `<tr>
      <td data-sort="${esc(c.ref || '')}"><a class="fw-semibold" href="${esc(controlHref(c.id))}">${esc(c.ref || 'Control')}</a></td>
      <td>${esc(c.title || '')}</td>
      <td>${scoped}</td>
      <td class="no-print"><a class="btn btn-sm btn-outline-secondary" href="${esc(controlHref(c.id))}">View</a></td>
    </tr>`;
  }).join('');
}

async function loadControlsWithoutRisks() {
  if (!canViewRisks) return;
  showTableLoading(controlsWithoutRisksRows, 5, 'Loading controls…');
  const u = new URL('/api/v1/risks/controls-without-risks', location.origin);
  if (framework) u.searchParams.set('framework', framework);
  if (controlsWithoutRisksQ?.value) u.searchParams.set('q', controlsWithoutRisksQ.value.trim());
  u.searchParams.set('limit', '5000');
  try {
    const data = await apiGet(u.pathname + u.search);
    const items = data?.items || [];
    renderControlsWithoutRisksRows(items);
    controlsWithoutRisksLoaded = true;
    if (controlsWithoutRisksMeta) {
      const total = Number(data?.total || items.length);
      controlsWithoutRisksMeta.textContent = `${total} control${total === 1 ? '' : 's'} without associated risk scenarios for ${data?.framework || framework || 'the current framework'}.`;
    }
  } catch (e) {
    if (controlsWithoutRisksRows) controlsWithoutRisksRows.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-danger">Failed to load controls without risks.</td></tr>';
    toast(status, `Failed to load controls without risks: ${String(e)}`, 'danger');
  }
}

async function loadCategories() {
  const data = await apiGet('/api/v1/risks/categories');
  categories = data?.items || [];
  renderCategories();
}

async function loadAssets() {
  if (!canViewRisks) return;
  const data = await apiGet('/api/v1/risks/assets');
  assets = data?.items || [];
  renderAssets();
}

async function loadUsers() {
  if (!canManageRisks) return;
  const data = await apiGet('/api/v1/risks/users');
  users = data?.items || [];
  renderUsers();
}

async function loadControls() {
  if (!canViewRisks) return;
  const u = new URL('/api/v1/controls', location.origin);
  if (framework) u.searchParams.set('framework', framework);
  u.searchParams.set('limit', '5000');
  const data = await apiGet(u.pathname + u.search);
  allControls = data?.items || [];
  renderControlOptions();
}

async function loadRisks() {
  if (!canViewRisks) return;
  showTableLoading(rows, 9, 'Loading risks…');
  const u = new URL('/api/v1/risks', location.origin);
  if (framework) u.searchParams.set('framework', framework);
  if (q?.value) u.searchParams.set('q', q.value.trim());
  setCsvParam(u, 'category_id', filterValues(categoryFilter));
  setCsvParam(u, 'subcategory_id', filterValues(subcategoryFilter));
  setCsvParam(u, 'risk_type', filterValues(typeFilter));
  const data = await apiGet(u.pathname + u.search);
  currentItems = [...(data?.items || [])].sort(defaultRiskSort);
  renderRows(currentItems);
  markDefaultRiskSort();
  if (resultMeta) {
    resultMeta.textContent = `${Number(data?.total || currentItems.length)} risk scenarios; control mappings shown for ${data?.framework || framework || 'current framework'}.`;
  }
}

async function openRisk(id) {
  if (!id) return;
  const u = new URL(`/api/v1/risks/${encodeURIComponent(id)}`, location.origin);
  if (framework) u.searchParams.set('framework', framework);
  try {
    const risk = await apiGet(u.pathname + u.search);
    showEditor(risk);
    history.replaceState({}, '', riskEditHref(id));
  } catch (e) {
    toast(status, `Failed to open risk: ${String(e)}`, 'danger');
  }
}

function formPayload() {
  const types = selectedRiskTypes();
  if (!types.length) throw new Error('Select at least one risk type');
  const payload = {
    risk_types: types,
    risk_owner_user_id: owner?.value || null,
    threat_summary: threatSummary?.value || '',
    threat_score: clampScore(threatScore?.value),
    vulnerability_score: clampScore(vulnerabilityScore?.value),
    impact_score: clampScore(impactScore?.value),
    residual_vulnerability_score: clampScore(residualVulnerabilityScore?.value),
    residual_impact_score: clampScore(residualImpactScore?.value),
    note: note?.value || '',
    framework,
    controls: selectedControls(),
  };

  syncAssetSelectionFromText();
  const selectedAssetId = assetId?.value || '';
  if (selectedAssetId) {
    payload.asset_id = selectedAssetId;
  } else {
    const assetName = (asset?.value || '').trim();
    if (!assetName) throw new Error('Enter an asset name, or choose an existing matching asset');
    if (!category?.value) throw new Error('Select an asset category, or add one in the Asset categories tab');
    if (!subcategory?.value) throw new Error('Select an asset subcategory, or add one in the Asset categories tab');
    payload.asset_name = assetName;
    payload.category_id = category.value;
    payload.subcategory_id = subcategory.value;
  }
  return payload;
}

async function saveCurrentRisk(ev) {
  ev?.preventDefault();
  if (!canManageRisks) return;
  saveRisk.disabled = true;
  try {
    const id = riskId?.value || '';
    const payload = formPayload();
    const saved = id
      ? await apiPatch(`/api/v1/risks/${encodeURIComponent(id)}`, payload)
      : await apiPost('/api/v1/risks', payload);
    toast(status, 'Risk scenario saved', 'success');
    await loadCategories();
    await loadAssets();
    await loadRisks();
    hideEditor();
  } catch (e) {
    toast(status, `Failed to save risk: ${String(e)}`, 'danger');
  } finally {
    saveRisk.disabled = false;
  }
}

async function deleteCurrentRisk() {
  if (!canManageRisks) return;
  const id = riskId?.value || '';
  if (!id) return;
  const label = `${asset?.value || id}${threatSummary?.value ? ` — ${threatSummary.value}` : ''}`;
  if (!confirm(`Delete risk scenario “${label}”?`)) return;
  deleteRisk.disabled = true;
  try {
    await apiDelete(`/api/v1/risks/${encodeURIComponent(id)}`);
    toast(status, 'Risk scenario deleted', 'success');
    hideEditor();
    await loadRisks();
  } catch (e) {
    toast(status, `Failed to delete risk: ${String(e)}`, 'danger');
  } finally {
    deleteRisk.disabled = false;
  }
}

async function createCategoryFromInput(inputEl, buttonEl, {syncEditor = false} = {}) {
  if (!canManageRisks) return;
  const name = (inputEl?.value || '').trim();
  if (!name) return;
  if (buttonEl) buttonEl.disabled = true;
  try {
    const row = await apiPost('/api/v1/risks/categories', {name});
    if (inputEl) inputEl.value = '';
    await loadCategories();
    if (row?.id) {
      if (manageSubcategoryCategory) manageSubcategoryCategory.value = row.id;
      if (syncEditor && category) {
        category.value = row.id;
        renderSubcategories();
      }
    }
    toast(status, 'Category added', 'success');
  } catch (e) {
    toast(status, `Failed to add category: ${String(e)}`, 'danger');
  } finally {
    if (buttonEl) buttonEl.disabled = false;
  }
}

async function createSubcategoryFromInput(inputEl, categoryEl, buttonEl, {syncEditor = false} = {}) {
  if (!canManageRisks) return;
  const name = (inputEl?.value || '').trim();
  const categoryId = categoryEl?.value || '';
  if (!name || !categoryId) return;
  if (buttonEl) buttonEl.disabled = true;
  try {
    const row = await apiPost('/api/v1/risks/subcategories', {name, category_id: categoryId});
    if (inputEl) inputEl.value = '';
    await loadCategories();
    if (syncEditor) {
      if (category) category.value = categoryId;
      renderSubcategories(row?.id || '');
    }
    toast(status, 'Subcategory added', 'success');
  } catch (e) {
    toast(status, `Failed to add subcategory: ${String(e)}`, 'danger');
  } finally {
    if (buttonEl) buttonEl.disabled = false;
  }
}


function syncMitigatorWeightLabels() {
  document.querySelectorAll('.mitigator-weight').forEach((range) => {
    const rt = range.dataset.riskType || '';
    const out = mitigatorWeightOutputs[rt];
    if (out) out.textContent = String(range.value || 0);
  });
}

function selectedMitigatorRiskTypes() {
  return [...document.querySelectorAll('.mitigator-risk-type')]
      .filter((x) => x.checked)
      .map((x) => x.value);
}

function mitigatorRiskWeights() {
  const out = {};
  document.querySelectorAll('.mitigator-weight').forEach((range) => {
    const rt = range.dataset.riskType || '';
    if (!rt) return;
    const n = Math.max(0, Math.min(100, Math.floor(Number(range.value || 0))));
    out[rt] = n;
  });
  return out;
}

function mitigatorImpactFlags() {
  const out = {};
  document.querySelectorAll('.mitigator-impact').forEach((cb) => {
    out[cb.value] = !!cb.checked;
  });
  return out;
}

function subcategoriesForMitigatorCategory(categoryId) {
  const cat = categories.find((c) => String(c.id) === String(categoryId));
  return cat?.subcategories || [];
}

function renderMitigatorSubcategories(targetValue = '') {
  if (!mitigatorSubcategory) return;
  const selectedCategoryId = mitigatorCategory?.value || categories[0]?.id || '';
  const subs = subcategoriesForMitigatorCategory(selectedCategoryId);
  const cur = targetValue || mitigatorSubcategory.value || subs[0]?.id || '';
  mitigatorSubcategory.innerHTML = subs.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('') || '<option value="">No subcategories</option>';
  mitigatorSubcategory.value = subs.some((s) => String(s.id) === String(cur)) ? cur : (subs[0]?.id || '');
}

function renderMitigatorCategories() {
  if (!mitigatorCategory) return;
  const cur = mitigatorCategory.value || categories.find((c) => String(c.name || '').toLowerCase() === 'data')?.id || categories[0]?.id || '';
  mitigatorCategory.innerHTML = categories.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('') || '<option value="">No categories</option>';
  mitigatorCategory.value = categories.some((c) => String(c.id) === String(cur)) ? cur : (categories[0]?.id || '');
  renderMitigatorSubcategories();
}

function resetMitigator() {
  lastMitigatorAnalysis = null;
  if (mitigatorIssue) mitigatorIssue.value = '';
  if (mitigatorAsset) mitigatorAsset.value = '';
  document.querySelectorAll('.mitigator-risk-type').forEach((cb) => { cb.checked = cb.value === 'Confidentiality'; });
  const defaults = {Confidentiality: 60, Integrity: 40, Availability: 40};
  document.querySelectorAll('.mitigator-weight').forEach((range) => { range.value = String(defaults[range.dataset.riskType] || 40); });
  document.querySelectorAll('.mitigator-impact').forEach((cb) => { cb.checked = false; });
  if (mitigatorResultsCard) mitigatorResultsCard.style.display = 'none';
  if (mitigatorSignals) mitigatorSignals.innerHTML = '';
  if (mitigatorSuggestions) mitigatorSuggestions.innerHTML = '';
  if (mitigatorCreateRisk) mitigatorCreateRisk.disabled = true;
  if (mitigatorCreatePestle) mitigatorCreatePestle.disabled = true;
  if (mitigatorCreateInterested) mitigatorCreateInterested.disabled = true;
  syncMitigatorWeightLabels();
  renderMitigatorCategories();
}

function confidenceBadge(confidence) {
  const c = String(confidence || '').toLowerCase();
  const cls = c === 'strong' ? 'text-bg-success' : (c === 'medium' ? 'text-bg-warning' : 'text-bg-secondary');
  return `<span class="badge ${cls}">${esc(c ? c[0].toUpperCase() + c.slice(1) : 'Suggested')}</span>`;
}

function renderMitigatorSignals(signals, data = null) {
  if (!mitigatorSignals) return;
  const rows = signals || [];
  const existing = data?.existing_matches || {};
  const cia = existing.cia_risks || [];
  const pestle = existing.pestle_items || [];
  const parties = existing.interested_parties || [];
  const duplicateBits = [];
  if (cia.length) duplicateBits.push(`<strong>${esc(String(cia.length))}</strong> possible CIA Triad risk${cia.length === 1 ? '' : 's'}`);
  if (pestle.length) duplicateBits.push(`<strong>${esc(String(pestle.length))}</strong> possible PESTLE(E) item${pestle.length === 1 ? '' : 's'}`);
  if (parties.length) duplicateBits.push(`<strong>${esc(String(parties.length))}</strong> possible Interested Part${parties.length === 1 ? 'y' : 'ies'}`);

  const duplicateHtml = duplicateBits.length ? `<div class="alert alert-warning py-2 mb-3">
    <div class="fw-semibold">Potential existing risks found</div>
    <div class="small">${duplicateBits.join(', ')} already exist or look related. Review these before creating a new risk definition.</div>
  </div>` : '';

  const signalHtml = rows.length ? `<div class="small-muted mb-2">Signals used by the deterministic rules</div>
    <div class="d-flex flex-wrap gap-2">${rows.map((sig) => `<span class="badge text-bg-light border" title="${esc(sig.reason || '')}">${esc(sig.label || sig.kind || 'Signal')}</span>`).join('')}</div>` : '<div class="small-muted">No extra rule signals were matched beyond the selected questionnaire answers.</div>';

  mitigatorSignals.innerHTML = `${duplicateHtml}${signalHtml}`;
}

function renderExistingMatchesSection(existing) {
  const cia = existing?.cia_risks || [];
  const pestle = existing?.pestle_items || [];
  const parties = existing?.interested_parties || [];
  if (!cia.length && !pestle.length && !parties.length) return '';
  const ciaHtml = cia.map((r) => `<li><a href="${esc(riskHref(r.id || ''))}">${esc(r.asset_name || 'CIA Triad risk')}</a> — ${esc((r.risk_types || []).join(', ') || 'CIA')} <span class="small-muted">${esc(r.threat_summary || '')}</span></li>`).join('');
  const pestleHtml = pestle.map((p) => `<li><a href="${esc(pestleHref(p.id || ''))}">${esc(p.item || 'PESTLE(E) item')}</a> <span class="badge text-bg-light border">${esc(p.type || '')}</span>${(p.clauses || []).length ? ` <span class="small-muted">Clauses: ${esc((p.clauses || []).map((c) => c?.clause?.ref || '').filter(Boolean).join(', '))}</span>` : ''}</li>`).join('');
  const partyHtml = parties.map((p) => `<li><a href="${esc(interestedPartyHref(p.id || ''))}">${esc(p.label || p.name || 'Interested Party')}</a></li>`).join('');
  return `<div class="mitigator-suggestion-card border-warning-subtle">
    <div class="fw-semibold mb-2"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i> Existing related risks to review first</div>
    ${ciaHtml ? `<div class="small fw-semibold mt-2">CIA Triad risks</div><ul class="small mb-1">${ciaHtml}</ul>` : ''}
    ${pestleHtml ? `<div class="small fw-semibold mt-2">PESTLE(E) items</div><ul class="small mb-1">${pestleHtml}</ul>` : ''}
    ${partyHtml ? `<div class="small fw-semibold mt-2">Interested Parties</div><ul class="small mb-1">${partyHtml}</ul>` : ''}
  </div>`;
}

function renderControlSuggestionsSection(suggestions) {
  if (!suggestions.length) {
    return `<div class="mitigator-suggestion-card">
      <div class="fw-semibold mb-1">Suggested Annex A controls (if you were to create a new risk)</div>
      <div class="alert alert-warning mb-0">No controls matched strongly enough. Try adding more detail or selecting more impact prompts.</div>
    </div>`;
  }
  const rows = suggestions.map((item, idx) => {
    const c = item.control || {};
    const terms = (item.matched_terms || []).slice(0, 8).map((t) => `<span class="badge text-bg-light mitigator-term-badge">${esc(t)}</span>`).join('');
    const reasons = (item.reasons || []).map((r) => `<li>${esc(r)}</li>`).join('');
    const checked = idx < 8 ? 'checked' : '';
    return `<div class="mitigator-suggestion-card">
      <div class="d-flex flex-wrap gap-2 align-items-start justify-content-between">
        <div class="form-check">
          <input class="form-check-input mitigator-suggestion-select" type="checkbox" value="${esc(c.id || '')}" id="mitigatorSuggestion${idx}" ${checked}>
          <label class="form-check-label" for="mitigatorSuggestion${idx}">
            <span class="fw-semibold"><a href="${esc(controlHref(c.id || ''))}">${esc(c.ref || 'Control')} — ${esc(c.title || '')}</a></span>
          </label>
        </div>
        <div class="d-flex flex-wrap gap-2 align-items-center">${confidenceBadge(item.confidence)}<span class="badge text-bg-light border">Score ${esc(String(item.score || 0))}</span></div>
      </div>
      ${reasons ? `<ul class="small mt-2 mb-2">${reasons}</ul>` : ''}
      ${terms ? `<div class="d-flex flex-wrap gap-1">${terms}</div>` : ''}
    </div>`;
  }).join('');
  return `<div>
    <div class="fw-semibold mb-2">Suggested Annex A controls</div>
    <div class="vstack gap-2">${rows}</div>
  </div>`;
}

function renderPestleSuggestionsSection(data) {
  const suggestions = data?.pestle_suggestions || [];
  const hits = data?.pestle_hits || [];
  const draft = data?.draft_pestle_item || null;
  if (!suggestions.length && !hits.length && !draft) return '';
  const hitBadges = hits.map((h) => `<span class="badge text-bg-info">${esc(h.type || 'PESTLE(E)')}</span>`).join('');
  const draftHtml = draft ? `<div class="small-muted mb-2">Draft if you choose to create: <span class="fw-semibold">${esc(draft.type || '')}</span> / ${esc(draft.lens || '')} — ${esc(draft.item || '')}${(draft.business_processes || []).length ? `; business processes: ${esc((draft.business_processes || []).map((bp) => bp.name).join(', '))}` : ''}</div>` : '';
  const existingHtml = suggestions.length ? suggestions.map((item) => {
    const terms = (item.matched_terms || []).slice(0, 8).map((t) => `<span class="badge text-bg-light mitigator-term-badge">${esc(t)}</span>`).join('');
    const clauses = (item.clauses || []).map((c) => c?.clause?.ref || '').filter(Boolean).join(', ');
    return `<div class="mitigator-suggestion-card">
      <div class="d-flex flex-wrap gap-2 justify-content-between align-items-start">
        <div><a class="fw-semibold" href="${esc(pestleHref(item.id || ''))}">${esc(item.item || 'PESTLE(E) item')}</a>
          <div class="small-muted">${esc(item.type || '')} / ${esc(item.lens || '')}${clauses ? ` • Clauses: ${esc(clauses)}` : ''}</div>
        </div>
        <div>${confidenceBadge(item.confidence)} <span class="badge text-bg-light border">Score ${esc(String(item.score || 0))}</span></div>
      </div>
      ${terms ? `<div class="d-flex flex-wrap gap-1 mt-2">${terms}</div>` : ''}
    </div>`;
  }).join('') : '<div class="small-muted">No existing PESTLE(E) items matched strongly.</div>';
  return `<div>
    <div class="fw-semibold mb-2">PESTLE(E) signals and related existing items ${hitBadges}</div>
    ${draftHtml}
    <div class="vstack gap-2">${existingHtml}</div>
  </div>`;
}

function renderInterestedPartySuggestionsSection(data) {
  const suggestions = data?.interested_party_suggestions || [];
  if (!suggestions.length) return '';
  const rows = suggestions.map((item, idx) => {
    const terms = (item.matched_terms || []).slice(0, 8).map((t) => `<span class="badge text-bg-light mitigator-term-badge">${esc(t)}</span>`).join('');
    const disabled = item.duplicate_exact ? 'disabled' : '';
    const checked = item.duplicate_exact ? '' : 'checked';
    return `<div class="mitigator-suggestion-card">
      <div class="d-flex flex-wrap gap-2 align-items-start justify-content-between">
        <div class="form-check">
          <input class="form-check-input mitigator-interested-select" type="checkbox" value="${esc(String(idx))}" id="mitigatorParty${idx}" ${checked} ${disabled}>
          <label class="form-check-label" for="mitigatorParty${idx}">
            <span class="fw-semibold">${esc(item.name || 'Interested Party')}</span> — ${esc(item.nature || '')}
          </label>
          ${item.duplicate_exact ? '<div class="small text-warning">Exact name/nature already exists; creation is disabled for this suggestion.</div>' : ''}
        </div>
        <span class="badge text-bg-light border">Interested Party</span>
      </div>
      <div class="small-muted mt-1">${esc(item.reason || '')}</div>
      ${terms ? `<div class="d-flex flex-wrap gap-1 mt-2">${terms}</div>` : ''}
    </div>`;
  }).join('');
  return `<div>
    <div class="fw-semibold mb-2">Suggested Interested Parties</div>
    <div class="vstack gap-2">${rows}</div>
  </div>`;
}

function renderMitigatorSuggestions(data) {
  lastMitigatorAnalysis = data || null;
  const suggestions = data?.control_suggestions || data?.suggestions || [];
  const pestle = data?.pestle_suggestions || [];
  const parties = data?.interested_party_suggestions || [];
  const existing = data?.existing_matches || {};
  if (mitigatorResultsCard) mitigatorResultsCard.style.display = '';
  if (mitigatorResultsMeta) {
    mitigatorResultsMeta.textContent = `${suggestions.length} suggested control${suggestions.length === 1 ? '' : 's'}, ${pestle.length} related PESTLE(E) item${pestle.length === 1 ? '' : 's'}, and ${parties.length} Interested Part${parties.length === 1 ? 'y' : 'ies'} for ${data?.framework || framework || 'the current framework'}.`;
  }
  renderMitigatorSignals(data?.signals || [], data);
  if (!mitigatorSuggestions) return;
  if (mitigatorCreateRisk) mitigatorCreateRisk.disabled = !canManageRisks || !data?.draft_risk;
  if (mitigatorCreatePestle) mitigatorCreatePestle.disabled = !canManagePestle || !data?.draft_pestle_item;
  if (mitigatorCreateInterested) mitigatorCreateInterested.disabled = !canManageInterestedParties || !parties.some((p) => !p.duplicate_exact);

  const sections = [
    renderExistingMatchesSection(existing),
    renderControlSuggestionsSection(suggestions),
    renderPestleSuggestionsSection(data),
    renderInterestedPartySuggestionsSection(data),
  ].filter(Boolean);
  mitigatorSuggestions.innerHTML = sections.join('');
}

function mitigatorPayload() {
  const issue = (mitigatorIssue?.value || '').trim();
  if (!issue) throw new Error('Describe the potential issue first');
  if (!mitigatorCategory?.value) throw new Error('Select an asset category');
  if (!mitigatorSubcategory?.value) throw new Error('Select an asset subcategory');
  const cat = categories.find((c) => String(c.id) === String(mitigatorCategory.value));
  const sub = (cat?.subcategories || []).find((x) => String(x.id) === String(mitigatorSubcategory.value));
  return {
    issue,
    asset_name: (mitigatorAsset?.value || '').trim(),
    category_id: mitigatorCategory.value,
    category_name: cat?.name || '',
    subcategory_id: mitigatorSubcategory.value,
    subcategory_name: sub?.name || '',
    risk_types: selectedMitigatorRiskTypes(),
    risk_weights: mitigatorRiskWeights(),
    impact_flags: mitigatorImpactFlags(),
    framework,
    limit: 10,
  };
}

async function analyseMitigator(ev) {
  ev?.preventDefault();
  if (!canViewRisks) return;
  if (mitigatorAnalyse) mitigatorAnalyse.disabled = true;
  try {
    const data = await apiPost('/api/v1/risks/mitigator/analyse', mitigatorPayload());
    renderMitigatorSuggestions(data);
    toast(status, 'Keen Mitigator analysis complete', 'success');
  } catch (e) {
    toast(status, `Failed to analyse mitigations: ${String(e)}`, 'danger');
  } finally {
    if (mitigatorAnalyse) mitigatorAnalyse.disabled = false;
  }
}

function selectedMitigatorControls() {
  return [...document.querySelectorAll('.mitigator-suggestion-select')]
      .filter((cb) => cb.checked)
      .map((cb) => cb.value)
      .filter(Boolean);
}

async function createRiskFromMitigator() {
  if (!canManageRisks || !lastMitigatorAnalysis?.draft_risk) return;
  const assetName = (mitigatorAsset?.value || '').trim();
  if (!assetName) {
    toast(status, 'Enter an affected asset or process before creating the risk.', 'warning');
    mitigatorAsset?.focus();
    return;
  }
  const selected = selectedMitigatorControls();
  if (!selected.length && !confirm('Create the risk without any linked controls?')) return;
  const draft = {...lastMitigatorAnalysis.draft_risk};
  draft.asset_name = assetName;
  draft.category_id = mitigatorCategory?.value || draft.category_id || null;
  draft.subcategory_id = mitigatorSubcategory?.value || draft.subcategory_id || null;
  draft.controls = selected;
  draft.framework = framework;
  draft.mitigator_context = {
    ...(draft.mitigator_context || {}),
    selected_control_ids: selected,
  };
  if (mitigatorCreateRisk) mitigatorCreateRisk.disabled = true;
  try {
    const saved = await apiPost('/api/v1/risks', draft);
    toast(status, 'Risk Register entry created from Keen Mitigator', 'success');
    await loadAssets();
    await loadRisks();
    if (saved?.id) location.href = riskHref(saved.id);
  } catch (e) {
    toast(status, `Failed to create risk: ${String(e)}`, 'danger');
  } finally {
    if (mitigatorCreateRisk) mitigatorCreateRisk.disabled = false;
  }
}


async function createPestleFromMitigator() {
  if (!canManagePestle || !lastMitigatorAnalysis?.draft_pestle_item) return;
  const existing = lastMitigatorAnalysis.existing_matches?.pestle_items || [];
  if (existing.length && !confirm('Related PESTLE(E) items already exist. Create a new item anyway?')) return;
  const draft = {...lastMitigatorAnalysis.draft_pestle_item, framework};
  const businessProcesses = draft.business_processes || [];
  delete draft.business_processes;
  if (mitigatorCreatePestle) mitigatorCreatePestle.disabled = true;
  try {
    const saved = await apiPost('/api/v1/pestle/items', draft);
    if (saved?.id && businessProcesses.length) {
      await apiPatch(`/api/v1/pestle/items/${encodeURIComponent(saved.id)}/business-processes`, {
        items: businessProcesses
          .filter((bp) => bp.id)
          .map((bp) => ({business_process_id: bp.id, relevance_code: bp.relevance_code || 'medium'})),
      });
    }
    toast(status, 'PESTLE(E) item created from Keen Mitigator', 'success');
    if (saved?.id) location.href = pestleHref(saved.id);
  } catch (e) {
    toast(status, `Failed to create PESTLE(E) item: ${String(e)}`, 'danger');
  } finally {
    if (mitigatorCreatePestle) mitigatorCreatePestle.disabled = false;
  }
}

function selectedMitigatorInterestedPartyDrafts() {
  const suggestions = lastMitigatorAnalysis?.interested_party_suggestions || [];
  return [...document.querySelectorAll('.mitigator-interested-select')]
      .filter((cb) => cb.checked && !cb.disabled)
      .map((cb) => suggestions[Number(cb.value)]?.draft)
      .filter(Boolean);
}

async function createInterestedPartiesFromMitigator() {
  if (!canManageInterestedParties || !lastMitigatorAnalysis) return;
  const drafts = selectedMitigatorInterestedPartyDrafts();
  if (!drafts.length) {
    toast(status, 'Select at least one Interested Party suggestion to create.', 'warning');
    return;
  }
  const selectedControls = selectedMitigatorControls();
  if (mitigatorCreateInterested) mitigatorCreateInterested.disabled = true;
  const created = [];
  const failed = [];
  try {
    for (const originalDraft of drafts) {
      const draft = {
        ...originalDraft,
        framework,
        controls: selectedControls,
      };
      try {
        const row = await apiPost('/api/v1/interested-parties', draft);
        if (row?.id) created.push(row);
      } catch (e) {
        failed.push(String(e));
      }
    }
    if (created.length) {
      toast(status, `${created.length} Interested Part${created.length === 1 ? 'y' : 'ies'} created from Keen Mitigator`, 'success');
      if (created.length === 1 && created[0]?.id) location.href = interestedPartyHref(created[0].id);
    }
    if (failed.length) toast(status, `Some Interested Parties could not be created: ${failed.join('; ')}`, 'warning');
  } finally {
    if (mitigatorCreateInterested) mitigatorCreateInterested.disabled = false;
  }
}

function showTab(tabButton) {
  if (!tabButton) return;
  try {
    window.bootstrap?.Tab?.getOrCreateInstance(tabButton)?.show();
  } catch {}
}

function setRiskPageTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (!tabName || tabName === 'risks') p.delete('tab');
  else p.set('tab', tabName);
  const qs = p.toString();
  history.replaceState({}, '', `${location.pathname}${qs ? `?${qs}` : ''}${location.hash || ''}`);
}

for (const el of [threatScore, vulnerabilityScore, impactScore, residualVulnerabilityScore, residualImpactScore]) {
  el?.addEventListener('input', syncScorePreviews);
  el?.addEventListener('change', () => {
    el.value = String(clampScore(el.value));
    syncScorePreviews();
  });
}

setupRiskFilterPickers();
setFilterPillOptions(typeFilter, RISK_TYPE_FILTER_OPTIONS, {dispatchChange: false});

q?.addEventListener('input', debounce(loadRisks, 250));
controlsWithoutRisksQ?.addEventListener('input', debounce(loadControlsWithoutRisks, 250));
asset?.addEventListener('input', debounce(syncAssetSelectionFromText, 100));
asset?.addEventListener('change', syncAssetSelectionFromText);
category?.addEventListener('change', () => {
  renderSubcategories();
  syncAssetEditorState();
});
categoryFilter?.addEventListener('change', () => {
  renderFilterSubcategories();
  loadRisks();
});
subcategoryFilter?.addEventListener('change', loadRisks);
typeFilter?.addEventListener('change', loadRisks);
reloadBtn?.addEventListener('click', loadRisks);
mitigatorCategory?.addEventListener('change', () => renderMitigatorSubcategories());
mitigatorForm?.addEventListener('submit', analyseMitigator);
mitigatorClear?.addEventListener('click', resetMitigator);
mitigatorCreateRisk?.addEventListener('click', createRiskFromMitigator);
mitigatorCreatePestle?.addEventListener('click', createPestleFromMitigator);
mitigatorCreateInterested?.addEventListener('click', createInterestedPartiesFromMitigator);
document.querySelectorAll('.mitigator-weight').forEach((range) => range.addEventListener('input', syncMitigatorWeightLabels));
reloadControlsWithoutRisks?.addEventListener('click', loadControlsWithoutRisks);
reloadRiskVisualisations?.addEventListener('click', () => loadRiskVisualisations({force: true}).catch(e => toast(status, String(e), 'danger')));
riskVizTopRisks?.addEventListener('change', () => loadRiskVisualisations().catch(e => toast(status, String(e), 'danger')));
riskVizTopControls?.addEventListener('change', () => loadRiskVisualisations().catch(e => toast(status, String(e), 'danger')));
for (const input of riskVizScoreModeInputs) {
  input.addEventListener('change', () => {
    if (!input.checked) return;
    riskVizScoreMode = normaliseRiskVizScoreMode(input.value);
    renderRiskVisualisations();
  });
}
risksListTab?.addEventListener('shown.bs.tab', () => setRiskPageTabQuery('risks'));
riskVisualisationsTab?.addEventListener('shown.bs.tab', () => {
  setRiskPageTabQuery('visualisations');
  if (!riskVisualisationsLoaded) loadRiskVisualisations().catch(e => toast(status, `Failed to load risk visualisations: ${String(e)}`, 'danger'));
});
controlsWithoutRisksTab?.addEventListener('shown.bs.tab', () => {
  setRiskPageTabQuery('controls-without-risks');
  if (!controlsWithoutRisksLoaded) loadControlsWithoutRisks();
});
newRiskScenarioTab?.addEventListener('shown.bs.tab', () => setRiskPageTabQuery('new'));
document.getElementById('riskCategoriesTab')?.addEventListener('shown.bs.tab', () => setRiskPageTabQuery('categories'));
newRiskBtn?.addEventListener('click', () => showEditor());
newRiskScenarioTab?.addEventListener('click', () => {
  if (canViewRisks && !riskId?.value && editorCard?.style.display === 'none') showEditor();
});
riskForm?.addEventListener('submit', saveCurrentRisk);
deleteRisk?.addEventListener('click', deleteCurrentRisk);
manageAddCategory?.addEventListener('click', () => createCategoryFromInput(manageCategoryName, manageAddCategory, {syncEditor: false}));
manageAddSubcategory?.addEventListener('click', () => createSubcategoryFromInput(manageSubcategoryName, manageSubcategoryCategory, manageAddSubcategory, {syncEditor: false}));
manageCategoryName?.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter') {
    ev.preventDefault();
    createCategoryFromInput(manageCategoryName, manageAddCategory, {syncEditor: false});
  }
});
manageSubcategoryName?.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter') {
    ev.preventDefault();
    createSubcategoryFromInput(manageSubcategoryName, manageSubcategoryCategory, manageAddSubcategory, {syncEditor: false});
  }
});

if (!canViewRisks) {
  toast(status, isMitigatorPage ? 'You need risk.read permission to use Keen Mitigator.' : 'You need risk.read permission to view the risk register.', 'danger');
  if (rows) rows.innerHTML = '<tr><td colspan="9" class="p-4 text-center text-danger">risk.read permission required</td></tr>';
  if (isMitigatorPage && mitigatorForm) {
    mitigatorForm.querySelectorAll('input, select, textarea, button').forEach((el) => { el.disabled = true; });
  }
  if (newRiskBtn) newRiskBtn.style.display = 'none';
  if (newRiskScenarioTabItem) newRiskScenarioTabItem.style.display = 'none';
  if (riskCategoriesTabItem) riskCategoriesTabItem.style.display = 'none';
  if (controlsWithoutRisksTabItem) controlsWithoutRisksTabItem.style.display = 'none';
  if (riskVisualisationsTabItem) riskVisualisationsTabItem.style.display = 'none';
} else {
  if (newRiskScenarioTab) newRiskScenarioTab.textContent = canManageRisks ? 'New risk scenario' : 'Risk scenario';
  if (!canManageRisks) {
    if (newRiskBtn) newRiskBtn.style.display = 'none';
    if (mitigatorCreateRisk) mitigatorCreateRisk.style.display = 'none';
    if (riskCategoriesTabItem) riskCategoriesTabItem.style.display = 'none';
    if (categoryCard) categoryCard.style.display = 'none';
  } else if (categoryCard) {
    categoryCard.style.display = '';
  }
  if (!canManagePestle && mitigatorCreatePestle) mitigatorCreatePestle.style.display = 'none';
  if (!canManageInterestedParties && mitigatorCreateInterested) mitigatorCreateInterested.style.display = 'none';

  syncMitigatorWeightLabels();
  await loadCategories();
  await Promise.all([loadUsers(), loadControls(), loadAssets()]);
  if (isMitigatorPage) {
    resetMitigator();
    mitigatorIssue?.focus();
  } else {
    await loadRisks();
  }
  if (!isMitigatorPage && requestedEditRiskId) {
    await openRisk(requestedEditRiskId);
    history.replaceState({}, '', riskEditHref(requestedEditRiskId));
  } else if (!isMitigatorPage && requestedRiskId) {
    // Older links used /risks.html?id=... for both viewing and editing.
    // The register is now list/edit-only; specific risk viewing has its own page.
    location.replace(riskHref(requestedRiskId));
  } else if (!isMitigatorPage && requestedTemplateId) {
    const library = await apiGet('/api/v1/risks/library');
    const template = (library.items || []).find(item => item.id === requestedTemplateId);
    if (template) {
      showEditor();
      setRiskTypes(template.risk_types || []);
      threatSummary.value = template.threat_summary || '';
      note.value = template.treatment_guidance || '';
      toast(status, 'Template loaded. Select an asset and review its scores and controls.', 'info');
    }
  } else if (!isMitigatorPage && requestedTab === 'new') {
    showEditor();
  } else if (!isMitigatorPage && requestedTab === 'visualisations') {
    showTab(riskVisualisationsTab);
    await loadRiskVisualisations();
  } else if (!isMitigatorPage && requestedTab === 'controls-without-risks') {
    showTab(controlsWithoutRisksTab);
    await loadControlsWithoutRisks();
  } else if (!isMitigatorPage && requestedTab === 'categories' && canManageRisks) {
    showTab(document.getElementById('riskCategoriesTab'));
  }
}
