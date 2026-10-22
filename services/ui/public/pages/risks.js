// The Risks dashboard: every risk with its 5×5 heatmap and rating card, CIA
// risks, PESTLE(E) factors, the reusable library, risk/control visualisations
// and the controls that no risk mentions yet. Creating and editing happens in
// the risk wizard; rating and treatment can also be adjusted in place.
import { featureEnabled } from '/pages/feature-availability.js';
import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiPostForm,
  apiDelete,
  esc,
  toast,
  debounce,
  enableTableSorting,
  getCurrentFramework,
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
import {
  RISK_TYPES,
  TREATMENT_STRATEGIES,
  TREATMENT_STATUSES,
  methodOf,
  methodBadgeHtml,
  riskTypeBadgesHtml,
  partyBadgesHtml,
  scoreBadge,
  severityBadge,
  treatmentLabel,
  treatmentStatusLabel,
  riskDisplayTitle,
  riskHref,
  controlHref,
  pestleHref,
  riskWizardHref,
} from '/pages/risk-common.js';
import {
  initRiskVisualisations,
  loadRiskVisualisations,
  setRiskVizScoreMode,
  riskVisualisationsAreLoaded,
} from '/pages/risk-visualisations.js';

const me = await initNavbar();
initCollapsibleFilterSections();
const framework = getCurrentFramework();
enableTableSorting();

const canViewRisks = !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
const canManageRisks = !!(me?.is_admin || me?.can_manage_risks);
const canViewPestle = !!(
  me?.is_admin ||
  me?.can_view_pestle ||
  me?.can_manage_pestle ||
  canViewRisks
);
const canSampleAudits = canSampleIntoAudit(me, 'risk');
// Ratings, thresholds and the heatmap belong to the register feature.
const registerEnabled = featureEnabled(me, 'risk_register');

const params = new URLSearchParams(location.search);
const requestedTab = params.get('tab') || '';
const requestedTemplateId = params.get('template') || '';
const legacyEditId = params.get('edit') || '';
const legacyViewId = params.get('id') || '';

const $ = (id) => document.getElementById(id);
const status = $('status');
const base = '/api/v1/risks';

const RISK_TYPE_FILTER_OPTIONS = RISK_TYPES.map((value) => ({ value, label: value }));

let categories = [];
let currentItems = [];
let thresholds = { low_max: 4, moderate_max: 9, high_max: 16 };
let heatmapCounts = { inherent: [], residual: [] };
let selectedRisk = null;
let library = [];
let pestleItems = [];
let pestleMeta = null;
let loaded = { heatmap: false, cia: false, pestle: false, library: false, controlsWithout: false };

function notify(message, kind = 'danger') {
  toast(status, message, kind);
}

// Older links addressed the editor and viewer through this page.
if (legacyEditId) location.replace(riskWizardHref(framework, { id: legacyEditId }));
else if (legacyViewId) location.replace(riskHref(legacyViewId, framework));
else if (requestedTemplateId)
  location.replace(riskWizardHref(framework, { template: requestedTemplateId }));

// --- All risks ----------------------------------------------------------------------------

function setupFilterPickers() {
  enhanceFilterPillMultiSelect($('categoryFilter'), {
    title: 'Choose asset categories',
    description:
      'Select one or more asset categories to include. Clear the selection to include every category.',
    buttonLabel: 'Choose categories',
    singular: 'category',
    plural: 'categories',
    emptyText: 'All categories',
    emptySelectedText: 'No category filters selected. All categories are included.',
    noMatchText: 'No categories match your filter.',
    searchPlaceholder: 'Filter categories…',
  });
  enhanceFilterPillMultiSelect($('subcategoryFilter'), {
    title: 'Choose asset subcategories',
    description:
      'Select one or more asset subcategories to include. Category selections narrow this list.',
    buttonLabel: 'Choose subcategories',
    singular: 'subcategory',
    plural: 'subcategories',
    emptyText: 'All subcategories',
    emptySelectedText: 'No subcategory filters selected. All subcategories are included.',
    noMatchText: 'No subcategories match your filter.',
    searchPlaceholder: 'Filter subcategories…',
  });
  enhanceFilterPillMultiSelect($('typeFilter'), {
    title: 'Choose CIA risk types',
    description: 'Select one or more CIA types to include. Clear the selection to include all.',
    buttonLabel: 'Choose CIA types',
    singular: 'CIA type',
    plural: 'CIA types',
    emptyText: 'All CIA types',
    emptySelectedText: 'No CIA type filters selected. All types are included.',
    noMatchText: 'No types match your filter.',
    searchPlaceholder: 'Filter types…',
    options: RISK_TYPE_FILTER_OPTIONS,
  });
}

