import { featureEnabled } from '/pages/feature-availability.js';
import { createRelatedControlsBrowser } from './related-controls.js';
import { createControlRequirements } from './control-requirements.js';
import { scoreBadge } from '/pages/risk-common.js';
import {
  errorText,
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  confirmAction,
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
  safeAbsoluteHttpHref,
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
const crossForm = document.getElementById('crossFrameworkForm');
const crossList = document.getElementById('crossFrameworkLinks');
const crossFrameworkSource = document.getElementById('crossFrameworkSource');
const crossFrameworkControl = document.getElementById('crossFrameworkControl');
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
const controlRequirementsTab = document.getElementById('controlRequirementsTab');
const controlEvidenceTab = document.getElementById('controlEvidenceTab');
const controlRelatedTab = document.getElementById('controlRelatedTab');
const controlGovernanceTab = document.getElementById('controlGovernanceTab');
const controlIsmsView = document.getElementById('controlIsmsView');
const controlChangelogEl = document.getElementById('controlChangelog');
const interestedPartiesCard = document.getElementById('interestedPartiesCard');
const interestedPartiesView = document.getElementById('interestedPartiesView');
const interestedPartiesCount = document.getElementById('interestedPartiesCount');
const canViewRisks = !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
const canViewInterestedParties = !!(
  me?.is_admin ||
  me?.can_view_interested_parties ||
  me?.can_manage_interested_parties ||
  me?.can_view_risks ||
  me?.can_manage_risks
);
const canViewIsms = !!(
  me?.is_admin ||
  me?.can_view_isms ||
  me?.can_manage_isms ||
  me?.can_view_risks ||
  me?.can_manage_risks
);

// One shared offset-pager wiring path covers header buttons (#prev/#next)
// and footer buttons ([data-pager]).
const pagerButtons = collectOffsetPagerButtons();

let sourceMeta = {};
let ctrl = null;
// The tab showing. One chosen before the control has loaded fills once it has.
let activeTab = 'requirements';
let evidenceLoaded = null;
// The ISMS tab and Requirements read the same references, so one request
// serves both for the control's framework.
let ismsReferences = null;

function loadIsmsReferences() {
  const fw = ctrl?.framework || framework;
  if (ismsReferences?.framework !== fw) {
    const promise = apiGet(
      `/api/v1/controls/${encodeURIComponent(id)}/isms?framework=${encodeURIComponent(fw)}`,
    );
    ismsReferences = { framework: fw, promise };
    promise.catch(() => {
      if (ismsReferences?.promise === promise) ismsReferences = null;
    });
  }
  return ismsReferences.promise;
}

const requirements = createControlRequirements({
  root: document.getElementById('controlRequirements'),
  me,
  isms: loadIsmsReferences,
  sourceName: (source) => String(sourceLabel(source, sourceMeta) || source),
  onOpenEvidence: openEvidence,
});

function loadRequirements() {
  if (!ctrl) return null;
  return requirements.load({ id: ctrl.id || id, framework: ctrl.framework || framework });
}

// Evidence loads when its tab first opens.
function ensureEvidence() {
  if (!ctrl) return null;
  evidenceLoaded ||= Promise.all([loadEvents(), loadFacets()]);
  return evidenceLoaded;
}

// Opens the evidence list as Requirements counts it: every event, or one
// source's, without an earlier search.
function openEvidence(source) {
  state = { ...state, q: '', source: source || '', offset: 0 };
  setQuery({ q: null, source: state.source || null, offset: 0 });
  syncInputs();
  if (evidenceLoaded) evidenceLoaded = Promise.all([loadEvents(), loadFacets()]);
  activateControlTab('evidence');
  return ensureEvidence();
}

// Evidence and Requirements include what linked controls supply, so both
// load afresh when next shown.
function controlLinksChanged() {
  requirements.reset();
  evidenceLoaded = null;
}
let crossFrameworks = [];
let relatedLoaded = false;
const relatedControls = createRelatedControlsBrowser(document, { isAdmin: !!me?.is_admin });

async function loadCrossFrameworkLinks() {
  if (!id) return;
  try {
    const data = await apiGet(`/api/v1/controls/${encodeURIComponent(id)}/cross-framework`);
    if (!crossFrameworks.length) {
      try {
        const frameworks = await apiGet('/api/v1/frameworks');
        crossFrameworks = (frameworks.items || []).filter((f) => f.slug !== ctrl?.framework);
      } catch {
        // Links remain browsable by framework slug when the catalogue is unavailable.
      }
    }
    relatedControls.update(data.items || [], crossFrameworks);
    if (!me?.is_admin || !ctrl) return;
    crossForm.style.display = '';
    const previous = crossFrameworkSource.value;
    crossFrameworkSource.innerHTML =
      '<option value="">Choose framework</option>' +
      crossFrameworks
        .map((f) => `<option value="${esc(f.slug)}">${esc(f.name || f.slug)}</option>`)
        .join('');
    if (crossFrameworks.some((f) => f.slug === previous)) crossFrameworkSource.value = previous;
    if (crossFrameworkSource.value) await loadCrossSourceControls();
  } catch (e) {
    relatedControls.showError(`Could not load control links: ${errorText(e)}`);
  }
}

async function loadCrossSourceControls() {
  const source = crossFrameworkSource.value;
  crossFrameworkControl.innerHTML = '<option value="">Choose control</option>';
  if (!source) return;
  try {
    const data = await apiGet(
      `/api/v1/controls?framework=${encodeURIComponent(source)}&limit=5000`,
    );
    crossFrameworkControl.innerHTML += (data.items || [])
      .map((c) => `<option value="${esc(c.id)}">${esc(c.ref)} · ${esc(c.title || c.type)}</option>`)
      .join('');
  } catch (e) {
    toast(status, `Could not load source controls: ${errorText(e)}`, 'danger');
  }
}

// Related controls load when their tab first opens, once the control is known.
async function loadRelatedControls() {
  if (relatedLoaded || !ctrl) return;
  relatedLoaded = true;
  await loadCrossFrameworkLinks();
}

crossFrameworkSource?.addEventListener('change', loadCrossSourceControls);
crossForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!me?.is_admin || !crossFrameworkControl.value) return;
  try {
    await apiPost(`/api/v1/controls/${encodeURIComponent(id)}/cross-framework`, {
      source_control_id: crossFrameworkControl.value,
      rationale: document.getElementById('crossFrameworkRationale').value,
    });
    document.getElementById('crossFrameworkRationale').value = '';
    toast(status, 'Control link saved. Existing evidence is now visible here.', 'success');
    controlLinksChanged();
    await loadCrossFrameworkLinks();
  } catch (e) {
    toast(status, `Could not link controls: ${errorText(e)}`, 'danger');
  }
});
crossList?.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-remove-cross-link]');
  if (!button || !me?.is_admin) return;
  if (
    !(await confirmAction({
      title: 'Remove inherited evidence relationship',
      message: 'Remove the inherited evidence relationship with this control?',
      confirmLabel: 'Remove',
    }))
  )
    return;
  try {
    await apiDelete(
      `/api/v1/controls/${encodeURIComponent(id)}/cross-framework/${encodeURIComponent(button.dataset.removeCrossLink)}`,
    );
    toast(status, 'Control link removed.', 'success');
    controlLinksChanged();
    await loadCrossFrameworkLinks();
  } catch (e) {
    toast(status, `Could not remove control link: ${errorText(e)}`, 'danger');
  }
});
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
  if (metaEl) metaEl.textContent = bits.filter(Boolean).join(' · ');
  let description = document.getElementById('osaControlDescription');
  if (!description && metaEl) {
    description = document.createElement('div');
    description.id = 'osaControlDescription';
    description.className = 'mt-3';
    metaEl.after(description);
  }
  if (description) {
    description.replaceChildren();
    if (ctrl.metadata?.description) {
      const text = document.createElement('p');
      text.style.whiteSpace = 'pre-wrap';
      text.textContent = ctrl.metadata.description;
      description.append(text);
    }
    if (
      ctrl.framework === 'KEEN-AF:1.0' ||
      ctrl.metadata?.source_url === 'https://github.com/VantaInc/vanta-control-set'
    ) {
      const credit = document.createElement('p');
      credit.className = 'small-muted';
      credit.innerHTML = `Keen Assurance Framework is based on <a href="https://github.com/VantaInc/vanta-control-set" target="_blank" rel="noopener">Vanta's controls</a>, licensed under <a href="/licenses/VANTA-LICENSE.txt" target="_blank" rel="noopener">Apache 2.0</a>. Copyright 2021 Vanta Inc. Adapted by Keen. Keen is also licensed under Apache 2.0.`;
      description.append(credit);
    }
    if (ctrl.metadata?.source === 'https://www.opensecurityarchitecture.org/') {
      const credit = document.createElement('p');
      credit.className = 'small-muted';
      credit.innerHTML =
        'Adapted from <a href="https://www.opensecurityarchitecture.org/" target="_blank" rel="noopener">Open Security Architecture</a> · <a href="/licenses/OSA-LICENSE.txt" target="_blank" rel="noopener">CC BY-SA 4.0</a>. Copyright Open Security Architecture and contributors. <a href="/licenses/OSA-NOTICE.txt" target="_blank" rel="noopener">Attribution and Keen adaptations</a>. Shared NIST controls identify related evidence; coverage estimates do not certify compliance.';
      description.append(credit);
    }
  }
}

