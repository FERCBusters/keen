import { mountAssignedStories } from '/pages/story-related.js';
import { catalogueLabel } from '/pages/catalogue-options.js';
import { featureEnabled } from '/pages/feature-availability.js';
import { createRelatedControlsBrowser } from './related-controls.js';
import { createControlRequirements } from './control-requirements.js';
import { linkedRisksData, pestleItemsData } from '/pages/risk-common.js';
import { mountOwners, ownerNames } from '/pages/owners.js';
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
  node,
  refreshPicker,
  applicabilityPill,
  relatedCard,
  relatedCards,
  updateRelatedCard,
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
// The Related items tab: every card is the design system's related-item
// card, so the frame, the headings and the tables match those of other
// records' pages. Clauses and Stories are short and share a row; the cards
// that hold a table each take the full width.
const editClauseApplicabilityBtn = node(
  'button',
  {
    id: 'editClauseApplicability',
    class: 'btn btn-sm btn-outline-primary no-print',
    type: 'button',
  },
  'Edit',
);
editClauseApplicabilityBtn.hidden = true;
const clausesCard = relatedCard({
  id: 'clausesCard',
  title: 'Clauses',
  description: 'Clauses linked to this control and their applicability.',
  count: 0,
  action: editClauseApplicabilityBtn,
  empty: 'No clauses linked.',
});
const controlStoriesHost = node('div', { id: 'controlStories' });
const controlStoriesCard = relatedCard({
  id: 'controlStoriesCard',
  title: 'Stories',
  description:
    "Stories whose authors assigned them to this control, on the story's Controls and clauses tab.",
  body: controlStoriesHost,
  wide: false,
});
controlStoriesCard.dataset.feature = 'stories';
const adminClausesCard = relatedCard({
  id: 'adminClausesCard',
  title: 'Admin: clause applicability',
  description:
    'Choose the clauses this control supports and how far. A clause left out is not applicable; a parent clause follows its children.',
  body: document.getElementById('adminClausesEditor'),
  wide: true,
});
adminClausesCard.classList.add('no-print');
const risksCard = relatedCard({
  id: 'risksCard',
  title: 'Cross-referenced risks',
  description: 'Risks mapped to this control for the selected framework.',
  count: 0,
  empty: 'No risks linked.',
  wide: true,
});
const controlIsmsCard = relatedCard({
  id: 'controlIsmsCard',
  title: 'ISMS references',
  description:
    'SWOT items, legal register items, objectives, organisation roles, assets and meetings linked to this control. Policies, processes and effectiveness measures are under Requirements.',
  action: node(
    'a',
    { class: 'btn btn-sm btn-outline-primary', 'data-nav-fw': '1', href: '/isms.html' },
    'Open ISMS',
  ),
  empty: 'Loading ISMS references',
  wide: true,
});
const controlPestleCard = relatedCard({
  id: 'controlPestleCard',
  title: 'PESTLE(E) relevance',
  description: 'PESTLE(E) items rated against this control.',
  wide: true,
});
// A card shows once its records are loaded for a reader who may see them.
for (const card of [
  clausesCard,
  controlStoriesCard,
  adminClausesCard,
  risksCard,
  controlPestleCard,
])
  card.hidden = true;
// Each card's content and count keep the names the rest of the page knows.
for (const [card, body, count] of [
  [clausesCard, 'clausesView', 'clausesCount'],
  [risksCard, 'risksView', 'risksCount'],
  [controlIsmsCard, 'controlIsmsView', ''],
  [controlPestleCard, 'controlPestleView', ''],
]) {
  card.querySelector('.mosp-related-card-body').id = body;
  if (count) card.querySelector('.mosp-related-card-count').id = count;
}
relatedCards(
  [
    clausesCard,
    controlStoriesCard,
    adminClausesCard,
    risksCard,
    controlIsmsCard,
    controlPestleCard,
  ],
  document.getElementById('controlRelatedItems'),
);
const controlJustificationCard = document.getElementById('controlJustificationCard');
const adminClausesList = document.getElementById('adminClausesList');
const controlUpstreamUrl = document.getElementById('controlUpstreamUrl');
const saveControlUpstreamUrlBtn = document.getElementById('saveControlUpstreamUrl');
const controlJustification = document.getElementById('controlJustification');
const saveControlJustificationBtn = document.getElementById('saveControlJustification');
const saveClauseLinksBtn = document.getElementById('saveClauseLinks');
const controlsBreadcrumb = document.getElementById('controlsBreadcrumb');
const controlRequirementsTab = document.getElementById('controlRequirementsTab');
const controlEvidenceTab = document.getElementById('controlEvidenceTab');
const controlRelatedTab = document.getElementById('controlRelatedTab');
const controlItemsTab = document.getElementById('controlItemsTab');
const controlGovernanceTab = document.getElementById('controlGovernanceTab');
const controlChangelogTab = document.getElementById('controlChangelogTab');
const controlChangelogEl = document.getElementById('controlChangelog');
const canViewRisks = !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
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
  sourceName: (source) => String(sourceLabel(source, sourceMeta)),
  onOpenEvidence: openEvidence,
});