function setCsvParam(url, key, values) {
  const cleaned = Array.from(
    new Set((values || []).map((v) => String(v || '').trim()).filter(Boolean)),
  );
  if (cleaned.length) url.searchParams.set(key, cleaned.join(','));
}

function renderCategoryFilters() {
  setFilterPillOptions(
    $('categoryFilter'),
    categories.map((c) => ({ value: c.id, label: c.name })),
    { dispatchChange: false },
  );
  renderSubcategoryFilter();
}

function renderSubcategoryFilter() {
  const chosen = new Set(selectedFilterPillValues($('categoryFilter')));
  const options = categories
    .filter((c) => !chosen.size || chosen.has(String(c.id)))
    .flatMap((c) =>
      (c.subcategories || []).map((s) => ({ value: s.id, label: `${s.name} (${c.name})` })),
    );
  setFilterPillOptions($('subcategoryFilter'), options, {
    dispatchChange: false,
    selectedValues: selectedFilterPillValues($('subcategoryFilter')).filter((v) =>
      options.some((o) => String(o.value) === String(v)),
    ),
  });
}

function defaultRiskSort(a, b) {
  const riskCmp = Number(b.risk_score || 0) - Number(a.risk_score || 0);
  if (riskCmp) return riskCmp;
  return riskDisplayTitle(a).localeCompare(riskDisplayTitle(b), undefined, { numeric: true });
}

function markDefaultRiskSort() {
  const table = $('riskTable');
  if (!table) return;
  const headers = [...table.querySelectorAll('thead th')];
  headers.forEach((th) => {
    th.removeAttribute('data-sort-dir');
    th.classList.remove('sort-asc', 'sort-desc');
  });
  const riskHeader = headers[5];
  if (!riskHeader) return;
  riskHeader.setAttribute('data-sort-dir', 'desc');
  riskHeader.classList.add('sort-desc');
}

function actionsHtml(r, { rate = false } = {}) {
  return `<div class="d-flex flex-wrap gap-1">
    <a class="btn btn-sm btn-outline-secondary" href="${esc(riskHref(r.id, framework))}">Open</a>
    ${rate && registerEnabled ? `<button class="btn btn-sm btn-outline-primary" type="button" data-rate="${esc(r.id)}">${canManageRisks ? 'Rate / treat' : 'Rating'}</button>` : ''}
    ${canSampleAudits ? `<button class="btn btn-sm btn-outline-success" type="button" data-sample-risk="${esc(r.id)}" title="Sample into audit"><i class="bi bi-clipboard2-plus" aria-hidden="true"></i><span class="visually-hidden">Sample into audit</span></button>` : ''}
    ${canManageRisks ? `<a class="btn btn-sm btn-outline-primary" href="${esc(riskWizardHref(framework, { id: r.id }))}">Edit</a>` : ''}
  </div>`;
}

function controlsCountHtml(r) {
  const n = Number(r.control_count || 0);
  return `<span class="badge text-bg-light">${esc(n === 1 ? '1 control' : `${n} controls`)}</span>`;
}

function riskCellHtml(r) {
  const threat = String(r.threat_summary || '').trim();
  const title = riskDisplayTitle(r);
  return `<div class="fw-semibold"><a href="${esc(riskHref(r.id, framework))}">${esc(title)}</a></div>${threat && threat !== title && !title.endsWith(threat.slice(0, 120)) ? `<div class="small-muted text-truncate" style="max-width:420px;">${esc(threat)}</div>` : ''}`;
}

function renderRows(items) {
  const rows = $('rows');
  if (!items.length) {
    rows.innerHTML =
      '<tr><td colspan="10" class="p-4 text-center small-muted">No risks match. Use “Add a risk” to record the first one.</td></tr>';
    return;
  }
  rows.innerHTML = items
    .map(
      (r) => `<tr data-risk-row="${esc(r.id)}">
      <td>${riskCellHtml(r)}</td>
      <td>${esc(r.asset || '')}<div class="small-muted">${esc([r.category?.name, r.subcategory?.name].filter(Boolean).join(' › '))}</div></td>
      <td>${methodBadgeHtml(methodOf(r))}${(r.risk_types || []).length ? `<div class="mt-1">${riskTypeBadgesHtml(r.risk_types)}</div>` : ''}</td>
      <td>${partyBadgesHtml(r.interested_parties, framework)}</td>
      <td>${userPillHtml(r.risk_owner)}</td>
      <td class="text-end" data-sort="${esc(Number(r.risk_score || 0))}">${ratingBadge(r, 'inherent')}</td>
      <td class="text-end" data-sort="${esc(Number(r.residual_risk_score || 0))}">${ratingBadge(r, 'residual')}</td>
      <td>${esc(treatmentLabel(r.treatment_strategy))}<div class="small-muted">${esc(treatmentStatusLabel(r.treatment_status))}${r.treatment_due_at ? ` · due ${esc(r.treatment_due_at)}` : ''}</div></td>
      <td data-sort="${esc(Number(r.control_count || 0))}">${controlsCountHtml(r)}</td>
      <td class="no-print">${actionsHtml(r, { rate: true })}</td>
    </tr>`,
    )
    .join('');
}

