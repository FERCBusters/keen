import {
  initNavbar,
  apiGet,
  apiPatch,
  esc,
  qs,
  toast,
  enableTableSorting,
  safeExternalHref,
  titledLinksHtml,
  withFramework,
  getCurrentFramework,
  showTableLoading,
  debounce,
  loadEntityChangelog,
} from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();
enableTableSorting();

const id = qs('id');
const status = document.getElementById('status');
const rowsEl = document.getElementById('rows');
const clauseTitle = document.getElementById('clauseTitle');
const clauseMeta = document.getElementById('clauseMeta');
const clauseLinks = document.getElementById('clauseLinks');
const controlsMeta = document.getElementById('controlsMeta');
const adminClauseUrlCard = document.getElementById('adminClauseUrlCard');
const clauseUpstreamUrl = document.getElementById('clauseUpstreamUrl');
const saveClauseUpstreamUrl = document.getElementById('saveClauseUpstreamUrl');
const clausesBreadcrumb = document.getElementById('clausesBreadcrumb');
const adminControlsCard = document.getElementById('adminControlsCard');
const adminControlsList = document.getElementById('adminControlsList');
const saveClauseControls = document.getElementById('saveClauseControls');
const controlSearch = document.getElementById('controlSearch');
const editLinkedControls = document.getElementById('editLinkedControls');
const clauseEvidenceMeta = document.getElementById('clauseEvidenceMeta');
const clauseEvidenceLinks = document.getElementById('clauseEvidenceLinks');
const editClauseEvidence = document.getElementById('editClauseEvidence');
const adminClauseEvidenceEditor = document.getElementById('adminClauseEvidenceEditor');
const clauseEvidenceUrlInputs = document.getElementById('clauseEvidenceUrlInputs');
const addClauseEvidenceUrl = document.getElementById('addClauseEvidenceUrl');
const saveClauseEvidenceUrls = document.getElementById('saveClauseEvidenceUrls');
const clauseControlsTab = document.getElementById('clauseControlsTab');
const clauseIsmsTab = document.getElementById('clauseIsmsTab');
const clauseIsmsView = document.getElementById('clauseIsmsView');
const clauseChangelogTab = document.getElementById('clauseChangelogTab');
const clausePestleTab = document.getElementById('clausePestleTab');
const clausePestleRows = document.getElementById('clausePestleRows');
const openPestleFromClause = document.getElementById('openPestleFromClause');
const clauseChangelogEl = document.getElementById('clauseChangelog');
const canViewIsms = !!(me?.is_admin || me?.can_view_isms || me?.can_manage_isms || me?.can_view_risks || me?.can_manage_risks);
let ismsLoaded = false;

function setClauseTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (!tabName || tabName === 'controls') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}?${p.toString()}`);
}

function activateClauseTab(tabName) {
  const tab = (tabName === 'changelog')
    ? clauseChangelogTab
    : (tabName === 'isms' ? clauseIsmsTab : (tabName === 'pestle' ? clausePestleTab : clauseControlsTab));
  if (!tab || !window.bootstrap?.Tab) return;
  window.bootstrap.Tab.getOrCreateInstance(tab).show();
}


function pestleHref(itemId) {
  return withFramework(`/pestle_item.html?id=${encodeURIComponent(itemId || '')}`, clause?.framework || framework);
}

function pestleRelevanceBadge(rel) {
  const code = rel?.code || 'na';
  let cls = 'text-bg-light';
  if (code === 'high') cls = 'text-bg-danger';
  else if (code === 'medium') cls = 'text-bg-warning';
  else if (code === 'low') cls = 'text-bg-success';
  return `<span class="badge ${cls}">${esc(rel?.label || 'N/A')}</span>`;
}

async function loadClausePestle() {
  if (!clausePestleRows || !id) return;
  clausePestleRows.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">Loading PESTLE(E) relevance…</td></tr>';
  try {
    const data = await apiGet(`/api/v1/clauses/${encodeURIComponent(id)}/pestle`);
    const items = data?.items || [];
    if (openPestleFromClause) openPestleFromClause.href = withFramework('/pestle.html', clause?.framework || framework);
    if (!items.length) {
      clausePestleRows.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">No PESTLE(E) items are currently linked to this clause.</td></tr>';
      return;
    }
    clausePestleRows.innerHTML = items.map((row) => {
      const it = row.pestle_item || {};
      return `<tr>
        <td>${esc(it.type || '')}</td>
        <td>${esc(it.lens || '')}</td>
        <td><a class="fw-semibold" href="${esc(pestleHref(it.id))}">${esc(it.item || '')}</a></td>
        <td>${pestleRelevanceBadge(row.relevance)}</td>
        <td>${pestleRelevanceBadge(it.overall_relevance)}</td>
        <td class="wrap small-muted">${esc(it.rationale || '—')}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    clausePestleRows.innerHTML = '<tr><td colspan="6" class="p-4 text-danger">Failed to load PESTLE(E) relevance.</td></tr>';
  }
}

