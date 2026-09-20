import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  apiPostForm,
  enableTableSorting,
  esc,
  fmtTs,
  getCurrentFramework,
  shorten,
  toast,
  withFramework,
  initCollapsibleFilterSections,
  initRichTextEditors,
  plainTextFromRichText,
  setRichTextEditorValue,
  syncRichTextEditor,
  userPillHtml,
  userPillsHtml,
  canSampleIntoAudit,
  openAuditSampleModal,
  effectivenessMetricValueHtml,
  effectivenessMetricThresholdBadgeHtml,
} from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const canManage = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me);
enableTableSorting();
const status = document.getElementById('status');
const metaEl = document.getElementById('ismsMeta');

let meta = {users: [], org_nodes: [], documents: [], assets: [], asset_categories: [], licenses: [], aws_accounts: [], business_processes: [], controls: [], clauses: [], effectiveness_metric_sources: [], effectiveness_metric_source_types: [], effectiveness_threshold_operators: []};
let data = {counts: {}, objectives: [], documents: [], org_nodes: [], assets: [], application_configurations: [], access_control_matrix: [], effectiveness_measures: [], meetings: []};

const loadedMetaSections = new Set();
const loadedSummarySections = new Set();
const sectionLoadPromises = new Map();
const sectionByKind = {objective: 'objectives', document: 'documents', org: 'org', asset: 'assets', app: 'app', access: 'access', effectiveness: 'effectiveness', meeting: 'meetings'};
const tabIdBySection = {overview: 'overviewTab', objectives: 'objectivesTab', documents: 'documentsTab', org: 'orgTab', assets: 'assetsTab', access: 'accessControlTab', effectiveness: 'effectivenessTab', app: 'appConfigTab', meetings: 'meetingsTab', soa: 'soaTab'};
const sectionAliases = {
  overview: 'overview', summary: 'overview',
  objective: 'objectives', objectives: 'objectives',
  document: 'documents', documents: 'documents', policies: 'documents', policy: 'documents', processes: 'documents',
  org: 'org', organisation: 'org', organization: 'org', orgnode: 'org', org_node: 'org', orgnodes: 'org', org_nodes: 'org',
  asset: 'assets', assets: 'assets',
  access: 'access', accesscontrol: 'access', access_control: 'access', accesscontrolmatrix: 'access', access_control_matrix: 'access',
  effectiveness: 'effectiveness', effectivenessmeasure: 'effectiveness', effectiveness_measure: 'effectiveness', effectivenessmeasures: 'effectiveness', effectiveness_measures: 'effectiveness', measures: 'effectiveness', metrics: 'effectiveness',
  app: 'app', appconfig: 'app', app_config: 'app', applicationconfiguration: 'app', application_configuration: 'app',
  meeting: 'meetings', meetings: 'meetings',
  soa: 'soa',
};

const $ = (id) => document.getElementById(id);
const rows = {
  objectives: $('objectivesRows'),
  documents: $('documentsRows'),
  org: $('orgRows'),
  assets: $('assetsRows'),
  licenses: $('licensesRows'),
  awsAccounts: $('awsAccountsRows'),
  access: $('accessMatrixRows'),
  effectiveness: $('effectivenessRows'),
  app: $('appConfigRows'),
  meetings: $('meetingsRows'),
};

const editState = {kind: null, id: null};
const endpoints = {
  objective: 'objectives',
  document: 'documents',
  org: 'org-nodes',
  asset: 'assets',
  app: 'application-configuration',
  access: 'access-control-matrix',
  effectiveness: 'effectiveness-measures',
  meeting: 'meetings',
};
const formIds = {
  objective: 'objectiveForm',
  document: 'documentForm',
  org: 'orgForm',
  asset: 'assetForm',
  app: 'appConfigForm',
  access: 'accessControlForm',
  effectiveness: 'effectivenessForm',
  meeting: 'meetingForm',
};
const submitLabels = {
  objective: ['Create objective', 'Save objective'],
  document: ['Create document record', 'Save document record'],
  org: ['Create org node', 'Save org node'],
  asset: ['Create asset', 'Save asset'],
  app: ['Create matrix row', 'Save matrix row'],
  access: ['Create access control row', 'Save access control row'],
  effectiveness: ['Create effectiveness measure', 'Save effectiveness measure'],
  meeting: ['Create meeting minutes', 'Save meeting minutes'],
};

