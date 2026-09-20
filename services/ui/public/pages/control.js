import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  esc,
  fmtTs,
  tsSortKey,
  toast,
  debounce,
  qs,
  qint,
  setQuery,
  enableTableSorting,
  shorten,
  getSourceMeta,
  sourceBadgeHtml,
  sourceLabel,
  upstreamLinkHtml,
  getCurrentFramework,
  withFramework,
  showTableLoading,
  collectOffsetPagerButtons,
  setOffsetPagerDisabled,
  offsetPagerState,
  wireOffsetPagerButtons,
  loadEntityChangelog,
  userPillHtml,
} from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();
enableTableSorting();


const id = qs('id');
const status = document.getElementById('status');
const tbody = document.getElementById('rows');
const meta = document.getElementById('resultMeta');

const localQ = document.getElementById('localQ');
const sourceFilter = document.getElementById('sourceFilter');
const linksEl = document.getElementById('ctrlLinks');
const selLimit = document.getElementById('limit');
const clausesCard = document.getElementById('clausesCard');
const clausesView = document.getElementById('clausesView');
const clausesCount = document.getElementById('clausesCount');
const risksCard = document.getElementById('risksCard');
const risksView = document.getElementById('risksView');
const risksCount = document.getElementById('risksCount');
const adminClausesCard = document.getElementById('adminClausesCard');
const controlJustificationCard = document.getElementById('controlJustificationCard');
const adminClausesList = document.getElementById('adminClausesList');
const controlUpstreamUrl = document.getElementById('controlUpstreamUrl');
const saveControlUpstreamUrlBtn = document.getElementById('saveControlUpstreamUrl');
const controlJustification = document.getElementById('controlJustification');
const saveControlJustificationBtn = document.getElementById('saveControlJustification');
const saveClauseLinksBtn = document.getElementById('saveClauseLinks');
const editClauseApplicabilityBtn = document.getElementById('editClauseApplicability');
const controlsBreadcrumb = document.getElementById('controlsBreadcrumb');
const controlEvidenceTab = document.getElementById('controlEvidenceTab');
const controlClausesTab = document.getElementById('controlClausesTab');
const controlRisksTab = document.getElementById('controlRisksTab');
const controlIsmsTab = document.getElementById('controlIsmsTab');
const controlIsmsView = document.getElementById('controlIsmsView');
const controlChangelogTab = document.getElementById('controlChangelogTab');
const controlChangelogEl = document.getElementById('controlChangelog');
const controlRisksTabItem = document.getElementById('controlRisksTabItem');
const controlRisksPane = document.getElementById('controlRisksPane');
const controlInterestedPartiesTabItem = document.getElementById('controlInterestedPartiesTabItem');
const controlInterestedPartiesTab = document.getElementById('controlInterestedPartiesTab');
const controlInterestedPartiesPane = document.getElementById('controlInterestedPartiesPane');
const interestedPartiesCard = document.getElementById('interestedPartiesCard');
const interestedPartiesView = document.getElementById('interestedPartiesView');
const interestedPartiesCount = document.getElementById('interestedPartiesCount');
const canViewRisks = !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
const canViewInterestedParties = !!(me?.is_admin || me?.can_view_interested_parties || me?.can_manage_interested_parties || me?.can_view_risks || me?.can_manage_risks);
const canViewIsms = !!(me?.is_admin || me?.can_view_isms || me?.can_manage_isms || me?.can_view_risks || me?.can_manage_risks);

// One shared offset-pager wiring path covers header buttons (#prev/#next)
// and footer buttons ([data-pager]).
const pagerButtons = collectOffsetPagerButtons();

let sourceMeta = {};
let ctrl = null;
let clauseCatalog = [];
let clauseApplicabilityEditMode = false;
let interestedPartiesLoaded = false;
let ismsLoaded = false;

let state = {
  q: qs('q', ''),
  source: qs('source', ''),
  limit: qint('limit', 50),
  offset: qint('offset', 0),
  total: 0,
};

// Clamp limit to UI options
if (![25, 50, 100, 200].includes(state.limit)) state.limit = 50;
if (state.offset < 0) state.offset = 0;