function loadRequirements() {
  if (!ctrl) return null;
  return requirements.load({ id: ctrl.id, framework: ctrl.framework });
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
  try {
    const data = await apiGet(`/api/v1/controls/${encodeURIComponent(id)}/cross-framework`);
    if (!crossFrameworks.length) {
      try {
        const frameworks = await apiGet('/api/v1/frameworks');
        crossFrameworks = frameworks.items.filter((f) => f.slug !== ctrl?.framework);
      } catch {
        // Links remain browsable by framework slug when the catalogue is unavailable.
      }
    }
    relatedControls.update(data.items, crossFrameworks);
    if (!me?.is_admin || !ctrl) return;
    crossForm.style.display = '';
    const previous = crossFrameworkSource.value;
    crossFrameworkSource.innerHTML =
      '<option value="">Choose framework</option>' +
      crossFrameworks.map((f) => `<option value="${esc(f.slug)}">${esc(f.name)}</option>`).join('');
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
    crossFrameworkControl.innerHTML += data.items
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
let clauseCatalog = { items: [], tree: [] };
let clauseApplicabilityEditMode = false;
// Declared before the tabs are wired: a deep-linked tab loads as the page starts.
let risksLoaded = false;
let pestleLoaded = false;
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

// What Open Security Architecture records of how NIST SP 800-53 maps to a
// control it publishes: a crosswalk note, shown as one and never as the
// control's description.
function osaCrosswalk(meta) {
  const rows = [
    ['Related NIST SP 800-53 controls', (meta.osa_nist_controls || []).join(', ')],
    ['Mapping note', meta.rationale],
    ['Gaps in the mapping', meta.gaps],
    ['Estimated coverage', meta.coverage_pct === undefined ? '' : `${meta.coverage_pct}%`],
  ].filter(([, value]) => value);
  if (!rows.length) return null;
  return node(
    'div',
    { class: 'border rounded p-3 mb-2', id: 'osaCrosswalk' },
    node('div', { class: 'fw-semibold mb-2' }, 'Crosswalk to NIST SP 800-53'),
    node(
      'dl',
      { class: 'row mb-0 small' },
      ...rows.flatMap(([term, value]) => [
        node('dt', { class: 'col-sm-3' }, term),
        node('dd', { class: 'col-sm-9' }, value),
      ]),
    ),
  );
}

function renderControlChrome() {
  const titleEl = document.getElementById('ctrlTitle');
  const metaEl = document.getElementById('ctrlMeta');
  if (titleEl) titleEl.textContent = `${ctrl.ref}  ${ctrl.title || ''}`.trim();
  const bits = [ctrl.framework, ctrl.type, ctrl.in_scope ? 'in scope' : 'out of scope'];
  if (ctrl.justification) bits.push(`Justification: ${ctrl.justification}`);
  if (ctrl.owners?.length) bits.push(`Owners: ${ownerNames(ctrl.owners)}`);
  if (metaEl) metaEl.textContent = bits.filter(Boolean).join(' · ');
  owners.show(ctrl.owners || []);
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
      if (ctrl.metadata.superseded)
        description.append(
          node(
            'p',
            { class: 'alert alert-warning py-2 mb-2', id: 'osaSuperseded' },
            `OSA no longer publishes this control, as of its data of ${ctrl.metadata.superseded}. Keen kept it because records here depend on it: move them to a current control, then delete this one in the framework editor.`,
          ),
        );
      const crosswalk = osaCrosswalk(ctrl.metadata);
      if (crosswalk) description.append(crosswalk);
      const credit = document.createElement('p');
      credit.className = 'small-muted';
      credit.innerHTML =
        'Adapted from <a href="https://www.opensecurityarchitecture.org/" target="_blank" rel="noopener">Open Security Architecture</a> · <a href="/licenses/OSA-LICENSE.txt" target="_blank" rel="noopener">CC BY-SA 4.0</a>. Copyright Open Security Architecture and contributors. <a href="/licenses/OSA-NOTICE.txt" target="_blank" rel="noopener">Attribution and Keen adaptations</a>. The crosswalk is OSA\'s assessment: shared NIST controls identify related evidence, and coverage estimates do not certify compliance. Consult the framework\'s own text for what the control requires.';
      description.append(credit);
    }
  }
}