// A rated risk shows its score in its severity band; an unrated one says so.
function ratingBadge(r, kind) {
  const likelihood = kind === 'inherent' ? r.register_likelihood : r.register_residual_likelihood;
  const impact = kind === 'inherent' ? r.register_impact : r.register_residual_impact;
  if (likelihood == null || impact == null) return severityBadge(null, thresholds);
  return severityBadge(Number(likelihood) * Number(impact), thresholds);
}

function wireSampleButtons(root, lookup) {
  root.querySelectorAll('[data-sample-risk]').forEach((el) =>
    el.addEventListener('click', (ev) => {
      ev.preventDefault();
      const risk = lookup(el.dataset.sampleRisk || '');
      openAuditSampleModal({
        me,
        entityType: 'risk',
        entityId: el.dataset.sampleRisk || '',
        title: risk ? riskDisplayTitle(risk) : 'Risk',
        framework,
        statusEl: status,
      });
    }),
  );
}

async function loadRisks() {
  if (!canViewRisks) return;
  showTableLoading($('rows'), 10, 'Loading risks…');
  const u = new URL(base, location.origin);
  if (framework) u.searchParams.set('framework', framework);
  if ($('q').value) u.searchParams.set('q', $('q').value.trim());
  setCsvParam(u, 'category_id', selectedFilterPillValues($('categoryFilter')));
  setCsvParam(u, 'subcategory_id', selectedFilterPillValues($('subcategoryFilter')));
  try {
    const data = await apiGet(u.pathname + u.search);
    const method = $('methodFilter').value;
    const treatment = $('treatmentFilter').value;
    currentItems = [...(data?.items || [])]
      .filter((r) => !method || methodOf(r) === method)
      .filter((r) => !treatment || String(r.treatment_status || 'open') === treatment)
      .sort(defaultRiskSort);
    renderRows(currentItems);
    markDefaultRiskSort();
    wireSampleButtons($('rows'), (id) => currentItems.find((x) => String(x.id) === String(id)));
    if (selectedRisk) {
      const fresh = currentItems.find((x) => x.id === selectedRisk.id);
      if (fresh) selectRisk(fresh, { scroll: false });
    }
    $('resultMeta').textContent =
      `${currentItems.length} of ${Number(data?.total || currentItems.length)} risks shown; control mappings for ${data?.framework || framework || 'the current framework'}.`;
  } catch (e) {
    $('rows').innerHTML =
      '<tr><td colspan="10" class="p-4 text-center text-danger">Failed to load risks.</td></tr>';
    notify(`Failed to load risks: ${String(e)}`);
  }
}

// --- CIA risks ------------------------------------------------------------------------------