function renderControlJustificationCard() {
  if (!controlJustificationCard) return;
  const canEditJustification = !!me?.is_admin;
  controlJustificationCard.style.display = canEditJustification ? '' : 'none';
  if (!canEditJustification) return;
  if (controlJustification) controlJustification.value = ctrl?.justification || '';
}

// Records the tab showing and keeps it in the address.
function setControlTabQuery(tabName) {
  activeTab = tabName || 'requirements';
  const p = new URLSearchParams(location.search);
  if (!tabName || tabName === 'requirements') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}?${p.toString()}`);
}

// Shows a tab and returns its name. Unknown names and inherited names such as
// `constructor` open Requirements.
function activateControlTab(tabName) {
  const name = tabName || 'requirements';
  const tabs = {
    requirements: controlRequirementsTab,
    evidence: controlEvidenceTab,
    related: controlRelatedTab,
    governance: controlGovernanceTab,
  };
  const resolved = Object.hasOwn(tabs, name) ? name : 'requirements';
  if (tabs[resolved] && window.bootstrap?.Tab)
    window.bootstrap.Tab.getOrCreateInstance(tabs[resolved]).show();
  return resolved;
}

function wireControlTabs() {
  if (!canViewRisks) risksCard?.classList.add('d-none');
  if (!canViewInterestedParties) interestedPartiesCard?.classList.add('d-none');
  controlRequirementsTab?.addEventListener('shown.bs.tab', () => {
    setControlTabQuery('requirements');
    loadRequirements();
  });
  controlEvidenceTab?.addEventListener('shown.bs.tab', () => {
    setControlTabQuery('evidence');
    ensureEvidence();
  });
  controlRelatedTab?.addEventListener('shown.bs.tab', async () => {
    setControlTabQuery('related');
    await loadRelatedControls();
  });
  controlGovernanceTab?.addEventListener('shown.bs.tab', async () => {
    setControlTabQuery('governance');
    await Promise.all([
      loadLinkedRisks(),
      loadLinkedInterestedParties(),
      loadControlIsms(),
      loadControlChangelog(),
    ]);
  });
  activeTab = activateControlTab(qs('tab', 'requirements'));
}

wireControlTabs();

async function loadControlChangelog() {
  if (!controlChangelogEl || !id) return;
  await loadEntityChangelog(
    controlChangelogEl,
    `/api/v1/controls/${encodeURIComponent(id)}/changelog?limit=100`,
    { empty: 'No control changes have been recorded yet.' },
  );
}

function _ismsEntityRows(data) {
  const groups = [
    ['SWOT', data.swot, (x) => x.title, (x) => `/swot.html?id=${encodeURIComponent(x.id)}`],
    [
      'Objectives',
      data.objectives,
      (x) => x.goal || x.requirement || 'Objective',
      () => '/isms.html?tab=objectives',
    ],
    ['Organisation chart', data.org_nodes, (x) => x.name || 'Org node', () => '/isms.html?tab=org'],
    ['Assets', data.assets, (x) => x.asset || 'Asset', () => '/isms.html?tab=assets'],
    [
      'Meetings',
      data.meetings,
      (x) => x.title || 'Meeting',
      (x) =>
        x.id ? `/isms-meeting.html?id=${encodeURIComponent(x.id)}` : '/isms.html?tab=meetings',
    ],
  ];
  const rows = [];
  for (const [label, items, titleFn, hrefFn] of groups) {
    for (const item of items || []) {
      const href = typeof hrefFn === 'function' ? hrefFn(item) : hrefFn;
      rows.push(
        `<tr><td>${esc(label)}</td><td class="wrap"><a class="fw-semibold" href="${esc(withFramework(href, ctrl?.framework || framework))}">${esc(shorten(titleFn(item), 180))}</a></td><td class="small-muted">${esc(fmtTs(item.updated_at) || '')}</td></tr>`,
      );
    }
  }
  if (!rows.length)
    return '<div class="small-muted">No SWOT items, objectives, organisation roles, assets or meetings are linked to this control.</div>';
  return `<div class="table-responsive"><table class="table table-sm align-middle mb-0"><thead><tr><th style="width:190px;">Type</th><th>Entity</th><th style="width:170px;">Updated</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}

