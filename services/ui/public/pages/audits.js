import {initNavbar, apiGet, apiPost, apiDelete, esc, fmtTs, toast, debounce, qs, getCurrentFramework, withFramework, wireIsoDateTextField, isoDateToDisplay, dateFormatPlaceholder, initRichTextEditors, setRichTextEditorValue, syncRichTextEditor, syncRichTextEditors, userPillHtml} from '/app.js';

const me = await initNavbar();
const status = document.getElementById('status');
const rows = document.getElementById('rows');
const createCard = document.getElementById('createCard');
const showCreate = document.getElementById('showCreate');
const cancelCreate = document.getElementById('cancelCreate');
const createForm = document.getElementById('createForm');
const newTitle = document.getElementById('newTitle');
const newFramework = document.getElementById('newFramework');
const newType = document.getElementById('newType');
const newStart = document.getElementById('newStart');
const newEnd = document.getElementById('newEnd');
const newNotes = document.getElementById('newNotes');
const newExecutiveSummary = document.getElementById('newExecutiveSummary');
const newDocumentFilter = document.getElementById('newDocumentFilter');
const newClauseFilter = document.getElementById('newClauseFilter');
const newControlFilter = document.getElementById('newControlFilter');
const newDocumentsList = document.getElementById('newDocumentsList');
const newClausesList = document.getElementById('newClausesList');
const newControlsList = document.getElementById('newControlsList');
const newDocumentCount = document.getElementById('newDocumentCount');
const newClauseCount = document.getElementById('newClauseCount');
const newControlCount = document.getElementById('newControlCount');
const q = document.getElementById('q');
const statusFilter = document.getElementById('statusFilter');
const scheduledRows = document.getElementById('scheduledRows');
const scheduleForm = document.getElementById('scheduleForm');
const schedStart = document.getElementById('schedStart');
const schedEnd = document.getElementById('schedEnd');
const schedUntil = document.getElementById('schedUntil');
const schedStartText = document.getElementById('schedStartText');
const schedEndText = document.getElementById('schedEndText');
const schedUntilText = document.getElementById('schedUntilText');
const schedDateRule = document.getElementById('schedDateRule');
const schedExactDateFields = document.getElementById('schedExactDateFields');
const schedFuzzyDateFields = document.getElementById('schedFuzzyDateFields');
const schedFuzzyMonth = document.getElementById('schedFuzzyMonth');
const schedFuzzyYear = document.getElementById('schedFuzzyYear');
const schedDatePreview = document.getElementById('schedDatePreview');
const schedAttendeeUserId = document.getElementById('schedAttendeeUserId');
const schedDocuments = document.getElementById('schedDocuments');
const coverageMode = document.getElementById('coverageMode');
const coverageStart = document.getElementById('coverageStart');
const coverageEnd = document.getElementById('coverageEnd');
const coverageQ = document.getElementById('coverageQ');
const coverageReload = document.getElementById('coverageReload');
const coverageSummary = document.getElementById('coverageSummary');
const coverageIncludedRows = document.getElementById('coverageIncludedRows');
const coverageGapRows = document.getElementById('coverageGapRows');
const coverageChart = document.getElementById('coverageChart');
const coverageGapMatrix = document.getElementById('coverageGapMatrix');

let createDocuments = [];
let createClauses = [];
let createControls = [];
let selectedCreateDocumentIds = new Set();
let selectedCreateClauseIds = new Set();
let selectedCreateControlIds = new Set();
let createClauseControlMap = new Map();
let createScopeFramework = '';
let scheduledAttendees = [];
let scheduledAttendeeUsers = [];
let scheduledTemplates = [];
let scheduledOccurrences = [];
let scheduledDocuments = [];
let coverageData = null;

const schedStartWire = wireIsoDateTextField(schedStart, schedStartText, document.getElementById('schedStartPick'), {dispatchChangeOnText: true});
const schedEndWire = wireIsoDateTextField(schedEnd, schedEndText, document.getElementById('schedEndPick'), {dispatchChangeOnText: true});
const schedUntilWire = wireIsoDateTextField(schedUntil, schedUntilText, document.getElementById('schedUntilPick'), {dispatchChangeOnText: true});
for (const el of [schedStartText, schedEndText, schedUntilText]) {
  if (el) el.setAttribute('placeholder', dateFormatPlaceholder());
}

initRichTextEditors(document);

function canViewAudits() {
  return Boolean(me?.can_view_audits || me?.can_manage_audits || me?.is_admin);
}

function canManageAudits() {
  return Boolean(me?.can_manage_audits || me?.is_admin);
}

function canDeleteAudits() {
  return Boolean(me?.is_admin);
}

function ensureAuditRead() {
  if (canViewAudits()) return true;
  toast(status, 'audits.read permission required.', 'danger');
  return false;
}

function ensureAuditManage() {
  if (canManageAudits()) return true;
  toast(status, 'audits.manage permission required.', 'danger');
  return false;
}

function statusLabel(s) {
  const v = String(s || 'open');
  if (v === 'in_progress') return 'In progress';
  return v ? v.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase()) : 'Open';
}

function auditTypeLabel(v) {
  return String(v || 'internal').toLowerCase() === 'external' ? 'External' : 'Internal';
}

function _graphRawId(value) {
  const s = String(value || '');
  const idx = s.indexOf(':');
  return idx >= 0 ? s.slice(idx + 1) : s;
}

function _buildCreateClauseControlMap(graphData) {
  const out = new Map();
  for (const link of graphData?.links || []) {
    const clauseId = _graphRawId(link.source);
    const controlId = _graphRawId(link.target);
    if (!clauseId || !controlId) continue;
    if (!out.has(clauseId)) out.set(clauseId, new Set());
    out.get(clauseId).add(controlId);
  }
  return out;
}

function addScopeLinkedToCreateDocument(documentId) {
  const doc = createDocuments.find((d) => String(d.id) === String(documentId));
  let added = 0;
  for (const clause of doc?.clauses || []) {
    const id = String(clause.id || '');
    if (!id) continue;
    if (!selectedCreateClauseIds.has(id)) {
      selectedCreateClauseIds.add(id);
      added += 1;
    }
    added += addControlsLinkedToCreateClause(id);
  }
  for (const control of doc?.controls || []) {
    const id = String(control.id || '');
    if (id && !selectedCreateControlIds.has(id)) {
      selectedCreateControlIds.add(id);
      added += 1;
    }
  }
  return added;
}

function addControlsLinkedToCreateClause(clauseId) {
  const linked = createClauseControlMap.get(String(clauseId)) || new Set();
  let added = 0;
  for (const controlId of linked) {
    if (!selectedCreateControlIds.has(String(controlId))) {
      selectedCreateControlIds.add(String(controlId));
      added += 1;
    }
  }
  return added;
}