async function loadCiaRisks() {
  if (!canViewRisks) return;
  showTableLoading($('ciaRows'), 8, 'Loading CIA risks…');
  const u = new URL(base, location.origin);
  if (framework) u.searchParams.set('framework', framework);
  const chosen = selectedFilterPillValues($('typeFilter'));
  setCsvParam(u, 'risk_type', chosen.length ? chosen : RISK_TYPES);
  try {
    const data = await apiGet(u.pathname + u.search);
    const items = [...(data?.items || [])]
      .filter((r) => (r.risk_types || []).length)
      .sort(defaultRiskSort);
    $('ciaRows').innerHTML = items.length
      ? items
          .map(
            (r) => `<tr>
        <td>${riskCellHtml(r)}</td>
        <td>${esc(r.asset || '')}</td>
        <td>${riskTypeBadgesHtml(r.risk_types)}</td>
        <td>${userPillHtml(r.risk_owner)}</td>
        <td class="text-end" data-sort="${esc(Number(r.risk_score || 0))}">${scoreBadge(r.risk_score)}</td>
        <td class="text-end" data-sort="${esc(Number(r.residual_risk_score || 0))}">${scoreBadge(r.residual_risk_score)}</td>
        <td data-sort="${esc(Number(r.control_count || 0))}">${controlsCountHtml(r)}</td>
        <td class="no-print">${actionsHtml(r)}</td>
      </tr>`,
          )
          .join('')
      : '<tr><td colspan="8" class="p-4 text-center small-muted">No CIA risks yet. Use “Add a CIA risk” to record one.</td></tr>';
    wireSampleButtons($('ciaRows'), (id) => items.find((x) => String(x.id) === String(id)));
    $('ciaMeta').textContent =
      `${items.length} information-security risk${items.length === 1 ? '' : 's'}${chosen.length ? ` tagged ${chosen.join(', ')}` : ''}.`;
    loaded.cia = true;
  } catch (e) {
    $('ciaRows').innerHTML =
      '<tr><td colspan="8" class="p-4 text-center text-danger">Failed to load CIA risks.</td></tr>';
    notify(`Failed to load CIA risks: ${String(e)}`);
  }
}

// --- Heatmap, thresholds and the rating card ------------------------------------------------

function ratingOptions(id) {
  $(id).innerHTML =
    '<option value="">Not assessed</option>' +
    Array.from({ length: 5 }, (_, i) => `<option value="${i + 1}">${i + 1}</option>`).join('');
}

function renderHeatmap() {
  const mode = document.querySelector('input[name="scoreMode"]:checked')?.value || 'inherent';
  const byCell = new Map();
  for (const cell of heatmapCounts[mode] || [])
    byCell.set(`${cell.likelihood}:${cell.impact}`, cell.count);
  let html =
    '<thead><tr><th scope="col">Likelihood \\ Impact</th>' +
    [1, 2, 3, 4, 5].map((i) => `<th scope="col">${i}</th>`).join('') +
    '</tr></thead><tbody>';
  for (let likelihood = 5; likelihood >= 1; likelihood--) {
    html += `<tr><th scope="row">${likelihood}</th>`;
    for (let impact = 1; impact <= 5; impact++) {
      const score = likelihood * impact;
      const amount = byCell.get(`${likelihood}:${impact}`) || 0;
      html += `<td title="Likelihood ${likelihood}, impact ${impact}: ${amount} risks" style="background-color:${esc(severityBadgeColor(score))}">${amount ? `<strong>${amount}</strong>` : '·'}</td>`;
    }
    html += '</tr>';
  }
  $('heatmap').innerHTML = html + '</tbody>';
}

function severityBadgeColor(score) {
  const span = document.createElement('span');
  span.innerHTML = severityBadge(score, thresholds);
  return span.firstElementChild?.style?.backgroundColor || '#eef0f4';
}

function showThresholds() {
  for (const [key, id] of Object.entries({
    low_max: 'lowMax',
    moderate_max: 'moderateMax',
    high_max: 'highMax',
  }))
    $(id).value = thresholds[key];
}

// The heatmap carries the thresholds that band every score on the page.
async function loadHeatmap() {
  if (!registerEnabled) return;
  try {
    const data = await apiGet(`${base}/register/heatmap`);
    heatmapCounts = data;
    if (data.thresholds) thresholds = data.thresholds;
    showThresholds();
    renderHeatmap();
    loaded.heatmap = true;
  } catch (e) {
    notify(`Could not load the risk heatmap: ${String(e)}`);
  }
}