async function loadClauseChangelog() {
  if (!clauseChangelogEl || !id) return;
  try {
    await loadEntityChangelog(
      clauseChangelogEl,
      `/api/v1/clauses/${encodeURIComponent(id)}/changelog?limit=100`,
      {empty: 'No clause changes have been recorded yet.'}
    );
  } catch (e) {
    clauseChangelogEl.innerHTML = '<div class="text-danger p-3">Failed to load changelog.</div>';
  }
}

function _ismsEntityRows(data) {
  const groups = [
    ['Objectives', data.objectives, (x) => x.goal || x.requirement || 'Objective', '/isms.html?tab=objectives'],
    ['Documents', data.documents, (x) => x.title || 'Document', '/isms.html?tab=documents'],
    ['Organisation Chart', data.org_nodes, (x) => x.name || 'Org node', '/isms.html?tab=org'],
    ['Assets', data.assets, (x) => x.asset || 'Asset', '/isms.html?tab=assets'],
    ['Application Configuration', data.application_configurations, (x) => x.display || x.source?.label || 'App config', '/isms.html?tab=appconfig'],
    ['Meetings', data.meetings, (x) => x.title || 'Meeting', '/isms.html?tab=meetings'],
  ];
  const rows = [];
  for (const [label, items, titleFn, href] of groups) {
    for (const item of items || []) rows.push(`<tr><td>${esc(label)}</td><td class="wrap"><a class="fw-semibold" href="${esc(withFramework(href, clause?.framework || framework))}">${esc(titleFn(item))}</a></td><td class="small-muted">${esc(item.updated_at || '')}</td></tr>`);
  }
  if (!rows.length) return '<div class="small-muted">No ISMS entities are currently linked to this clause.</div>';
  return `<div class="table-responsive"><table class="table table-sm align-middle mb-0"><thead><tr><th style="width:190px;">Type</th><th>Entity</th><th style="width:190px;">Updated</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}

async function loadClauseIsms({force = false} = {}) {
  if (!clauseIsmsView || !id) return;
  if (!canViewIsms) { clauseIsmsView.innerHTML = '<span class="small-muted">You do not have permission to view ISMS references.</span>'; return; }
  if (ismsLoaded && !force) return;
  clauseIsmsView.innerHTML = '<span class="small-muted">Loading ISMS references…</span>';
  try {
    const data = await apiGet(`/api/v1/clauses/${encodeURIComponent(id)}/isms?framework=${encodeURIComponent(clause?.framework || framework)}`);
    ismsLoaded = true;
    clauseIsmsView.innerHTML = _ismsEntityRows(data || {});
  } catch (e) {
    ismsLoaded = false;
    clauseIsmsView.innerHTML = '<span class="text-danger">Failed to load ISMS references.</span>';
  }
}

function wireClauseTabs() {
  clauseControlsTab?.addEventListener('shown.bs.tab', () => setClauseTabQuery('controls'));
  clausePestleTab?.addEventListener('shown.bs.tab', () => {
    setClauseTabQuery('pestle');
    loadClausePestle();
  });
  clauseIsmsTab?.addEventListener('shown.bs.tab', async () => {
    setClauseTabQuery('isms');
    await loadClauseIsms();
  });
  clauseChangelogTab?.addEventListener('shown.bs.tab', () => {
    setClauseTabQuery('changelog');
    loadClauseChangelog();
  });
  activateClauseTab(qs('tab', 'controls'));
}

wireClauseTabs();

let clause = null;
let linkedControls = [];
let allControls = [];
let linkedControlsEditMode = false;
let evidenceEditMode = false;

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

function linkedMap() {
  const m = new Map();
  for (const c of linkedControls || []) {
    if (c?.id) m.set(String(c.id), c.applicability || 'applicable');
  }
  return m;
}

function isIntermediateParentClause() {
  return Boolean(clause?.parent_id && Number(clause?.child_count || 0) > 0);
}

function clauseEvidenceMappings() {
  if (Array.isArray(clause?.evidence_mappings)) {
    return clause.evidence_mappings
        .map((item) => ({title: String(item?.title || '').trim(), url: String(item?.url || '').trim()}))
        .filter((item) => item.url);
  }
  return (Array.isArray(clause?.evidence_urls) ? clause.evidence_urls : [])
      .map((url) => ({title: '', url: String(url || '').trim()}))
      .filter((item) => item.url);
}

function evidenceMappingRow(item = {}) {
  const title = typeof item === 'string' ? '' : String(item?.title || '');
  const url = typeof item === 'string' ? item : String(item?.url || '');
  return `<div class="clause-evidence-mapping-row border rounded p-2">
    <div class="row g-2 align-items-end">
      <div class="col-12 col-lg-4">
        <label class="form-label small-muted mb-1">Title</label>
        <input class="form-control form-control-sm clause-evidence-title" type="text" value="${esc(title)}" maxlength="256" placeholder="e.g. Statement of Applicability">
      </div>
      <div class="col-12 col-lg-7">
        <label class="form-label small-muted mb-1">URL</label>
        <input class="form-control form-control-sm clause-evidence-url" type="text" value="${esc(url)}" placeholder="https://… or /event.html?id=…">
      </div>
      <div class="col-12 col-lg-1 d-grid">
        <button class="btn btn-sm btn-outline-secondary remove-clause-evidence-url" type="button" title="Remove evidence mapping" aria-label="Remove evidence mapping"><i class="bi bi-x-lg" aria-hidden="true"></i></button>
      </div>
    </div>
  </div>`;
}

function wireEvidenceUrlRemoveButtons() {
  if (!clauseEvidenceUrlInputs) return;
  for (const btn of clauseEvidenceUrlInputs.querySelectorAll('.remove-clause-evidence-url')) {
    btn.onclick = () => {
      const row = btn.closest('.clause-evidence-mapping-row');
      if (row) row.remove();
      if (!clauseEvidenceUrlInputs.querySelector('.clause-evidence-mapping-row')) {
        clauseEvidenceUrlInputs.insertAdjacentHTML('beforeend', evidenceMappingRow({}));
        wireEvidenceUrlRemoveButtons();
      }
    };
  }
}

function renderEvidenceEditor() {
  if (!clauseEvidenceUrlInputs) return;
  const mappings = clauseEvidenceMappings();
  const rows = mappings.length ? mappings : [{}];
  clauseEvidenceUrlInputs.innerHTML = rows.map((item) => evidenceMappingRow(item)).join('');
  wireEvidenceUrlRemoveButtons();
}

function renderEvidenceMapping() {
  const mappings = clauseEvidenceMappings();
  if (clauseEvidenceMeta) {
    clauseEvidenceMeta.textContent = mappings.length
      ? `${mappings.length} mapped evidence link${mappings.length === 1 ? '' : 's'}`
      : 'No evidence links mapped directly to this clause yet.';
  }
  if (clauseEvidenceLinks) {
    clauseEvidenceLinks.innerHTML = mappings.length
      ? titledLinksHtml(mappings, {title: 'Open evidence mapping'})
      : '<span class="small-muted">No evidence mapping links have been set.</span>';
  }
  if (editClauseEvidence) {
    editClauseEvidence.style.display = me?.is_admin ? '' : 'none';
    editClauseEvidence.textContent = evidenceEditMode ? 'Hide edit' : 'Edit';
  }
  if (adminClauseEvidenceEditor) {
    adminClauseEvidenceEditor.style.display = (me?.is_admin && evidenceEditMode) ? '' : 'none';
  }
  if (me?.is_admin && evidenceEditMode) renderEvidenceEditor();
}

function renderClauseHeader() {
  clauseTitle.textContent = `${clause?.ref || ''}  ${clause?.title || ''}`.trim() || 'Clause';
  const parts = [clause?.framework || framework];
  if (clause?.parent_ref) parts.push(`parent ${clause.parent_ref}`);
  clauseMeta.textContent = parts.filter(Boolean).join(' • ') || '—';
  if (clausesBreadcrumb) clausesBreadcrumb.href = withFramework('/clauses.html', clause?.framework || framework);

  const url = safeExternalHref(clause?.upstream_url);
  clauseLinks.innerHTML = url
    ? `<a class="link-subtle" href="${esc(url)}" target="_blank" rel="noopener noreferrer"><i class="bi bi-info-circle"></i> Guidance</a>`
    : '';

  if (adminClauseUrlCard) {
    adminClauseUrlCard.style.display = me?.is_admin ? '' : 'none';
    if (clauseUpstreamUrl) clauseUpstreamUrl.value = clause?.upstream_url || '';
  }
  if (editLinkedControls) {
    editLinkedControls.style.display = me?.is_admin ? '' : 'none';
    editLinkedControls.textContent = linkedControlsEditMode ? 'Hide edit' : 'Edit';
  }
  if (adminControlsCard) {
    adminControlsCard.style.display = (me?.is_admin && linkedControlsEditMode) ? '' : 'none';
  }
  renderEvidenceMapping();
  if (openPestleFromClause) openPestleFromClause.href = withFramework('/pestle.html', clause?.framework || framework);
}

function renderControls(items) {
  const rows = Array.isArray(items) ? items : [];
  if (controlsMeta) controlsMeta.textContent = `${rows.length} linked control${rows.length === 1 ? '' : 's'}`;
  if (!rows.length) {
    rowsEl.innerHTML = '<tr><td colspan="4" class="small-muted p-4">No controls are linked to this clause.</td></tr>';
    return;
  }

  rowsEl.innerHTML = rows.map((c) => {
    const href = withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, c.framework || framework);
    return `<tr>
      <td class="fw-semibold"><a href="${esc(href)}">${esc(c.ref || '')}</a></td>
      <td class="wrap">${esc(c.title || '')}</td>
      <td data-sort="${esc(applicabilityLabel(c.applicability))}">${applicabilityBadge(c.applicability)}</td>
      <td>${c.in_scope ? '<span class="badge badge-soft">In scope</span>' : '<span class="badge text-bg-light">Out</span>'}</td>
    </tr>`;
  }).join('');
}

function renderAdminControls() {
  if (!adminControlsList || !me?.is_admin) return;
  if (isIntermediateParentClause()) {
    if (controlSearch) controlSearch.disabled = true;
    if (saveClauseControls) saveClauseControls.disabled = true;
    adminControlsList.innerHTML = `
      <div class="alert alert-info mb-0">
        This clause has more specific child clauses. Link controls to the child clauses instead;
        this parent clause is inferred for reporting and visualisations.
      </div>`;
    return;
  }
  if (controlSearch) controlSearch.disabled = false;
  if (saveClauseControls) saveClauseControls.disabled = false;
  const selected = linkedMap();
  const q = (controlSearch?.value || '').trim().toLowerCase();
  const rows = (allControls || [])
      .filter((c) => !q || `${c.ref || ''} ${c.title || ''}`.toLowerCase().includes(q));
  if (!rows.length) {
    adminControlsList.innerHTML = '<span class="small-muted">No controls match.</span>';
    return;
  }
  adminControlsList.innerHTML = `
    <div class="table-responsive admin-clause-controls-wrap">
      <table class="table table-sm align-middle mb-0">
        <thead>
          <tr>
            <th style="width:130px;">Control</th>
            <th>Title</th>
            <th style="width:220px;">Applicability</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((c) => {
            const cid = String(c.id || '');
            const cur = selected.get(cid) || '';
            const href = withFramework(`/control.html?id=${encodeURIComponent(cid)}`, c.framework || framework);
            return `<tr>
              <td class="fw-semibold"><a href="${esc(href)}">${esc(c.ref || '')}</a></td>
              <td class="wrap small-muted">${esc(c.title || '')}</td>
              <td>
                <select class="form-select form-select-sm control-applicability" data-control-id="${esc(cid)}">
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
}

async function loadAdminControls() {
  if (!me?.is_admin || !clause) return;
  const data = await apiGet(`/api/v1/controls?framework=${encodeURIComponent(clause.framework || framework)}&limit=5000`);
  allControls = (data?.items || []).filter((c) => c.type !== 'clause');
  renderAdminControls();
}

async function reloadLinkedControls() {
  const data = await apiGet(`/api/v1/clauses/${encodeURIComponent(id)}/controls`);
  clause = data?.clause || null;
  linkedControls = data?.items || [];
  renderClauseHeader();
  renderControls(linkedControls);
  renderAdminControls();
  if (qs('tab') === 'pestle') await loadClausePestle();
}


async function load() {
  if (!id) {
    toast(status, 'Missing clause id.', 'danger');
    return;
  }

  showTableLoading(rowsEl, 4, 'Loading linked controls…');
  try {
    await reloadLinkedControls();
    await loadClauseChangelog();
    await loadAdminControls();
  } catch (e) {
    toast(status, `Failed to load clause: ${String(e)}`, 'danger');
    rowsEl.innerHTML = '<tr><td colspan="4" class="small-muted p-4">Failed to load linked controls.</td></tr>';
  }
}

editClauseEvidence?.addEventListener('click', () => {
  if (!me?.is_admin) return;
  evidenceEditMode = !evidenceEditMode;
  renderEvidenceMapping();
  if (evidenceEditMode) {
    adminClauseEvidenceEditor?.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }
});

addClauseEvidenceUrl?.addEventListener('click', () => {
  if (!me?.is_admin || !clauseEvidenceUrlInputs) return;
  clauseEvidenceUrlInputs.insertAdjacentHTML('beforeend', evidenceMappingRow({}));
  wireEvidenceUrlRemoveButtons();
  const inputs = clauseEvidenceUrlInputs.querySelectorAll('.clause-evidence-title');
  inputs[inputs.length - 1]?.focus();
});

saveClauseEvidenceUrls?.addEventListener('click', async () => {
  if (!me?.is_admin || !clause?.id) return;
  const evidenceMappings = Array.from(clauseEvidenceUrlInputs?.querySelectorAll('.clause-evidence-mapping-row') || [])
      .map((row) => ({
        title: String(row.querySelector('.clause-evidence-title')?.value || '').trim(),
        url: String(row.querySelector('.clause-evidence-url')?.value || '').trim(),
      }))
      .filter((item) => item.url);
  saveClauseEvidenceUrls.disabled = true;
  try {
    const data = await apiPatch(`/api/v1/admin/clauses/${encodeURIComponent(clause.id)}/evidence-urls`, {
      evidence_mappings: evidenceMappings,
    });
    clause = {...clause, evidence_mappings: data?.evidence_mappings || [], evidence_urls: data?.evidence_urls || []};
    evidenceEditMode = false;
    renderEvidenceMapping();
    await loadClauseChangelog();
    toast(status, 'Clause evidence mapping saved', 'success');
  } catch (e) {
    toast(status, `Failed to save clause evidence mapping: ${String(e)}`, 'danger');
  } finally {
    saveClauseEvidenceUrls.disabled = false;
  }
});

editLinkedControls?.addEventListener('click', async () => {
  if (!me?.is_admin) return;
  linkedControlsEditMode = !linkedControlsEditMode;
  renderClauseHeader();
  if (linkedControlsEditMode && allControls.length === 0) {
    try { await loadAdminControls(); } catch (e) { toast(status, `Failed to load controls: ${String(e)}`, 'danger'); }
  } else if (linkedControlsEditMode) {
    renderAdminControls();
  }
  if (linkedControlsEditMode) {
    adminControlsCard?.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
});

saveClauseUpstreamUrl?.addEventListener('click', async () => {
  if (!me?.is_admin || !clause?.id) return;
  saveClauseUpstreamUrl.disabled = true;
  try {
    const data = await apiPatch(`/api/v1/admin/clauses/${encodeURIComponent(clause.id)}/upstream-url`, {
      upstream_url: (clauseUpstreamUrl?.value || '').trim() || null,
    });
    clause = {...clause, upstream_url: data?.upstream_url || null};
    renderClauseHeader();
    await loadClauseChangelog();
    toast(status, 'Clause URL saved', 'success');
  } catch (e) {
    toast(status, `Failed to save clause URL: ${String(e)}`, 'danger');
  } finally {
    saveClauseUpstreamUrl.disabled = false;
  }
});

saveClauseControls?.addEventListener('click', async () => {
  if (!me?.is_admin || !clause?.id) return;
  const controls = [];
  for (const sel of adminControlsList?.querySelectorAll('.control-applicability') || []) {
    const applicability = (sel.value || '').trim();
    if (!applicability) continue;
    controls.push({control_id: sel.dataset.controlId, applicability});
  }
  saveClauseControls.disabled = true;
  try {
    const data = await apiPatch(`/api/v1/admin/clauses/${encodeURIComponent(clause.id)}/controls`, {controls});
    if (data?.skipped_intermediate_parent) {
      toast(status, data?.detail || 'This parent clause is inferred from child clauses.', 'info');
    } else {
      toast(status, 'Linked controls saved', 'success');
    }
    await reloadLinkedControls();
    if (qs('tab') === 'changelog') await loadClauseChangelog();
    if (qs('tab') === 'isms') await loadClauseIsms({force: true});
  } catch (e) {
    toast(status, `Failed to save linked controls: ${String(e)}`, 'danger');
  } finally {
    saveClauseControls.disabled = false;
  }
});

controlSearch?.addEventListener('input', debounce(renderAdminControls, 200));

load();