function setCount(id, n) { const el = $(id); if (el) el.textContent = String(Number(n || 0)); }
function names(list) { return (list || []).map((x) => x?.username || x?.user?.username || x?.name || '').filter(Boolean).join(', ') || '—'; }
function userBadges(list) { return userPillsHtml(list); }
function objectiveUserPill(user) { return userPillHtml(user, {className: 'isms-objective-user-pill'}); }
function objectiveUserBadges(list) { return userPillsHtml(list, {className: 'isms-objective-user-pill'}); }
function badgeList(items, labelKey = 'ref', urlFn = null) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">—</span>';
  return arr.map((it) => {
    const label = String(it?.[labelKey] || it?.name || it?.title || 'Item');
    const title = String(it?.title || it?.name || label);
    const href = urlFn ? urlFn(it) : '';
    if (href) return `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(href)}" title="${esc(title)}">${esc(label)}</a>`;
    return `<span class="badge badge-soft me-1 mb-1" title="${esc(title)}">${esc(label)}</span>`;
  }).join('');
}
function controlBadges(items) { return badgeList(items, 'ref', (c) => withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework)); }
function clauseBadges(items) { return badgeList(items, 'ref', (c) => withFramework(`/clause.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework)); }
function docLink(d) {
  if (d.external_url) return `<a href="${esc(d.external_url)}" target="_blank" rel="noopener noreferrer">External URL</a>`;
  if (d.has_file) return `<a href="/api/v1/isms/documents/${encodeURIComponent(d.id)}/file" target="_blank" rel="noopener noreferrer">${esc(d.filename || 'Uploaded file')}</a>`;
  return '<span class="small-muted">—</span>';
}
function optionList(items, {value = 'id', label = 'username', includeBlank = false, blankLabel = '—'} = {}) {
  const opts = [];
  if (includeBlank) opts.push(`<option value="">${esc(blankLabel)}</option>`);
  for (const it of items || []) {
    const text = typeof label === 'function' ? label(it) : (it?.[label] || it?.name || it?.title || it?.asset || it?.username || 'Item');
    opts.push(`<option value="${esc(it?.[value] || '')}">${esc(text)}</option>`);
  }
  return opts.join('');
}
function selectedValues(select) { return Array.from(select?.selectedOptions || []).map((o) => o.value).filter(Boolean); }


function initMeetingNotesEditor() {
  initRichTextEditors(document.getElementById('meetingForm') || document);
}

function setMeetingNotesValue(value) {
  initMeetingNotesEditor();
  setRichTextEditorValue('meetingNotes', value);
}

function getMeetingNotesValue() {
  initMeetingNotesEditor();
  return syncRichTextEditor($('meetingNotes'));
}


const formCardIds = {
  objective: 'objectiveFormCard',
  document: 'documentFormCard',
  org: 'orgFormCard',
  asset: 'assetFormCard',
  app: 'appConfigFormCard',
  access: 'accessControlFormCard',
  effectiveness: 'effectivenessFormCard',
  meeting: 'meetingFormCard',
};
const ismsTableSections = [
  {key: 'objectives', kind: 'objective', rowsId: 'objectivesRows', countId: 'objectivesCount', formCardId: 'objectiveFormCard', title: 'Objective filters', placeholder: 'Filter objectives by goal, metric, owner, controls or clauses…', addLabel: 'Add new objective'},
  {key: 'documents', kind: 'document', rowsId: 'documentsRows', countId: 'documentsCount', formCardId: 'documentFormCard', title: 'Policies & Processes filters', placeholder: 'Filter documents by title, type, description, controls or clauses…', addLabel: 'Add new policy/process'},
  {key: 'org', kind: 'org', rowsId: 'orgRows', countId: 'orgCount', formCardId: 'orgFormCard', title: 'Organisation Chart filters', placeholder: 'Filter organisation chart rows by name, parent, people, controls or clauses…', addLabel: 'Add new org node'},
  {key: 'assets', kind: 'asset', rowsId: 'assetsRows', countId: 'assetsCount', formCardId: 'assetFormCard', title: 'Asset Matrix filters', placeholder: 'Filter assets by name, category, owner, license, controls or clauses…', addLabel: 'Add new asset'},
  {key: 'access', kind: 'access', rowsId: 'accessMatrixRows', countId: 'accessControlCount', formCardId: 'accessControlFormCard', title: 'Access Control Matrix filters', placeholder: 'Filter access rows by task/action, service, account, role, approver or status…', addLabel: 'Add new access row'},
  {key: 'effectiveness', kind: 'effectiveness', rowsId: 'effectivenessRows', countId: 'effectivenessCount', formCardId: 'effectivenessFormCard', title: 'Effectiveness Measures filters', placeholder: 'Filter effectiveness measures by summary, description, metric, linked controls, owner, source or latest value…', addLabel: 'Add new effectiveness measure'},
  {key: 'app', kind: 'app', rowsId: 'appConfigRows', countId: 'appConfigCount', formCardId: 'appConfigFormCard', title: 'Application Configuration filters', placeholder: 'Filter application configuration by group, linkage or business process…', addLabel: 'Add new matrix row'},
  {key: 'meetings', kind: 'meeting', rowsId: 'meetingsRows', countId: 'meetingsCount', formCardId: 'meetingFormCard', title: 'Minutes of Meetings filters', placeholder: 'Filter meetings by date, title, attendees, links, controls or clauses…', addLabel: 'Add new meeting'},
];

function formCardIdForKind(kind) {
  return formCardIds[kind] || (kind === 'app' ? 'appConfigFormCard' : `${kind}FormCard`);
}

function formCardForKind(kind) {
  return $(formCardIdForKind(kind));
}

function setFormCardVisible(kind, visible) {
  const card = formCardForKind(kind);
  if (!card) return;
  card.dataset.ismsFormOpen = visible ? '1' : '0';
  card.style.display = visible ? '' : 'none';
}

function hideAllIsmsForms(exceptKind = '') {
  for (const kind of Object.keys(formIds)) {
    if (kind === exceptKind) continue;
    setFormCardVisible(kind, false);
  }
  const assetCategoryCard = $('assetCategoryCard');
  if (assetCategoryCard && exceptKind !== 'assetCategory') assetCategoryCard.style.display = 'none';
  const licenseCard = $('licenseCard');
  if (licenseCard && exceptKind !== 'license') licenseCard.style.display = 'none';
  const awsAccountCard = $('awsAccountCard');
  if (awsAccountCard && exceptKind !== 'awsAccount') awsAccountCard.style.display = 'none';
  const metricEntryCard = $('metricEntryFormCard');
  if (metricEntryCard && exceptKind !== 'metricEntry') metricEntryCard.style.display = 'none';
}

function tableCardForSection(cfg) {
  return $(cfg.countId)?.closest('.card') || $(cfg.rowsId)?.closest('.card') || null;
}

function addHeaderButton(header, {html, dataset}) {
  if (!header) return null;
  let actionWrap = header.querySelector('[data-isms-section-actions]');
  if (!actionWrap) {
    actionWrap = document.createElement('div');
    actionWrap.className = 'd-flex flex-wrap gap-2 align-items-center justify-content-end no-print';
    actionWrap.setAttribute('data-isms-section-actions', '1');
    const count = Array.from(header.querySelectorAll('.badge[id]')).find((el) => /Count$/.test(el.id));
    if (count) {
      count.replaceWith(actionWrap);
      actionWrap.appendChild(count);
    } else {
      header.appendChild(actionWrap);
    }
  }
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-sm btn-primary';
  button.innerHTML = html;
  for (const [k, v] of Object.entries(dataset || {})) button.setAttribute(k, v);
  actionWrap.appendChild(button);
  return button;
}

function installIsmsSectionChrome() {
  for (const cfg of ismsTableSections) {
    const card = tableCardForSection(cfg);
    if (!card || card.dataset.ismsChromeInstalled === '1') continue;
    card.dataset.ismsChromeInstalled = '1';
    const header = card.querySelector(':scope > .card-header');
    if (canManage) {
      addHeaderButton(header, {
        html: `<i class="bi bi-plus-lg me-1" aria-hidden="true"></i>${esc(cfg.addLabel)}`,
        dataset: {'data-isms-add': cfg.kind},
      });
      if (cfg.kind === 'asset') {
        addHeaderButton(header, {html: 'Manage categories', dataset: {'data-isms-toggle-card': 'assetCategoryCard'}});
        addHeaderButton(header, {html: 'Manage licenses', dataset: {'data-isms-toggle-card': 'licenseCard'}});
      }
      if (cfg.kind === 'access') {
        addHeaderButton(header, {html: 'Manage hosting accounts', dataset: {'data-isms-toggle-card': 'awsAccountCard'}});
      }
    }
    const filterId = `${cfg.key}TableFilter`;
    if (!document.getElementById(filterId)) {
      const filter = document.createElement('div');
      filter.className = 'card mb-3 no-print';
      filter.setAttribute('data-mosp-filter-section', `${cfg.key}-isms-filters`);
      filter.setAttribute('data-mosp-filter-title', cfg.title);
      filter.innerHTML = `<div class="card-body" data-mosp-filter-body>
        <label class="form-label small-muted mb-1" for="${esc(filterId)}">Filter table</label>
        <div class="input-group input-group-sm">
          <input id="${esc(filterId)}" class="form-control" type="search" data-isms-table-filter="${esc(cfg.rowsId)}" placeholder="${esc(cfg.placeholder)}">
          <button class="btn btn-outline-secondary" type="button" data-isms-filter-clear="${esc(filterId)}">Reset</button>
        </div>
      </div>`;
      card.insertAdjacentElement('beforebegin', filter);
    }
  }
  try { initCollapsibleFilterSections(); } catch {}
}

function applyIsmsTableFilter(input) {
  const rowsId = input?.getAttribute?.('data-isms-table-filter') || '';
  const tbody = rowsId ? $(rowsId) : null;
  if (!tbody) return;
  const q = String(input.value || '').trim().toLowerCase();
  for (const tr of Array.from(tbody.querySelectorAll('tr'))) {
    if (tr.querySelector('td[colspan]')) {
      tr.style.display = '';
      continue;
    }
    const text = String(tr.getAttribute('data-isms-filter-text') || tr.textContent || '').toLowerCase();
    tr.style.display = !q || text.includes(q) ? '' : 'none';
  }
}

function showNewIsmsForm(kind) {
  if (!canManage) return;
  hideAllIsmsForms(kind);
  resetForm(kind);
  setFormMode(kind, null);
  setFormCardVisible(kind, true);
  const form = $(formIds[kind]);
  form?.scrollIntoView({behavior: 'smooth', block: 'start'});
  const first = form?.querySelector('input:not([type="hidden"]), textarea, select');
  setTimeout(() => { try { first?.focus?.(); } catch {} }, 80);
}

function toggleAuxCard(id) {
  const card = $(id);
  if (!card || !canManage) return;
  const nextVisible = card.style.display === 'none' || card.hidden;
  if (nextVisible) hideAllIsmsForms(id === 'assetCategoryCard' ? 'assetCategory' : id === 'licenseCard' ? 'license' : id === 'awsAccountCard' ? 'awsAccount' : '');
  card.hidden = false;
  card.style.display = nextVisible ? '' : 'none';
  if (nextVisible) card.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function assetCategories() { return meta.asset_categories || []; }
function currentAssetCategory() {
  const id = $('assetCategory')?.value || '';
  return assetCategories().find((c) => String(c.id) === String(id)) || assetCategories()[0] || null;
}
function renderAssetSubcategoryOptions(targetValue = '') {
  const select = $('assetSubcategory');
  if (!select) return;
  const category = currentAssetCategory();
  const subs = category?.subcategories || [];
  const cur = targetValue || select.value || subs[0]?.id || '';
  select.innerHTML = subs.map((s) => `<option value="${esc(s.id)}">${esc(s.name || 'Subcategory')}</option>`).join('') || '<option value="">Add a subcategory first</option>';
  select.value = subs.some((s) => String(s.id) === String(cur)) ? cur : (subs[0]?.id || '');
}
function renderAssetCategoryList() {
  const holder = $('assetCategoryList');
  if (!holder) return;
  const cats = assetCategories();
  if (!cats.length) { holder.innerHTML = '<span class="small-muted">No asset categories yet.</span>'; return; }
  holder.innerHTML = `<div class="vstack gap-2">${cats.map((c) => `
    <div class="border rounded-3 p-2">
      <div class="fw-semibold">${esc(c.name || 'Category')}</div>
      <div class="d-flex flex-wrap gap-1 mt-1">${(c.subcategories || []).map((s) => `<span class="badge badge-soft">${esc(s.name || 'Subcategory')}</span>`).join('') || '<span class="small-muted">No subcategories</span>'}</div>
    </div>`).join('')}</div>`;
}
function populateAssetCategoryControls(targetCategoryId = '', targetSubcategoryId = '') {
  const cats = assetCategories();
  const opts = cats.map((c) => `<option value="${esc(c.id)}">${esc(c.name || 'Category')}</option>`).join('');
  for (const id of ['assetCategory', 'assetSubcategoryCategory']) {
    const el = $(id);
    if (!el) continue;
    const cur = targetCategoryId || el.value || cats[0]?.id || '';
    el.innerHTML = opts || '<option value="">Add a category first</option>';
    el.value = cats.some((c) => String(c.id) === String(cur)) ? cur : (cats[0]?.id || '');
    el.disabled = !cats.length;
  }
  renderAssetSubcategoryOptions(targetSubcategoryId);
  renderAssetCategoryList();
}

function licenses() { return meta.licenses || []; }
function licenseLabel(value) {
  const text = String(value || '').trim();
  return text || '—';
}
function renderLicenseOptions(targetValue = '') {
  const select = $('assetLicense');
  if (!select) return;
  const opts = optionList(licenses(), {label: 'name', includeBlank: true, blankLabel: '—'});
  const cur = targetValue || select.value || '';
  select.innerHTML = opts;
  select.value = licenses().some((l) => String(l.id) === String(cur)) ? cur : '';
}
function renderLicenses() {
  if (!rows.licenses) return;
  const items = licenses();
  rows.licenses.innerHTML = items.map((l) => `<tr>
    <td class="fw-semibold">${esc(l.name || 'License')}</td>
    <td class="wrap small-muted">${esc(shorten(l.description || '', 180) || '—')}</td>
    <td>${Number(l.asset_count || 0)}</td>
    <td class="text-nowrap no-print"><div class="btn-group btn-group-sm" role="group"><button class="btn btn-outline-primary" type="button" data-license-edit="${esc(l.id)}"><i class="bi bi-pencil" aria-hidden="true"></i><span class="visually-hidden">Edit license</span></button><button class="btn btn-outline-danger" type="button" data-license-delete="${esc(l.id)}"><i class="bi bi-trash" aria-hidden="true"></i><span class="visually-hidden">Delete license</span></button></div></td>
  </tr>`).join('') || emptyRow(4, 'No licenses yet.');
}
function resetLicenseForm(item = null) {
  const form = $('licenseForm');
  if (!form) return;
  $('licenseId').value = item?.id || '';
  $('licenseName').value = item?.name || '';
  $('licenseDescription').value = item?.description || '';
  const button = $('saveLicense');
  if (button) button.textContent = item ? 'Save license' : 'Add license';
}

function awsAccounts() { return meta.aws_accounts || []; }
function awsAccountLabel(account) {
  if (!account) return 'hosting account';
  return `${account.name || 'hosting account'}${account.account_id ? ` (${account.account_id})` : ''}`;
}
function renderAwsAccountOptions(targetValues = []) {
  const select = $('accessAwsAccounts');
  if (!select) return;
  select.innerHTML = optionList(awsAccounts(), {label: awsAccountLabel});
  if (Array.isArray(targetValues) && targetValues.length) setSelectValues(select, targetValues);
}
function renderAwsAccounts() {
  if (!rows.awsAccounts) return;
  const items = awsAccounts();
  rows.awsAccounts.innerHTML = items.map((a) => `<tr>
    <td class="fw-semibold">${esc(a.name || 'hosting account')}</td>
    <td>${esc(a.account_id || '—')}</td>
    <td class="wrap small-muted">${esc(shorten(a.notes || '', 180) || '—')}</td>
    <td>${Number(a.entry_count || 0)}</td>
    <td class="text-nowrap no-print"><div class="btn-group btn-group-sm" role="group"><button class="btn btn-outline-primary" type="button" data-aws-account-edit="${esc(a.id)}"><i class="bi bi-pencil" aria-hidden="true"></i><span class="visually-hidden">Edit hosting account</span></button><button class="btn btn-outline-danger" type="button" data-aws-account-delete="${esc(a.id)}"><i class="bi bi-trash" aria-hidden="true"></i><span class="visually-hidden">Delete hosting account</span></button></div></td>
  </tr>`).join('') || emptyRow(5, 'No managed hosting accounts yet. Add at least one before creating access control matrix rows.');
}
function resetAwsAccountForm(item = null) {
  const form = $('awsAccountForm');
  if (!form) return;
  $('awsAccountId').value = item?.id || '';
  $('awsAccountName').value = item?.name || '';
  $('awsAccountNumber').value = item?.account_id || '';
  $('awsAccountNotes').value = item?.notes || '';
  const button = $('saveAwsAccount');
  if (button) button.textContent = item ? 'Save hosting account' : 'Add hosting account';
}

function setSelectValues(select, values = []) {
  if (!select) return;
  const wanted = new Set((values || []).map((v) => String(v)));
  for (const opt of Array.from(select.options || [])) opt.selected = wanted.has(String(opt.value));
}
function setSelectValue(select, value = '') {
  if (!select) return;
  const text = String(value || '');
  if (text && !Array.from(select.options || []).some((opt) => opt.value === text)) {
    select.insertAdjacentHTML('beforeend', `<option value="${esc(text)}">${esc(text)}</option>`);
  }
  select.value = text;
}
function clearFileInput(input) { if (input) input.value = ''; }
function timeForInput(value) { return String(value || '').slice(0, 5); }
function itemControls(item) { return (item?.controls || []).map((x) => x.id).filter(Boolean); }
function itemClauses(item) { return (item?.clauses || []).map((x) => x.id).filter(Boolean); }
function actionsHtml(kind, id, extraClass = '') {
  const cls = ['text-nowrap', 'no-print', extraClass].filter(Boolean).join(' ');
  if (!id) return `<td class="${esc(cls)}"></td>`;
  const sample = canSampleAudits ? `<button class="btn btn-outline-success" type="button" data-audit-sample-entity="${esc(kind)}" data-isms-id="${esc(id)}" title="Sample into audit"><i class="bi bi-clipboard2-plus" aria-hidden="true"></i><span class="visually-hidden">Sample into audit</span></button>` : '';
  const editDelete = canManage ? `<button class="btn btn-outline-primary" type="button" data-isms-edit="${esc(kind)}" data-isms-id="${esc(id)}"><i class="bi bi-pencil" aria-hidden="true"></i><span class="visually-hidden">Edit</span></button><button class="btn btn-outline-danger" type="button" data-isms-delete="${esc(kind)}" data-isms-id="${esc(id)}"><i class="bi bi-trash" aria-hidden="true"></i><span class="visually-hidden">Delete</span></button>` : '';
  if (!sample && !editDelete) return `<td class="${esc(cls)}"></td>`;
  return `<td class="${esc(cls)}"><div class="btn-group btn-group-sm" role="group">${sample}${editDelete}</div></td>`;
}
function emptyRow(colspan, message) { return `<tr><td colspan="${colspan}" class="p-4 small-muted">${esc(message)}</td></tr>`; }
function findItem(kind, id) {
  const key = {objective: 'objectives', document: 'documents', org: 'org_nodes', asset: 'assets', app: 'application_configurations', access: 'access_control_matrix', effectiveness: 'effectiveness_measures', meeting: 'meetings'}[kind];
  return (data?.[key] || []).find((x) => String(x.id) === String(id)) || null;
}

function findMetricEntry(id) {
  for (const measure of data.effectiveness_measures || []) {
    const entry = (measure.metric_entries || []).find((x) => String(x.id) === String(id));
    if (entry) return {...entry, measure};
  }
  return null;
}

const auditSampleEntityTypes = {
  objective: 'isms_objective',
  document: 'isms_document',
  org: 'isms_org_node',
  asset: 'isms_asset',
  access: 'isms_access_control_matrix',
  app: 'isms_application_configuration',
  effectiveness: 'isms_effectiveness_measure',
  metric: 'isms_effectiveness_metric',
  meeting: 'isms_meeting',
};

function objectiveHref(item) {
  return withFramework(`/isms-objective.html?id=${encodeURIComponent(item?.id || '')}`, framework);
}

function documentHref(item) {
  return withFramework(`/isms-document.html?id=${encodeURIComponent(item?.id || '')}`, framework);
}

function assetHref(item) {
  return withFramework(`/isms-asset.html?id=${encodeURIComponent(item?.id || '')}`, framework);
}

function accessControlHref(item) {
  return withFramework(`/isms-access.html?id=${encodeURIComponent(item?.id || '')}`, framework);
}

function meetingHref(item) {
  return withFramework(`/isms-meeting.html?id=${encodeURIComponent(item?.id || '')}`, framework);
}

function ismsEntityEvidenceUrl(kind, id, item = null) {
  const row = item || {id};
  if (kind === 'objective') return objectiveHref(row);
  if (kind === 'document') return documentHref(row);
  if (kind === 'asset') return assetHref(row);
  if (kind === 'access') return accessControlHref(row);
  if (kind === 'effectiveness') return effectivenessMeasureHref(row);
  if (kind === 'metric') return effectivenessMetricHref(row);
  if (kind === 'meeting') return meetingHref(row);
  return null;
}

function ismsSampleTitle(kind, item) {
  if (!item) return 'ISMS item';
  if (kind === 'objective') return item.goal || item.requirement || 'ISMS objective';
  if (kind === 'document') return item.title || 'ISMS document';
  if (kind === 'org') return item.name || 'Organisation chart node';
  if (kind === 'asset') return item.asset || item.name || 'ISMS asset';
  if (kind === 'access') return item.task_action || 'Access control matrix row';
  if (kind === 'effectiveness') return item.summary || item.metric || item.effectiveness_measure || 'Effectiveness measure';
  if (kind === 'metric') return item.value_display || item.measure?.metric || 'Effectiveness metric';
  if (kind === 'app') {
    const source = item.source?.label || item.source?.title || item.source?.name || item.source?.username || item.display || item.source_type || 'Application configuration';
    const bp = item.business_process?.name || '';
    return [source, bp].filter(Boolean).join(' — ') || 'Application configuration row';
  }
  if (kind === 'meeting') return item.title || 'ISMS meeting';
  return item.display || 'ISMS item';
}

function sampleIsmsEntity(kind, id) {
  const entityType = auditSampleEntityTypes[kind];
  const item = kind === 'metric' ? findMetricEntry(id) : findItem(kind, id);
  if (!entityType) return;
  openAuditSampleModal({
    me,
    entityType,
    entityId: id,
    title: ismsSampleTitle(kind, item),
    evidenceUrl: ismsEntityEvidenceUrl(kind, id, item),
    framework,
    statusEl: status,
  });
}
function ensureCancelButton(kind) {
  const form = $(formIds[kind]);
  if (!form || form.querySelector('[data-isms-cancel-edit]')) return;
  const submit = form.querySelector('button[type="submit"]');
  if (!submit) return;
  submit.insertAdjacentHTML('afterend', ` <button class="btn btn-outline-secondary ms-2" type="button" style="display:none;" data-isms-cancel-edit="${esc(kind)}">Cancel edit</button>`);
}
function setFormMode(kind, item = null) {
  ensureCancelButton(kind);
  if (item) {
    for (const [otherKind, formId] of Object.entries(formIds)) {
      if (otherKind === kind) continue;
      const otherForm = $(formId);
      if (!otherForm) continue;
      otherForm.dataset.editingId = '';
      const otherSubmit = otherForm.querySelector('button[type="submit"]');
      if (otherSubmit) otherSubmit.textContent = submitLabels[otherKind][0];
      const otherCancel = otherForm.querySelector('[data-isms-cancel-edit]');
      if (otherCancel) otherCancel.style.display = 'none';
    }
  }
  editState.kind = item ? kind : null;
  editState.id = item?.id || null;
  const form = $(formIds[kind]);
  if (!form) return;
  const submit = form.querySelector('button[type="submit"]');
  if (submit) submit.textContent = item ? submitLabels[kind][1] : submitLabels[kind][0];
  const cancel = form.querySelector('[data-isms-cancel-edit]');
  if (cancel) cancel.style.display = item ? '' : 'none';
  form.dataset.editingId = item?.id || '';
}
function resetForm(kind) {
  const form = $(formIds[kind]);
  if (!form) return;
  form.reset();
  setFormMode(kind, null);
  if (kind === 'document') clearFileInput($('docFile'));
  if (kind === 'app') populateAppSourceOptions();
  if (kind === 'access') renderAccessFormOptions();
  if (kind === 'effectiveness') renderEffectivenessFormOptions();
  if (kind === 'asset') { populateAssetCategoryControls(); renderLicenseOptions(); }
  if (kind === 'meeting') { resetMeetingExternalLinks(); setMeetingNotesValue(''); }
  setFormCardVisible(kind, false);
}
function addMeetingExternalLinkRow(title = '', url = '') {
  const holder = $('meetingExternalLinks');
  if (!holder) return;
  const row = document.createElement('div');
  row.className = 'meeting-external-link-row row g-2 align-items-start';
  row.innerHTML = `
    <div class="col-12 col-lg-5"><input class="form-control form-control-sm" data-meeting-link-title="1" placeholder="Link title" value="${esc(title || '')}"></div>
    <div class="col-12 col-lg-6"><input class="form-control form-control-sm" data-meeting-link-url="1" type="url" placeholder="https://…" value="${esc(url || '')}"></div>
    <div class="col-12 col-lg-1 text-lg-end"><button class="btn btn-sm btn-outline-danger" type="button" data-remove-meeting-link="1" title="Remove link"><i class="bi bi-x-lg" aria-hidden="true"></i><span class="visually-hidden">Remove link</span></button></div>`;
  holder.appendChild(row);
}
function resetMeetingExternalLinks(links = null) {
  const holder = $('meetingExternalLinks');
  if (!holder) return;
  holder.innerHTML = '';
  const external = Array.isArray(links) ? links.filter((l) => l.link_type === 'external_url' && l.url) : [];
  if (external.length) {
    for (const link of external) addMeetingExternalLinkRow(link.title || '', link.url || '');
  } else {
    addMeetingExternalLinkRow();
  }
}
function selectedMeetingLinks() {
  const meetingLinks = selectedValues($('meetingDocuments')).map((id) => ({link_type: 'isms_document', document_id: id}));
  for (const row of Array.from(document.querySelectorAll('.meeting-external-link-row'))) {
    const title = String(row.querySelector('[data-meeting-link-title]')?.value || '').trim();
    const url = String(row.querySelector('[data-meeting-link-url]')?.value || '').trim();
    if (!title && !url) continue;
    if (!url) throw new Error('External meeting links need a URL.');
    meetingLinks.push({link_type: 'external_url', title: title || null, url});
  }
  return meetingLinks;
}
function appPayloadFromForm() {
  const sourceType = $('appSourceType').value || 'document';
  const sourceId = $('appSourceId').value || null;
  const payload = {
    source_type: sourceType,
    business_process_id: $('appBusinessProcess').value || null,
    value: $('appValue').value || 'Low',
    notes: $('appNotes').value,
    document_id: null,
    user_id: null,
    asset_id: null,
    org_node_id: null,
  };
  if (sourceType === 'person') payload.user_id = sourceId;
  else if (sourceType === 'asset') payload.asset_id = sourceId;
  else if (sourceType === 'org_node') payload.org_node_id = sourceId;
  else payload.document_id = sourceId;
  return payload;
}

function accessPayloadFromForm() {
  return {
    task_action: $('accessTaskAction').value,
    service_asset_id: $('accessService').value || null,
    aws_account_ids: selectedValues($('accessAwsAccounts')),
    status: $('accessStatus').value || 'Pending Approval',
    approved_by_user_id: $('accessApprovedBy').value || null,
    role_org_node_ids: selectedValues($('accessRole')),
    notes: $('accessNotes').value,
  };
}
function populateEditForm(kind, item) {
  if (!item) return;
  hideAllIsmsForms(kind);
  setFormCardVisible(kind, true);
  setFormMode(kind, item);
  if (kind === 'objective') {
    $('objRequirement').value = item.requirement || '';
    $('objGoal').value = item.goal || '';
    $('objMetric').value = item.metric || '';
    $('objOwner').value = item.owner_user_id || '';
    setSelectValue($('objTarget'), item.completion_target_date || '');
    $('objCompletion').value = item.completion_method || '';
    $('objEvaluation').value = item.evaluation_method || '';
    $('objResourceText').value = item.resource_requirements_text || '';
    setSelectValues($('objResources'), item.resource_user_ids || []);
    setSelectValues($('objControls'), itemControls(item));
    setSelectValues($('objClauses'), itemClauses(item));
  } else if (kind === 'document') {
    $('docTitle').value = item.title || '';
    $('docType').value = item.document_type || 'policy';
    $('docUrl').value = item.external_url || '';
    $('docDescription').value = item.description || '';
    clearFileInput($('docFile'));
    setSelectValues($('docControls'), itemControls(item));
    setSelectValues($('docClauses'), itemClauses(item));
  } else if (kind === 'org') {
    $('orgParent').value = item.parent_id || '';
    $('orgName').value = item.name || '';
    $('orgType').value = item.node_type || 'role';
    $('orgDescription').value = item.description || '';
    setSelectValues($('orgUsers'), (item.people || []).map((x) => x?.user?.id).filter(Boolean));
    setSelectValues($('orgControls'), itemControls(item));
    setSelectValues($('orgClauses'), itemClauses(item));
  } else if (kind === 'asset') {
    $('assetName').value = item.asset || item.name || '';
    populateAssetCategoryControls(item.category_id || item.category?.id || '', item.subcategory_id || item.subcategory?.id || '');
    renderLicenseOptions(item.license_id || '');
    $('assetOwner').value = item.owner_org_node_id || '';
    $('assetRegister').value = item.register_held_by_org_node_id || '';
    $('assetDescription').value = item.description || '';
    setSelectValues($('assetControls'), itemControls(item));
    setSelectValues($('assetClauses'), itemClauses(item));
  } else if (kind === 'app') {
    $('appSourceType').value = item.source_type || 'document';
    populateAppSourceOptions();
    $('appSourceId').value = item.document_id || item.user_id || item.asset_id || item.org_node_id || '';
    $('appBusinessProcess').value = item.business_process_id || '';
    $('appValue').value = item.value || 'Low';
    $('appNotes').value = item.notes || '';
  } else if (kind === 'access') {
    renderAccessFormOptions(item.aws_account_ids || []);
    $('accessTaskAction').value = item.task_action || '';
    $('accessService').value = item.service_asset_id || item.service?.id || '';
    setSelectValues($('accessAwsAccounts'), item.aws_account_ids || []);
    $('accessStatus').value = item.status || 'Pending Approval';
    $('accessApprovedBy').value = item.approved_by_user_id || '';
    setSelectValues($('accessRole'), item.role_org_node_ids || (item.role_org_node_id ? [item.role_org_node_id] : []));
    $('accessNotes').value = item.notes || '';
  } else if (kind === 'effectiveness') {
    $('effSummary').value = item.summary || '';
    $('effDescription').value = item.description || '';
    $('effMeasure').value = item.effectiveness_measure || '';
    $('effMetric').value = item.metric || '';
    $('effMetricKey').value = item.metric_key || '';
    $('effThresholdOperator').value = item.threshold_operator || '';
    $('effTargetValue').value = item.target_value ?? '';
    $('effTargetUnit').value = item.target_unit || '';
    $('effOwner').value = item.owner_user_id || '';
    $('effFrequency').value = item.frequency || '';
    $('effNotes').value = item.notes || '';
    setSelectValues($('effControls'), itemControls(item));
  } else if (kind === 'meeting') {
    $('meetingTitle').value = item.title || 'ISMS Meeting';
    $('meetingDate').value = item.date || '';
    $('meetingStart').value = timeForInput(item.start_time);
    $('meetingEnd').value = timeForInput(item.end_time);
    setSelectValues($('meetingAttendees'), item.attendee_user_ids || []);
    setSelectValues($('meetingApologies'), item.apology_user_ids || []);
    setSelectValues($('meetingDocuments'), (item.links || []).filter((l) => l.link_type === 'isms_document' && l.document_id).map((l) => l.document_id));
    resetMeetingExternalLinks(item.links || []);
    setMeetingNotesValue(item.agenda_minutes_notes || '');
    setSelectValues($('meetingControls'), itemControls(item));
    setSelectValues($('meetingClauses'), itemClauses(item));
  }
  const card = $(kind === 'app' ? 'appConfigFormCard' : kind === 'access' ? 'accessControlFormCard' : kind === 'effectiveness' ? 'effectivenessFormCard' : `${kind}FormCard`);
  if (card) card.style.display = '';
  formIds[kind] && $(formIds[kind])?.scrollIntoView({behavior: 'smooth', block: 'start'});
}
async function saveIsms(kind, createPayload, editingId = null) {
  const ep = endpoints[kind];
  if (!ep) throw new Error(`Unknown ISMS entity type: ${kind}`);
  const base = `/api/v1/isms/${ep}`;
  if (editingId) return apiPatch(`${base}/${encodeURIComponent(editingId)}?framework=${encodeURIComponent(framework)}`, createPayload);
  return apiPost(`${base}?framework=${encodeURIComponent(framework)}`, createPayload);
}
async function deleteIsms(kind, id) {
  const ep = endpoints[kind];
  if (!ep || !id) return;
  const item = findItem(kind, id);
  const label = item?.display || item?.title || item?.name || item?.asset || item?.goal || 'this ISMS item';
  if (!confirm(`Delete ${label}? This will remove the ISMS record and its ISMS links.`)) return;
  await apiDelete(`/api/v1/isms/${ep}/${encodeURIComponent(id)}?framework=${encodeURIComponent(framework)}`);
  if (editState.id === id && editState.kind === kind) resetForm(kind);
  await reloadAfter('ISMS item deleted');
}

function renderOverview() {
  const root = $('overviewCards');
  if (!root) return;

  const link = (href) => esc(withFramework(href, framework));
  const button = ({label, href, x, y, w, h, lines = null}) => {
    const textLines = Array.isArray(lines) && lines.length ? lines : [label];
    const centreX = x + (w / 2);
    const firstY = y + (h / 2) - ((textLines.length - 1) * 7) + 4;
    const tspans = textLines.map((line, idx) => `<tspan x="${centreX}" ${idx ? 'dy="14"' : `y="${firstY}"`}>${esc(line)}</tspan>`).join('');
    return `<a class="isms-flow-button" href="${link(href)}" aria-label="Open ${esc(label)}">
      <rect class="isms-flow-button-bg" x="${x}" y="${y}" width="${w}" height="${h}" rx="8"></rect>
      <text class="isms-flow-button-label" text-anchor="middle">${tspans}</text>
    </a>`;
  };

  root.className = 'row g-3';
  root.innerHTML = `<div class="col-12">
    <div class="card isms-flow-card">
      <div class="card-header d-flex flex-wrap gap-2 align-items-center justify-content-between">
        <div>
          <div class="fw-bold">ISMS navigation flow</div>
        </div>
      </div>
      <div class="card-body">
        <div class="isms-flow-wrap">
          <svg class="isms-flow-svg" viewBox="0 0 1120 560" role="img" aria-labelledby="ismsFlowTitle ismsFlowDesc" preserveAspectRatio="xMidYMid meet">
            <title id="ismsFlowTitle">KEEN ISMS circular navigation flow</title>
            <desc id="ismsFlowDesc">Clickable ISMS flow from the ISMS management section to the compliance framework, sources, evidence and diary entries, audits, and back to ISMS management.</desc>
            <defs>
              <marker id="ismsFlowArrow" viewBox="0 0 12 12" refX="10" refY="6" markerWidth="10" markerHeight="10" orient="auto-start-reverse">
                <path class="isms-flow-arrow-head" d="M 1 1 L 11 6 L 1 11 z"></path>
              </marker>
              <marker id="ismsFlowHintArrow" viewBox="0 0 12 12" refX="10" refY="6" markerWidth="10" markerHeight="10" orient="auto-start-reverse">
                <path class="isms-flow-hint-arrow-head" d="M 1 1 L 11 6 L 1 11 z"></path>
              </marker>
            </defs>

            <path class="isms-flow-connector" marker-end="url(#ismsFlowArrow)" d="M 355 166 H 314"></path>
            <path class="isms-flow-connector" marker-end="url(#ismsFlowArrow)" d="M 314 286 H 370 V 336 H 427"></path>
            <path class="isms-flow-connector" marker-end="url(#ismsFlowArrow)" d="M 583 336 H 700"></path>
            <path class="isms-flow-connector" marker-end="url(#ismsFlowArrow)" d="M 805 285 V 189"></path>
            <path class="isms-flow-connector" marker-end="url(#ismsFlowArrow)" d="M 827 156 H 781"></path>
            <path class="isms-flow-pulse" d="M 355 166 H 205 V 286 H 370 V 336 H 805 V 156 H 781"></path>

            <g class="isms-flow-section isms-flow-main">
              <rect class="isms-flow-section-bg" x="355" y="48" width="426" height="230" rx="18"></rect>
              <text class="isms-flow-section-title" x="568" y="88" text-anchor="middle">Strategy and Planning</text>
              ${button({label: 'Objectives', href: '/isms.html?tab=objectives', x: 384, y: 100, w: 110, h: 34})}
              ${button({label: 'CIA Triad Risks', href: '/risks.html', x: 506, y: 100, w: 138, h: 34})}
              ${button({label: 'PESTLE(E)', href: '/pestle.html', x: 656, y: 100, w: 96, h: 34})}
              ${button({label: 'Org Chart', href: '/isms.html?tab=org', x: 384, y: 146, w: 110, h: 34})}
              ${button({label: 'Interested Parties', href: '/interested_parties.html', x: 506, y: 146, w: 138, h: 34})}
              ${button({label: 'Asset Matrix', href: '/isms.html?tab=assets', x: 656, y: 146, w: 96, h: 34})}
              ${button({label: 'Policies & Processes', href: '/isms.html?tab=documents', x: 384, y: 196, w: 174, h: 34})}
              ${button({label: 'Access Control Matrix', href: '/isms.html?tab=access', x: 570, y: 196, w: 182, h: 34, lines: ['Access Control', 'Matrix']})}
              ${button({label: 'Effectiveness Measures', href: '/isms.html?tab=effectiveness', x: 456, y: 238, w: 224, h: 34, lines: ['Effectiveness', 'Measures']})}
            </g>

            <g class="isms-flow-section isms-flow-compliance">
              <rect class="isms-flow-section-bg" x="96" y="155" width="218" height="178" rx="16"></rect>
              <text class="isms-flow-section-title isms-flow-section-title-sm" x="205" y="184" text-anchor="middle">Compliance Framework</text>
              <path class="isms-flow-inner-connector" marker-end="url(#ismsFlowArrow)" d="M 205 242 V 267"></path>
              ${button({label: 'Clauses', href: '/clauses.html', x: 134, y: 206, w: 142, h: 34})}
              ${button({label: 'Controls', href: '/controls.html', x: 134, y: 268, w: 142, h: 34})}
            </g>

            <g class="isms-flow-section">
              <rect class="isms-flow-section-bg" x="427" y="303" width="156" height="66" rx="14"></rect>
              ${button({label: 'Sources', href: '/sources.html', x: 456, y: 319, w: 98, h: 34})}
            </g>

            <g class="isms-flow-section">
              <rect class="isms-flow-section-bg" x="700" y="285" width="210" height="102" rx="16"></rect>
              <text class="isms-flow-section-title isms-flow-section-title-sm" x="805" y="312" text-anchor="middle">Evidence records</text>
              ${button({label: 'Evidence', href: '/events.html', x: 722, y: 331, w: 82, h: 34})}
              ${button({label: 'Diary', href: '/events.html?source=diary', x: 814, y: 331, w: 74, h: 34})}
            </g>

            <g class="isms-flow-section">
              <rect class="isms-flow-section-bg" x="827" y="123" width="158" height="66" rx="14"></rect>
              ${button({label: 'Audits', href: '/audits.html', x: 858, y: 139, w: 96, h: 34})}
            </g>
          </svg>
        </div>
      </div>
    </div>
  </div>`;
}
function renderObjectives() {
  const items = data.objectives || [];
  setCount('objectivesCount', items.length);
  rows.objectives.innerHTML = items.map((o) => `<tr data-isms-row="objective" data-isms-id="${esc(o.id)}">
    <td class="wrap"><div class="fw-semibold"><a href="${esc(objectiveHref(o))}">${esc(shorten(o.goal || o.requirement || 'Objective', 150))}</a></div><div class="small-muted">${esc(shorten(o.requirement || '', 160))}</div></td>
    <td class="wrap">${esc(shorten(o.metric || '—', 180))}</td>
    <td class="isms-objectives-owner-cell">${objectiveUserPill(o.owner)}</td>
    <td class="wrap">${esc(o.completion_target_date || '—')}</td>
    <td class="isms-objectives-resources-cell">${objectiveUserBadges(o.resource_users)}<div class="small-muted">${esc(shorten(o.resource_requirements_text || '', 100))}</div></td>
    <td class="wrap">${controlBadges(o.controls)}</td><td class="wrap">${clauseBadges(o.clauses)}</td>${actionsHtml('objective', o.id, 'isms-objectives-actions')}
  </tr>`).join('') || emptyRow(8, 'No ISMS objectives yet.');
}

function renderDocuments() {
  const items = data.documents || [];
  setCount('documentsCount', items.length);
  rows.documents.innerHTML = items.map((d) => `<tr data-isms-row="document">
    <td class="fw-semibold wrap"><a href="${esc(documentHref(d))}">${esc(d.title || 'Document')}</a></td><td>${esc(d.document_type || '—')}</td><td>${docLink(d)}</td>
    <td class="wrap small-muted">${esc(shorten(d.description || '', 180) || '—')}</td><td>${controlBadges(d.controls)}</td><td>${clauseBadges(d.clauses)}</td><td class="small-muted">${esc(fmtTs(d.updated_at))}</td>${actionsHtml('document', d.id)}
  </tr>`).join('') || emptyRow(8, 'No ISMS documents yet.');
}

function renderOrg() {
  const items = data.org_nodes || [];
  setCount('orgCount', items.length);
  rows.org.innerHTML = items.map((n) => `<tr data-isms-row="org">
    <td class="fw-semibold">${esc(n.name || 'Org node')}</td><td>${esc(n.parent?.name || 'Root')}</td><td>${esc(n.node_type || '—')}</td><td>${userBadges(n.people)}</td><td class="wrap small-muted">${esc(shorten(n.description || '', 160) || '—')}</td><td>${controlBadges(n.controls)}</td><td>${clauseBadges(n.clauses)}</td>${actionsHtml('org', n.id)}
  </tr>`).join('') || emptyRow(8, 'No organisation chart nodes yet.');
}

function orgDisplayName(node) {
  const people = names(node.people);
  return `${node.name || 'Org node'}${people && people !== '—' ? ` (${people})` : ''}`;
}

function buildOrgHierarchy(items) {
  const nodes = new Map();
  const root = {id: 'root', name: 'ISMS Organisation', node_type: 'root', children: []};
  for (const item of items || []) nodes.set(String(item.id), {...item, children: []});
  for (const node of nodes.values()) {
    const parentId = node.parent_id || node.parent?.id;
    const parent = parentId ? nodes.get(String(parentId)) : null;
    if (parent && parent.id !== node.id) parent.children.push(node);
    else root.children.push(node);
  }
  const sortTree = (node) => {
    node.children = (node.children || []).sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), undefined, {numeric: true}));
    node.children.forEach(sortTree);
  };
  sortTree(root);
  return root;
}


function wrapSvgLabel(text, maxChars = 26, maxLines = 2) {
  const raw = String(text || '').trim();
  if (!raw) return [''];
  const words = raw.split(/\s+/);
  const lines = [];
  let line = '';
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (candidate.length <= maxChars || !line) {
      line = candidate;
      continue;
    }
    lines.push(line);
    line = word;
    if (lines.length === maxLines) break;
  }
  if (line && lines.length < maxLines) lines.push(line);
  const joined = lines.join(' ');
  if (joined !== raw && lines.length) {
    const lastIdx = Math.min(lines.length, maxLines) - 1;
    let last = lines[lastIdx] || '';
    while (last.length > Math.max(1, maxChars - 1)) last = last.slice(0, -1);
    lines[lastIdx] = `${last.replace(/[.…]+$/, '')}…`;
  }
  return lines.length ? lines : [''];
}

function orgNodeSubtitle(node) {
  const people = names(node.people);
  return [node.node_type, people && people !== '—' ? people : ''].filter(Boolean).join(' • ');
}

function renderOrgChart() {
  const el = $('orgChartViz');
  if (!el) return;
  const items = data.org_nodes || [];
  el.innerHTML = '';
  if (!items.length) {
    el.innerHTML = '<div class="p-4 small-muted">No organisation chart nodes yet.</div>';
    return;
  }
  if (!window.d3) {
    el.innerHTML = '<div class="p-4 small-muted">D3 is not available.</div>';
    return;
  }
  const d3 = window.d3;
  const treeData = buildOrgHierarchy(items);
  const root = d3.hierarchy(treeData);
  const nodeBox = {width: 240, height: 76, rx: 12};
  const nodeXSpacing = 275;
  const nodeYSpacing = 132;
  const width = Math.max(920, el.clientWidth || 920);
  const margin = {top: 48, right: 96, bottom: 64, left: 96};
  const tree = d3.tree().nodeSize([nodeXSpacing, nodeYSpacing]);
  tree(root);
  const nodes = root.descendants();
  const minX = d3.min(nodes, (d) => d.x) || 0;
  const maxX = d3.max(nodes, (d) => d.x) || 0;
  const minY = d3.min(nodes, (d) => d.y) || 0;
  const maxY = d3.max(nodes, (d) => d.y) || 0;
  const viewW = Math.max(width, maxX - minX + margin.left + margin.right + nodeBox.width);
  const viewH = Math.max(420, maxY - minY + margin.top + margin.bottom + nodeBox.height);
  const initialTranslateX = margin.left + (nodeBox.width / 2) - minX;
  const initialTranslateY = margin.top + (nodeBox.height / 2) - minY;
  const svg = d3.select(el).append('svg')
    .attr('width', '100%')
    .attr('height', viewH)
    .attr('viewBox', `0 0 ${viewW} ${viewH}`)
    .attr('role', 'img')
    .attr('aria-label', 'ISMS organisation chart hierarchy');
  const g = svg.append('g').attr('transform', `translate(${initialTranslateX},${initialTranslateY})`);
  const link = d3.linkVertical().x((d) => d.x).y((d) => d.y);
  g.append('g').attr('fill', 'none').attr('stroke', 'currentColor').attr('stroke-opacity', 0.32).attr('stroke-width', 1.6)
    .selectAll('path').data(root.links()).join('path').attr('d', link);
  const node = g.append('g').selectAll('g').data(nodes).join('g').attr('transform', (d) => `translate(${d.x},${d.y})`);
  node.append('rect')
    .attr('x', -nodeBox.width / 2)
    .attr('y', -nodeBox.height / 2)
    .attr('width', nodeBox.width)
    .attr('height', nodeBox.height)
    .attr('rx', nodeBox.rx)
    .attr('class', (d) => d.depth === 0 ? 'isms-org-node isms-org-node-root' : 'isms-org-node');
  node.each(function drawNodeLabel(d) {
    const nodeGroup = d3.select(this);
    const titleLines = wrapSvgLabel(d.data.name || 'Org node', 28, 2);
    const subtitleLines = wrapSvgLabel(orgNodeSubtitle(d.data), 34, 1).filter((line) => line);
    const titleStart = titleLines.length > 1 ? -18 : -12;
    titleLines.forEach((line, idx) => {
      nodeGroup.append('text')
        .attr('text-anchor', 'middle')
        .attr('dy', `${titleStart + (idx * 14)}px`)
        .attr('class', 'isms-org-node-title')
        .text(line);
    });
    if (subtitleLines.length) {
      nodeGroup.append('text')
        .attr('text-anchor', 'middle')
        .attr('dy', `${titleLines.length > 1 ? 20 : 15}px`)
        .attr('class', 'isms-org-node-subtitle')
        .text(subtitleLines[0]);
    }
  });
  node.append('title').text((d) => orgDisplayName(d.data));
  const zoom = d3.zoom().scaleExtent([0.35, 3]).on('zoom', (event) => g.attr('transform', event.transform));
  svg.call(zoom);
  const initial = d3.zoomIdentity.translate(initialTranslateX, initialTranslateY).scale(1);
  svg.call(zoom.transform, initial);
  const zoomIn = $('orgChartZoomIn');
  const zoomOut = $('orgChartZoomOut');
  const zoomReset = $('orgChartZoomReset');
  if (zoomIn) zoomIn.onclick = () => svg.transition().duration(140).call(zoom.scaleBy, 1.2);
  if (zoomOut) zoomOut.onclick = () => svg.transition().duration(140).call(zoom.scaleBy, 1 / 1.2);
  if (zoomReset) zoomReset.onclick = () => svg.transition().duration(160).call(zoom.transform, initial);
}

function renderAssets() {
  const items = data.assets || [];
  setCount('assetsCount', items.length);
  rows.assets.innerHTML = items.map((a) => `<tr data-isms-row="asset">
    <td class="fw-semibold"><a href="${esc(assetHref(a))}">${esc(a.asset || a.name || 'Asset')}</a></td>
    <td>${esc(a.category?.name || a.category_name || '—')}</td>
    <td>${esc(a.subcategory?.name || a.subcategory_name || '—')}</td>
    <td>${esc(licenseLabel(a.license_name || a.license))}</td>
    <td>${esc(a.owner_org_node?.name || '—')}</td>
    <td>${esc(a.register_held_by_org_node?.name || '—')}</td>
    <td class="wrap small-muted">${esc(shorten(a.description || '', 160) || '—')}</td>
    <td>${Number(a.risk_count || 0)}</td>
    <td>${controlBadges(a.controls)}</td><td>${clauseBadges(a.clauses)}</td>${actionsHtml('asset', a.id)}
  </tr>`).join('') || emptyRow(11, 'No assets yet.');
}


function accessStatusClass(value) {
  const v = String(value || '').toLowerCase();
  if (v === 'approved') return 'isms-access-cell-approved';
  if (v === 'pending approval') return 'isms-access-cell-pending';
  return 'isms-app-cell-empty';
}

function accessServiceLabel(item) {
  return item?.service?.asset || item?.service?.name || item?.service?.display || 'Service';
}

function accessRoles(item) {
  const roles = Array.isArray(item?.roles) ? item.roles : [];
  if (roles.length) return roles;
  return item?.role ? [item.role] : [];
}

function accessRoleColumns(items = []) {
  const out = [];
  const seen = new Set();
  const add = (role) => {
    const id = role?.id || role?.org_node_id;
    if (!id || seen.has(String(id))) return;
    seen.add(String(id));
    out.push(role);
  };
  for (const node of meta.org_nodes || []) {
    const type = String(node?.node_type || '').toLowerCase();
    if (!type || type === 'role') add(node);
  }
  for (const entry of items || []) {
    for (const role of accessRoles(entry)) add(role);
  }
  return out.sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), undefined, {numeric: true}));
}

function accessKeyLabel(item) {
  const roles = accessRoles(item).map((role) => role?.name || '').filter(Boolean).join(', ');
  const accounts = (item?.aws_accounts || []).map((acct) => awsAccountLabel(acct)).filter(Boolean).join(', ');
  const approver = item?.approved_by?.username || '';
  return [item?.task_action || 'Task/Action', accessServiceLabel(item), item?.status || '', accounts, roles, approver, item?.notes || ''].filter(Boolean).join(' • ');
}

function accessAccountPillsHtml(entry) {
  const accounts = Array.isArray(entry?.aws_accounts) ? entry.aws_accounts : [];
  if (!accounts.length) return '<span class="small-muted">—</span>';
  const labels = accounts.map((acct) => awsAccountLabel(acct)).filter(Boolean);
  const visible = accounts.slice(0, 2);
  const pills = visible.map((acct) => {
    const fullLabel = awsAccountLabel(acct);
    const label = acct?.name || acct?.account_id || 'hosting account';
    return `<span class="badge badge-soft user-pill isms-aws-account-pill" title="${esc(fullLabel)}">${esc(shorten(label, 18))}</span>`;
  });
  const remaining = accounts.length - visible.length;
  if (remaining > 0) {
    pills.push(`<span class="badge badge-soft user-pill isms-aws-account-pill" title="${esc(labels.join('\n'))}">+${remaining}</span>`);
  }
  return `<span class="user-pill-list isms-aws-account-pill-list">${pills.join('')}</span>`;
}

function noteTooltipHtml(notes, label = 'Notes') {
  const text = String(notes || '').trim();
  if (!text) return '<span class="small-muted">—</span>';
  return `<button class="btn btn-sm btn-outline-secondary isms-note-tooltip-trigger" type="button" data-bs-toggle="tooltip" data-bs-placement="left" title="${esc(text)}" aria-label="${esc(`${label}: ${text}`)}"><i class="bi bi-card-text" aria-hidden="true"></i><span class="visually-hidden">${esc(label)}</span></button>`;
}

function initIsmsTooltips(root = document) {
  if (!window.bootstrap?.Tooltip || !root) return;
  root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((el) => {
    const existing = window.bootstrap.Tooltip.getInstance(el);
    if (existing) existing.dispose();
    new window.bootstrap.Tooltip(el, {container: 'body', trigger: 'hover focus'});
  });
}

function accessRoleCellHtml(entry, roleId) {
  const roles = accessRoles(entry);
  const matched = roles.find((role) => String(role?.id || role?.org_node_id || '') === String(roleId));
  if (!matched) return '<td class="isms-app-cell isms-app-cell-empty isms-access-role-cell" data-sort=""></td>';
  const name = matched.name || 'Role';
  return `<td class="isms-app-cell isms-access-cell-linked isms-access-role-cell text-center" data-sort="1" title="${esc(name)}"><span aria-label="${esc(name)} included">✓</span><span class="visually-hidden"> ${esc(name)}</span></td>`;
}

function renderAccessControlMatrix() {
  const items = data.access_control_matrix || [];
  const roleColumns = accessRoleColumns(items);
  const head = $('accessMatrixHead');
  setCount('accessControlCount', items.length);
  if (head) {
    head.innerHTML = `<tr><th class="text-center isms-app-head-group">Service</th><th class="text-center isms-app-head-linkage">Task/Action</th><th class="isms-access-accounts-head">Managed Hosting Accounts</th><th class="text-center isms-access-status-head">Status</th><th class="text-center isms-access-approver-head">Approved by</th>${roleColumns.map((role) => `<th class="text-center isms-access-role-heading" title="${esc(role.name || 'Role')}">${esc(role.name || 'Role')}</th>`).join('')}<th class="text-center isms-access-notes-head" title="Notes">Notes</th><th class="text-center no-print isms-access-actions-head">Actions</th></tr>`;
  }
  if (!items.length) {
    rows.access.innerHTML = emptyRow(roleColumns.length + 7, 'No access control matrix rows yet.');
    return;
  }
  const grouped = [];
  for (const entry of items) {
    const serviceId = entry.service_asset_id || entry.service?.id || 'unknown';
    let group = grouped.find((g) => String(g.id) === String(serviceId));
    if (!group) {
      group = {id: serviceId, name: accessServiceLabel(entry), rows: []};
      grouped.push(group);
    }
    group.rows.push(entry);
  }
  grouped.sort((a, b) => String(a.name).localeCompare(String(b.name), undefined, {numeric: true}));
  rows.access.innerHTML = grouped.map((group) => group.rows.map((entry, idx) => {
    const serviceHref = entry.service?.id ? assetHref({id: entry.service.id}) : '';
    const serviceCell = idx === 0 ? `<td class="isms-app-group" rowspan="${group.rows.length}" data-sort="${esc(group.name)}"><span>${serviceHref ? `<a class="link-light" href="${esc(serviceHref)}">${esc(group.name)}</a>` : esc(group.name)}</span></td>` : '';
    const approverText = entry.approved_by?.username || '';
    return `<tr data-isms-row="access" data-isms-filter-text="${esc(accessKeyLabel(entry))}">
      ${serviceCell}
      <td class="wrap isms-app-key-linkage" data-sort="${esc(entry.task_action || '')}"><div class="fw-semibold"><a href="${esc(accessControlHref(entry))}">${esc(entry.task_action || 'Task/Action')}</a></div></td>
      <td class="wrap isms-access-accounts" data-sort="${esc((entry.aws_accounts || []).map((acct) => awsAccountLabel(acct)).join(', '))}">${accessAccountPillsHtml(entry)}</td>
      <td class="isms-app-cell isms-access-status ${accessStatusClass(entry.status)} text-center" data-sort="${esc(entry.status || 'Pending Approval')}"><div class="fw-semibold">${esc(entry.status || 'Pending Approval')}</div></td>
      <td class="wrap isms-access-approver" data-sort="${esc(approverText)}">${entry.approved_by ? userPillHtml(entry.approved_by, {className: 'isms-access-approver-pill'}) : '<span class="small-muted">—</span>'}</td>
      ${roleColumns.map((role) => accessRoleCellHtml(entry, role.id)).join('')}
      <td class="text-center small-muted isms-access-notes" data-sort="${esc(entry.notes || '')}">${noteTooltipHtml(entry.notes, 'Access row notes')}</td>
      ${actionsHtml('access', entry.id, 'isms-access-actions')}
    </tr>`;
  }).join('')).join('');
  initIsmsTooltips(rows.access);
}


function thresholdLabel(op) {
  return {lt: '<', lte: '≤', eq: '=', gte: '≥', gt: '>'}[String(op || '')] || '';
}

function measureTitle(item) {
  return item?.summary || item?.metric || item?.effectiveness_measure || 'Effectiveness measure';
}

function effectivenessMeasureHref(item) {
  return withFramework(`/isms-effectiveness-measure.html?id=${encodeURIComponent(item?.id || '')}`, framework);
}

function effectivenessMetricHref(entry) {
  return withFramework(`/isms-effectiveness-metric.html?id=${encodeURIComponent(entry?.id || '')}`, framework);
}

function metricValueHtml(entry, measure) {
  if (!entry) return '<span class="small-muted">No metric entries yet.</span>';
  const period = entry.period || [entry.period_start || '', entry.period_end || ''].filter(Boolean).join(' → ');
  const source = entry.source_url ? `<a href="${esc(entry.source_url)}" target="_blank" rel="noopener noreferrer">${esc(entry.source_title || entry.source_type || 'source')}</a>` : esc(entry.source_title || entry.source_type || 'other');
  const eventLink = entry.source_event_id ? ` · <a class="font-monospace" href="${esc(withFramework(`/event.html?id=${encodeURIComponent(entry.source_event_id)}`, framework))}" title="Open KEEN event">${esc(entry.source_event_id)}</a>` : '';
  const valueHtml = effectivenessMetricValueHtml(entry, measure, {href: entry.id ? effectivenessMetricHref(entry) : '', fallback: 'Metric entry'});
  const thresholdHtml = effectivenessMetricThresholdBadgeHtml(measure, entry);
  return `<div>${valueHtml}${thresholdHtml}</div><div class="small-muted">${esc(period || 'No period')} · ${source}${eventLink}</div>`;
}

function metricHistoryLinkHtml(item) {
  const count = Number(item?.metric_entry_count || 0);
  if (!count) return '';
  const label = `${count} metric ${count === 1 ? 'entry' : 'entries'}`;
  return `<div class="small-muted mt-1"><a href="${esc(effectivenessMeasureHref(item))}#metrics">${esc(label)} in metric history</a></div>`;
}


function effectivenessActionsHtml(item) {
  const open = `<a class="btn btn-outline-primary" href="${esc(effectivenessMeasureHref(item))}" title="Open effectiveness measure"><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i><span class="visually-hidden">Open</span></a>`;
  const record = canManage ? `<button class="btn btn-outline-secondary" type="button" data-effectiveness-record="${esc(item.id)}" title="Record metric entry"><i class="bi bi-plus-circle" aria-hidden="true"></i><span class="visually-hidden">Record metric entry</span></button>` : '';
  const sample = canSampleAudits ? `<button class="btn btn-outline-success" type="button" data-audit-sample-entity="effectiveness" data-isms-id="${esc(item.id)}" title="Sample into audit"><i class="bi bi-clipboard2-plus" aria-hidden="true"></i><span class="visually-hidden">Sample into audit</span></button>` : '';
  const editDelete = canManage ? `<button class="btn btn-outline-primary" type="button" data-isms-edit="effectiveness" data-isms-id="${esc(item.id)}"><i class="bi bi-pencil" aria-hidden="true"></i><span class="visually-hidden">Edit</span></button><button class="btn btn-outline-danger" type="button" data-isms-delete="effectiveness" data-isms-id="${esc(item.id)}"><i class="bi bi-trash" aria-hidden="true"></i><span class="visually-hidden">Delete</span></button>` : '';
  return `<td class="text-nowrap no-print isms-effectiveness-actions"><div class="btn-group btn-group-sm" role="group">${open}${record}${sample}${editDelete}</div></td>`;
}

function effectivenessFilterText(item) {
  const entryBits = (item.metric_entries || []).flatMap((entry) => [entry?.value_display, entry?.source_type, entry?.source_title, entry?.source_reference]);
  return [item.summary, item.description, item.effectiveness_measure, item.metric, item.metric_key, item.target_display, item.owner?.username, item.frequency, item.notes, item.latest_entry?.value_display, item.latest_entry?.source_type, item.latest_entry?.source_title, ...entryBits, ...(item.controls || []).map((c) => `${c.ref || ''} ${c.title || ''}`)].filter(Boolean).join(' • ');
}

function renderEffectivenessMeasures() {
  const items = data.effectiveness_measures || [];
  setCount('effectivenessCount', items.length);
  if (!rows.effectiveness) return;
  rows.effectiveness.innerHTML = items.map((item) => `<tr data-isms-row="effectiveness" data-isms-id="${esc(item.id)}" data-isms-filter-text="${esc(effectivenessFilterText(item))}">
    <td class="wrap"><div class="fw-semibold"><a href="${esc(effectivenessMeasureHref(item))}">${esc(shorten(item.summary || 'Effectiveness measure', 140))}</a></div>${item.description ? `<div class="small-muted whitespace-pre-wrap mt-1">${esc(shorten(item.description, 260))}</div>` : ''}${item.metric_key ? `<div class="small-muted mt-1"><code>${esc(item.metric_key)}</code></div>` : ''}</td>
    <td class="wrap">${esc(shorten(item.effectiveness_measure || '', 220) || '—')}</td>
    <td class="wrap">${esc(shorten(item.metric || '', 180) || '—')}</td>
    <td class="wrap">${metricValueHtml(item.latest_entry, item)}${metricHistoryLinkHtml(item)}</td>
    <td>${esc(item.target_display || '—')}</td>
    <td>${userPillHtml(item.owner)}</td>
    <td class="wrap">${controlBadges(item.controls)}</td>
    ${effectivenessActionsHtml(item)}
  </tr>`).join('') || emptyRow(8, 'No effectiveness measures yet.');
  initIsmsTooltips(rows.effectiveness);
}

function effectivenessMetricSourceOptions() {
  const sources = Array.isArray(meta.effectiveness_metric_sources) && meta.effectiveness_metric_sources.length
    ? meta.effectiveness_metric_sources
    : (meta.effectiveness_metric_source_types || ['other']).map((x) => ({id: x, source: x, label: x === 'other' ? 'Other' : x}));
  const seen = new Set();
  const out = [];
  for (const src of sources) {
    const id = String(src?.id || src?.source || '').trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({
      id,
      label: id === 'other' ? 'Other' : (src?.label || src?.name || src?.source || id),
      source: src?.source || id,
    });
  }
  if (!seen.has('other')) out.push({id: 'other', label: 'Other', source: 'other'});
  return out;
}

function renderEffectivenessFormOptions() {
  const measureSelect = $('metricMeasureId');
  if (measureSelect) measureSelect.innerHTML = optionList(data.effectiveness_measures || [], {label: (m) => `${measureTitle(m)}${m.metric_key ? ` (${m.metric_key})` : ''}`});
  const owner = $('effOwner');
  if (owner) owner.innerHTML = optionList(meta.users || [], {label: 'username', includeBlank: true});
  const sourceType = $('metricSourceType');
  if (sourceType) sourceType.innerHTML = optionList(effectivenessMetricSourceOptions(), {label: (src) => src.source === 'other' ? 'Other' : `${src.label} (${src.source})`});
}

function resetMetricEntryForm(measureId = '') {
  const form = $('metricEntryForm');
  if (!form) return;
  form.reset();
  renderEffectivenessFormOptions();
  if ($('metricSourceType')) $('metricSourceType').value = 'other';
  if (measureId) $('metricMeasureId').value = measureId;
  const measure = findItem('effectiveness', measureId || $('metricMeasureId')?.value);
  if (measure && !$('metricUnit').value) $('metricUnit').value = measure.target_unit || '';
}

function showMetricEntryForm(measureId = '') {
  if (!canManage) return;
  hideAllIsmsForms('metricEntry');
  const card = $('metricEntryFormCard');
  if (!card) return;
  resetMetricEntryForm(measureId);
  card.style.display = '';
  card.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function metricEntryPayloadFromForm() {
  const raw = {
    recorded_at: null,
    period_start: $('metricPeriodStart')?.value || null,
    period_end: $('metricPeriodEnd')?.value || null,
    metric_value: $('metricValue')?.value === '' ? null : Number($('metricValue')?.value),
    metric_unit: $('metricUnit')?.value || '',
    qualitative_value: $('metricQualitative')?.value || '',
    source_type: $('metricSourceType')?.value || 'other',
    source_title: $('metricSourceTitle')?.value || '',
    source_url: $('metricSourceUrl')?.value || null,
    source_reference: $('metricSourceReference')?.value || '',
    source_event_id: $('metricSourceEventId')?.value || null,
    notes: $('metricNotes')?.value || '',
    raw_payload: {},
  };
  if (raw.metric_value === null && !raw.qualitative_value.trim()) throw new Error('Provide a numeric value or a qualitative value.');
  return raw;
}

function appSourceTypeLabel(type) {
  if (type === 'document') return 'Policies and Processes';
  if (type === 'person') return 'People';
  if (type === 'asset') return 'Assets';
  if (type === 'org_node') return 'Org chart positions';
  return 'Other';
}

function appSourceSort(type) {
  return {document: 1, person: 2, asset: 3, org_node: 4}[type] || 9;
}

function appSourceKey(sourceType, id) {
  return `${sourceType || ''}:${id || ''}`;
}

function appSourceRows(items) {
  const seen = new Set();
  const out = [];
  const push = (sourceType, id, label, subtitle = '', source = null) => {
    const key = appSourceKey(sourceType, id);
    if (!id || seen.has(key)) return;
    seen.add(key);
    out.push({key, source_type: sourceType, group: appSourceTypeLabel(sourceType), label: label || 'Source', subtitle, source});
  };
  for (const d of meta.documents || []) push('document', d.id, d.title, d.document_type || '', d);
  for (const u of meta.users || []) push('person', u.id, u.username || u.email || 'User', u.email || '', u);
  for (const a of meta.assets || []) push('asset', a.id, a.asset || a.name, [a.category?.name, a.subcategory?.name].filter(Boolean).join(' / ') || a.license || '', a);
  for (const n of meta.org_nodes || []) push('org_node', n.id, n.name, n.node_type || '', n);
  for (const entry of items || []) {
    const src = entry.source || {};
    push(entry.source_type, src.id || entry.document_id || entry.user_id || entry.asset_id || entry.org_node_id, src.label || entry.display || 'Source', entry.source_type || '', src);
  }
  return out.sort((a, b) => appSourceSort(a.source_type) - appSourceSort(b.source_type) || String(a.label).localeCompare(String(b.label), undefined, {numeric: true}));
}

function appCellClass(value) {
  const v = String(value || '').toLowerCase();
  if (v === 'high') return 'isms-app-cell-high';
  if (v === 'medium') return 'isms-app-cell-medium';
  if (v === 'low') return 'isms-app-cell-low';
  return 'isms-app-cell-empty';
}

function appCellHtml(entries) {
  if (!entries.length) return '<td class="isms-app-cell isms-app-cell-empty" data-sort=""></td>';
  const best = entries[0];
  const value = best.value || '—';
  const buttons = (canManage || canSampleAudits) ? `<div class="isms-app-cell-actions no-print">${canSampleAudits ? `<button class="btn btn-sm btn-light py-0 px-1" type="button" data-audit-sample-entity="app" data-isms-id="${esc(best.id)}" title="Sample into audit"><i class="bi bi-clipboard2-plus" aria-hidden="true"></i></button>` : ''}${canManage ? `<button class="btn btn-sm btn-light py-0 px-1" type="button" data-isms-edit="app" data-isms-id="${esc(best.id)}" title="Edit matrix entry"><i class="bi bi-pencil" aria-hidden="true"></i></button><button class="btn btn-sm btn-light py-0 px-1" type="button" data-isms-delete="app" data-isms-id="${esc(best.id)}" title="Delete matrix entry"><i class="bi bi-trash" aria-hidden="true"></i></button>` : ''}</div>` : '';
  const extra = entries.length > 1 ? `<div class="small">+${entries.length - 1}</div>` : '';
  const title = entries.map((entry) => `${entry.value || ''}${entry.notes ? ` — ${entry.notes}` : ''}`).join('\n');
  return `<td class="isms-app-cell ${appCellClass(value)}" data-sort="${esc(value)}" title="${esc(title)}"><div class="fw-semibold">${esc(value)}</div>${extra}${buttons}</td>`;
}

function renderAppConfig() {
  const items = data.application_configurations || [];
  const processes = meta.business_processes || [];
  const head = $('appConfigHead');
  setCount('appConfigCount', items.length);
  if (head) {
    head.innerHTML = `<tr><th class="isms-app-head-group">Group</th><th class="isms-app-head-linkage">Key linkages</th>${processes.map((bp) => `<th class="text-center isms-app-process-heading" title="${esc(bp.description || bp.name || '')}">${esc(bp.name || 'Business process')}</th>`).join('')}</tr>`;
  }
  const entryMap = new Map();
  for (const entry of items) {
    const srcId = entry.source?.id || entry.document_id || entry.user_id || entry.asset_id || entry.org_node_id;
    const key = `${appSourceKey(entry.source_type, srcId)}::${entry.business_process_id || entry.business_process?.id || ''}`;
    if (!entryMap.has(key)) entryMap.set(key, []);
    entryMap.get(key).push(entry);
  }
  const sourceRows = appSourceRows(items);
  if (!sourceRows.length || !processes.length) {
    rows.app.innerHTML = emptyRow(Math.max(3, processes.length + 2), !processes.length ? 'No business processes are configured yet.' : 'No policy/process, people, asset or org chart rows are available yet.');
    return;
  }
  const grouped = [];
  for (const src of sourceRows) {
    let group = grouped.find((g) => g.name === src.group);
    if (!group) {
      group = {name: src.group, rows: []};
      grouped.push(group);
    }
    group.rows.push(src);
  }
  rows.app.innerHTML = grouped.map((group) => group.rows.map((src, idx) => {
    const href = src.source_type === 'document' && src.source?.id
      ? `/isms-document.html?id=${encodeURIComponent(src.source.id)}`
      : (src.source_type === 'asset' && src.source?.id
        ? `/isms-asset.html?id=${encodeURIComponent(src.source.id)}`
        : (src.source_type === 'org_node' ? `/isms.html?tab=org` : ''));
    const label = src.source_type === 'person'
      ? userPillHtml(src.source || src.label)
      : (href ? `<a class="fw-semibold" href="${esc(withFramework(href, framework))}">${esc(src.label)}</a>` : `<span class="fw-semibold">${esc(src.label)}</span>`);
    const groupCell = idx === 0 ? `<td class="isms-app-group" rowspan="${group.rows.length}" data-sort="${esc(group.name)}"><span>${esc(group.name)}</span></td>` : '';
    return `<tr data-isms-row="app">
      ${groupCell}
      <td class="wrap isms-app-key-linkage" data-sort="${esc(src.label)}">${label}${src.subtitle ? `<div class="small-muted">${esc(src.subtitle)}</div>` : ''}</td>
      ${processes.map((bp) => appCellHtml(entryMap.get(`${src.key}::${bp.id}`) || [])).join('')}
    </tr>`;
  }).join('')).join('');
}

function renderMeetings() {
  const items = data.meetings || [];
  setCount('meetingsCount', items.length);
  rows.meetings.innerHTML = items.map((m) => {
    const links = (m.links || []).map((l) => l.link_type === 'isms_document' && l.document ? `<a class="badge badge-soft text-decoration-none me-1" href="${esc(documentHref(l.document))}">${esc(l.document.title || 'ISMS document')}</a>` : (l.url ? `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.title || l.url)}</a>` : '')).filter(Boolean).join('<br>') || '<span class="small-muted">—</span>';
    const href = meetingHref(m);
    return `<tr data-isms-row="meeting"><td>${esc(m.date || '—')}</td><td class="fw-semibold wrap"><a href="${esc(href)}">${esc(m.title || 'Meeting')}</a><div class="small-muted">${esc(shorten(plainTextFromRichText(m.agenda_minutes_notes || ''), 140))}</div></td><td>${esc([m.start_time, m.end_time].filter(Boolean).join('–') || '—')}</td><td>${userBadges(m.attendees)}</td><td>${userBadges(m.apologies)}</td><td>${links}</td><td>${controlBadges(m.controls)}</td><td>${clauseBadges(m.clauses)}</td>${actionsHtml('meeting', m.id)}</tr>`;
  }).join('') || emptyRow(9, 'No meeting minutes yet.');
}

function populateForms() {
  for (const id of ['objectiveFormCard', 'documentFormCard', 'orgFormCard', 'assetFormCard', 'assetCategoryCard', 'licenseCard', 'awsAccountCard', 'accessControlFormCard', 'appConfigFormCard', 'meetingFormCard']) {
    const el = $(id); if (el) el.style.display = 'none';
  }
  for (const kind of Object.keys(formIds)) ensureCancelButton(kind);
  const userOpts = optionList(meta.users || [], {label: 'username'});
  for (const id of ['objResources', 'orgUsers', 'meetingAttendees', 'meetingApologies']) { const el = $(id); if (el) el.innerHTML = userOpts; }
  const owner = $('objOwner'); if (owner) owner.innerHTML = optionList(meta.users || [], {label: 'username', includeBlank: true});
  const docType = $('docType'); if (docType) docType.innerHTML = optionList((meta.document_types || []).map((x) => ({id: x, name: x})), {label: 'name'});
  const orgOpts = optionList(meta.org_nodes || [], {label: (n) => `${n.name || 'Node'} (${n.node_type || 'role'})`, includeBlank: true, blankLabel: 'Root'});
  for (const id of ['orgParent', 'assetOwner', 'assetRegister']) { const el = $(id); if (el) el.innerHTML = orgOpts; }
  const controlOpts = optionList(meta.controls || [], {label: (c) => `${c.ref || ''} ${c.title || ''}`.trim()});
  const clauseOpts = optionList(meta.clauses || [], {label: (c) => `${c.ref || ''} ${c.title || ''}`.trim()});
  for (const id of ['objControls', 'docControls', 'orgControls', 'assetControls', 'effControls', 'meetingControls']) { const el = $(id); if (el) el.innerHTML = controlOpts; }
  for (const id of ['objClauses', 'docClauses', 'orgClauses', 'assetClauses', 'meetingClauses']) { const el = $(id); if (el) el.innerHTML = clauseOpts; }
  const meetingDocs = $('meetingDocuments'); if (meetingDocs) meetingDocs.innerHTML = optionList(meta.documents || [], {label: 'title'});
  const bp = $('appBusinessProcess'); if (bp) bp.innerHTML = optionList(meta.business_processes || [], {label: 'name'});
  renderAccessFormOptions();
  renderEffectivenessFormOptions();
  renderLicenseOptions();
  populateAssetCategoryControls();
  populateAppSourceOptions();
}

function renderAccessFormOptions(targetAccountValues = []) {
  const service = $('accessService');
  if (service) service.innerHTML = optionList(meta.assets || [], {label: (a) => a.asset || a.name || 'Asset'});
  const approver = $('accessApprovedBy');
  if (approver) approver.innerHTML = optionList(meta.users || [], {label: 'username', includeBlank: true});
  const role = $('accessRole');
  if (role) role.innerHTML = optionList(meta.org_nodes || [], {label: (n) => `${n.name || 'Node'} (${n.node_type || 'role'})`});
  const status = $('accessStatus');
  if (status) status.innerHTML = optionList((meta.access_statuses || ['Pending Approval', 'Approved']).map((x) => ({id: x, name: x})), {label: 'name'});
  renderAwsAccountOptions(targetAccountValues);
}

function populateAppSourceOptions() {
  const type = $('appSourceType')?.value || 'document';
  const source = $('appSourceId');
  if (!source) return;
  if (type === 'person') { source.innerHTML = optionList(meta.users || [], {label: 'username'}); return; }
  if (type === 'asset') { source.innerHTML = optionList(meta.assets || [], {label: (a) => a.asset || a.name || 'Asset'}); return; }
  if (type === 'org_node') { source.innerHTML = optionList(meta.org_nodes || [], {label: (n) => `${n.name || 'Node'} (${n.node_type || 'role'})`}); return; }
  source.innerHTML = optionList(meta.documents || [], {label: 'title'});
}

$('assetCategory')?.addEventListener('change', () => renderAssetSubcategoryOptions());
$('addAssetCategory')?.addEventListener('click', async () => {
  const input = $('assetCategoryName');
  const name = String(input?.value || '').trim();
  if (!name) return;
  await apiPost('/api/v1/isms/asset-categories', {name});
  if (input) input.value = '';
  await reloadAfter('Asset category added');
});
$('addAssetSubcategory')?.addEventListener('click', async () => {
  const input = $('assetSubcategoryName');
  const name = String(input?.value || '').trim();
  const categoryId = $('assetSubcategoryCategory')?.value || $('assetCategory')?.value || '';
  if (!name || !categoryId) return;
  await apiPost('/api/v1/isms/asset-subcategories', {name, category_id: categoryId});
  if (input) input.value = '';
  await reloadAfter('Asset subcategory added');
});

$('licenseForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const licenseId = $('licenseId')?.value || '';
  const payload = {
    name: $('licenseName')?.value || '',
    description: $('licenseDescription')?.value || '',
  };
  if (licenseId) {
    await apiPatch(`/api/v1/isms/licenses/${encodeURIComponent(licenseId)}?framework=${encodeURIComponent(framework)}`, payload);
  } else {
    await apiPost(`/api/v1/isms/licenses?framework=${encodeURIComponent(framework)}`, payload);
  }
  resetLicenseForm();
  await reloadAfter(licenseId ? 'License updated' : 'License added');
});


$('awsAccountForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const accountId = $('awsAccountId')?.value || '';
  const payload = {
    name: $('awsAccountName')?.value || '',
    account_id: $('awsAccountNumber')?.value || null,
    notes: $('awsAccountNotes')?.value || '',
  };
  if (accountId) {
    await apiPatch(`/api/v1/isms/aws-accounts/${encodeURIComponent(accountId)}`, payload);
  } else {
    await apiPost('/api/v1/isms/aws-accounts', payload);
  }
  resetAwsAccountForm();
  await reloadAfter(accountId ? 'hosting account updated' : 'hosting account added');
});

$('appSourceType')?.addEventListener('change', populateAppSourceOptions);

function selectedLinkPayload(prefix) {
  return {
    controls: selectedValues($(`${prefix}Controls`)),
    clauses: selectedValues($(`${prefix}Clauses`)),
  };
}

function selectedControlOnlyPayload(prefix) {
  return {controls: selectedValues($(`${prefix}Controls`))};
}

function normaliseIsmsSection(value) {
  const key = String(value || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  return sectionAliases[key] || sectionAliases[key.replace(/_/g, '')] || null;
}

function sectionFromTabId(id) {
  if (!id) return 'overview';
  for (const [section, tabId] of Object.entries(tabIdBySection)) {
    if (tabId === id) return section;
  }
  return normaliseIsmsSection(String(id || '').replace(/Tab$/, '')) || 'overview';
}

function currentIsmsSection() {
  const active = document.querySelector('#ismsTabs [data-bs-toggle="tab"].active');
  return sectionFromTabId(active?.id) || 'overview';
}

function mergePayload(target, payload) {
  if (!payload || typeof payload !== 'object') return;
  for (const [key, value] of Object.entries(payload)) {
    if (key === 'counts') target.counts = {...(target.counts || {}), ...(value || {})};
    else target[key] = value;
  }
}

function renderMetaSummary() {
  const c = data.counts || {};
  if (!metaEl) return;
  metaEl.textContent = `${data.framework || meta.framework || framework} • ${c.objectives || 0} objectives • ${c.documents || 0} documents • ${c.org_nodes || 0} org nodes • ${c.assets || 0} assets • ${c.licenses || 0} licenses • ${c.access_control_matrix || 0} access rows • ${c.effectiveness_measures || 0} effectiveness measures • ${c.effectiveness_metric_entries || 0} metric entries • ${c.application_configurations || 0} app config rows • ${c.meetings || 0} meetings`;
}

function setSectionLoading(section) {
  const loading = 'Loading this ISMS section…';
  if (section === 'objectives' && rows.objectives) rows.objectives.innerHTML = emptyRow(9, loading);
  if (section === 'documents' && rows.documents) rows.documents.innerHTML = emptyRow(8, loading);
  if (section === 'org' && rows.org) rows.org.innerHTML = emptyRow(8, loading);
  if (section === 'assets' && rows.assets) rows.assets.innerHTML = emptyRow(11, loading);
  if (section === 'access' && rows.access) rows.access.innerHTML = emptyRow(3, loading);
  if (section === 'effectiveness' && rows.effectiveness) rows.effectiveness.innerHTML = emptyRow(8, loading);
  if (section === 'app' && rows.app) rows.app.innerHTML = emptyRow(3, loading);
  if (section === 'meetings' && rows.meetings) rows.meetings.innerHTML = emptyRow(9, loading);
}

function renderSection(section) {
  if (section === 'overview') { renderOverview(); return; }
  if (section === 'objectives') { renderObjectives(); return; }
  if (section === 'documents') { renderDocuments(); return; }
  if (section === 'org') { renderOrg(); renderOrgChart(); return; }
  if (section === 'assets') { renderLicenses(); renderAssets(); return; }
  if (section === 'access') { renderAwsAccounts(); renderAccessControlMatrix(); return; }
  if (section === 'effectiveness') { renderEffectivenessMeasures(); return; }
  if (section === 'app') { renderAppConfig(); return; }
  if (section === 'meetings') { renderMeetings(); return; }
}

function renderAll() {
  renderMetaSummary();
  renderOverview();
  for (const section of ['objectives', 'documents', 'org', 'assets', 'access', 'effectiveness', 'app', 'meetings']) renderSection(section);
  populateForms();
}

async function loadMetaSection(section, {force = false} = {}) {
  if (section === 'soa') return false;
  if (!force && loadedMetaSections.has(section)) return false;
  const payload = await apiGet(`/api/v1/isms/meta?framework=${encodeURIComponent(framework)}&section=${encodeURIComponent(section)}`);
  mergePayload(meta, payload);
  loadedMetaSections.add(section);
  return true;
}

async function loadSummarySection(section, {force = false} = {}) {
  if (section === 'soa') return false;
  if (!force && loadedSummarySections.has(section)) return false;
  const payload = await apiGet(`/api/v1/isms/summary?framework=${encodeURIComponent(framework)}&section=${encodeURIComponent(section)}`);
  mergePayload(data, payload);
  loadedSummarySections.add(section);
  return true;
}

async function ensureSection(section, {force = false} = {}) {
  const target = normaliseIsmsSection(section) || 'overview';
  if (target === 'soa') {
    renderMetaSummary();
    return;
  }
  if (!force && loadedMetaSections.has(target) && loadedSummarySections.has(target)) {
    renderMetaSummary();
    renderSection(target);
    return;
  }
  if (!force && sectionLoadPromises.has(target)) return sectionLoadPromises.get(target);
  const task = (async () => {
    if (status) status.style.display = 'none';
    setSectionLoading(target);
    const metaChanged = await loadMetaSection(target, {force});
    await loadSummarySection(target, {force});
    renderMetaSummary();
    if (metaChanged) populateForms();
    renderSection(target);
  })();
  if (!force) sectionLoadPromises.set(target, task);
  try {
    await task;
  } finally {
    if (!force) sectionLoadPromises.delete(target);
  }
}

function invalidateIsmsCache() {
  loadedMetaSections.clear();
  loadedSummarySections.clear();
  sectionLoadPromises.clear();
}

async function load() {
  try {
    await ensureSection('overview');
  } catch (e) {
    toast(status, `Failed to load ISMS: ${String(e)}`, 'danger');
  }
}

async function reloadAfter(message, section = null) {
  try {
    const target = section || currentIsmsSection();
    invalidateIsmsCache();
    await ensureSection(target, {force: true});
    toast(status, message, 'success');
  } catch (e) {
    toast(status, `Failed to reload ISMS: ${String(e)}`, 'danger');
  }
}

$('objectiveForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const editingId = ev.target.dataset.editingId || null;
  await saveIsms('objective', {
    requirement: $('objRequirement').value,
    goal: $('objGoal').value,
    metric: $('objMetric').value,
    owner_user_id: $('objOwner').value || null,
    completion_target_date: $('objTarget').value || null,
    completion_method: $('objCompletion').value,
    evaluation_method: $('objEvaluation').value,
    resource_requirements_text: $('objResourceText').value,
    resource_user_ids: selectedValues($('objResources')),
    ...selectedLinkPayload('obj'),
  }, editingId);
  resetForm('objective'); await reloadAfter(editingId ? 'ISMS objective updated' : 'ISMS objective created');
});

$('documentForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const editingId = ev.target.dataset.editingId || null;
  const saved = await saveIsms('document', {title: $('docTitle').value, document_type: $('docType').value, external_url: $('docUrl').value || null, description: $('docDescription').value, ...selectedLinkPayload('doc')}, editingId);
  const file = $('docFile')?.files?.[0];
  if (file && saved?.id) {
    const fd = new FormData();
    fd.append('file', file, file.name);
    await apiPostForm(`/api/v1/isms/documents/${encodeURIComponent(saved.id)}/file`, fd);
  }
  resetForm('document'); await reloadAfter(file ? 'ISMS document record saved and file uploaded' : (editingId ? 'ISMS document record updated' : 'ISMS document record created'));
});

$('orgForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const editingId = ev.target.dataset.editingId || null;
  await saveIsms('org', {parent_id: $('orgParent').value || null, name: $('orgName').value, node_type: $('orgType').value || 'role', description: $('orgDescription').value, user_ids: selectedValues($('orgUsers')), ...selectedLinkPayload('org')}, editingId);
  resetForm('org'); await reloadAfter(editingId ? 'Organisation chart node updated' : 'Organisation chart node created');
});

$('assetForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const editingId = ev.target.dataset.editingId || null;
  await saveIsms('asset', {asset: $('assetName').value, category_id: $('assetCategory').value || null, subcategory_id: $('assetSubcategory').value || null, license_id: $('assetLicense').value || null, owner_org_node_id: $('assetOwner').value || null, register_held_by_org_node_id: $('assetRegister').value || null, description: $('assetDescription').value, ...selectedLinkPayload('asset')}, editingId);
  resetForm('asset'); await reloadAfter(editingId ? 'ISMS asset updated' : 'ISMS asset created');
});


$('effectivenessForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const editingId = ev.target.dataset.editingId || null;
  const targetRaw = $('effTargetValue')?.value;
  await saveIsms('effectiveness', {
    summary: $('effSummary').value,
    description: $('effDescription').value,
    effectiveness_measure: $('effMeasure').value,
    metric: $('effMetric').value,
    metric_key: $('effMetricKey').value || null,
    threshold_operator: $('effThresholdOperator').value || '',
    target_value: targetRaw === '' ? null : Number(targetRaw),
    target_unit: $('effTargetUnit').value || '',
    owner_user_id: $('effOwner').value || null,
    frequency: $('effFrequency').value || '',
    notes: $('effNotes').value || '',
    ...selectedControlOnlyPayload('eff'),
  }, editingId);
  resetForm('effectiveness'); await reloadAfter(editingId ? 'Effectiveness measure updated' : 'Effectiveness measure created');
});

$('metricEntryForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const measureId = $('metricMeasureId')?.value || '';
  if (!measureId) throw new Error('Select an effectiveness measure.');
  await apiPost(`/api/v1/isms/effectiveness-measures/${encodeURIComponent(measureId)}/metrics?framework=${encodeURIComponent(framework)}`, metricEntryPayloadFromForm());
  $('metricEntryFormCard').style.display = 'none';
  await reloadAfter('Effectiveness metric recorded');
});

$('cancelMetricEntry')?.addEventListener('click', () => {
  const card = $('metricEntryFormCard');
  if (card) card.style.display = 'none';
});

$('appConfigForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const editingId = ev.target.dataset.editingId || null;
  await saveIsms('app', appPayloadFromForm(), editingId);
  resetForm('app');
  await reloadAfter(editingId ? 'Application configuration matrix row updated' : 'Application configuration matrix row created');
});


$('accessControlForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const editingId = ev.target.dataset.editingId || null;
  await saveIsms('access', accessPayloadFromForm(), editingId);
  resetForm('access');
  await reloadAfter(editingId ? 'Access control matrix row updated' : 'Access control matrix row created');
});

$('meetingForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const editingId = ev.target.dataset.editingId || null;
  await saveIsms('meeting', {title: $('meetingTitle').value || 'ISMS Meeting', date: $('meetingDate').value, start_time: $('meetingStart').value || null, end_time: $('meetingEnd').value || null, attendee_user_ids: selectedValues($('meetingAttendees')), apology_user_ids: selectedValues($('meetingApologies')), links: selectedMeetingLinks(), agenda_minutes_notes: getMeetingNotesValue(), ...selectedLinkPayload('meeting')}, editingId);
  resetForm('meeting'); await reloadAfter(editingId ? 'Meeting minutes updated' : 'Meeting minutes created');
});

$('addMeetingExternalLink')?.addEventListener('click', () => addMeetingExternalLinkRow());
resetMeetingExternalLinks();

document.addEventListener('input', (ev) => {
  const input = ev.target?.closest?.('[data-isms-table-filter]');
  if (input) applyIsmsTableFilter(input);
});
installIsmsSectionChrome();

document.addEventListener('click', async (ev) => {
  const addBtn = ev.target.closest?.('[data-isms-add]');
  const toggleCardBtn = ev.target.closest?.('[data-isms-toggle-card]');
  const filterClearBtn = ev.target.closest?.('[data-isms-filter-clear]');
  const sampleBtn = ev.target.closest?.('[data-audit-sample-entity]');
  const editBtn = ev.target.closest?.('[data-isms-edit]');
  const deleteBtn = ev.target.closest?.('[data-isms-delete]');
  const cancelBtn = ev.target.closest?.('[data-isms-cancel-edit]');
  const licenseEditBtn = ev.target.closest?.('[data-license-edit]');
  const licenseDeleteBtn = ev.target.closest?.('[data-license-delete]');
  const awsAccountEditBtn = ev.target.closest?.('[data-aws-account-edit]');
  const awsAccountDeleteBtn = ev.target.closest?.('[data-aws-account-delete]');
  const removeMeetingLinkBtn = ev.target.closest?.('[data-remove-meeting-link]');
  const effectivenessRecordBtn = ev.target.closest?.('[data-effectiveness-record]');
  const metricEntryDeleteBtn = ev.target.closest?.('[data-metric-entry-delete]');
  try {
    if (addBtn) {
      const kind = addBtn.getAttribute('data-isms-add');
      await ensureSection(sectionByKind[kind] || currentIsmsSection());
      showNewIsmsForm(kind);
      return;
    }
    if (toggleCardBtn) {
      toggleAuxCard(toggleCardBtn.getAttribute('data-isms-toggle-card'));
      return;
    }
    if (filterClearBtn) {
      const input = $(filterClearBtn.getAttribute('data-isms-filter-clear'));
      if (input) { input.value = ''; applyIsmsTableFilter(input); input.focus(); }
      return;
    }
    if (removeMeetingLinkBtn) {
      const row = removeMeetingLinkBtn.closest('.meeting-external-link-row');
      row?.remove();
      if (!document.querySelector('.meeting-external-link-row')) addMeetingExternalLinkRow();
      return;
    }
    if (effectivenessRecordBtn) {
      showMetricEntryForm(effectivenessRecordBtn.getAttribute('data-effectiveness-record'));
      return;
    }
    if (metricEntryDeleteBtn) {
      const id = metricEntryDeleteBtn.getAttribute('data-metric-entry-delete');
      if (!confirm('Delete this effectiveness metric entry?')) return;
      await apiDelete(`/api/v1/isms/effectiveness-metrics/${encodeURIComponent(id)}?framework=${encodeURIComponent(framework)}`);
      await reloadAfter('Effectiveness metric entry deleted');
      return;
    }
    if (sampleBtn) {
      sampleIsmsEntity(sampleBtn.getAttribute('data-audit-sample-entity'), sampleBtn.getAttribute('data-isms-id'));
      return;
    }
    if (licenseEditBtn) {
      const id = licenseEditBtn.getAttribute('data-license-edit');
      const item = licenses().find((l) => String(l.id) === String(id));
      if (item) resetLicenseForm(item);
      $('licenseName')?.focus();
      return;
    }
    if (licenseDeleteBtn) {
      const id = licenseDeleteBtn.getAttribute('data-license-delete');
      const item = licenses().find((l) => String(l.id) === String(id));
      const label = item?.name || 'this license';
      if (!confirm(`Delete ${label}? Licenses related to assets cannot be deleted.`)) return;
      await apiDelete(`/api/v1/isms/licenses/${encodeURIComponent(id)}?framework=${encodeURIComponent(framework)}`);
      resetLicenseForm();
      await reloadAfter('License deleted');
      return;
    }
    if (awsAccountEditBtn) {
      const id = awsAccountEditBtn.getAttribute('data-aws-account-edit');
      const item = awsAccounts().find((a) => String(a.id) === String(id));
      if (item) resetAwsAccountForm(item);
      $('awsAccountName')?.focus();
      return;
    }
    if (awsAccountDeleteBtn) {
      const id = awsAccountDeleteBtn.getAttribute('data-aws-account-delete');
      const item = awsAccounts().find((a) => String(a.id) === String(id));
      const label = item?.name || 'this hosting account';
      if (!confirm(`Delete ${label}? This removes it from access control matrix rows.`)) return;
      await apiDelete(`/api/v1/isms/aws-accounts/${encodeURIComponent(id)}`);
      resetAwsAccountForm();
      await reloadAfter('hosting account deleted');
      return;
    }
    if (editBtn) {
      const kind = editBtn.getAttribute('data-isms-edit');
      const id = editBtn.getAttribute('data-isms-id');
      populateEditForm(kind, findItem(kind, id));
      return;
    }
    if (deleteBtn) {
      await deleteIsms(deleteBtn.getAttribute('data-isms-delete'), deleteBtn.getAttribute('data-isms-id'));
      return;
    }
    if (cancelBtn) {
      resetForm(cancelBtn.getAttribute('data-isms-cancel-edit'));
    }
  } catch (e) {
    toast(status, `ISMS action failed: ${String(e)}`, 'danger');
  }
});

async function activateInitialTab() {
  const params = new URLSearchParams(window.location.search || '');
  const edit = params.get('edit');
  let section = normaliseIsmsSection(params.get('tab')) || 'overview';
  if (edit && !params.get('tab')) {
    const [kind] = String(edit).split(':', 2);
    section = sectionByKind[kind] || section;
  }
  const measureId = params.get('measure') || params.get('effectiveness_measure');
  const recordId = params.get('record') || params.get('record_metric');
  if ((measureId || recordId) && !params.get('tab')) section = 'effectiveness';

  const target = document.getElementById(tabIdBySection[section] || 'overviewTab');
  if (target && window.bootstrap?.Tab && !target.classList.contains('active')) {
    window.bootstrap.Tab.getOrCreateInstance(target).show();
  }
  await ensureSection(section);

  if (edit) {
    const [kind, id] = String(edit).split(':', 2);
    const item = findItem(kind, id);
    if (item) populateEditForm(kind, item);
  }
  if (recordId) {
    window.setTimeout(() => showMetricEntryForm(recordId), 180);
  } else if (measureId) {
    window.setTimeout(() => {
      const row = document.querySelector(`[data-isms-row="effectiveness"][data-isms-id="${CSS.escape(String(measureId))}"]`);
      if (!row) return;
      row.classList.add('table-warning');
      row.scrollIntoView({behavior: 'smooth', block: 'center'});
    }, 180);
  }
}

for (const btn of Array.from(document.querySelectorAll('#ismsTabs [data-bs-toggle="tab"]'))) {
  btn.addEventListener('shown.bs.tab', () => {
    const section = sectionFromTabId(btn.id);
    const tabName = section === 'app' ? 'appconfig' : section;
    const url = new URL(window.location.href);
    url.searchParams.set('tab', tabName);
    window.history.replaceState({}, '', url.toString());
    ensureSection(section).catch((e) => toast(status, `Failed to load ISMS section: ${String(e)}`, 'danger'));
  });
}

load().then(() => activateInitialTab()).catch((e) => toast(status, `Failed to load ISMS: ${String(e)}`, 'danger'));