function selectRisk(r, { scroll = true } = {}) {
  selectedRisk = r;
  $('ratingCard').style.display = '';
  $('ratingTitle').textContent = riskDisplayTitle(r);
  for (const [id, key] of Object.entries({
    inherentLikelihood: 'register_likelihood',
    inherentImpact: 'register_impact',
    residualLikelihood: 'register_residual_likelihood',
    residualImpact: 'register_residual_impact',
  }))
    $(id).value = r[key] || '';
  $('treatmentStrategy').value = r.treatment_strategy || '';
  $('treatmentStatus').value = r.treatment_status || 'open';
  $('treatmentDue').value = r.treatment_due_at || '';
  $('treatmentPlan').value = r.treatment_plan || '';
  $('editScenario').href = riskWizardHref(framework, { id: r.id });
  if (!canManageRisks)
    $('ratingForm')
      .querySelectorAll('input, textarea, select')
      .forEach((el) => (el.disabled = true));
  if (scroll) $('ratingCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function closeRating() {
  selectedRisk = null;
  $('ratingCard').style.display = 'none';
}

// --- PESTLE(E) --------------------------------------------------------------------------------

function pestleRelevanceBadge(level) {
  const code = String(level?.code || '').toLowerCase();
  const cls =
    code === 'high'
      ? 'text-bg-danger'
      : code === 'medium'
        ? 'text-bg-warning'
        : code === 'low'
          ? 'text-bg-success'
          : 'text-bg-light border';
  return `<span class="badge ${cls}">${esc(level?.label || 'N/A')}</span>`;
}

async function loadPestle() {
  if (!canViewPestle || !featureEnabled(me, 'pestle')) return;
  showTableLoading($('pestleRows'), 7, 'Loading PESTLE(E) factors…');
  try {
    if (!pestleMeta) {
      pestleMeta = await apiGet('/api/v1/pestle/meta');
      $('pestleTypeFilter').innerHTML =
        '<option value="">All types</option>' +
        (pestleMeta.types || [])
          .map((t) => `<option value="${esc(t)}">${esc(t)}</option>`)
          .join('');
      $('pestleLensFilter').innerHTML =
        '<option value="">All lenses</option>' +
        (pestleMeta.lenses || [])
          .map((l) => `<option value="${esc(l)}">${esc(l)}</option>`)
          .join('');
    }
    const u = new URL('/api/v1/pestle/items', location.origin);
    if (framework) u.searchParams.set('framework', framework);
    if ($('pestleQ').value.trim()) u.searchParams.set('q', $('pestleQ').value.trim());
    if ($('pestleTypeFilter').value) u.searchParams.set('type', $('pestleTypeFilter').value);
    if ($('pestleLensFilter').value) u.searchParams.set('lens', $('pestleLensFilter').value);
    u.searchParams.set('limit', '500');
    const data = await apiGet(u.pathname + u.search);
    pestleItems = data?.items || [];
    $('pestleRows').innerHTML = pestleItems.length
      ? pestleItems
          .map(
            (item) => `<tr>
        <td><span class="badge text-bg-info">${esc(item.type || '')}</span></td>
        <td>${esc(item.lens || '')}</td>
        <td><a class="fw-semibold" href="${esc(pestleHref(item.id, framework))}">${esc(item.item || 'PESTLE(E) factor')}</a>${item.rationale ? `<div class="small-muted text-truncate" style="max-width:420px;">${esc(item.rationale)}</div>` : ''}</td>
        <td>${pestleRelevanceBadge(item.overall_relevance)}</td>
        <td>${esc(String(item.business_process_count ?? (item.business_processes || []).length ?? 0))}</td>
        <td>${esc(String(item.clause_count ?? (item.clauses || []).length ?? 0))}</td>
        <td class="no-print"><a class="btn btn-sm btn-outline-secondary" href="${esc(pestleHref(item.id, framework))}">Open</a></td>
      </tr>`,
          )
          .join('')
      : '<tr><td colspan="7" class="p-4 text-center small-muted">No PESTLE(E) factors yet. Use “Add a PESTLE(E) factor” to record one.</td></tr>';
    $('pestleMeta').textContent =
      `${pestleItems.length} factor${pestleItems.length === 1 ? '' : 's'} for ${data?.framework || framework || 'the current framework'}. Open a factor to score its relevance to business processes and clauses.`;
    loaded.pestle = true;
  } catch (e) {
    $('pestleRows').innerHTML =
      '<tr><td colspan="7" class="p-4 text-center text-danger">Failed to load PESTLE(E) factors.</td></tr>';
    notify(`Failed to load PESTLE(E) factors: ${String(e)}`);
  }
}

// --- Library ----------------------------------------------------------------------------------

function renderLibrary() {
  const method = $('libraryMethodFilter').value;
  const q = $('librarySearch').value.trim().toLowerCase();
  const items = library.filter(
    (item) =>
      (!method || (item.method || 'risk') === method) &&
      (!q ||
        `${item.name} ${item.threat_summary} ${item.treatment_guidance}`.toLowerCase().includes(q)),
  );
  $('libraryList').innerHTML = items.length
    ? items
        .map((item) => {
          const example = item.suggested_assessment || {};
          const kind =
            item.method === 'pestle'
              ? `${methodBadgeHtml('pestle')} <span class="small-muted">${esc(item.pestle_type || '')} · ${esc(item.lens || '')}</span>`
              : `${methodBadgeHtml((item.risk_types || []).length ? 'cia' : 'general')} ${riskTypeBadgesHtml(item.risk_types)}`;
          return `<div class="list-group-item d-flex flex-wrap justify-content-between gap-2">
        <div style="min-width:0">
          <div class="fw-semibold">${esc(item.name)}</div>
          <div class="mt-1">${kind}</div>
          <div class="small-muted mt-1">${esc(item.threat_summary || '')}</div>
          ${item.treatment_guidance ? `<div class="small-muted">Guidance: ${esc(item.treatment_guidance)}</div>` : ''}
          ${example.inherent_impact ? `<div class="small-muted">Example rating: today ${esc(String(example.inherent_likelihood))} × ${esc(String(example.inherent_impact))}; after controls ${esc(String(example.residual_likelihood))} × ${esc(String(example.residual_impact))}. Review for your organisation.</div>` : ''}
          ${(example.keen_af_control_refs || []).length ? `<div class="small-muted">Links KEEN-AF controls ${esc(example.keen_af_control_refs.join(', '))}.</div>` : ''}
        </div>
        <div class="d-flex gap-1 align-self-start no-print">
          ${canManageRisks ? `<a class="btn btn-sm btn-outline-primary" href="${esc(riskWizardHref(framework, { template: item.id }))}">Use template</a>` : ''}
          ${canManageRisks ? `<button type="button" class="btn btn-sm btn-outline-danger" data-delete-library="${esc(item.id)}">Delete</button>` : ''}
        </div>
      </div>`;
        })
        .join('')
    : `<div class="list-group-item small-muted">${library.length ? 'No templates match.' : 'No templates yet. On the Risks tab, choose Rate / treat on a risk and then “Save as a template”.'}</div>`;
}

async function loadLibrary() {
  try {
    library = (await apiGet(`${base}/library`)).items || [];
    renderLibrary();
    loaded.library = true;
  } catch (e) {
    notify(`Could not load the risk library: ${String(e)}`);
  }
}

// --- Controls without risks -------------------------------------------------------------------

async function loadControlsWithoutRisks() {
  if (!canViewRisks) return;
  showTableLoading($('controlsWithoutRisksRows'), 4, 'Loading controls…');
  const u = new URL(`${base}/controls-without-risks`, location.origin);
  if (framework) u.searchParams.set('framework', framework);
  if ($('controlsWithoutRisksQ').value)
    u.searchParams.set('q', $('controlsWithoutRisksQ').value.trim());
  u.searchParams.set('limit', '5000');
  try {
    const data = await apiGet(u.pathname + u.search);
    const items = data?.items || [];
    $('controlsWithoutRisksRows').innerHTML = items.length
      ? items
          .map(
            (c) => `<tr>
      <td data-sort="${esc(c.ref || '')}"><a class="fw-semibold" href="${esc(controlHref(c.id, framework))}">${esc(c.ref || 'Control')}</a></td>
      <td>${esc(c.title || '')}</td>
      <td>${c.in_scope === false ? '<span class="badge text-bg-secondary">Out of scope</span>' : '<span class="badge text-bg-success">In scope</span>'}</td>
      <td class="no-print"><a class="btn btn-sm btn-outline-secondary" href="${esc(controlHref(c.id, framework))}">View</a></td>
    </tr>`,
          )
          .join('')
      : '<tr><td colspan="4" class="p-4 text-center small-muted">All controls currently have at least one associated risk.</td></tr>';
    loaded.controlsWithout = true;
    const total = Number(data?.total || items.length);
    $('controlsWithoutRisksMeta').textContent =
      `${total} control${total === 1 ? '' : 's'} without associated risks for ${data?.framework || framework || 'the current framework'}.`;
  } catch (e) {
    $('controlsWithoutRisksRows').innerHTML =
      '<tr><td colspan="4" class="p-4 text-center text-danger">Failed to load controls without risks.</td></tr>';
    notify(`Failed to load controls without risks: ${String(e)}`);
  }
}

// --- Tabs -------------------------------------------------------------------------------------

function showTab(tabButton) {
  if (!tabButton) return;
  try {
    window.bootstrap?.Tab?.getOrCreateInstance(tabButton)?.show();
  } catch {}
}

function setTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (!tabName || tabName === 'risks') p.delete('tab');
  else p.set('tab', tabName);
  const qs = p.toString();
  history.replaceState({}, '', `${location.pathname}${qs ? `?${qs}` : ''}${location.hash || ''}`);
}