function matchesFilter(item, filter) {
  if (!filter) return true;
  return String(item.ref || '').toLowerCase().includes(filter) || String(item.title || '').toLowerCase().includes(filter);
}

function clauseIndent(c) {
  const ref = String(c.ref || '');
  return Math.min(Math.max(ref.split('.').length - 1, 0), 3);
}

function updateCreateScopeCounts() {
  if (newDocumentCount) newDocumentCount.textContent = `${selectedCreateDocumentIds.size} documents`;
  if (newClauseCount) newClauseCount.textContent = `${selectedCreateClauseIds.size} clauses`;
  if (newControlCount) newControlCount.textContent = `${selectedCreateControlIds.size} controls`;
}

function renderCreateDocuments() {
  if (!newDocumentsList) return;
  const filter = (newDocumentFilter?.value || '').trim().toLowerCase();
  const items = createDocuments.filter((d) => matchesFilter({ref: d.document_type, title: d.title}, filter));
  newDocumentsList.innerHTML = items.map((d) => {
    const id = String(d.id);
    const checked = selectedCreateDocumentIds.has(id) ? 'checked' : '';
    const linked = `${(d.clauses || []).length} clauses, ${(d.controls || []).length} controls`;
    return `<div class="col-12">
      <label class="border rounded p-2 d-flex gap-2 align-items-start h-100">
        <input class="form-check-input mt-1" type="checkbox" data-new-document-id="${esc(id)}" ${checked}>
        <span><span class="fw-semibold">${esc(d.title || 'Document')}</span> <span class="badge text-bg-light ms-1">${esc(d.document_type || 'document')}</span><div class="small-muted">${esc(linked)}</div></span>
      </label>
    </div>`;
  }).join('') || '<div class="small-muted">No ISMS documents match.</div>';
  newDocumentsList.querySelectorAll('[data-new-document-id]').forEach((cb) => cb.addEventListener('change', () => {
    const id = cb.getAttribute('data-new-document-id');
    if (cb.checked) {
      selectedCreateDocumentIds.add(id);
      const added = addScopeLinkedToCreateDocument(id);
      if (added > 0) { renderCreateClauses(); renderCreateControls(); }
    } else {
      selectedCreateDocumentIds.delete(id);
    }
    updateCreateScopeCounts();
  }));
  updateCreateScopeCounts();
}

function renderCreateClauses() {
  if (!newClausesList) return;
  const filter = (newClauseFilter?.value || '').trim().toLowerCase();
  const items = createClauses.filter((c) => matchesFilter(c, filter));
  newClausesList.innerHTML = items.map((c) => {
    const id = String(c.id);
    const checked = selectedCreateClauseIds.has(id) ? 'checked' : '';
    const pad = clauseIndent(c) * 1.1;
    return `<div class="col-12">
      <label class="border rounded p-2 d-flex gap-2 align-items-start h-100" style="margin-left:${pad}rem">
        <input class="form-check-input mt-1" type="checkbox" data-new-clause-id="${esc(id)}" ${checked}>
        <span><span class="fw-semibold">${esc(c.ref || '')}</span> <span class="small-muted">${esc(c.title || '')}</span></span>
      </label>
    </div>`;
  }).join('') || '<div class="small-muted">No clauses match.</div>';
  newClausesList.querySelectorAll('[data-new-clause-id]').forEach((cb) => cb.addEventListener('change', () => {
    const id = cb.getAttribute('data-new-clause-id');
    if (cb.checked) {
      selectedCreateClauseIds.add(id);
      const added = addControlsLinkedToCreateClause(id);
      if (added > 0) renderCreateControls();
    } else {
      selectedCreateClauseIds.delete(id);
    }
    updateCreateScopeCounts();
  }));
  updateCreateScopeCounts();
}

function renderCreateControls() {
  if (!newControlsList) return;
  const filter = (newControlFilter?.value || '').trim().toLowerCase();
  const items = createControls.filter((c) => matchesFilter(c, filter));
  newControlsList.innerHTML = items.map((c) => {
    const id = String(c.id);
    const checked = selectedCreateControlIds.has(id) ? 'checked' : '';
    return `<div class="col-12 col-lg-6">
      <label class="border rounded p-2 d-flex gap-2 align-items-start h-100">
        <input class="form-check-input mt-1" type="checkbox" data-new-control-id="${esc(id)}" ${checked}>
        <span><span class="fw-semibold">${esc(c.ref || '')}</span> <span class="small-muted">${esc(c.title || '')}</span></span>
      </label>
    </div>`;
  }).join('') || '<div class="small-muted">No controls match.</div>';
  newControlsList.querySelectorAll('[data-new-control-id]').forEach((cb) => cb.addEventListener('change', () => {
    const id = cb.getAttribute('data-new-control-id');
    if (cb.checked) selectedCreateControlIds.add(id);
    else selectedCreateControlIds.delete(id);
    updateCreateScopeCounts();
  }));
  updateCreateScopeCounts();
}

function renderCreateScope() {
  renderCreateDocuments();
  renderCreateClauses();
  renderCreateControls();
}

async function loadCreateScope({resetSelection = false} = {}) {
  const fw = (newFramework?.value || getCurrentFramework() || '').trim();
  if (!fw || createScopeFramework === fw) {
    renderCreateScope();
    return;
  }
  if (resetSelection) {
    selectedCreateDocumentIds = new Set();
    selectedCreateClauseIds = new Set();
    selectedCreateControlIds = new Set();
  }
  if (newDocumentsList) newDocumentsList.innerHTML = '<div class="small-muted">Loading ISMS documents…</div>';
  if (newClausesList) newClausesList.innerHTML = '<div class="small-muted">Loading clauses…</div>';
  if (newControlsList) newControlsList.innerHTML = '<div class="small-muted">Loading controls…</div>';
  try {
    const [controlsData, clausesData, graphData, documentsData] = await Promise.all([
      apiGet(`/api/v1/controls?framework=${encodeURIComponent(fw)}&limit=5000`),
      apiGet(`/api/v1/clauses?framework=${encodeURIComponent(fw)}`),
      apiGet(`/api/v1/graph/clause_control?framework=${encodeURIComponent(fw)}`),
      apiGet(`/api/v1/isms/documents?framework=${encodeURIComponent(fw)}&limit=1000`),
    ]);
    createScopeFramework = fw;
    createDocuments = documentsData?.items || [];
    createControls = (controlsData?.items || []).filter((c) => String(c.type || '') !== 'clause');
    createClauses = clausesData?.items || [];
    createClauseControlMap = _buildCreateClauseControlMap(graphData);
    renderCreateScope();
  } catch (e) {
    createScopeFramework = '';
    createClauseControlMap = new Map();
    if (newDocumentsList) newDocumentsList.innerHTML = `<div class="text-danger small">${esc(String(e))}</div>`;
    if (newClausesList) newClausesList.innerHTML = `<div class="text-danger small">${esc(String(e))}</div>`;
    if (newControlsList) newControlsList.innerHTML = `<div class="text-danger small">${esc(String(e))}</div>`;
  }
}