async function loadControlIsms({ force = false } = {}) {
  if (!featureEnabled(me, 'isms')) return;
  if (!controlIsmsView || !id) return;
  if (!canViewIsms) {
    controlIsmsView.innerHTML =
      '<span class="small-muted">You do not have permission to view ISMS references.</span>';
    return;
  }
  if (ismsLoaded && !force) return;
  controlIsmsView.innerHTML = '<span class="small-muted">Loading ISMS references</span>';
  try {
    const data = await loadIsmsReferences();
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

function applicabilityBadge(v) {
  if (v === 'partially_applicable')
    return '<span class="badge text-bg-warning">Partially applicable</span>';
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

  const rows = [...links].sort((a, b) =>
    String(a.ref || '').localeCompare(String(b.ref || ''), undefined, { numeric: true }),
  );
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
          ${rows
            .map((c) => {
              const clauseHref = c.clause_id
                ? withFramework(
                    `/clause.html?id=${encodeURIComponent(c.clause_id)}`,
                    ctrl?.framework || framework,
                  )
                : '';
              const refHtml = clauseHref
                ? `<a href="${esc(clauseHref)}">${esc(c.ref || '')}</a>`
                : esc(c.ref || '');
              return `<tr>
              <td class="fw-semibold">${refHtml}</td>
              <td class="wrap">${esc(c.title || '')}</td>
              <td>${applicabilityBadge(c.applicability)}</td>
            </tr>`;
            })
            .join('')}
        </tbody>
      </table>
    </div>`;
}

let risksLoaded = false;
async function loadLinkedRisks({ force = false } = {}) {
  if (!featureEnabled(me, 'risk_cia')) return;
  if (!risksCard || !risksView) return;
  if (!canViewRisks) {
    risksCard.style.display = 'none';
    return;
  }
  if (risksLoaded && !force) return;
  try {
    const data = await apiGet(
      `/api/v1/controls/${encodeURIComponent(id)}/risks?framework=${encodeURIComponent(ctrl?.framework || framework)}`,
    );
    const items = data?.items || [];
    risksLoaded = true;
    risksCard.style.display = '';
    if (risksCount) risksCount.textContent = String(items.length);
    if (!items.length) {
      risksView.innerHTML =
        '<span class="small-muted">No risks mapped to this control for this framework.</span>';
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
            ${items
              .map((r) => {
                const href = withFramework(
                  `/risk.html?id=${encodeURIComponent(r.id || '')}`,
                  ctrl?.framework || framework,
                );
                const types = (r.risk_types || [])
                  .map((x) => `<span class="badge badge-soft me-1">${esc(x)}</span>`)
                  .join('');
                return `<tr>
                <td><a class="fw-semibold" href="${esc(href)}">${esc(r.asset || '')}</a><div class="small-muted text-truncate" style="max-width:360px;">${esc(r.threat_summary || '')}</div></td>
                <td>${esc(r.category?.name || '')}</td>
                <td>${esc(r.subcategory?.name || '')}</td>
                <td>${types || '<span class="small-muted">-</span>'}</td>
                <td class="text-end">${scoreBadge(r.risk_score)}</td>
                <td class="text-end">${scoreBadge(r.residual_risk_score)}</td>
                <td>${userPillHtml(r.risk_owner)}</td>
              </tr>`;
              })
              .join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    // Users without risk.read should not get a noisy control page.
    risksLoaded = false;
    risksCard.style.display = 'none';
  }
}
async function loadLinkedInterestedParties({ force = false } = {}) {
  if (!featureEnabled(me, 'interested_parties')) return;
  if (!interestedPartiesCard || !interestedPartiesView) return;
  if (!canViewInterestedParties) {
    interestedPartiesCard.style.display = 'none';
    return;
  }
  if (interestedPartiesLoaded && !force) return;
  interestedPartiesCard.style.display = '';
  interestedPartiesView.innerHTML = '<span class="small-muted">Loading interested parties</span>';
  try {
    const data = await apiGet(
      `/api/v1/controls/${encodeURIComponent(id)}/interested-parties?framework=${encodeURIComponent(ctrl?.framework || framework)}`,
    );
    const items = data?.items || [];
    interestedPartiesLoaded = true;
    if (interestedPartiesCount) interestedPartiesCount.textContent = String(items.length);
    if (!items.length) {
      interestedPartiesView.innerHTML =
        '<span class="small-muted">No interested parties mapped to this control for this framework.</span>';
      return;
    }
    interestedPartiesView.innerHTML = `
      <div class="table-responsive">
        <table class="table table-sm align-middle mb-0">
          <thead><tr><th>Name</th><th>Nature of interest</th><th>Note</th><th>Communications</th><th>Methods</th></tr></thead>
          <tbody>
            ${items
              .map((it) => {
                const href = withFramework(
                  `/interested_party.html?id=${encodeURIComponent(it.id || '')}`,
                  ctrl?.framework || framework,
                );
                const comms =
                  (it.communications || [])
                    .map((c) => `${esc(c.event || '')} / ${esc(c.with_whom || '')}`)
                    .join('<br>') || '<span class="small-muted">-</span>';
                const methods =
                  Array.from(new Set((it.communications || []).flatMap((c) => c.methods || [])))
                    .map((m) => `<span class="badge badge-soft me-1 mb-1">${esc(m)}</span>`)
                    .join('') || '<span class="small-muted">-</span>';
                const note = (it.note || '').trim()
                  ? esc(shorten(it.note, 140))
                  : '<span class="small-muted">-</span>';
                return `<tr><td><a class="fw-semibold" href="${esc(href)}">${esc(it.name?.name || '')}</a></td><td>${esc(it.nature?.name || '')}</td><td class="small text-wrap" style="min-width:220px;">${note}</td><td class="small">${comms}</td><td>${methods}</td></tr>`;
              })
              .join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    interestedPartiesLoaded = false;
    if (interestedPartiesCount) interestedPartiesCount.textContent = '0';
    interestedPartiesView.innerHTML =
      '<span class="text-danger">Failed to load interested parties for this control.</span>';
  }
}

function flattenClauseTree(nodes, depth = 0, out = []) {
  for (const n of nodes || []) {
    out.push({ ...n, depth });
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
  return adminClausesList.querySelector(
    `.clause-applicability[data-clause-id="${CSS.escape(String(id))}"]`,
  );
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
    } else if (
      selected.length === childValues.length &&
      selected.every((v) => v === 'applicable')
    ) {
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
    adminClausesList.innerHTML =
      '<span class="small-muted">No clauses are loaded for this framework.</span>';
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
          ${flat
            .map((c) => {
              const cid = String(c.id || '');
              const cur = selected.get(cid) || '';
              const depth = Math.max(0, Number(c.depth || 0));
              const indent = depth ? `style="padding-left:${Math.min(depth * 1.25, 4)}rem"` : '';
              const parentClass = depth === 0 ? 'fw-bold' : '';
              const clauseHref = withFramework(
                `/clause.html?id=${encodeURIComponent(cid)}`,
                ctrl?.framework || framework,
              );
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
            })
            .join('')}
        </tbody>
      </table>
    </div>`;

  for (const sel of adminClausesList.querySelectorAll('.clause-applicability')) {
    sel.addEventListener('change', applyInferredParentClauseApplicability);
  }
  applyInferredParentClauseApplicability();
}

async function loadClauseCatalog() {
  const data = await apiGet(
    `/api/v1/clauses?framework=${encodeURIComponent(ctrl?.framework || framework)}`,
  );
  clauseCatalog = data || { items: [], tree: [] };
}

async function ensureClauseCatalogLoaded() {
  if (!me?.is_admin) return;
  const hasItems = Array.isArray(clauseCatalog?.items) && clauseCatalog.items.length > 0;
  const hasTree = Array.isArray(clauseCatalog?.tree) && clauseCatalog.tree.length > 0;
  if (hasItems || hasTree) return;
  await loadClauseCatalog();
}

async function reloadControlOnly() {
  ctrl = await apiGet(
    `/api/v1/controls/${encodeURIComponent(id)}?framework=${encodeURIComponent(framework)}`,
  );
  renderControlChrome();
  renderControlJustificationCard();
  renderLinkedClauses();
  risksLoaded = false;
  await loadLinkedRisks({ force: true });
  interestedPartiesLoaded = false;
  await loadLinkedInterestedParties({ force: true });
  renderAdminClauses();
}

function renderRows(items) {
  tbody.innerHTML = '';

  for (const e of items || []) {
    const srcChip = e.source
      ? sourceBadgeHtml(e.source, sourceMeta, controlScopedSourceHref(e.source))
      : '';

    const ext = upstreamLinkHtml(e.source_url, { title: 'Open source event' });

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="small-muted" data-sort="${esc(tsSortKey(e.timestamp))}">${esc(fmtTs(e.timestamp))}</td>
      <td data-sort="${esc(e.source || '')}">${srcChip}</td>
      <td class="wrap"><a href="${withFramework(`/event.html?id=${encodeURIComponent(e.event_id || e.id)}`, ctrl?.framework || framework)}" title="${esc(e.summary || '')}">${esc(
        shorten(e.summary || '', 160),
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
    // The page offers a source filter only; the controls facet is not needed.
    p.set('max_controls', '0');

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
    .sort((a, b) => b.count - a.count || a.source.localeCompare(b.source));

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
  if (meta) meta.textContent = 'Loading';
  showTableLoading(tbody, 4, 'Loading evidence');

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
    if (meta) meta.textContent = `${page.start}-${page.end} of ${page.total}`;

    renderRows(data.items || []);

    setOffsetPagerDisabled(pagerButtons, state);
  } catch (e) {
    toast(status, `Failed to load evidence: ${errorText(e)}`, 'danger');
    if (meta) meta.textContent = '-';
    tbody.innerHTML =
      '<tr><td colspan="4" class="small-muted p-4">Failed to load evidence.</td></tr>';
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
    ctrl = await apiGet(
      `/api/v1/controls/${encodeURIComponent(id)}?framework=${encodeURIComponent(framework)}`,
    );
    renderControlChrome();
    renderControlJustificationCard();
    if (controlsBreadcrumb)
      controlsBreadcrumb.href = withFramework('/controls.html', ctrl.framework || framework);

    if (linksEl) {
      const guidanceHref = safeAbsoluteHttpHref(ctrl.upstream_url);
      if (guidanceHref) {
        linksEl.innerHTML = `<a class="link-subtle" href="${esc(
          guidanceHref,
        )}" target="_blank" rel="noopener noreferrer"><i class="bi bi-info-circle"></i> Guidance</a>`;
      } else {
        linksEl.textContent = '';
      }
    }

    renderLinkedClauses();
    renderAdminClauses();

    // The tab showing loads first, whether the page opened on it or it was
    // chosen while the control loaded; the linked records load alongside it.
    const tab = activeTab;
    const opening = (async () => {
      sourceMeta = await getSourceMeta();
      syncInputs();
      if (tab === 'evidence') await ensureEvidence();
      if (tab === 'requirements') await loadRequirements();
    })();
    await Promise.all([
      opening,
      loadLinkedRisks(),
      loadLinkedInterestedParties({ force: true }),
      tab === 'related' ? loadRelatedControls() : null,
      tab === 'governance'
        ? Promise.all([loadControlChangelog(), loadControlIsms({ force: true })])
        : null,
    ]);
  } catch (e) {
    toast(status, `Failed to load control: ${errorText(e)}`, 'danger');
    if (meta) meta.textContent = '-';
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
  state = { ...state, q: '', source: '', limit: 50, offset: 0 };
  reload(true);
});