// Maps, so a tab name from the URL only ever matches a tab defined here.
const TAB_LOADERS = new Map([
  ['risks', null],
  ['cia', () => (loaded.cia ? null : loadCiaRisks())],
  ['pestle', () => (loaded.pestle ? null : loadPestle())],
  ['library', () => (loaded.library ? null : loadLibrary())],
  [
    'visualisations',
    () =>
      riskVisualisationsAreLoaded()
        ? null
        : loadRiskVisualisations().catch((e) =>
            notify(`Failed to load risk visualisations: ${String(e)}`),
          ),
  ],
  ['controls-without-risks', () => (loaded.controlsWithout ? null : loadControlsWithoutRisks())],
]);
const TAB_BUTTONS = new Map([
  ['risks', 'risksListTab'],
  ['cia', 'ciaTab'],
  ['pestle', 'pestleTab'],
  ['library', 'libraryTab'],
  ['visualisations', 'riskVisualisationsTab'],
  ['controls-without-risks', 'controlsWithoutRisksTab'],
]);

for (const [name, id] of TAB_BUTTONS) {
  $(id)?.addEventListener('shown.bs.tab', () => {
    setTabQuery(name);
    TAB_LOADERS.get(name)?.();
  });
}

// --- Events -----------------------------------------------------------------------------------