function showCreateCard() {
  if (!ensureAuditManage()) return;
  if (newFramework) newFramework.value = getCurrentFramework();
  if (newType) newType.value = 'internal';
  setRichTextEditorValue(newExecutiveSummary, '');
  setRichTextEditorValue(newNotes, '');
  createScopeFramework = '';
  selectedCreateDocumentIds = new Set();
  selectedCreateClauseIds = new Set();
  selectedCreateControlIds = new Set();
  createCard.style.display = '';
  loadCreateScope({resetSelection: true});
  setTimeout(() => newTitle?.focus(), 50);
}

function renderAudits(items) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) {
    rows.innerHTML = `<tr><td colspan="10" class="p-4 small-muted">No audits found.</td></tr>`;
    return;
  }
  rows.innerHTML = arr.map((a) => {
    const url = withFramework(`/audit.html?id=${encodeURIComponent(a.id)}`, a.framework_slug || getCurrentFramework());
    const dates = [a.start_date, a.end_date].filter(Boolean).join(' → ') || '—';
    const scope = `${a.scope_count ?? 0}`;
    const scopeBreakdown = `${a.document_scope_count ?? 0} documents, ${a.clause_scope_count ?? 0} clauses, ${a.control_scope_count ?? 0} controls`;
    return `<tr>
      <td>
        <a class="fw-semibold" href="${esc(url)}">${esc(a.title || 'Audit')}</a>
        <div class="small-muted">Updated ${esc(fmtTs(a.updated_at))}</div>
      </td>
      <td><span class="badge badge-soft">${esc(a.framework_slug || '')}</span></td>
      <td><span class="badge text-bg-light">${esc(auditTypeLabel(a.audit_type))}</span></td>
      <td class="small-muted">${esc(dates)}</td>
      <td>${userPillHtml(a.created_by_username || a.created_by_user_id)}</td>
      <td>${esc(a.evidence_count ?? 0)}</td>
      <td><span title="${esc(scopeBreakdown)}">${esc(scope)}</span><div class="small-muted">${esc(scopeBreakdown)}</div></td>
      <td>${esc(a.finding_count ?? 0)}</td>
      <td><span class="badge text-bg-light">${esc(statusLabel(a.status))}</span></td>
      <td class="text-end">
        <div class="btn-group btn-group-sm">
          <a class="btn btn-outline-primary" href="${esc(url)}">Open</a>
          ${canDeleteAudits() && String(a.status || '').toLowerCase() !== 'template' ? `<button class="btn btn-outline-danger" type="button" data-delete-audit="${esc(a.id)}" data-delete-audit-title="${esc(a.title || 'Audit')}">Delete</button>` : ''}
        </div>
      </td>
    </tr>`;
  }).join('');
  rows.querySelectorAll('[data-delete-audit]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!canDeleteAudits()) {
      toast(status, 'Admin role required.', 'danger');
      return;
    }
    const title = btn.getAttribute('data-delete-audit-title') || 'this audit';
    if (!confirm(`Delete audit "${title}"? This permanently removes the audit, its scope, attendees, sampled evidence and findings.`)) return;
    try {
      await apiDelete(`/api/v1/audits/${encodeURIComponent(btn.getAttribute('data-delete-audit'))}`);
      toast(status, 'Audit deleted', 'success');
      await loadAudits();
    } catch (e) {
      toast(status, `Failed: ${String(e)}`, 'danger');
    }
  }));
}

async function loadAudits() {
  if (!ensureAuditRead()) return;
  rows.innerHTML = `<tr><td colspan="10" class="p-4 small-muted">Loading…</td></tr>`;
  const params = new URLSearchParams();
  const fw = getCurrentFramework();
  if (fw) params.set('framework', fw);
  const query = (q?.value || '').trim();
  if (query) params.set('q', query);
  const st = (statusFilter?.value || '').trim();
  if (st) params.set('status', st);
  try {
    const data = await apiGet(`/api/v1/audits?${params.toString()}`);
    renderAudits(data?.items || []);
  } catch (e) {
    rows.innerHTML = `<tr><td colspan="10" class="p-4 text-danger">${esc(String(e))}</td></tr>`;
  }
}


function _localIsoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function _daysAgoIso(days) {
  const d = new Date();
  d.setDate(d.getDate() - Number(days || 0));
  return _localIsoDate(d);
}

function _yearStartIso() {
  const d = new Date();
  d.setMonth(0, 1); // January 1
  d.setHours(0, 0, 0, 0);
  return _localIsoDate(d);
}

function setDefaultCoverageDates() {
  if (coverageEnd && !coverageEnd.value) coverageEnd.value = _localIsoDate(new Date());
  if (coverageStart && !coverageStart.value) coverageStart.value = _yearStartIso();
}

function coverageTypeLabel(type) {
  return {controls: 'Control', clauses: 'Clause', documents: 'Document'}[String(type || '')] || 'Item';
}

function coverageItemTitle(type, item) {
  if (type === 'documents') return item?.title || 'Document';
  return `${item?.ref || ''} ${item?.title || ''}`.trim() || 'Item';
}

function coverageItemHref(type, item) {
  const fw = coverageData?.framework_slug || getCurrentFramework();
  if (type === 'controls') return withFramework(`/controls.html?selected=${encodeURIComponent(item?.id || '')}`, fw);
  if (type === 'clauses') return withFramework(`/clauses.html?selected=${encodeURIComponent(item?.id || '')}`, fw);
  if (type === 'documents') return withFramework(`/isms.html?tab=documents&edit=document:${encodeURIComponent(item?.id || '')}`, fw);
  return '#';
}

function flattenCoverage(group, type) {
  return (group?.[type] || []).map((item) => ({type, item}));
}

function coverageSourceHtml(sources) {
  const arr = Array.isArray(sources) ? sources : [];
  if (!arr.length) return '<span class="small-muted">—</span>';
  return arr.slice(0, 8).map((src) => {
    const label = `${src.start_date || src.occurrence_date || ''} ${src.title || 'Audit'}`.trim();
    const href = withFramework(`/audit.html?id=${encodeURIComponent(src.audit_id || src.id || '')}`, src.framework_slug || coverageData?.framework_slug || getCurrentFramework());
    const badge = src.source === 'scheduled' ? 'Scheduled' : 'Completed';
    return `<div class="mb-1"><a href="${esc(href)}">${esc(label)}</a> <span class="badge text-bg-light">${esc(badge)}</span></div>`;
  }).join('') + (arr.length > 8 ? `<div class="small-muted">+${arr.length - 8} more</div>` : '');
}