function renderControlChrome() {
  if (!ctrl) return;
  const titleEl = document.getElementById('ctrlTitle');
  const metaEl = document.getElementById('ctrlMeta');
  if (titleEl) titleEl.textContent = `${ctrl.ref}  ${ctrl.title || ''}`.trim();
  const bits = [ctrl.framework, ctrl.type, ctrl.in_scope ? 'in scope' : 'out of scope'];
  if (ctrl.justification) bits.push(`Justification: ${ctrl.justification}`);
  if (metaEl) metaEl.textContent = bits.filter(Boolean).join(' • ');
}

function renderControlJustificationCard() {
  if (!controlJustificationCard) return;
  const canEditJustification = !!me?.is_admin;
  controlJustificationCard.style.display = canEditJustification ? '' : 'none';
  if (!canEditJustification) return;
  if (controlJustification) controlJustification.value = ctrl?.justification || '';
}

function setControlTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (!tabName || tabName === 'evidence') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}?${p.toString()}`);
}

function activateControlTab(tabName) {
  let name = tabName || 'evidence';
  if (name === 'interestedparties') name = 'interested-parties';
  if (name === 'risks' && !canViewRisks) name = 'evidence';
  if (name === 'interested-parties' && !canViewInterestedParties) name = 'evidence';
  if (name === 'isms' && !canViewIsms) name = 'evidence';
  if ((name === 'effectiveness' || name === 'effectiveness-measures') && !canViewIsms) name = 'evidence';
  const tab = {
    evidence: controlEvidenceTab,
    clauses: controlClausesTab,
    risks: controlRisksTab,
    'interested-parties': controlInterestedPartiesTab,
    isms: controlIsmsTab,
    // Backwards-compatible: old links to the removed dedicated
    // Effectiveness Measures tab now land on the combined ISMS tab.
    effectiveness: controlIsmsTab,
    'effectiveness-measures': controlIsmsTab,
    changelog: controlChangelogTab,
  }[name] || controlEvidenceTab;
  if (!tab || !window.bootstrap?.Tab) return;
  window.bootstrap.Tab.getOrCreateInstance(tab).show();
}

function wireControlTabs() {
  if (!canViewRisks) {
    controlRisksTabItem?.classList.add('d-none');
    controlRisksPane?.classList.add('d-none');
  }
  if (!canViewInterestedParties) {
    controlInterestedPartiesTabItem?.classList.add('d-none');
    controlInterestedPartiesPane?.classList.add('d-none');
  }
  controlEvidenceTab?.addEventListener('shown.bs.tab', () => setControlTabQuery('evidence'));
  controlClausesTab?.addEventListener('shown.bs.tab', () => setControlTabQuery('clauses'));
  controlRisksTab?.addEventListener('shown.bs.tab', () => setControlTabQuery('risks'));
  controlInterestedPartiesTab?.addEventListener('shown.bs.tab', async () => {
    setControlTabQuery('interested-parties');
    await loadLinkedInterestedParties();
  });
  controlIsmsTab?.addEventListener('shown.bs.tab', async () => {
    setControlTabQuery('isms');
    await loadControlIsms();
  });
  controlChangelogTab?.addEventListener('shown.bs.tab', () => {
    setControlTabQuery('changelog');
    loadControlChangelog();
  });
  activateControlTab(qs('tab', 'evidence'));
}

wireControlTabs();

async function loadControlChangelog() {
  if (!controlChangelogEl || !id) return;
  try {
    await loadEntityChangelog(
      controlChangelogEl,
      `/api/v1/controls/${encodeURIComponent(id)}/changelog?limit=100`,
      {empty: 'No control changes have been recorded yet.'}
    );
  } catch (e) {
    controlChangelogEl.innerHTML = '<div class="text-danger p-3">Failed to load changelog.</div>';
  }
}

function _ismsEntityRows(data) {
  const groups = [
    ['Objectives', data.objectives, (x) => x.goal || x.requirement || 'Objective', () => '/isms.html?tab=objectives'],
    ['Documents', data.documents, (x) => x.title || 'Document', () => '/isms.html?tab=documents'],
    ['Organisation Chart', data.org_nodes, (x) => x.name || 'Org node', () => '/isms.html?tab=org'],
    ['Assets', data.assets, (x) => x.asset || 'Asset', () => '/isms.html?tab=assets'],
    ['Effectiveness Measures', data.effectiveness_measures, (x) => x.summary || x.metric || x.effectiveness_measure || 'Effectiveness measure', (x) => `/isms-effectiveness-measure.html?id=${encodeURIComponent(x.id)}`],
    ['Application Configuration', data.application_configurations, (x) => x.display || x.source?.label || 'App config', () => '/isms.html?tab=appconfig'],
    ['Meetings', data.meetings, (x) => x.title || 'Meeting', (x) => x.id ? `/isms-meeting.html?id=${encodeURIComponent(x.id)}` : '/isms.html?tab=meetings'],
  ];
  const rows = [];
  for (const [label, items, titleFn, hrefFn] of groups) {
    for (const item of items || []) {
      const href = typeof hrefFn === 'function' ? hrefFn(item) : hrefFn;
      rows.push(`<tr><td>${esc(label)}</td><td class="wrap"><a class="fw-semibold" href="${esc(withFramework(href, ctrl?.framework || framework))}">${esc(shorten(titleFn(item), 180))}</a></td><td class="small-muted">${esc(fmtTs(item.updated_at) || '')}</td></tr>`);
    }
  }
  if (!rows.length) return '<div class="small-muted">No ISMS entities are currently linked to this control.</div>';
  return `<div class="table-responsive"><table class="table table-sm align-middle mb-0"><thead><tr><th style="width:190px;">Type</th><th>Entity</th><th style="width:170px;">Updated</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}