setupFilterPickers();
setFilterPillOptions($('typeFilter'), RISK_TYPE_FILTER_OPTIONS, { dispatchChange: false });
for (const id of ['inherentLikelihood', 'inherentImpact', 'residualLikelihood', 'residualImpact'])
  ratingOptions(id);
$('treatmentStrategy').innerHTML = TREATMENT_STRATEGIES.map(
  ([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`,
).join('');
$('treatmentStatus').innerHTML = TREATMENT_STATUSES.map(
  ([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`,
).join('');

$('q').addEventListener('input', debounce(loadRisks, 250));
$('categoryFilter').addEventListener('change', () => {
  renderSubcategoryFilter();
  loadRisks();
});
$('subcategoryFilter').addEventListener('change', loadRisks);
$('methodFilter').addEventListener('change', loadRisks);
$('treatmentFilter').addEventListener('change', loadRisks);
$('reload').addEventListener('click', loadRisks);
$('typeFilter').addEventListener('change', loadCiaRisks);
$('pestleQ').addEventListener('input', debounce(loadPestle, 250));
$('pestleTypeFilter').addEventListener('change', loadPestle);
$('pestleLensFilter').addEventListener('change', loadPestle);
$('libraryMethodFilter').addEventListener('change', renderLibrary);
$('librarySearch').addEventListener('input', debounce(renderLibrary, 150));
$('controlsWithoutRisksQ').addEventListener('input', debounce(loadControlsWithoutRisks, 250));
$('reloadControlsWithoutRisks').addEventListener('click', loadControlsWithoutRisks);
document
  .querySelectorAll('input[name="scoreMode"]')
  .forEach((input) => input.addEventListener('change', renderHeatmap));
$('rows').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-rate]');
  if (!btn) return;
  const r = currentItems.find((item) => item.id === btn.dataset.rate);
  if (r) selectRisk(r);
});
$('closeRating').addEventListener('click', closeRating);
$('ratingForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!selectedRisk || !canManageRisks) return;
  const val = (id) => ($(id).value ? Number($(id).value) : null);
  const payload = {
    register_likelihood: val('inherentLikelihood'),
    register_impact: val('inherentImpact'),
    register_residual_likelihood: val('residualLikelihood'),
    register_residual_impact: val('residualImpact'),
    treatment_strategy: $('treatmentStrategy').value,
    treatment_status: $('treatmentStatus').value,
    treatment_due_at: $('treatmentDue').value || null,
    treatment_plan: $('treatmentPlan').value,
  };
  try {
    await apiPatch(`${base}/${encodeURIComponent(selectedRisk.id)}/register`, payload);
    loaded.cia = false;
    await Promise.all([loadRisks(), loadHeatmap()]);
    notify('Rating and treatment saved.', 'success');
  } catch (err) {
    notify(`Could not save the rating: ${String(err)}`);
  }
});
$('thresholdForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!canManageRisks) return;
  try {
    thresholds = await apiPatch(`${base}/register/settings`, {
      low_max: Number($('lowMax').value),
      moderate_max: Number($('moderateMax').value),
      high_max: Number($('highMax').value),
    });
    renderHeatmap();
    renderRows(currentItems);
    wireSampleButtons($('rows'), (id) => currentItems.find((x) => String(x.id) === String(id)));
    notify('Risk thresholds saved.', 'success');
  } catch (err) {
    notify(`Could not save thresholds: ${String(err)}`);
  }
});
$('addToLibrary').addEventListener('click', async () => {
  const r = selectedRisk;
  if (!r || !canManageRisks) return;
  const name = prompt('Name this reusable template', riskDisplayTitle(r).slice(0, 100));
  if (!name) return;
  try {
    await apiPost(`${base}/library`, {
      name,
      method: 'risk',
      threat_summary: r.threat_summary,
      risk_types: r.risk_types || [],
      treatment_guidance: r.treatment_plan || '',
      source_risk_id: r.id,
      suggested_assessment: {
        asset: r.asset,
        inherent_likelihood: r.register_likelihood,
        inherent_impact: r.register_impact,
        residual_likelihood: r.register_residual_likelihood,
        residual_impact: r.register_residual_impact,
      },
    });
    await loadLibrary();
    notify('Template saved to the risk library.', 'success');
  } catch (err) {
    notify(`Could not save the template: ${String(err)}`);
  }
});
$('libraryList').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-delete-library]');
  if (!btn || !canManageRisks || !confirm('Delete this reusable template?')) return;
  try {
    await apiDelete(`${base}/library/${encodeURIComponent(btn.dataset.deleteLibrary)}`);
    await loadLibrary();
    notify('Template deleted.', 'success');
  } catch (err) {
    notify(`Could not delete the template: ${String(err)}`);
  }
});
$('exportRegister').href = `${base}/register/export.csv?framework=${encodeURIComponent(framework)}`;
$('importForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!canManageRisks) return;
  const file = $('importFile').files[0];
  if (!file) return;
  const body = new FormData();
  body.append('file', file);
  try {
    const response = await apiPostForm(
      `${base}/register/import.csv?framework=${encodeURIComponent(framework)}`,
      body,
    );
    $('importForm').reset();
    await Promise.all([loadRisks(), loadHeatmap()]);
    notify(`${response.created} risks imported.`, 'success');
  } catch (err) {
    notify(`Could not import the CSV: ${String(err)}`);
  }
});