function coverageMatches(row, query) {
  if (!query) return true;
  const haystack = [
    coverageTypeLabel(row.type),
    coverageItemTitle(row.type, row.item),
    row.item?.description || '',
    ...(row.item?.sources || []).flatMap((s) => [s.title || '', s.start_date || '', s.occurrence_date || '', s.source || '']),
  ].join(' ').toLowerCase();
  return haystack.includes(query);
}

function renderCoverageSummary(data) {
  if (!coverageSummary) return;
  const summary = data?.summary || {};
  coverageSummary.innerHTML = ['controls', 'clauses', 'documents'].map((key) => {
    const row = summary[key] || {};
    const label = key.charAt(0).toUpperCase() + key.slice(1);
    return `<div class="col-12 col-md-4"><div class="card h-100"><div class="card-body">
      <div class="small-muted">${esc(label)}</div>
      <div class="d-flex flex-wrap gap-3 align-items-end">
        <div><div class="fs-4 fw-bold text-success">${esc(row.included || 0)}</div><div class="small-muted">included</div></div>
        <div><div class="fs-4 fw-bold text-danger">${esc(row.gaps || 0)}</div><div class="small-muted">gaps</div></div>
        <div><div class="fs-4 fw-bold">${esc(row.total || 0)}</div><div class="small-muted">total</div></div>
      </div>
    </div></div></div>`;
  }).join('') + `<div class="col-12"><div class="small-muted">Audit scope entries analysed: ${esc(data?.source_audit_count || 0)} audit/template records${data?.scheduled_occurrence_count ? ` across ${esc(data.scheduled_occurrence_count)} scheduled occurrences` : ''}. Window: ${esc(data?.start || '')} → ${esc(data?.end || '')}.</div></div>`;
}


function coverageSummaryRows(data) {
  const summary = data?.summary || {};
  return ['controls', 'clauses', 'documents'].map((key) => {
    const row = summary[key] || {};
    const included = Number(row.included || 0);
    const gaps = Number(row.gaps || 0);
    const total = Number(row.total || included + gaps || 0);
    return {
      key,
      label: key.charAt(0).toUpperCase() + key.slice(1),
      included,
      gaps,
      total,
      pct: total > 0 ? included / total : 0,
    };
  });
}

function renderCoverageVisualisations(data) {
  if (!coverageChart && !coverageGapMatrix) return;
  const d3lib = globalThis.d3;
  if (!d3lib) {
    if (coverageChart) coverageChart.innerHTML = '<div class="small-muted">D3 visualisations are unavailable because D3 was not loaded.</div>';
    if (coverageGapMatrix) coverageGapMatrix.innerHTML = '';
    return;
  }

  const rows = coverageSummaryRows(data);
  const chartWidth = Math.max(520, Math.floor((coverageChart?.clientWidth || 820)));
  const margin = {top: 34, right: 118, bottom: 34, left: 118};
  const barHeight = 34;
  const gap = 22;
  const height = margin.top + margin.bottom + rows.length * (barHeight + gap);
  const innerWidth = chartWidth - margin.left - margin.right;

  const svg = d3lib.select(coverageChart)
    .html('')
    .append('svg')
    .attr('viewBox', `0 0 ${chartWidth} ${height}`)
    .attr('role', 'img')
    .attr('aria-label', 'Audit scope coverage by item type');

  const maxTotal = Math.max(1, ...rows.map((d) => d.total));
  const x = d3lib.scaleLinear().domain([0, maxTotal]).range([0, innerWidth]);

  svg.append('text')
    .attr('x', margin.left)
    .attr('y', 18)
    .attr('class', 'coverage-axis-label')
    .text('Included vs gaps by scope type');

  const legend = svg.append('g').attr('transform', `translate(${chartWidth - 210}, 8)`);
  legend.append('rect').attr('width', 12).attr('height', 12).attr('rx', 2).attr('fill', '#198754');
  legend.append('text').attr('x', 18).attr('y', 10).attr('class', 'coverage-axis-label').text('Included');
  legend.append('rect').attr('x', 96).attr('width', 12).attr('height', 12).attr('rx', 2).attr('fill', '#dc3545');
  legend.append('text').attr('x', 114).attr('y', 10).attr('class', 'coverage-axis-label').text('Gap');

  const groups = svg.selectAll('g.coverage-row')
    .data(rows)
    .join('g')
    .attr('class', 'coverage-row')
    .attr('transform', (d, i) => `translate(${margin.left}, ${margin.top + i * (barHeight + gap)})`);

  groups.append('text')
    .attr('x', -12)
    .attr('y', barHeight / 2 + 5)
    .attr('text-anchor', 'end')
    .attr('class', 'coverage-axis-label')
    .text((d) => d.label);

  groups.append('rect')
    .attr('width', innerWidth)
    .attr('height', barHeight)
    .attr('rx', 8)
    .attr('fill', '#f1f5f9');

  groups.append('rect')
    .attr('width', (d) => x(d.included))
    .attr('height', barHeight)
    .attr('rx', 8)
    .attr('fill', '#198754')
    .append('title')
    .text((d) => `${d.label}: ${d.included} included`);

  groups.append('rect')
    .attr('x', (d) => x(d.included))
    .attr('width', (d) => x(d.gaps))
    .attr('height', barHeight)
    .attr('rx', 8)
    .attr('fill', '#dc3545')
    .append('title')
    .text((d) => `${d.label}: ${d.gaps} gaps`);

  groups.append('text')
    .attr('x', (d) => x(d.included + d.gaps) >= innerWidth - 140 ? innerWidth - 10 : x(d.included + d.gaps) + 8)
    .attr('y', barHeight / 2 + 5)
    .attr('text-anchor', (d) => x(d.included + d.gaps) >= innerWidth - 140 ? 'end' : 'start')
    .attr('class', 'coverage-value')
    .style('fill', (d) => x(d.included + d.gaps) >= innerWidth - 140 ? '#fff' : '#111827')
    .text((d) => `${Math.round(d.pct * 100)}% included · ${d.gaps} gaps`);

  const allItems = [
    ...flattenCoverage(data?.included, 'controls').map((row) => ({...row, covered: true})),
    ...flattenCoverage(data?.included, 'clauses').map((row) => ({...row, covered: true})),
    ...flattenCoverage(data?.included, 'documents').map((row) => ({...row, covered: true})),
    ...flattenCoverage(data?.gaps, 'controls').map((row) => ({...row, covered: false})),
    ...flattenCoverage(data?.gaps, 'clauses').map((row) => ({...row, covered: false})),
    ...flattenCoverage(data?.gaps, 'documents').map((row) => ({...row, covered: false})),
  ];

  const matrixWidth = Math.max(520, Math.floor((coverageGapMatrix?.clientWidth || 820)));
  const cell = 16;
  const cellGap = 4;
  const columns = Math.max(12, Math.floor((matrixWidth - 160) / (cell + cellGap)));
  const grouped = ['controls', 'clauses', 'documents'].map((type) => ({
    type,
    label: coverageTypeLabel(type),
    items: allItems.filter((row) => row.type === type),
  }));
  const maxRows = Math.max(1, ...grouped.map((g) => Math.ceil(g.items.length / columns)));
  const matrixHeight = 38 + grouped.length * (maxRows * (cell + cellGap) + 34);
  const matrixSvg = d3lib.select(coverageGapMatrix)
    .html('')
    .append('svg')
    .attr('viewBox', `0 0 ${matrixWidth} ${matrixHeight}`)
    .attr('role', 'img')
    .attr('aria-label', 'Audit scope gap matrix');

  matrixSvg.append('text')
    .attr('x', 0)
    .attr('y', 18)
    .attr('class', 'coverage-axis-label')
    .text('Gap matrix: red squares need audit scope coverage; green squares have been included.');

  let y = 38;
  grouped.forEach((group) => {
    const gapCount = group.items.filter((row) => !row.covered).length;
    matrixSvg.append('text')
      .attr('x', 0)
      .attr('y', y + 14)
      .attr('class', 'coverage-axis-label')
      .text(`${group.label}s (${gapCount} gaps)`);
    const g = matrixSvg.append('g').attr('transform', `translate(150, ${y})`);
    const links = g.selectAll('a.gap-cell-link')
      .data(group.items)
      .join('a')
      .attr('class', 'gap-cell-link')
      .attr('href', (d) => coverageItemHref(d.type, d.item))
      .attr('aria-label', (d) => `${coverageTypeLabel(d.type)}: ${coverageItemTitle(d.type, d.item)} — ${d.covered ? 'included' : 'gap'}`);
    links.append('rect')
      .attr('class', 'gap-cell')
      .attr('x', (_d, i) => (i % columns) * (cell + cellGap))
      .attr('y', (_d, i) => Math.floor(i / columns) * (cell + cellGap))
      .attr('width', cell)
      .attr('height', cell)
      .attr('rx', 4)
      .attr('fill', (d) => d.covered ? '#198754' : '#dc3545');
    links.append('title')
      .text((d) => `${coverageTypeLabel(d.type)}: ${coverageItemTitle(d.type, d.item)} — ${d.covered ? 'included' : 'gap'}`);
    y += maxRows * (cell + cellGap) + 34;
  });
}

