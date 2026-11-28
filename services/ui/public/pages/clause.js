import { mountAssignedStories } from '/pages/story-related.js';
import { catalogueLabel } from '/pages/catalogue-options.js';
import { featureEnabled } from '/pages/feature-availability.js';
import { mountOwners, ownerNames } from '/pages/owners.js';
import { linkedRisksData, pestleItemsData } from '/pages/risk-common.js';
import {
  fmtTs,
  errorText,
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
  node,
  loadEntityChangelog,
  applicabilityBadge,
  relatedCard,
  relatedCards,
  shorten,
  updateRelatedCard,
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
const clauseControls = document.getElementById('clauseControls');
const editLinkedControls = document.getElementById('editLinkedControls');
const clauseEvidenceMeta = document.getElementById('clauseEvidenceMeta');
const clauseEvidenceLinks = document.getElementById('clauseEvidenceLinks');
const editClauseEvidence = document.getElementById('editClauseEvidence');
const adminClauseEvidenceEditor = document.getElementById('adminClauseEvidenceEditor');
const clauseEvidenceUrlInputs = document.getElementById('clauseEvidenceUrlInputs');
const addClauseEvidenceUrl = document.getElementById('addClauseEvidenceUrl');
const saveClauseEvidenceUrls = document.getElementById('saveClauseEvidenceUrls');
const clauseControlsTab = document.getElementById('clauseControlsTab');
const clauseItemsTab = document.getElementById('clauseItemsTab');
const clauseChangelogTab = document.getElementById('clauseChangelogTab');
// The Related items tab: every card is the design system's related-item
// card, as on a control's page.
const clauseRisksCard = relatedCard({
  id: 'clauseRisksCard',
  title: 'Cross-referenced risks',
  description: 'Risks linked to this clause.',
  count: 0,
  wide: true,
});
const openPestleFromClause = node(
  'a',
  { id: 'openPestleFromClause', class: 'btn btn-sm btn-outline-primary', href: '/pestle.html' },
  'Open PESTLE(E)',
);
const clausePestleCard = relatedCard({
  id: 'clausePestleCard',
  title: 'PESTLE(E) relevance',
  description: 'PESTLE(E) items linked to this clause for the selected framework.',
  action: openPestleFromClause,
  wide: true,
});
const clauseIsmsCard = relatedCard({
  id: 'clauseIsmsCard',
  title: 'ISMS references',
  description:
    'SWOT items, legal register items, objectives, documents, organisation roles, assets and meetings linked to this clause.',
  action: node(
    'a',
    { class: 'btn btn-sm btn-outline-primary', 'data-nav-fw': '1', href: '/isms.html' },
    'Open ISMS',
  ),
  empty: 'Loading ISMS references',
  wide: true,
});
const clauseStoriesHost = node('div', { id: 'clauseStories' });
const clauseStoriesCard = relatedCard({
  id: 'clauseStoriesCard',
  title: 'Stories',
  description:
    "Stories whose authors assigned them to this clause, on the story's Controls and clauses tab.",
  body: clauseStoriesHost,
  wide: false,
});
// A card shows once its records are loaded for a reader who may see them.
for (const card of [clauseRisksCard, clausePestleCard, clauseStoriesCard]) card.hidden = true;
clauseIsmsCard.hidden = !featureEnabled(me, 'isms');
// Each card's content and count keep the names the rest of the page knows.
for (const [card, body, count] of [
  [clauseRisksCard, 'clauseRisksView', 'clauseRisksCount'],
  [clauseIsmsCard, 'clauseIsmsView', ''],
  [clausePestleCard, 'clausePestleView', ''],
]) {
  card.querySelector('.mosp-related-card-body').id = body;
  if (count) card.querySelector('.mosp-related-card-count').id = count;
}
relatedCards(
  [clauseStoriesCard, clauseRisksCard, clauseIsmsCard, clausePestleCard],
  document.getElementById('clauseRelatedItems'),
);
const clauseChangelogEl = document.getElementById('clauseChangelog');
const canViewIsms = !!(
  me?.is_admin ||
  me?.can_view_isms ||
  me?.can_manage_isms ||
  me?.can_view_risks ||
  me?.can_manage_risks
);
let ismsLoaded = false;
let pestleLoaded = false;

// Earlier addresses named a tab for each kind of related record; they open
// Related items now.
const TAB_NAMES = { isms: 'related', pestle: 'related', stories: 'related', items: 'related' };

function openTab() {
  const name = qs('tab', 'controls');
  return TAB_NAMES[name] || name;
}

function setClauseTabQuery(tabName) {
  const p = new URLSearchParams(location.search);
  if (tabName === 'controls') p.delete('tab');
  else p.set('tab', tabName);
  history.replaceState({}, '', `${location.pathname}?${p.toString()}`);
}

function activateClauseTab(tabName) {
  const tab =
    tabName === 'changelog'
      ? clauseChangelogTab
      : tabName === 'related'
        ? clauseItemsTab
        : clauseControlsTab;
  if (!window.bootstrap?.Tab) return;
  window.bootstrap.Tab.getOrCreateInstance(tab).show();
}

// Risk readers see the risks linked to the clause; nobody else sees the card.
async function loadClauseRisks() {
  if (!featureEnabled(me, 'risk_cia')) return;
  if (!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks)) return;
  try {
    const data = await apiGet(`/api/v1/clauses/${encodeURIComponent(id)}/risks`);
    updateRelatedCard(clauseRisksCard, {
      count: data.items.length,
      table: linkedRisksData(data.items, data.framework),
      empty: 'No risks are linked to this clause.',
    });
    clauseRisksCard.hidden = false;
  } catch (e) {
    clauseRisksCard.hidden = true;
  }
}