// Pager listeners (bind to BOTH top + bottom)
wireOffsetPagerButtons(pagerButtons, (direction) => {
  state.offset =
    direction === 'prev' ? Math.max(0, state.offset - state.limit) : state.offset + state.limit;
  applyFromInputs(false);
  loadEvents();
});

// Toggle editing of the control's clause applicability.
editClauseApplicabilityBtn?.addEventListener('click', async () => {
  if (!me?.is_admin) return;
  clauseApplicabilityEditMode = !clauseApplicabilityEditMode;
  renderLinkedClauses();
  if (clauseApplicabilityEditMode) {
    try {
      await ensureClauseCatalogLoaded();
      renderAdminClauses();
      adminClausesCard?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) {
      clauseApplicabilityEditMode = false;
      renderLinkedClauses();
      renderAdminClauses();
      toast(status, `Failed to load clauses: ${errorText(e)}`, 'danger');
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
  const defName = (title + (parts.length ? ` (${parts.join(', ')})` : '')).slice(0, 128);

  const name = (prompt('Name this saved search', defName) || '').trim();
  if (!name) return;

  // Canonical URL without pagination offset
  const p = new URLSearchParams();
  p.set('id', id);
  if (ctrl?.framework || framework) p.set('framework', ctrl?.framework || framework);
  p.set('tab', 'evidence');
  if (state.q) p.set('q', state.q);
  if (state.source) p.set('source', state.source);
  if (state.limit) p.set('limit', String(state.limit));
  const url = `/control.html?${p.toString()}`;

  try {
    await apiPost('/api/v1/me/saved-searches', { name, url });
    toast(status, 'Saved', 'success');
  } catch (e) {
    toast(status, `Failed to save: ${errorText(e)}`, 'danger');
  }
});

saveClauseLinksBtn?.addEventListener('click', async () => {
  if (!me?.is_admin) return;
  applyInferredParentClauseApplicability();
  const clauses = [];
  for (const sel of adminClausesList?.querySelectorAll('.clause-applicability') || []) {
    const applicability = (sel.value || '').trim();
    if (!applicability) continue;
    clauses.push({ clause_id: sel.dataset.clauseId, applicability });
  }
  saveClauseLinksBtn.disabled = true;
  try {
    await apiPatch(`/api/v1/admin/controls/${encodeURIComponent(id)}/clauses`, { clauses });
    toast(status, 'Clause applicability saved', 'success');
    await reloadControlOnly();
    await loadControlChangelog();
  } catch (e) {
    toast(status, `Failed to save clauses: ${errorText(e)}`, 'danger');
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
    toast(status, `Failed to save justification: ${errorText(e)}`, 'danger');
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
      const guidanceHref = safeAbsoluteHttpHref(ctrl.upstream_url);
      if (guidanceHref) {
        linksEl.innerHTML = `<a class="link-subtle" href="${esc(guidanceHref)}" target="_blank" rel="noopener noreferrer"><i class="bi bi-info-circle"></i> Guidance</a>`;
      } else {
        linksEl.textContent = '';
      }
    }
  } catch (e) {
    toast(status, `Failed to save control URL: ${errorText(e)}`, 'danger');
  } finally {
    saveControlUpstreamUrlBtn.disabled = false;
  }
});

load();