function renderCoverageRows(target, rows, emptyText, isGap = false) {
  if (!target) return;
  const query = String(coverageQ?.value || '').trim().toLowerCase();
  const filtered = rows.filter((row) => coverageMatches(row, query));
  if (!filtered.length) {
    target.innerHTML = `<tr><td colspan="3" class="p-4 small-muted">${esc(emptyText)}</td></tr>`;
    return;
  }
  target.innerHTML = filtered.map((row) => {
    const type = coverageTypeLabel(row.type);
    const title = coverageItemTitle(row.type, row.item);
    const href = coverageItemHref(row.type, row.item);
    const meta = row.type === 'documents'
      ? [row.item?.document_type, row.item?.status].filter(Boolean).join(' · ')
      : row.item?.ref || '';
    return `<tr>
      <td><span class="badge text-bg-light">${esc(type)}</span></td>
      <td><a class="fw-semibold" href="${esc(href)}">${esc(title)}</a>${meta ? `<div class="small-muted">${esc(meta)}</div>` : ''}</td>
      <td>${isGap ? '<span class="small-muted">Not included in selected window</span>' : coverageSourceHtml(row.item?.sources)}</td>
    </tr>`;
  }).join('');
}

function renderCoverage() {
  const data = coverageData || {};
  renderCoverageSummary(data);
  renderCoverageVisualisations(data);
  const included = [
    ...flattenCoverage(data.included, 'controls'),
    ...flattenCoverage(data.included, 'clauses'),
    ...flattenCoverage(data.included, 'documents'),
  ];
  const gaps = [
    ...flattenCoverage(data.gaps, 'controls'),
    ...flattenCoverage(data.gaps, 'clauses'),
    ...flattenCoverage(data.gaps, 'documents'),
  ];
  renderCoverageRows(coverageIncludedRows, included, 'No included scope entries match the selected filters.');
  renderCoverageRows(coverageGapRows, gaps, 'No gaps match the selected filters.', true);
}

async function loadScopeCoverage() {
  if (!coverageIncludedRows || !coverageGapRows) return;
  if (!ensureAuditRead()) return;
  setDefaultCoverageDates();
  coverageIncludedRows.innerHTML = '<tr><td colspan="3" class="p-4 small-muted">Loading…</td></tr>';
  coverageGapRows.innerHTML = '<tr><td colspan="3" class="p-4 small-muted">Loading…</td></tr>';
  const params = new URLSearchParams();
  const fw = getCurrentFramework();
  if (fw) params.set('framework', fw);
  if (coverageMode?.value) params.set('mode', coverageMode.value);
  if (coverageStart?.value) params.set('start', coverageStart.value);
  if (coverageEnd?.value) params.set('end', coverageEnd.value);
  try {
    coverageData = await apiGet(`/api/v1/audits/scope-coverage?${params.toString()}`);
    renderCoverage();
  } catch (e) {
    coverageData = null;
    if (coverageSummary) coverageSummary.innerHTML = '';
    if (coverageChart) coverageChart.innerHTML = '';
    if (coverageGapMatrix) coverageGapMatrix.innerHTML = '';
    coverageIncludedRows.innerHTML = `<tr><td colspan="3" class="p-4 text-danger">${esc(String(e))}</td></tr>`;
    coverageGapRows.innerHTML = `<tr><td colspan="3" class="p-4 text-danger">${esc(String(e))}</td></tr>`;
  }
}

function _todayIsoDate() {
  return new Date().toISOString().slice(0, 10);
}

function _isFuzzyScheduleDateRule(rule) {
  return String(rule || 'exact') !== 'exact';
}

function _fuzzyScheduleRuleLabel(rule) {
  return {
    first_weekday_of_month: 'first weekday',
    first_monday_of_month: 'first Monday',
    first_tuesday_of_month: 'first Tuesday',
    first_wednesday_of_month: 'first Wednesday',
    first_thursday_of_month: 'first Thursday',
    first_friday_of_month: 'first Friday',
  }[String(rule || '')] || 'first weekday';
}

function _firstScheduleRuleDateIso(year, month, rule) {
  const y = Number(year) || new Date().getFullYear();
  const m = Math.max(1, Math.min(12, Number(month) || 1));
  const d = new Date(Date.UTC(y, m - 1, 1));
  const targetWeekdays = {
    first_monday_of_month: 1,
    first_tuesday_of_month: 2,
    first_wednesday_of_month: 3,
    first_thursday_of_month: 4,
    first_friday_of_month: 5,
  };
  const target = targetWeekdays[String(rule || '')];
  if (target) {
    while (d.getUTCDay() !== target) d.setUTCDate(d.getUTCDate() + 1);
  } else {
    while (d.getUTCDay() === 0 || d.getUTCDay() === 6) d.setUTCDate(d.getUTCDate() + 1);
  }
  return d.toISOString().slice(0, 10);
}