// PESTLE(E) readers see the items rated against the clause.
async function loadClausePestle({ force = false } = {}) {
  if (!id || !featureEnabled(me, 'pestle')) return;
  if (!(me?.is_admin || me?.can_view_pestle || me?.can_manage_pestle)) return;
  if (pestleLoaded && !force) return;
  try {
    const data = await apiGet(`/api/v1/clauses/${encodeURIComponent(id)}/pestle`);
    pestleLoaded = true;
    openPestleFromClause.href = withFramework('/pestle.html', clause?.framework || framework);
    updateRelatedCard(clausePestleCard, {
      table: pestleItemsData(data.items, clause?.framework || framework, 'Clause'),
      empty: 'No PESTLE(E) items are currently linked to this clause.',
    });
    clausePestleCard.hidden = false;
  } catch (e) {
    pestleLoaded = false;
    clausePestleCard.hidden = true;
  }
}

async function loadClauseChangelog() {
  if (!id) return;
  await loadEntityChangelog(
    clauseChangelogEl,
    `/api/v1/clauses/${encodeURIComponent(id)}/changelog?limit=100`,
    { empty: 'No clause changes have been recorded yet.' },
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
    ['Documents', data.documents, (x) => x.title || 'Document', () => '/isms.html?tab=documents'],
    ['Organisation chart', data.org_nodes, (x) => x.name || 'Org node', () => '/isms.html?tab=org'],
    ['Assets', data.assets, (x) => x.asset || 'Asset', () => '/isms.html?tab=assets'],
    ['Meetings', data.meetings, (x) => x.title || 'Meeting', () => '/isms.html?tab=meetings'],
  ];
  const rows = [];
  for (const [label, items, titleFn, hrefFn] of groups) {
    for (const item of items || []) {
      rows.push([
        label,
        {
          text: shorten(titleFn(item), 180),
          href: withFramework(hrefFn(item), clause?.framework || framework),
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
    empty: 'No ISMS entities are currently linked to this clause.',
  };
}

async function loadClauseIsms({ force = false } = {}) {
  if (!featureEnabled(me, 'isms')) return;
  if (!id) return;
  if (!canViewIsms) {
    updateRelatedCard(clauseIsmsCard, {
      empty: 'You do not have permission to view ISMS references.',
    });
    return;
  }
  if (ismsLoaded && !force) return;
  updateRelatedCard(clauseIsmsCard, { empty: 'Loading ISMS references' });
  try {
    const data = await apiGet(
      `/api/v1/clauses/${encodeURIComponent(id)}/isms?framework=${encodeURIComponent(clause?.framework || framework)}`,
    );
    ismsLoaded = true;
    updateRelatedCard(clauseIsmsCard, _ismsEntityRows(data));
  } catch (e) {
    ismsLoaded = false;
    updateRelatedCard(clauseIsmsCard, {
      body: node('span', { class: 'text-danger' }, 'Failed to load ISMS references.'),
    });
  }
}

function wireClauseTabs() {
  clauseControlsTab.addEventListener('shown.bs.tab', () => setClauseTabQuery('controls'));
  clauseItemsTab.addEventListener('shown.bs.tab', async () => {
    setClauseTabQuery('related');
    await Promise.all([loadClausePestle(), loadClauseIsms()]);
  });
  clauseChangelogTab.addEventListener('shown.bs.tab', () => {
    setClauseTabQuery('changelog');
    loadClauseChangelog();
  });
  activateClauseTab(openTab());
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

function linkedMap() {
  const m = new Map();
  for (const c of linkedControls) {
    if (c?.id) m.set(String(c.id), c.applicability);
  }
  return m;
}

function isIntermediateParentClause() {
  return Boolean(clause?.parent_id && Number(clause?.child_count) > 0);
}

function clauseEvidenceMappings() {
  return clause.evidence_mappings
    .map((item) => ({
      title: String(item?.title || '').trim(),
      url: String(item?.url).trim(),
    }))
    .filter((item) => item.url);
}

function evidenceMappingRow(item = {}) {
  const title = String(item?.title || '');
  const url = String(item?.url || '');
  return `<div class="clause-evidence-mapping-row border rounded p-2">
    <div class="row g-2 align-items-end">
      <div class="col-12 col-lg-4">
        <label class="form-label small-muted mb-1">Title</label>
        <input class="form-control form-control-sm clause-evidence-title" type="text" value="${esc(title)}" maxlength="256" placeholder="e.g. Statement of Applicability">
      </div>
      <div class="col-12 col-lg-7">
        <label class="form-label small-muted mb-1">URL</label>
        <input class="form-control form-control-sm clause-evidence-url" type="text" value="${esc(url)}" placeholder="https:// or /event.html?id=">
      </div>
      <div class="col-12 col-lg-1 d-grid">
        <button class="btn btn-sm btn-outline-secondary remove-clause-evidence-url" type="button" title="Remove evidence mapping" aria-label="Remove evidence mapping"><i class="bi bi-x-lg" aria-hidden="true"></i></button>
      </div>
    </div>
  </div>`;
}

function wireEvidenceUrlRemoveButtons() {
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
      ? titledLinksHtml(mappings, { title: 'Open evidence mapping' })
      : '<span class="small-muted">No evidence mapping links have been set.</span>';
  }
  if (editClauseEvidence) {
    editClauseEvidence.style.display = me?.is_admin ? '' : 'none';
    editClauseEvidence.textContent = evidenceEditMode ? 'Hide edit' : 'Edit';
  }
  if (adminClauseEvidenceEditor) {
    adminClauseEvidenceEditor.style.display = me?.is_admin && evidenceEditMode ? '' : 'none';
  }
  if (me?.is_admin && evidenceEditMode) renderEvidenceEditor();
}

const owners = mountOwners({
  me,
  status,
  path: () => `/api/v1/admin/clauses/${encodeURIComponent(id)}/owners`,
  onSaved: (saved) => {
    clause = { ...clause, owners: saved };
    renderClauseHeader();
    loadClauseChangelog();
  },
});

function renderClauseHeader() {
  clauseTitle.textContent = `${clause.ref}  ${clause.title}`.trim();
  const parts = [clause.framework];
  if (clause?.parent_ref) parts.push(`parent ${clause.parent_ref}`);
  if (clause?.owners?.length) parts.push(`Owners: ${ownerNames(clause.owners)}`);
  clauseMeta.textContent = parts.filter(Boolean).join(' · ');
  owners.show(clause?.owners || []);
  if (clausesBreadcrumb) clausesBreadcrumb.href = withFramework('/clauses.html', clause.framework);

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
    adminControlsCard.style.display = me?.is_admin && linkedControlsEditMode ? '' : 'none';
  }
  renderEvidenceMapping();
  openPestleFromClause.href = withFramework('/pestle.html', clause.framework);
}

function renderControls(rows) {
  if (controlsMeta)
    controlsMeta.textContent = `${rows.length} linked control${rows.length === 1 ? '' : 's'}`;
  if (!rows.length) {
    rowsEl.innerHTML =
      '<tr><td colspan="4" class="small-muted p-4">No controls are linked to this clause.</td></tr>';
    return;
  }

  rowsEl.innerHTML = rows
    .map((c) => {
      const href = withFramework(
        `/control.html?id=${encodeURIComponent(c.id)}`,
        c.framework || framework,
      );
      return `<tr>
      <td class="fw-semibold"><a href="${esc(href)}">${esc(c.ref)}</a></td>
      <td class="wrap">${esc(c.title || '')}</td>
      <td data-sort="${esc(applicabilityLabel(c.applicability))}">${applicabilityBadge(c.applicability)}</td>
      <td>${c.in_scope ? '<span class="badge badge-soft">In scope</span>' : '<span class="badge text-bg-light">Out</span>'}</td>
    </tr>`;
    })
    .join('');
}

// The linked controls are chosen in a multiple select, which the design system
// presents as its picker; each chosen control carries its applicability.
const APPLICABILITY = JSON.stringify({
  label: 'Applicability',
  choices: [
    ['applicable', 'Applicable'],
    ['partially_applicable', 'Partially applicable'],
  ],
  default: 'applicable',
});

function renderAdminControls() {
  if (!adminControlsList || !me?.is_admin) return;
  const parent = isIntermediateParentClause();
  clauseControls.disabled = parent;
  saveClauseControls.disabled = parent;
  if (parent) {
    clauseControls.replaceChildren();
    adminControlsList.innerHTML = `
      <div class="alert alert-info mb-0">
        This clause has more specific child clauses. Link controls to the child clauses instead;
        this parent clause is inferred for reporting and visualisations.
      </div>`;
    return;
  }
  const selected = linkedMap();
  clauseControls.dataset.pickerExtra = APPLICABILITY;
  clauseControls.replaceChildren(
    ...allControls.map((c) => {
      const option = node('option', { value: c.id }, catalogueLabel(c));
      const applicability = selected.get(String(c.id));
      option.selected = Boolean(applicability);
      if (applicability) option.dataset.extra = applicability;
      return option;
    }),
  );
  adminControlsList.textContent = allControls.length ? '' : 'No controls in this framework.';
}

async function loadAdminControls() {
  if (!me?.is_admin || !clause) return;
  const data = await apiGet(
    `/api/v1/controls?framework=${encodeURIComponent(clause.framework)}&limit=5000`,
  );
  allControls = data.items.filter((c) => c.type !== 'clause');
  renderAdminControls();
}

async function reloadLinkedControls() {
  const data = await apiGet(`/api/v1/clauses/${encodeURIComponent(id)}/controls`);
  clause = data.clause;
  linkedControls = data.items;
  renderClauseHeader();
  renderControls(linkedControls);
  renderAdminControls();
  if (openTab() === 'related') await loadClausePestle({ force: true });
}

async function load() {
  if (!id) {
    toast(status, 'Missing clause id.', 'danger');
    return;
  }

  showTableLoading(rowsEl, 4, 'Loading linked controls');
  try {
    await reloadLinkedControls();
    await loadClauseRisks();
    await loadClauseChangelog();
    await loadAdminControls();
  } catch (e) {
    toast(status, `Failed to load clause: ${errorText(e)}`, 'danger');
    rowsEl.innerHTML =
      '<tr><td colspan="4" class="small-muted p-4">Failed to load linked controls.</td></tr>';
  }
}

editClauseEvidence?.addEventListener('click', () => {
  evidenceEditMode = !evidenceEditMode;
  renderEvidenceMapping();
  if (evidenceEditMode) {
    adminClauseEvidenceEditor?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
});

addClauseEvidenceUrl?.addEventListener('click', () => {
  clauseEvidenceUrlInputs.insertAdjacentHTML('beforeend', evidenceMappingRow({}));
  wireEvidenceUrlRemoveButtons();
  const inputs = clauseEvidenceUrlInputs.querySelectorAll('.clause-evidence-title');
  inputs[inputs.length - 1]?.focus();
});

saveClauseEvidenceUrls?.addEventListener('click', async () => {
  const evidenceMappings = Array.from(
    clauseEvidenceUrlInputs.querySelectorAll('.clause-evidence-mapping-row'),
  )
    .map((row) => ({
      title: String(row.querySelector('.clause-evidence-title')?.value || '').trim(),
      url: String(row.querySelector('.clause-evidence-url')?.value || '').trim(),
    }))
    .filter((item) => item.url);
  saveClauseEvidenceUrls.disabled = true;
  try {
    const data = await apiPatch(
      `/api/v1/admin/clauses/${encodeURIComponent(clause.id)}/evidence-urls`,
      {
        evidence_mappings: evidenceMappings,
      },
    );
    clause = { ...clause, evidence_mappings: data.evidence_mappings };
    evidenceEditMode = false;
    renderEvidenceMapping();
    await loadClauseChangelog();
    toast(status, 'Clause evidence mapping saved', 'success');
  } catch (e) {
    toast(status, `Failed to save clause evidence mapping: ${errorText(e)}`, 'danger');
  } finally {
    saveClauseEvidenceUrls.disabled = false;
  }
});

editLinkedControls?.addEventListener('click', async () => {
  linkedControlsEditMode = !linkedControlsEditMode;
  renderClauseHeader();
  if (linkedControlsEditMode && allControls.length === 0) {
    try {
      await loadAdminControls();
    } catch (e) {
      toast(status, `Failed to load controls: ${errorText(e)}`, 'danger');
    }
  } else if (linkedControlsEditMode) {
    renderAdminControls();
  }
  if (linkedControlsEditMode) {
    adminControlsCard?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
});

saveClauseUpstreamUrl?.addEventListener('click', async () => {
  saveClauseUpstreamUrl.disabled = true;
  try {
    const data = await apiPatch(
      `/api/v1/admin/clauses/${encodeURIComponent(clause.id)}/upstream-url`,
      {
        upstream_url: (clauseUpstreamUrl?.value || '').trim() || null,
      },
    );
    clause = { ...clause, upstream_url: data?.upstream_url || null };
    renderClauseHeader();
    await loadClauseChangelog();
    toast(status, 'Clause URL saved', 'success');
  } catch (e) {
    toast(status, `Failed to save clause URL: ${errorText(e)}`, 'danger');
  } finally {
    saveClauseUpstreamUrl.disabled = false;
  }
});

saveClauseControls?.addEventListener('click', async () => {
  const controls = Array.from(clauseControls.selectedOptions, (option) => ({
    control_id: option.value,
    applicability: option.dataset.extra || 'applicable',
  }));
  saveClauseControls.disabled = true;
  try {
    const data = await apiPatch(`/api/v1/admin/clauses/${encodeURIComponent(clause.id)}/controls`, {
      controls,
    });
    if (data?.skipped_intermediate_parent) {
      toast(status, data.detail, 'info');
    } else {
      toast(status, 'Linked controls saved', 'success');
    }
    await reloadLinkedControls();
    if (qs('tab') === 'changelog') await loadClauseChangelog();
    if (openTab() === 'related') await loadClauseIsms({ force: true });
  } catch (e) {
    toast(status, `Failed to save linked controls: ${errorText(e)}`, 'danger');
  } finally {
    saveClauseControls.disabled = false;
  }
});

load();

// The stories whose authors assigned them to this clause, among its related items.
const stories = mountAssignedStories({
  me,
  kind: 'clause',
  id,
  host: clauseStoriesHost,
  trigger: clauseItemsTab,
});
clauseStoriesCard.hidden = !stories;