async function loadControlIsms({force = false} = {}) {
  if (!controlIsmsView || !id) return;
  if (!canViewIsms) { controlIsmsView.innerHTML = '<span class="small-muted">You do not have permission to view ISMS references.</span>'; return; }
  if (ismsLoaded && !force) return;
  controlIsmsView.innerHTML = '<span class="small-muted">Loading ISMS references…</span>';
  try {
    const data = await apiGet(`/api/v1/controls/${encodeURIComponent(id)}/isms?framework=${encodeURIComponent(ctrl?.framework || framework)}`);
    ismsLoaded = true;
    controlIsmsView.innerHTML = _ismsEntityRows(data || {});
  } catch (e) {
    ismsLoaded = false;
    controlIsmsView.innerHTML = '<span class="text-danger">Failed to load ISMS references.</span>';
  }
}

function syncInputs() {
  if (localQ) localQ.value = state.q;
  if (selLimit) selLimit.value = String(state.limit);
  // sourceFilter filled after options load
}

function controlScopedSourceHref(src) {
  const p = new URLSearchParams();
  if (ctrl?.framework || framework) p.set('framework', ctrl?.framework || framework);
  if (src) p.set('source', src);
  return `/source.html?${p.toString()}`;
}


function applicabilityLabel(v) {
  if (v === 'partially_applicable') return 'Partially applicable';
  if (v === 'applicable') return 'Applicable';
  return 'Not applicable';
}

function applicabilityBadge(v) {
  if (v === 'partially_applicable') return '<span class="badge text-bg-warning">Partially applicable</span>';
  if (v === 'applicable') return '<span class="badge badge-soft">Applicable</span>';
  return '<span class="badge text-bg-light">Not applicable</span>';
}