function updateScheduleDateMode() {
  const mode = schedDateRule?.value || 'exact';
  const fuzzy = _isFuzzyScheduleDateRule(mode);
  if (schedExactDateFields) schedExactDateFields.style.display = fuzzy ? 'none' : '';
  if (schedFuzzyDateFields) schedFuzzyDateFields.style.display = fuzzy ? '' : 'none';
  const recurrence = document.getElementById('schedRecurrence');
  const weekly = recurrence?.querySelector('option[value="weekly"]');
  if (weekly) weekly.disabled = fuzzy;
  if (fuzzy && recurrence?.value === 'weekly') recurrence.value = 'yearly';
  if (fuzzy && recurrence?.value === 'once') recurrence.value = 'yearly';

  if (fuzzy) {
    const year = Number(schedFuzzyYear?.value || new Date().getFullYear());
    const month = Number(schedFuzzyMonth?.value || (new Date().getMonth() + 1));
    const iso = _firstScheduleRuleDateIso(year, month, mode);
    const label = _fuzzyScheduleRuleLabel(mode);
    const monthLabel = document.getElementById('schedFuzzyMonthLabel');
    if (monthLabel) monthLabel.textContent = `${label.charAt(0).toUpperCase()}${label.slice(1)} of`;
    if (schedStart) schedStart.value = iso;
    if (schedStartText) schedStartText.value = isoDateToDisplay(iso);
    if (schedDatePreview) {
      schedDatePreview.textContent = `First run will be ${isoDateToDisplay(iso)}; future runs keep the ${label} rule.`;
    }
  } else if (schedDatePreview) {
    schedDatePreview.textContent = '';
  }
}

function _scheduleStartForSubmit() {
  updateScheduleDateMode();
  if (_isFuzzyScheduleDateRule(schedDateRule?.value || 'exact')) {
    return schedStart?.value || '';
  }
  const ok = schedStartWire?.syncHiddenFromText?.();
  if (ok === false) return '';
  return schedStart?.value || '';
}

function _monthValue(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function _monthRange(monthValue) {
  const [yRaw, mRaw] = String(monthValue || _monthValue()).split('-');
  const y = Number(yRaw) || new Date().getFullYear();
  const m = Math.max(1, Math.min(12, Number(mRaw) || (new Date().getMonth() + 1)));
  const start = new Date(y, m - 1, 1);
  const end = new Date(y, m, 0);
  return {
    start,
    end,
    startIso: `${y}-${String(m).padStart(2, '0')}-01`,
    endIso: `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, '0')}-${String(end.getDate()).padStart(2, '0')}`,
  };
}

function _shiftMonth(value, delta) {
  const [yRaw, mRaw] = String(value || _monthValue()).split('-');
  const d = new Date(Number(yRaw) || new Date().getFullYear(), (Number(mRaw) || 1) - 1 + delta, 1);
  return _monthValue(d);
}

function recurrenceLabel(a) {
  const recur = String(a?.schedule_recurrence || 'once');
  const interval = Number(a?.schedule_interval || 1);
  const labels = {once: 'Once', weekly: 'Weekly', monthly: 'Monthly', quarterly: 'Quarterly', yearly: 'Yearly'};
  const base = labels[recur] || recur;
  const recurText = interval > 1 && recur !== 'once' ? `${base} × ${interval}` : base;
  const dateRule = String(a?.schedule_date_rule || 'exact');
  if (_isFuzzyScheduleDateRule(dateRule)) {
    const months = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const month = months[Number(a?.schedule_anchor_month || 0)] || 'month';
    return `${recurText} · ${_fuzzyScheduleRuleLabel(dateRule)} of ${month}`;
  }
  return recurText;
}

function renderScheduledAttendeeUserOptions() {
  const sel = schedAttendeeUserId;
  if (!sel) return;
  const cur = sel.value || '';
  const rows = (scheduledAttendeeUsers || []).slice().sort((a, b) => String(a.username || '').localeCompare(String(b.username || '')));
  sel.innerHTML = `<option value="">Custom / external attendee</option>${rows.map((u) => `<option value="${esc(u.id)}">${esc(u.username)}${u.email ? ` · ${esc(u.email)}` : ''}</option>`).join('')}`;
  sel.value = rows.some((u) => String(u.id) === cur) ? cur : '';
}

async function loadScheduledAttendeeUsers() {
  if (!canManageAudits()) return;
  try {
    const data = await apiGet('/api/v1/audits/users');
    scheduledAttendeeUsers = Array.isArray(data?.items) ? data.items : [];
  } catch (e) {
    scheduledAttendeeUsers = [];
    toast(status, `Failed to load Keen users for scheduled audit attendees: ${String(e)}`, 'warning');
  }
  renderScheduledAttendeeUserOptions();
}

function selectedScheduledAttendeeUser() {
  const id = schedAttendeeUserId?.value || '';
  if (!id) return null;
  return (scheduledAttendeeUsers || []).find((u) => String(u.id) === String(id)) || null;
}

function syncScheduledAttendeeUserSelection() {
  const user = selectedScheduledAttendeeUser();
  const nameEl = document.getElementById('schedAttendeeName');
  const emailEl = document.getElementById('schedAttendeeEmail');
  if (user && nameEl && !String(nameEl.value || '').trim()) {
    nameEl.value = user.username || '';
  }
  if (user && emailEl && !String(emailEl.value || '').trim()) {
    emailEl.value = user.email || '';
  }
}

function renderScheduledAttendees() {
  const el = document.getElementById('schedAttendees');
  if (!el) return;
  if (!scheduledAttendees.length) {
    el.innerHTML = 'No attendees added.';
    return;
  }
  el.innerHTML = `<div class="list-group">${scheduledAttendees.map((a, idx) => {
    const meta = [a.email, a.role].filter(Boolean);
    const who = a.username ? userPillHtml(a) : esc(a.name || a.email || 'Attendee');
    return `
    <div class="list-group-item d-flex justify-content-between gap-2 align-items-start py-2">
      <div>
        <div class="fw-semibold">${who}</div>
        <div class="small-muted">${esc(meta.join(' · ') || 'No email')}</div>
      </div>
      <button class="btn btn-sm btn-outline-danger" type="button" data-sched-attendee-remove="${idx}">Remove</button>
    </div>`;
  }).join('')}</div>`;
  el.querySelectorAll('[data-sched-attendee-remove]').forEach((btn) => btn.addEventListener('click', () => {
    scheduledAttendees.splice(Number(btn.getAttribute('data-sched-attendee-remove')), 1);
    renderScheduledAttendees();
  }));
}

function renderScheduledRows(items) {
  if (!scheduledRows) return;
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) {
    scheduledRows.innerHTML = '<tr><td colspan="5" class="p-4 small-muted">No scheduled audit templates yet.</td></tr>';
    return;
  }
  scheduledRows.innerHTML = arr.map((a) => {
    const url = withFramework(`/audit.html?id=${encodeURIComponent(a.id)}`, a.framework_slug || getCurrentFramework());
    const attendees = Array.isArray(a.attendees) ? a.attendees : [];
    return `<tr>
      <td>
        <a class="fw-semibold" href="${esc(url)}">${esc(a.title || 'Scheduled audit')}</a>
        <div class="small-muted">${esc(auditTypeLabel(a.audit_type))} · ${esc(a.framework_slug || '')}</div>
      </td>
      <td>${esc(a.schedule_next_run_date || '—')}</td>
      <td>${esc(recurrenceLabel(a))}${a.schedule_until_date ? `<div class="small-muted">Until ${esc(a.schedule_until_date)}</div>` : ''}</td>
      <td>${esc(attendees.length)}</td>
      <td class="text-end">
        <div class="btn-group btn-group-sm">
          <a class="btn btn-outline-primary" href="${esc(url)}">Open template</a>
          ${canManageAudits() ? `<button class="btn btn-outline-danger" type="button" data-delete-schedule="${esc(a.id)}">Delete</button>` : ''}
        </div>
      </td>
    </tr>`;
  }).join('');
  scheduledRows.querySelectorAll('[data-delete-schedule]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!ensureAuditManage()) return;
    if (!confirm('Delete this scheduled audit template?')) return;
    try {
      await apiDelete(`/api/v1/audits/scheduled/${encodeURIComponent(btn.getAttribute('data-delete-schedule'))}`);
      toast(status, 'Scheduled audit template deleted', 'success');
      await loadScheduledAudits();
    } catch (e) {
      toast(status, `Failed: ${String(e)}`, 'danger');
    }
  }));
}