const owners = mountOwners({
  me,
  status,
  path: () => `/api/v1/admin/controls/${encodeURIComponent(id)}/owners`,
  onSaved: (saved) => {
    ctrl.owners = saved;
    renderControlChrome();
    loadControlChangelog();
  },
});

function renderControlJustificationCard() {
  const canEditJustification = !!me?.is_admin;
  controlJustificationCard.style.display = canEditJustification ? '' : 'none';
  if (!canEditJustification) return;
  if (controlJustification) controlJustification.value = ctrl?.justification || '';
}

// Records the tab showing and keeps it in the address.
function setControlTabQuery(tabName) {
  activeTab = tabName;
  const p = new URLSearchParams(location.search);
  if (tabName === 'requirements') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}?${p.toString()}`);
}

// Shows a tab and returns its name. Unknown names and inherited names such as
// `constructor` open Requirements.
function activateControlTab(name) {
  const tabs = {
    requirements: controlRequirementsTab,
    evidence: controlEvidenceTab,
    items: controlItemsTab,
    related: controlRelatedTab,
    governance: controlGovernanceTab,
    changelog: controlChangelogTab,
  };
  const resolved = Object.hasOwn(tabs, name) ? name : 'requirements';
  if (tabs[resolved] && window.bootstrap?.Tab)
    window.bootstrap.Tab.getOrCreateInstance(tabs[resolved]).show();
  return resolved;
}

function wireControlTabs() {
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
  // Related items: the records that refer to the control, each in its card.
  controlItemsTab?.addEventListener('shown.bs.tab', async () => {
    setControlTabQuery('items');
    await Promise.all([loadLinkedRisks(), loadControlPestle(), loadControlIsms()]);
  });
  // Governance holds the owners and the justification, loaded with the control.
  controlGovernanceTab?.addEventListener('shown.bs.tab', () => setControlTabQuery('governance'));
  controlChangelogTab?.addEventListener('shown.bs.tab', async () => {
    setControlTabQuery('changelog');
    await loadControlChangelog();
  });
  activeTab = activateControlTab(qs('tab', 'requirements'));
}

wireControlTabs();

async function loadControlChangelog() {
  if (!id) return;
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
      'Legal register',
      data.legal_items,
      (x) => x.title,
      (x) => `/legal-register.html?id=${encodeURIComponent(x.id)}`,
    ],
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
      rows.push([
        label,
        {
          text: shorten(titleFn(item), 180),
          href: withFramework(hrefFn(item), ctrl?.framework || framework),
        },
        fmtTs(item.updated_at),
      ]);
    }
  }
  return {
    table: {
      columns: [{ label: 'Type', width: '190px' }, 'Entity', { label: 'Updated', width: '170px' }],
      rows,
    },
    empty:
      'No SWOT items, legal register items, objectives, organisation roles, assets or meetings are linked to this control.',
  };
}

async function loadControlIsms({ force = false } = {}) {
  if (!featureEnabled(me, 'isms')) return;
  if (!id) return;
  if (!canViewIsms) {
    updateRelatedCard(controlIsmsCard, {
      empty: 'You do not have permission to view ISMS references.',
    });
    return;
  }
  if (ismsLoaded && !force) return;
  updateRelatedCard(controlIsmsCard, { empty: 'Loading ISMS references' });
  try {
    const data = await loadIsmsReferences();
    ismsLoaded = true;
    updateRelatedCard(controlIsmsCard, _ismsEntityRows(data));
  } catch (e) {
    ismsLoaded = false;
    updateRelatedCard(controlIsmsCard, {
      body: node('span', { class: 'text-danger' }, 'Failed to load ISMS references.'),
    });
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

function renderLinkedClauses() {
  const links = [...ctrl.clauses].sort((a, b) =>
    a.ref.localeCompare(b.ref, undefined, { numeric: true }),
  );
  clausesCard.hidden = false;
  editClauseApplicabilityBtn.hidden = !me?.is_admin;
  editClauseApplicabilityBtn.textContent = clauseApplicabilityEditMode ? 'Hide edit' : 'Edit';
  updateRelatedCard(clausesCard, {
    count: links.length,
    table: {
      columns: [
        { label: 'Clause', width: '120px' },
        'Title',
        { label: 'Applicability', width: '190px' },
      ],
      rows: links.map((c) => [
        c.clause_id
          ? {
              text: c.ref,
              href: withFramework(
                `/clause.html?id=${encodeURIComponent(c.clause_id)}`,
                ctrl.framework,
              ),
            }
          : node('span', { class: 'fw-semibold' }, c.ref),
        c.title,
        applicabilityPill(c.applicability),
      ]),
    },
    empty: 'No clauses linked to this control.',
  });
}

async function loadLinkedRisks({ force = false } = {}) {
  if (!featureEnabled(me, 'risk_cia')) return;
  if (!id) return;
  if (!canViewRisks) {
    risksCard.hidden = true;
    return;
  }
  if (risksLoaded && !force) return;
  try {
    const data = await apiGet(
      `/api/v1/controls/${encodeURIComponent(id)}/risks?framework=${encodeURIComponent(ctrl?.framework || framework)}`,
    );
    const items = data.items;
    risksLoaded = true;
    risksCard.hidden = false;
    updateRelatedCard(risksCard, {
      count: items.length,
      table: linkedRisksData(items, ctrl?.framework || framework),
      empty: 'No risks mapped to this control for this framework.',
    });
  } catch (e) {
    // Users without risk.read should not get a noisy control page.
    risksLoaded = false;
    risksCard.hidden = true;
  }
}
// PESTLE(E) readers see the items rated against the control, once per page.
async function loadControlPestle() {
  if (!id || pestleLoaded || !featureEnabled(me, 'pestle')) return;
  if (!(me?.is_admin || me?.can_view_pestle || me?.can_manage_pestle)) return;
  try {
    const data = await apiGet(`/api/v1/controls/${encodeURIComponent(id)}/pestle`);
    pestleLoaded = true;
    updateRelatedCard(controlPestleCard, {
      table: pestleItemsData(data.items, ctrl?.framework || framework, 'Control'),
      empty: 'No PESTLE(E) items are rated against this control.',
    });
    controlPestleCard.hidden = false;
  } catch (e) {
    controlPestleCard.hidden = true;
  }
}

function flattenClauseTree(nodes, depth = 0, out = []) {
  for (const n of nodes) {
    out.push({ ...n, depth });
    flattenClauseTree(n.children, depth + 1, out);
  }
  return out;
}

function selectedClauseMap() {
  const m = new Map();
  for (const c of ctrl.clauses) {
    if (c.clause_id) m.set(String(c.clause_id), c.applicability);
  }
  return m;
}

// The clauses are chosen in a multiple select, which the design system
// presents as its picker; each chosen clause carries its applicability. A
// clause that is not chosen is not applicable.
const controlClauses = document.getElementById('controlClauses');
const APPLICABILITY = JSON.stringify({
  label: 'Applicability',
  choices: [
    ['applicable', 'Applicable'],
    ['partially_applicable', 'Partially applicable'],
  ],
  default: 'applicable',
});

function clauseOption(id) {
  return Array.from(controlClauses.options).find((option) => option.value === String(id));
}

function clauseApplicability(option) {
  return option.selected ? option.dataset.extra || 'applicable' : '';
}

function setClauseApplicability(option, value) {
  option.selected = Boolean(value);
  if (value) option.dataset.extra = value;
}

function applyInferredParentClauseApplicability() {
  const flat = flattenClauseTree(clauseCatalog.tree);
  const childrenByParent = new Map();
  for (const c of flat) {
    if (!c.parent_id) continue;
    const key = String(c.parent_id);
    if (!childrenByParent.has(key)) childrenByParent.set(key, []);
    childrenByParent.get(key).push(String(c.id));
  }

  const byId = new Map(flat.map((c) => [String(c.id), c]));

  const memo = new Map();
  function inferValue(clauseId) {
    const key = String(clauseId);
    if (memo.has(key)) return memo.get(key);

    const kids = childrenByParent.get(key) || [];
    if (!kids.length) {
      const value = clauseApplicability(clauseOption(key));
      memo.set(key, value);
      return value;
    }

    const childValues = kids.map((kid) => inferValue(kid));
    const selected = childValues.filter(Boolean);
    let inferred;
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
    setClauseApplicability(clauseOption(key), valueToStore);
    memo.set(key, inferred);
    return inferred;
  }

  for (const c of flat) inferValue(c.id);
  // The parents were set from code, so the picker is asked to show them.
  refreshPicker(controlClauses);
}

function renderAdminClauses() {
  if (!me?.is_admin || !clauseApplicabilityEditMode) {
    adminClausesCard.hidden = true;
    return;
  }
  adminClausesCard.hidden = false;
  if (controlUpstreamUrl) controlUpstreamUrl.value = ctrl?.upstream_url || '';

  const flat = flattenClauseTree(clauseCatalog.tree);
  const selected = selectedClauseMap();
  adminClausesList.textContent = flat.length ? '' : 'No clauses are loaded for this framework.';
  controlClauses.dataset.pickerExtra = APPLICABILITY;
  controlClauses.replaceChildren(
    ...flat.map((c) => {
      const option = node('option', { value: c.id }, catalogueLabel(c));
      setClauseApplicability(option, selected.get(String(c.id)) || '');
      return option;
    }),
  );
  applyInferredParentClauseApplicability();
}

// A parent clause follows its children as they are chosen.
controlClauses.addEventListener('change', applyInferredParentClauseApplicability);

async function loadClauseCatalog() {
  const data = await apiGet(`/api/v1/clauses?framework=${encodeURIComponent(ctrl.framework)}`);
  clauseCatalog = data;
}

async function ensureClauseCatalogLoaded() {
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
  renderAdminClauses();
}

function renderRows(items) {
  tbody.innerHTML = '';

  for (const e of items) {
    const srcChip = sourceBadgeHtml(e.source, sourceMeta, controlScopedSourceHref(e.source));

    const ext = upstreamLinkHtml(e.source_url, { title: 'Open source event' });

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="small-muted" data-sort="${esc(tsSortKey(e.timestamp))}">${esc(fmtTs(e.timestamp))}</td>
      <td data-sort="${esc(e.source)}">${srcChip}</td>
      <td class="wrap"><a href="${withFramework(`/event.html?id=${encodeURIComponent(e.event_id || e.id)}`, ctrl?.framework || framework)}" title="${esc(e.summary)}">${esc(
        shorten(e.summary, 160),
      )}</a></td>
      <td>${ext}</td>
    `;
    tbody.appendChild(tr);
  }

  if (items.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="4" class="small-muted p-4">No evidence matches your filters.</td>`;
    tbody.appendChild(tr);
  }
}

function applyFromInputs(resetOffset = true) {
  state.q = (localQ?.value || '').trim();
  state.source = sourceFilter?.value || '';
  state.limit = parseInt(selLimit.value, 10);
  if (resetOffset) state.offset = 0;

  setQuery({
    id,
    framework: ctrl?.framework || framework,
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
    renderSourceOptions(data.sources);
  } catch {
    // Non-fatal; keep existing options.
  }
}

function renderSourceOptions(facets) {
  const current = state.source || '';
  const rows = facets;
  const counts = new Map(rows.map((r) => [String(r.source), Number(r.count || 0)]));

  const items = rows
    .filter((r) => r && r.source && Number(r.count || 0) > 0)
    .map((r) => ({
      source: String(r.source),
      count: Number(r.count),
    }))
    .sort((a, b) => b.count - a.count || a.source.localeCompare(b.source));

  let html = '<option value="">All sources</option>';

  // Preserve current selection even if it yields zero results.
  if (current && !counts.has(current)) {
    const label = String(sourceLabel(current, sourceMeta));
    const text = label && label !== current ? `${label} (${current})` : current;
    html += `<option value="${esc(current)}">${esc(text)} (0)</option>`;
  }

  for (const it of items) {
    const src = it.source;
    const label = String(sourceLabel(src, sourceMeta));
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

    renderRows(data.items);

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
      controlsBreadcrumb.href = withFramework('/controls.html', ctrl.framework);

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
      tab === 'related' ? loadRelatedControls() : null,
      tab === 'items' ? loadControlIsms({ force: true }) : null,
      tab === 'changelog' ? loadControlChangelog() : null,
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
editClauseApplicabilityBtn.addEventListener('click', async () => {
  clauseApplicabilityEditMode = !clauseApplicabilityEditMode;
  renderLinkedClauses();
  if (clauseApplicabilityEditMode) {
    try {
      await ensureClauseCatalogLoaded();
      renderAdminClauses();
      adminClausesCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

  const title = document.getElementById('ctrlTitle').textContent.trim();
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
  applyInferredParentClauseApplicability();
  const clauses = Array.from(controlClauses.selectedOptions, (option) => ({
    clause_id: option.value,
    applicability: clauseApplicability(option),
  }));
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
  saveControlUpstreamUrlBtn.disabled = true;
  try {
    await apiPatch(`/api/v1/admin/controls/${encodeURIComponent(id)}/upstream-url`, {
      upstream_url: controlUpstreamUrl.value.trim() || null,
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

// The stories whose authors assigned them to this control, among its related items.
const stories = mountAssignedStories({
  me,
  kind: 'control',
  id,
  host: controlStoriesHost,
  trigger: controlItemsTab,
});
controlStoriesCard.hidden = !stories;