function renderLinkedClauses() {
  const links = Array.isArray(ctrl?.clauses) ? ctrl.clauses : [];
  if (!clausesCard || !clausesView) return;
  clausesCard.style.display = '';
  if (clausesCount) clausesCount.textContent = String(links.length);
  if (editClauseApplicabilityBtn) {
    editClauseApplicabilityBtn.style.display = me?.is_admin ? '' : 'none';
    editClauseApplicabilityBtn.textContent = clauseApplicabilityEditMode ? 'Hide edit' : 'Edit';
  }
  if (!links.length) {
    clausesView.innerHTML = '<span class="small-muted">No clauses linked to this control.</span>';
    return;
  }

  const rows = [...links].sort((a, b) => String(a.ref || '').localeCompare(String(b.ref || ''), undefined, {numeric: true}));
  clausesView.innerHTML = `
    <div class="table-responsive">
      <table class="table table-sm align-middle mb-0">
        <thead>
          <tr>
            <th style="width:120px;">Clause</th>
            <th>Title</th>
            <th style="width:190px;">Applicability</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((c) => {
            const clauseHref = c.clause_id
              ? withFramework(`/clause.html?id=${encodeURIComponent(c.clause_id)}`, ctrl?.framework || framework)
              : '';
            const refHtml = clauseHref
              ? `<a href="${esc(clauseHref)}">${esc(c.ref || '')}</a>`
              : esc(c.ref || '');
            return `<tr>
              <td class="fw-semibold">${refHtml}</td>
              <td class="wrap">${esc(c.title || '')}</td>
              <td>${applicabilityBadge(c.applicability)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;
}


function riskScoreBadge(score) {
  const n = Number(score || 0);
  const cls = n < 5 ? 'text-bg-success' : (n <= 12 ? 'text-bg-warning' : 'text-bg-danger');
  return `<span class="badge ${cls}">${esc(String(n))}</span>`;
}

async function loadLinkedRisks() {
  if (!risksCard || !risksView) return;
  if (!canViewRisks) {
    risksCard.style.display = 'none';
    return;
  }
  try {
    const data = await apiGet(`/api/v1/controls/${encodeURIComponent(id)}/risks?framework=${encodeURIComponent(ctrl?.framework || framework)}`);
    const items = data?.items || [];
    risksCard.style.display = '';
    if (risksCount) risksCount.textContent = String(items.length);
    if (!items.length) {
      risksView.innerHTML = '<span class="small-muted">No risks mapped to this control for this framework.</span>';
      return;
    }
    risksView.innerHTML = `
      <div class="table-responsive">
        <table class="table table-sm align-middle mb-0">
          <thead>
            <tr>
              <th>Asset</th>
              <th style="width:130px;">Category</th>
              <th style="width:150px;">Subcategory</th>
              <th style="width:170px;">Type</th>
              <th class="text-end" style="width:90px;">Risk</th>
              <th class="text-end" style="width:100px;">Residual</th>
              <th style="width:120px;">Owner</th>
            </tr>
          </thead>
          <tbody>
            ${items.map((r) => {
              const href = withFramework(`/risk.html?id=${encodeURIComponent(r.id || '')}`, ctrl?.framework || framework);
              const types = (r.risk_types || []).map((x) => `<span class="badge badge-soft me-1">${esc(x)}</span>`).join('');
              return `<tr>
                <td><a class="fw-semibold" href="${esc(href)}">${esc(r.asset || '')}</a><div class="small-muted text-truncate" style="max-width:360px;">${esc(r.threat_summary || '')}</div></td>
                <td>${esc(r.category?.name || '')}</td>
                <td>${esc(r.subcategory?.name || '')}</td>
                <td>${types || '<span class="small-muted">—</span>'}</td>
                <td class="text-end">${riskScoreBadge(r.risk_score)}</td>
                <td class="text-end">${riskScoreBadge(r.residual_risk_score)}</td>
                <td>${userPillHtml(r.risk_owner)}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    // Users without risk.read should not get a noisy control page.
    risksCard.style.display = 'none';
  }
}
async function loadLinkedInterestedParties({force = false} = {}) {
  if (!interestedPartiesCard || !interestedPartiesView) return;
  if (!canViewInterestedParties) {
    interestedPartiesCard.style.display = 'none';
    return;
  }
  if (interestedPartiesLoaded && !force) return;
  interestedPartiesCard.style.display = '';
  interestedPartiesView.innerHTML = '<span class="small-muted">Loading Interested Parties…</span>';
  try {
    const data = await apiGet(`/api/v1/controls/${encodeURIComponent(id)}/interested-parties?framework=${encodeURIComponent(ctrl?.framework || framework)}`);
    const items = data?.items || [];
    interestedPartiesLoaded = true;
    if (interestedPartiesCount) interestedPartiesCount.textContent = String(items.length);
    if (!items.length) {
      interestedPartiesView.innerHTML = '<span class="small-muted">No Interested Parties mapped to this control for this framework.</span>';
      return;
    }
    interestedPartiesView.innerHTML = `
      <div class="table-responsive">
        <table class="table table-sm align-middle mb-0">
          <thead><tr><th>Name</th><th>Nature of Interest</th><th>Note</th><th>Communications</th><th>Methods</th></tr></thead>
          <tbody>
            ${items.map((it) => {
              const href = withFramework(`/interested_party.html?id=${encodeURIComponent(it.id || '')}`, ctrl?.framework || framework);
              const comms = (it.communications || []).map((c) => `${esc(c.event || '')} / ${esc(c.with_whom || '')}`).join('<br>') || '<span class="small-muted">—</span>';
              const methods = Array.from(new Set((it.communications || []).flatMap((c) => c.methods || []))).map((m) => `<span class="badge badge-soft me-1 mb-1">${esc(m)}</span>`).join('') || '<span class="small-muted">—</span>';
              const note = (it.note || '').trim() ? esc(shorten(it.note, 140)) : '<span class="small-muted">—</span>';
              return `<tr><td><a class="fw-semibold" href="${esc(href)}">${esc(it.name?.name || '')}</a></td><td>${esc(it.nature?.name || '')}</td><td class="small text-wrap" style="min-width:220px;">${note}</td><td class="small">${comms}</td><td>${methods}</td></tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    interestedPartiesLoaded = false;
    if (interestedPartiesCount) interestedPartiesCount.textContent = '0';
    interestedPartiesView.innerHTML = '<span class="text-danger">Failed to load Interested Parties for this control.</span>';
  }
}


function flattenClauseTree(nodes, depth = 0, out = []) {
  for (const n of nodes || []) {
    out.push({...n, depth});
    flattenClauseTree(n.children || [], depth + 1, out);
  }
  return out;
}

function selectedClauseMap() {
  const m = new Map();
  for (const c of ctrl?.clauses || []) {
    if (c.clause_id) m.set(String(c.clause_id), c.applicability || 'applicable');
  }
  return m;
}

function clauseSelect(id) {
  if (!adminClausesList || !id || !window.CSS?.escape) return null;
  return adminClausesList.querySelector(`.clause-applicability[data-clause-id="${CSS.escape(String(id))}"]`);
}

function applyInferredParentClauseApplicability() {
  if (!adminClausesList) return;
  const flat = flattenClauseTree(clauseCatalog?.tree || []);
  const childrenByParent = new Map();
  for (const c of flat) {
    if (!c.parent_id) continue;
    const key = String(c.parent_id);
    if (!childrenByParent.has(key)) childrenByParent.set(key, []);
    childrenByParent.get(key).push(String(c.id || ''));
  }

  const byId = new Map(flat.map((c) => [String(c.id || ''), c]));

  const memo = new Map();
  function inferValue(clauseId) {
    const key = String(clauseId || '');
    if (memo.has(key)) return memo.get(key);

    const kids = childrenByParent.get(key) || [];
    if (!kids.length) {
      const value = (clauseSelect(key)?.value || '').trim();
      memo.set(key, value);
      return value;
    }

    const childValues = kids.map((kid) => inferValue(kid));
    const selected = childValues.filter(Boolean);
    let inferred = '';
    if (selected.length === 0) {
      inferred = '';
    } else if (selected.length === childValues.length && selected.every((v) => v === 'applicable')) {
      inferred = 'applicable';
    } else {
      inferred = 'partially_applicable';
    }

    const clause = byId.get(key);
    const isIntermediateParent = Boolean(clause?.parent_id && kids.length);
    const valueToStore = isIntermediateParent ? '' : inferred;
    const sel = clauseSelect(key);
    if (sel && sel.value !== valueToStore) sel.value = valueToStore;
    memo.set(key, inferred);
    return inferred;
  }

  for (const c of flat) inferValue(c.id);
}

function renderAdminClauses() {
  if (!adminClausesCard || !adminClausesList) return;
  if (!me?.is_admin || !clauseApplicabilityEditMode) {
    adminClausesCard.style.display = 'none';
    return;
  }
  adminClausesCard.style.display = '';
  if (controlUpstreamUrl) controlUpstreamUrl.value = ctrl?.upstream_url || '';

  const flat = flattenClauseTree(clauseCatalog?.tree || []);
  const selected = selectedClauseMap();
  if (!flat.length) {
    adminClausesList.innerHTML = '<span class="small-muted">No clauses are loaded for this framework.</span>';
    return;
  }

  adminClausesList.innerHTML = `
    <div class="table-responsive admin-control-clauses-wrap">
      <table class="table table-sm align-middle mb-0">
        <thead>
          <tr>
            <th>Clause</th>
            <th style="width:220px;">Applicability</th>
          </tr>
        </thead>
        <tbody>
          ${flat.map((c) => {
            const cid = String(c.id || '');
            const cur = selected.get(cid) || '';
            const depth = Math.max(0, Number(c.depth || 0));
            const indent = depth ? `style="padding-left:${Math.min(depth * 1.25, 4)}rem"` : '';
            const parentClass = depth === 0 ? 'fw-bold' : '';
            const clauseHref = withFramework(`/clause.html?id=${encodeURIComponent(cid)}`, ctrl?.framework || framework);
            return `<tr>
              <td class="wrap ${parentClass}" ${indent}>
                <a class="me-2" href="${esc(clauseHref)}">${esc(c.ref || '')}</a>
                <span class="small-muted">${esc(c.title || '')}</span>
              </td>
              <td>
                <select class="form-select form-select-sm clause-applicability" data-clause-id="${esc(cid)}">
                  <option value="" ${cur ? '' : 'selected'}>Not applicable</option>
                  <option value="applicable" ${cur === 'applicable' ? 'selected' : ''}>Applicable</option>
                  <option value="partially_applicable" ${cur === 'partially_applicable' ? 'selected' : ''}>Partially applicable</option>
                </select>
              </td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;

  for (const sel of adminClausesList.querySelectorAll('.clause-applicability')) {
    sel.addEventListener('change', applyInferredParentClauseApplicability);
  }
  applyInferredParentClauseApplicability();
}

async function loadClauseCatalog() {
  const data = await apiGet(`/api/v1/clauses?framework=${encodeURIComponent(ctrl?.framework || framework)}`);
  clauseCatalog = data || {items: [], tree: []};
}

async function ensureClauseCatalogLoaded() {
  if (!me?.is_admin) return;
  const hasItems = Array.isArray(clauseCatalog?.items) && clauseCatalog.items.length > 0;
  const hasTree = Array.isArray(clauseCatalog?.tree) && clauseCatalog.tree.length > 0;
  if (hasItems || hasTree) return;
  await loadClauseCatalog();
}

async function reloadControlOnly() {
  ctrl = await apiGet(`/api/v1/controls/${encodeURIComponent(id)}?framework=${encodeURIComponent(framework)}`);
  renderControlChrome();
  renderControlJustificationCard();
  renderLinkedClauses();
  await loadLinkedRisks();
  interestedPartiesLoaded = false;
  await loadLinkedInterestedParties({force: true});
  renderAdminClauses();
}

function renderRows(items) {
  tbody.innerHTML = '';

  for (const e of items || []) {
    const srcChip = e.source ?
      sourceBadgeHtml(e.source, sourceMeta, controlScopedSourceHref(e.source)) :
      '';

    const ext = upstreamLinkHtml(e.source_url, {title: 'Open source event'});

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="small-muted" data-sort="${esc(tsSortKey(e.timestamp))}">${esc(fmtTs(e.timestamp))}</td>
      <td data-sort="${esc(e.source || '')}">${srcChip}</td>
      <td class="wrap"><a href="${withFramework(`/event.html?id=${encodeURIComponent(e.event_id || e.id)}`, ctrl?.framework || framework)}" title="${esc(e.summary || '')}">${esc(
    shorten(e.summary || '', 160)
)}</a></td>
      <td>${ext}</td>
    `;
    tbody.appendChild(tr);
  }

  if ((items || []).length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="4" class="small-muted p-4">No evidence matches your filters.</td>`;
    tbody.appendChild(tr);
  }
}

function applyFromInputs(resetOffset = true) {
  state.q = (localQ?.value || '').trim();
  state.source = sourceFilter?.value || '';
  state.limit = parseInt(selLimit?.value || '50', 10);
  if (!Number.isFinite(state.limit)) state.limit = 50;
  if (resetOffset) state.offset = 0;

  setQuery({
    id,
    framework: ctrl?.framework || framework || null,
    q: state.q || null,
    source: state.source || null,
    limit: state.limit,
    offset: state.offset,
  });
}

async function loadFacets() {
  try {
    const p = new URLSearchParams();
    p.set('control', id);
    if (ctrl?.framework || framework) p.set('framework', ctrl?.framework || framework);
    if (state.q) p.set('q', state.q);

    const data = await apiGet(`/api/v1/events/facets?${p.toString()}`);
    renderSourceOptions(data?.sources || []);
  } catch {
    // Non-fatal; keep existing options.
  }
}

function renderSourceOptions(facets) {
  const current = state.source || '';
  const rows = Array.isArray(facets) ? facets : [];
  const counts = new Map(rows.map((r) => [String(r.source), Number(r.count || 0)]));

  const items = rows
      .filter((r) => r && r.source && Number(r.count || 0) > 0)
      .map((r) => ({
        source: String(r.source),
        count: Number(r.count || 0),
      }))
      .sort((a, b) => (b.count - a.count) || a.source.localeCompare(b.source));

  let html = '<option value="">All sources</option>';

  // Preserve current selection even if it yields zero results.
  if (current && !counts.has(current)) {
    const label = String(sourceLabel(current, sourceMeta) || current);
    const text = label && label !== current ? `${label} (${current})` : current;
    html += `<option value="${esc(current)}">${esc(text)} (0)</option>`;
  }

  for (const it of items) {
    const src = it.source;
    const label = String(sourceLabel(src, sourceMeta) || src);
    const text = label && label !== src ? `${label} (${src})` : src;
    html += `<option value="${esc(src)}">${esc(text)} (${it.count})</option>`;
  }

  sourceFilter.innerHTML = html;
  if (current) sourceFilter.value = current;
}

async function loadEvents() {
  status.style.display = 'none';
  if (meta) meta.textContent = 'Loading…';
  showTableLoading(tbody, 4, 'Loading evidence…');

  try {
    const p = new URLSearchParams();
    p.set('control', id);
    if (ctrl?.framework || framework) p.set('framework', ctrl?.framework || framework);
    if (state.q) p.set('q', state.q);
    if (state.source) p.set('source', state.source);
    p.set('limit', String(state.limit));
    p.set('offset', String(state.offset));

    const data = await apiGet(`/api/v1/events?${p.toString()}`);
    state.total = data.total || 0;

    const page = offsetPagerState(state);
    if (meta) meta.textContent = `${page.start}–${page.end} of ${page.total}`;

    renderRows(data.items || []);

    setOffsetPagerDisabled(pagerButtons, state);
  } catch (e) {
    toast(status, `Failed to load evidence: ${String(e)}`, 'danger');
    if (meta) meta.textContent = '—';
    tbody.innerHTML = '<tr><td colspan="4" class="small-muted p-4">Failed to load evidence.</td></tr>';
  }
}

function reload(resetOffset = true) {
  applyFromInputs(resetOffset);
  loadEvents();
  loadFacets();
}

async function load() {
  if (!id) {
    toast(status, 'Missing control id.', 'danger');
    return;
  }

  status.style.display = 'none';

  try {
    ctrl = await apiGet(`/api/v1/controls/${encodeURIComponent(id)}?framework=${encodeURIComponent(framework)}`);
    renderControlChrome();
    renderControlJustificationCard();
    document.getElementById('viewAllEvents').href = withFramework(`/events.html?control=${encodeURIComponent(ctrl.ref)}`, ctrl.framework || framework);
    if (controlsBreadcrumb) controlsBreadcrumb.href = withFramework('/controls.html', ctrl.framework || framework);

    if (linksEl) {
      if (ctrl.upstream_url) {
        linksEl.innerHTML = `<a class="link-subtle" href="${esc(
            ctrl.upstream_url
        )}" target="_blank" rel="noopener noreferrer"><i class="bi bi-info-circle"></i> Guidance</a>`;
      } else {
        linksEl.textContent = '';
      }
    }

    renderLinkedClauses();
    await loadLinkedRisks();
    await loadLinkedInterestedParties({force: true});
    renderAdminClauses();
    if (qs('tab') === 'changelog') await loadControlChangelog();
    if (qs('tab') === 'isms') await loadControlIsms({force: true});

    sourceMeta = await getSourceMeta();

    syncInputs();
    // Populate source dropdown with counts, then load the current page.
    await loadFacets();
    await loadEvents();
  } catch (e) {
    toast(status, `Failed to load control: ${String(e)}`, 'danger');
    if (meta) meta.textContent = '—';
  }
}

// Filters
const debouncedReload = debounce(() => reload(true), 400);
localQ?.addEventListener('input', debouncedReload);
sourceFilter?.addEventListener('change', () => reload(true));
selLimit?.addEventListener('change', () => reload(true));

document.getElementById('clearFilters')?.addEventListener('click', (ev) => {
  ev.preventDefault();
  if (localQ) localQ.value = '';
  if (sourceFilter) sourceFilter.value = '';
  if (selLimit) selLimit.value = '50';
  state = {...state, q: '', source: '', limit: 50, offset: 0};
  reload(true);
});

// Pager listeners (bind to BOTH top + bottom)
wireOffsetPagerButtons(pagerButtons, (direction) => {
  state.offset = direction === 'prev'
    ? Math.max(0, state.offset - state.limit)
    : state.offset + state.limit;
  applyFromInputs(false);
  loadEvents();
});

// Save search
editClauseApplicabilityBtn?.addEventListener('click', async () => {
  if (!me?.is_admin) return;
  clauseApplicabilityEditMode = !clauseApplicabilityEditMode;
  renderLinkedClauses();
  if (clauseApplicabilityEditMode) {
    try {
      await ensureClauseCatalogLoaded();
      renderAdminClauses();
      adminClausesCard?.scrollIntoView({behavior: 'smooth', block: 'start'});
    } catch (e) {
      clauseApplicabilityEditMode = false;
      renderLinkedClauses();
      renderAdminClauses();
      toast(status, `Failed to load clauses: ${String(e)}`, 'danger');
    }
  } else {
    renderAdminClauses();
  }
});

document.getElementById('saveSearch')?.addEventListener('click', async () => {
  // Sync current inputs to state + URL
  applyFromInputs(false);

  const title = (document.getElementById('ctrlTitle')?.textContent || 'Control').trim();
  const parts = [];
  if (state.q) parts.push(`q=${state.q}`);
  if (state.source) parts.push(`source=${state.source}`);
  if (state.limit && state.limit !== 50) parts.push(`limit=${state.limit}`);
  const defName = (title + (parts.length ? ` (${parts.join(', ')})` : ''))
      .slice(0, 128);

  const name = (prompt('Name this saved search', defName) || '').trim();
  if (!name) return;

  // Canonical URL without pagination offset
  const p = new URLSearchParams();
  p.set('id', id);
  if (ctrl?.framework || framework) p.set('framework', ctrl?.framework || framework);
  if (state.q) p.set('q', state.q);
  if (state.source) p.set('source', state.source);
  if (state.limit) p.set('limit', String(state.limit));
  const url = `/control.html?${p.toString()}`;

  try {
    await apiPost('/api/v1/me/saved-searches', {name, url});
    toast(status, 'Saved', 'success');
  } catch (e) {
    toast(status, `Failed to save: ${String(e)}`, 'danger');
  }
});


saveClauseLinksBtn?.addEventListener('click', async () => {
  if (!me?.is_admin) return;
  applyInferredParentClauseApplicability();
  const clauses = [];
  for (const sel of adminClausesList?.querySelectorAll('.clause-applicability') || []) {
    const applicability = (sel.value || '').trim();
    if (!applicability) continue;
    clauses.push({clause_id: sel.dataset.clauseId, applicability});
  }
  saveClauseLinksBtn.disabled = true;
  try {
    await apiPatch(`/api/v1/admin/controls/${encodeURIComponent(id)}/clauses`, {clauses});
    toast(status, 'Clause applicability saved', 'success');
    await reloadControlOnly();
    await loadControlChangelog();
  } catch (e) {
    toast(status, `Failed to save clauses: ${String(e)}`, 'danger');
  } finally {
    saveClauseLinksBtn.disabled = false;
  }
});

saveControlJustificationBtn?.addEventListener('click', async () => {
  if (!me?.is_admin) return;
  saveControlJustificationBtn.disabled = true;
  try {
    await apiPatch(`/api/v1/admin/controls/${encodeURIComponent(id)}/justification`, {
      justification: (controlJustification?.value || '').trim() || null,
    });
    toast(status, 'Control justification saved', 'success');
    await reloadControlOnly();
    await loadControlChangelog();
  } catch (e) {
    toast(status, `Failed to save justification: ${String(e)}`, 'danger');
  } finally {
    saveControlJustificationBtn.disabled = false;
  }
});

saveControlUpstreamUrlBtn?.addEventListener('click', async () => {
  if (!me?.is_admin) return;
  saveControlUpstreamUrlBtn.disabled = true;
  try {
    await apiPatch(`/api/v1/admin/controls/${encodeURIComponent(id)}/upstream-url`, {
      upstream_url: (controlUpstreamUrl?.value || '').trim() || null,
    });
    toast(status, 'Control upstream URL saved', 'success');
    await reloadControlOnly();
    await loadControlChangelog();
    if (linksEl) {
      if (ctrl.upstream_url) {
        linksEl.innerHTML = `<a class="link-subtle" href="${esc(ctrl.upstream_url)}" target="_blank" rel="noopener noreferrer"><i class="bi bi-info-circle"></i> Guidance</a>`;
      } else {
        linksEl.textContent = '';
      }
    }
  } catch (e) {
    toast(status, `Failed to save control URL: ${String(e)}`, 'danger');
  } finally {
    saveControlUpstreamUrlBtn.disabled = false;
  }
});

load();