initRiskVisualisations({
  framework,
  topRisks: $('riskVizTopRisks'),
  topControls: $('riskVizTopControls'),
  graph: $('riskControlsGraph'),
  heatmap: $('riskControlsHeatmap'),
  graphMeta: $('riskControlsGraphMeta'),
  heatmapMeta: $('riskControlsHeatmapMeta'),
  scoreMode: document.querySelector('input[name="riskVizScoreMode"]:checked')?.value || 'residual',
});
$('reloadRiskVisualisations').addEventListener('click', () =>
  loadRiskVisualisations({ force: true }).catch((e) => notify(String(e))),
);
$('riskVizTopRisks').addEventListener('change', () =>
  loadRiskVisualisations().catch((e) => notify(String(e))),
);
$('riskVizTopControls').addEventListener('change', () =>
  loadRiskVisualisations().catch((e) => notify(String(e))),
);
for (const input of document.querySelectorAll('input[name="riskVizScoreMode"]')) {
  input.addEventListener('change', () => {
    if (input.checked) setRiskVizScoreMode(input.value);
  });
}

// --- Start ------------------------------------------------------------------------------------

if (!canViewRisks) {
  notify('You need risk.read permission to view risks.');
  $('rows').innerHTML =
    '<tr><td colspan="10" class="p-4 text-center text-danger">risk.read permission required</td></tr>';
  for (const id of [
    'newRisk',
    'newCiaRisk',
    'newPestleRisk',
    'riskVisualisationsTabItem',
    'controlsWithoutRisksTabItem',
  ])
    if ($(id)) $(id).style.display = 'none';
} else {
  if (!canManageRisks) {
    for (const id of ['newRisk', 'newCiaRisk', 'newPestleRisk', 'thresholdForm', 'importForm'])
      if ($(id)) $(id).style.display = 'none';
  }
  // Without the register feature the list takes the heatmap's room.
  if (!registerEnabled) $('riskListColumn').classList.remove('col-xl-8');
  try {
    categories = (await apiGet(`${base}/categories`))?.items || [];
    renderCategoryFilters();
  } catch (e) {
    notify(`Could not load asset categories: ${String(e)}`);
  }
  await Promise.all([loadRisks(), loadHeatmap()]);
  if (requestedTab === 'register') setTabQuery('risks');
  if (TAB_BUTTONS.has(requestedTab) && requestedTab !== 'risks') {
    showTab($(TAB_BUTTONS.get(requestedTab)));
    await TAB_LOADERS.get(requestedTab)?.();
  }
}