function renderScheduleCalendar(occurrences) {
  const el = document.getElementById('scheduleCalendar');
  const monthEl = document.getElementById('schedMonth');
  if (!el || !monthEl) return;
  const {start, end} = _monthRange(monthEl.value);
  const byDate = new Map();
  for (const occ of occurrences || []) {
    const key = occ.date;
    if (!byDate.has(key)) byDate.set(key, []);
    byDate.get(key).push(occ);
  }
  const first = new Date(start);
  first.setDate(first.getDate() - first.getDay());
  const last = new Date(end);
  last.setDate(last.getDate() + (6 - last.getDay()));
  const days = [];
  for (const d = new Date(first); d <= last; d.setDate(d.getDate() + 1)) {
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    days.push({iso, day: d.getDate(), inMonth: d.getMonth() === start.getMonth(), items: byDate.get(iso) || []});
  }
  const dow = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  el.innerHTML = `
    <div class="schedule-calendar-grid schedule-calendar-head">${dow.map((d) => `<div>${d}</div>`).join('')}</div>
    <div class="schedule-calendar-grid">${days.map((d) => `
      <div class="schedule-calendar-day ${d.inMonth ? '' : 'muted'}">
        <div class="schedule-calendar-date">${d.day}</div>
        ${d.items.slice(0, 4).map((x) => `<div class="schedule-calendar-event" title="${esc(x.title || '')}">${esc(x.title || 'Audit')}</div>`).join('')}
        ${d.items.length > 4 ? `<div class="small-muted">+${d.items.length - 4} more</div>` : ''}
      </div>`).join('')}</div>`;
}

async function loadScheduledAudits() {
  if (!canViewAudits()) return;
  if (!scheduledRows) return;
  scheduledRows.innerHTML = '<tr><td colspan="5" class="p-4 small-muted">Loading…</td></tr>';
  const monthEl = document.getElementById('schedMonth');
  if (monthEl && !monthEl.value) monthEl.value = _monthValue();
  const range = _monthRange(monthEl?.value || _monthValue());
  const params = new URLSearchParams({start: range.startIso, end: range.endIso});
  try {
    const data = await apiGet(`/api/v1/audits/scheduled?${params.toString()}`);
    scheduledTemplates = data?.items || [];
    scheduledOccurrences = data?.occurrences || [];
    renderScheduledRows(scheduledTemplates);
    renderScheduleCalendar(scheduledOccurrences);
  } catch (e) {
    scheduledRows.innerHTML = `<tr><td colspan="5" class="p-4 text-danger">${esc(String(e))}</td></tr>`;
  }
}

async function loadScheduledDocuments() {
  if (!schedDocuments) return;
  const fw = (document.getElementById('schedFramework')?.value || getCurrentFramework() || '').trim();
  if (!fw) return;
  schedDocuments.innerHTML = '<option>Loading ISMS documents…</option>';
  try {
    const data = await apiGet(`/api/v1/isms/documents?framework=${encodeURIComponent(fw)}&limit=1000`);
    scheduledDocuments = data?.items || [];
    schedDocuments.innerHTML = scheduledDocuments.map((d) => {
      const linked = `${(d.clauses || []).length} clauses, ${(d.controls || []).length} controls`;
      return `<option value="${esc(d.id)}">${esc(d.title || 'Document')} (${esc(d.document_type || 'document')}; ${esc(linked)})</option>`;
    }).join('');
  } catch (e) {
    scheduledDocuments = [];
    schedDocuments.innerHTML = '';
    toast(status, `Failed to load ISMS documents: ${String(e)}`, 'warning');
  }
}

function resetScheduleForm() {
  scheduleForm?.reset();
  const fw = getCurrentFramework();
  const schedFramework = document.getElementById('schedFramework');
  const schedInterval = document.getElementById('schedInterval');
  if (schedFramework) schedFramework.value = fw;
  if (schedStart) schedStart.value = _todayIsoDate();
  if (schedFuzzyYear) schedFuzzyYear.value = String(new Date().getFullYear());
  if (schedFuzzyMonth) schedFuzzyMonth.value = String(new Date().getMonth() + 1);
  if (schedDateRule) schedDateRule.value = 'exact';
  schedStartWire?.syncTextFromHidden?.();
  schedEndWire?.syncTextFromHidden?.();
  schedUntilWire?.syncTextFromHidden?.();
  updateScheduleDateMode();
  if (schedInterval) schedInterval.value = '1';
  setRichTextEditorValue('schedNotes', '');
  scheduledAttendees = [];
  if (schedAttendeeUserId) schedAttendeeUserId.value = '';
  if (schedDocuments) schedDocuments.innerHTML = '';
  renderScheduledAttendees();
  loadScheduledDocuments();
}

if (!canManageAudits()) {
  showCreate?.classList.add('d-none');
  if (createCard) createCard.style.display = 'none';
}
showCreate?.addEventListener('click', showCreateCard);
cancelCreate?.addEventListener('click', () => { createCard.style.display = 'none'; });
document.getElementById('reload')?.addEventListener('click', loadAudits);
q?.addEventListener('input', debounce(loadAudits, 350));
statusFilter?.addEventListener('change', loadAudits);
newDocumentFilter?.addEventListener('input', renderCreateDocuments);
newClauseFilter?.addEventListener('input', renderCreateClauses);
newControlFilter?.addEventListener('input', renderCreateControls);
newFramework?.addEventListener('input', debounce(() => loadCreateScope({resetSelection: true}), 350));
document.getElementById('schedFramework')?.addEventListener('input', debounce(loadScheduledDocuments, 350));

document.getElementById('reloadSchedules')?.addEventListener('click', loadScheduledAudits);
document.getElementById('schedMonth')?.addEventListener('change', loadScheduledAudits);
document.getElementById('schedPrevMonth')?.addEventListener('click', () => {
  const el = document.getElementById('schedMonth');
  if (el) { el.value = _shiftMonth(el.value, -1); loadScheduledAudits(); }
});
document.getElementById('schedNextMonth')?.addEventListener('click', () => {
  const el = document.getElementById('schedMonth');
  if (el) { el.value = _shiftMonth(el.value, 1); loadScheduledAudits(); }
});
coverageReload?.addEventListener('click', loadScopeCoverage);
coverageMode?.addEventListener('change', loadScopeCoverage);
coverageStart?.addEventListener('change', loadScopeCoverage);
coverageEnd?.addEventListener('change', loadScopeCoverage);
coverageQ?.addEventListener('input', debounce(renderCoverage, 200));
document.getElementById('coverage-tab')?.addEventListener('shown.bs.tab', () => {
  if (!coverageData) loadScopeCoverage();
});
document.getElementById('scheduleReset')?.addEventListener('click', resetScheduleForm);
schedDateRule?.addEventListener('change', updateScheduleDateMode);
schedFuzzyMonth?.addEventListener('change', updateScheduleDateMode);
schedFuzzyYear?.addEventListener('input', debounce(updateScheduleDateMode, 150));
document.getElementById('schedRecurrence')?.addEventListener('change', updateScheduleDateMode);
schedAttendeeUserId?.addEventListener('change', syncScheduledAttendeeUserSelection);
document.getElementById('schedAddAttendee')?.addEventListener('click', () => {
  const user = selectedScheduledAttendeeUser();
  const nameEl = document.getElementById('schedAttendeeName');
  const emailEl = document.getElementById('schedAttendeeEmail');
  const roleEl = document.getElementById('schedAttendeeRole');
  const name = (nameEl?.value || '').trim();
  const email = (emailEl?.value || '').trim();
  const role = (roleEl?.value || '').trim();
  if (!user && !name && !email) {
    toast(status, 'Choose a Keen user, or enter an attendee name or email first.', 'warning');
    return;
  }
  const finalEmail = email || user?.email || '';
  scheduledAttendees.push({
    user_id: user?.id || null,
    username: user?.username || null,
    name: name || user?.username || finalEmail,
    email: finalEmail,
    role,
  });
  if (schedAttendeeUserId) schedAttendeeUserId.value = '';
  if (nameEl) nameEl.value = '';
  if (emailEl) emailEl.value = '';
  if (roleEl) roleEl.value = '';
  renderScheduledAttendees();
});

scheduleForm?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    syncRichTextEditors(document);
    if (!ensureAuditManage()) return;
    const endOk = schedEndWire?.syncHiddenFromText?.() !== false;
    const untilOk = schedUntilWire?.syncHiddenFromText?.() !== false;
    if (!endOk || !untilOk) {
      toast(status, 'Enter dates using the configured date format.', 'warning');
      return;
    }
    const body = {
      title: (document.getElementById('schedTitle')?.value || '').trim(),
      framework_slug: (document.getElementById('schedFramework')?.value || getCurrentFramework() || '').trim(),
      audit_type: document.getElementById('schedType')?.value || 'internal',
      start_date: _scheduleStartForSubmit() || null,
      end_date: schedEnd?.value || null,
      schedule_recurrence: document.getElementById('schedRecurrence')?.value || 'once',
      schedule_interval: Number(document.getElementById('schedInterval')?.value || 1),
      schedule_date_rule: schedDateRule?.value || 'exact',
      schedule_anchor_month: _isFuzzyScheduleDateRule(schedDateRule?.value || 'exact') ? Number(schedFuzzyMonth?.value || 1) : null,
      schedule_until_date: schedUntil?.value || null,
      report_notes: syncRichTextEditor(document.getElementById('schedNotes')) || '',
      documents: schedDocuments ? Array.from(schedDocuments.selectedOptions || []).map((o) => String(o.value)).filter(Boolean) : [],
      attendees: scheduledAttendees.map((a) => ({
        user_id: a.user_id || null,
        name: a.name || null,
        email: a.email || null,
        role: a.role || null,
      })),
    };
    if (!body.start_date) {
      toast(status, 'Enter a valid first run date.', 'warning');
      return;
    }
    await apiPost('/api/v1/audits/scheduled', body);
    toast(status, 'Scheduled audit template created. Open it from the table below to adjust scope or attendees.', 'success');
    resetScheduleForm();
    await loadScheduledAudits();
  } catch (e) {
    toast(status, `Failed: ${String(e)}`, 'danger');
  }
});

createForm?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    syncRichTextEditors(document);
    const body = {
      title: (newTitle?.value || '').trim(),
      framework_slug: (newFramework?.value || getCurrentFramework() || '').trim(),
      audit_type: newType?.value || 'internal',
      start_date: newStart?.value || null,
      end_date: newEnd?.value || null,
      executive_summary: syncRichTextEditor(newExecutiveSummary) || '',
      report_notes: syncRichTextEditor(newNotes) || '',
      documents: Array.from(selectedCreateDocumentIds),
      clauses: Array.from(selectedCreateClauseIds),
      controls: Array.from(selectedCreateControlIds),
    };
    const res = await apiPost('/api/v1/audits', body);
    toast(status, 'Audit created', 'success');
    location.href = withFramework(`/audit.html?id=${encodeURIComponent(res.id)}`, res.framework_slug);
  } catch (e) {
    toast(status, `Failed: ${String(e)}`, 'danger');
  }
});

setDefaultCoverageDates();
if ((qs('new') === '1' || qs('new') === 'true') && canManageAudits()) showCreateCard();
if (location.hash === '#coverage' || qs('tab') === 'coverage') {
  try { new bootstrap.Tab(document.getElementById('coverage-tab')).show(); } catch {}
  loadScopeCoverage();
}
await loadScheduledAttendeeUsers();
resetScheduleForm();
await loadAudits();
await loadScheduledAudits();
