import { featureEnabled } from '/pages/feature-availability.js';
import { maskDomText, maskEventDataString } from '/pages/evidence-export/masking.js';
import {
  buildZipStore,
  downloadBlob,
  extFromContentType,
  MAX_ZIP_EXPORT_ARTIFACTS,
  MAX_ZIP_EXPORT_BYTES,
  parseContentDispositionFilename,
  readBoundedExportResponse,
} from '/pages/evidence-export/archive.js';
import {
  buildPdfFromJpegPages,
  sliceCanvasToA4JpegPages,
  waitForImages,
} from '/pages/evidence-export/pdf.js';
import { renderWrappedNodeLabel } from '/pages/visualisation/labels.js';
import { isoYearMonth, scheduleDateFields } from '/pages/schedule-fields.js';
import { createEvidenceSelection } from '/pages/evidence-selection.js';
let selectedSwotIds = new Set(),
  swotScopeLoaded = false;
import {
  errorText,
  initNavbar,
  canEditAuditForUser,
  canSampleIntoAudit,
  apiGet,
  apiPatch,
  apiPost,
  apiPut,
  apiDelete,
  apiPostForm,
  confirmAction,
  esc,
  fmtTs,
  fmtTsFilename,
  toast,
  qs,
  getCurrentFramework,
  withFramework,
  isAuditLocked,
  auditStatusLabel,
  wireIsoDateTextField,
  isoDateToDisplay,
  rememberSamplingAudit,
  dateFormatPlaceholder,
  initRichTextEditors,
  refreshRichTextEditorStates,
  renderRichTextContent,
  setRichTextEditorValue,
  syncRichTextEditor,
  syncRichTextEditors,
  userPillHtml,
  safeExternalHref,
} from '/app.js';

const me = await initNavbar();

const _eventDataMaskingMode = String(me?.event_data_masking || 'false')
  .trim()
  .toLowerCase();
function _maskSampleExportsEnabled() {
  return _eventDataMaskingMode === 'samples' || _eventDataMaskingMode === 'true';
}
function _maskExportDom(root) {
  if (_maskSampleExportsEnabled()) maskDomText(root);
}
function _sampleExportUrl(url) {
  if (!_maskSampleExportsEnabled()) return url;
  const sep = String(url).includes('?') ? '&' : '?';
  return `${url}${sep}sample_export=1`;
}
function _sampleMaskedFilename(value) {
  return _maskSampleExportsEnabled() ? maskEventDataString(value) : value;
}

const auditId = qs('id');
const status = document.getElementById('status');

const evidenceSelection = createEvidenceSelection({
  container: document.getElementById('evidence'),
  bar: document.getElementById('evidenceSelectionBar'),
  selectAllWrap: document.getElementById('evidenceSelectAllWrap'),
  statusEl: status,
});
document
  .querySelector('[data-remove-selected-evidence]')
  ?.addEventListener('click', () => removeSelectedEvidence());

async function removeSelectedEvidence() {
  const ids = evidenceSelection.ids();
  if (!ids.length) return;
  if (!ensureAuditEditable()) return;
  if (
    !(await confirmAction({
      title: 'Remove sampled evidence',
      message: `Remove ${ids.length} sampled evidence ${ids.length === 1 ? 'item' : 'items'} from this audit? The evidence itself stays in Keen.`,
      confirmLabel: 'Remove',
    }))
  )
    return;
  try {
    for (const id of ids)
      await apiDelete(
        `/api/v1/audits/${encodeURIComponent(auditId)}/evidence/${encodeURIComponent(id)}`,
      );
    evidenceSelection.clear();
  } catch (e) {
    toast(status, `Failed to remove sampled evidence: ${errorText(e)}`, 'danger');
  }
  await loadAudit({ preserveUnsaved: true });
}

const auditStart = document.getElementById('auditStart');
const auditEnd = document.getElementById('auditEnd');
const auditStartWire = wireIsoDateTextField(
  auditStart,
  document.getElementById('auditStartText'),
  document.getElementById('auditStartPick'),
  { dispatchChangeOnText: true },
);
const auditEndWire = wireIsoDateTextField(
  auditEnd,
  document.getElementById('auditEndText'),
  document.getElementById('auditEndPick'),
  { dispatchChangeOnText: true },
);
const tmplScheduleStart = document.getElementById('tmplScheduleStart');
const tmplUntil = document.getElementById('tmplUntil');
const tmplScheduleStartWire = wireIsoDateTextField(
  tmplScheduleStart,
  document.getElementById('tmplScheduleStartText'),
  document.getElementById('tmplScheduleStartPick'),
  { dispatchChangeOnText: true },
);
const tmplUntilWire = wireIsoDateTextField(
  tmplUntil,
  document.getElementById('tmplUntilText'),
  document.getElementById('tmplUntilPick'),
  { dispatchChangeOnText: true },
);
for (const el of ['auditStartText', 'auditEndText', 'tmplScheduleStartText', 'tmplUntilText'].map(
  (id) => document.getElementById(id),
)) {
  if (el) el.setAttribute('placeholder', dateFormatPlaceholder());
}
const templateDates = scheduleDateFields(
  {
    rule: document.getElementById('tmplDateRule'),
    recurrence: document.getElementById('tmplRecurrence'),
    exactFields: document.getElementById('tmplExactDateFields'),
    fuzzyFields: document.getElementById('tmplFuzzyDateFields'),
    month: document.getElementById('tmplFuzzyMonth'),
    monthLabel: document.getElementById('tmplFuzzyMonthLabel'),
    year: document.getElementById('tmplFuzzyYear'),
    start: tmplScheduleStart,
    startText: document.getElementById('tmplScheduleStartText'),
    preview: document.getElementById('tmplSchedulePreview'),
  },
  { lead: 'Next run' },
);

initRichTextEditors(document);

let audit = null;
let allDocuments = [];
let allControls = [];
let allClauses = [];
let selectedDocumentIds = new Set();
let selectedControlIds = new Set();
let selectedClauseIds = new Set();
let clauseControlMap = new Map();
let clauseChildrenByParentId = new Map();
let attendeeUsers = [];
let attendeePeople = [];

function canViewAudits() {
  return Boolean(me?.can_view_audits || me?.can_manage_audits || me?.is_admin);
}

function canManageAudits() {
  return audit ? canEditAuditForUser(me, audit) : canSampleIntoAudit(me);
}

function canDeleteAudit() {
  return Boolean((me?.is_admin || me?.can_admin_audits) && audit && !auditTemplate());
}

function auditLocked() {
  return isAuditLocked(audit);
}

function auditTemplate() {
  return (
    String(audit?.status || '')
      .trim()
      .toLowerCase() === 'template'
  );
}

function canEditAudit() {
  return Boolean(canManageAudits() && !auditLocked());
}

function auditLockedMessage() {
  return `This audit is ${auditStatusLabel(audit)}. Reopen it to Open or In progress before editing scope, evidence, people, findings, notes, or reports.`;
}

function ensureAuditEditable() {
  if (canEditAudit()) return true;
  if (auditLocked()) toast(status, auditLockedMessage(), 'warning');
  else
    toast(
      status,
      'Only the audit creator or an audit administrator can edit this audit.',
      'danger',
    );
  return false;
}

function _setDisabled(id, disabled) {
  const el = document.getElementById(id);
  if (el) el.disabled = Boolean(disabled);
}

function _setFormControlsDisabled(formId, disabled) {
  const form = document.getElementById(formId);
  if (!form) return;
  form.querySelectorAll('input, select, textarea, button').forEach((el) => {
    el.disabled = Boolean(disabled);
  });
}

function updateScopeActionButtons() {
  const canEdit = canEditAudit();
  _setDisabled('deselectDocuments', !canEdit || selectedDocumentIds.size === 0);
  _setDisabled('deselectClauses', !canEdit || selectedClauseIds.size === 0);
  _setDisabled('deselectControls', !canEdit || selectedControlIds.size === 0);
}

function applyAuditEditability() {
  const locked = auditLocked();
  const canManage = canManageAudits();
  const canEdit = canEditAudit();
  document.querySelectorAll('[data-swot-scope]').forEach((box) => {
    box.disabled = !canEdit || !swotScopeLoaded;
  });
  const notice = document.getElementById('auditLockNotice');
  if (notice) {
    notice.style.display = locked ? '' : 'none';
    notice.textContent = locked ? auditLockedMessage() : '';
  }

  [
    'auditTitle',
    'auditFramework',
    'auditType',
    'auditStart',
    'auditEnd',
    'auditStartText',
    'auditStartPick',
    'auditEndText',
    'auditEndPick',
    'auditExecutiveSummary',
  ].forEach((id) => _setDisabled(id, !canEdit));
  const template = auditTemplate();
  _setDisabled('auditStatus', !canManage || template);

  const saveDetailsBtn = document.querySelector('#detailsForm button[type="submit"]');
  if (saveDetailsBtn) {
    saveDetailsBtn.disabled = !canManage;
    saveDetailsBtn.textContent = locked ? 'Reopen audit' : 'Save details';
  }
  document.getElementById('templateScheduleCard')?.classList.toggle('d-none', !template);
  document.getElementById('btnAuditZip')?.classList.toggle('d-none', template);
  document.getElementById('sampleEventLink')?.classList.toggle('d-none', template);
  document.getElementById('deleteAudit')?.classList.toggle('d-none', !canDeleteAudit());

  _setFormControlsDisabled('reportForm', !canEdit || template);
  _setFormControlsDisabled('reportNotesForm', !canEdit);
  _setFormControlsDisabled('urlEvidenceForm', !canEdit || template);
  _setFormControlsDisabled('attendeeForm', !canEdit);
  _setFormControlsDisabled('findingForm', !canEdit || template);
  _setFormControlsDisabled('templateScheduleForm', !canEdit || !template);
  _setDisabled('saveScope', !canEdit);
  updateScopeActionButtons();
  refreshRichTextEditorStates(document);
}

function ensureAuditRead() {
  // Let the API be the source of truth. Users who are marked as audit
  // attendees may have access to this one audit even when they do not have
  // the global audits.read permission.
  if (auditId || canViewAudits()) return true;
  toast(status, 'Audit access required.', 'danger');
  return false;
}

function kindLabel(k) {
  const v = String(k || '');
  if (v === 'major_nc') return 'Major non-conformity';
  if (v === 'minor_nc') return 'Minor non-conformity';
  if (v === 'ofi') return 'Opportunity for improvement';
  if (v === 'best_practice') return 'Best practice';
  return v || 'Finding';
}

function findingStatusLabel(s) {
  return String(s || 'open')
    .replaceAll('_', ' ')
    .replace(/^./, (c) => c.toUpperCase());
}

function auditTypeLabel(v) {
  const raw = String(v || 'internal').toLowerCase();
  if (raw === 'external') return 'External';
  return 'Internal';
}

function selectedAttr(value, current) {
  return String(value) === String(current || '') ? 'selected' : '';
}

function showMeta(a) {
  document.getElementById('title').textContent = a?.title || 'Audit';
  const dates =
    [a?.start_date, a?.end_date]
      .filter(Boolean)
      .map((d) => isoDateToDisplay(d))
      .join(' → ') || 'No dates set';
  document.getElementById('meta').innerHTML =
    `${esc(a?.framework_slug || '')} · ${esc(auditTypeLabel(a?.audit_type))} · ${esc(auditStatusLabel(a?.status))} · ${esc(dates)} · Author: ${userPillHtml(a?.created_by_username)}`;
  document.getElementById('sampleEventLink').href = withFramework(
    '/events.html',
    a?.framework_slug || getCurrentFramework(),
  );
}

// Evidence found from here goes to this audit unless the auditor picks another.
document
  .getElementById('sampleEventLink')
  ?.addEventListener('click', () => rememberSamplingAudit(auditId));

function setTemplateScheduleForm(a) {
  const start = a?.schedule_next_run_date || a?.start_date || '';
  const dateRule = a?.schedule_date_rule || 'exact';
  setFieldValue('tmplDateRule', dateRule);
  setFieldValue('tmplScheduleStart', start);
  setFieldValue('tmplRecurrence', a?.schedule_recurrence || 'once');
  setFieldValue('tmplInterval', String(a?.schedule_interval || 1));
  setFieldValue('tmplUntil', a?.schedule_until_date || '');
  const startMonth = isoYearMonth(start);
  setFieldValue('tmplFuzzyMonth', String(a?.schedule_anchor_month || startMonth.month));
  setFieldValue('tmplFuzzyYear', String(startMonth.year));
  tmplScheduleStartWire?.syncTextFromHidden?.();
  tmplUntilWire?.syncTextFromHidden?.();
  templateDates.update();

  const info = document.getElementById('tmplScheduleInfo');
  if (info) {
    const bits = [];
    if (a?.schedule_last_run_date)
      bits.push(`Last materialised: ${isoDateToDisplay(a.schedule_last_run_date)}`);
    if (a?.schedule_next_run_date)
      bits.push(`Next run: ${isoDateToDisplay(a.schedule_next_run_date)}`);
    info.textContent = bits.join(' · ') || 'This template has not materialised an audit yet.';
  }
}

function setForm(a) {
  document.getElementById('auditTitle').value = a?.title || '';
  document.getElementById('auditFramework').value = a?.framework_slug || getCurrentFramework();
  document.getElementById('auditStatus').value = a?.status || 'open';
  document.getElementById('auditType').value = a?.audit_type || 'internal';
  document.getElementById('auditStart').value = a?.start_date || '';
  document.getElementById('auditEnd').value = a?.end_date || '';
  auditStartWire?.syncTextFromHidden?.();
  auditEndWire?.syncTextFromHidden?.();
  setRichTextEditorValue('auditExecutiveSummary', a?.executive_summary || '');
  setRichTextEditorValue('auditNotes', a?.report_notes ?? a?.notes ?? '');
  setTemplateScheduleForm(a);
}

function fieldValue(id) {
  const el = document.getElementById(id);
  if (!el) return '';
  if (el.matches?.('textarea[data-rich-text]')) return syncRichTextEditor(el);
  return el.value || '';
}

function setFieldValue(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.matches?.('textarea[data-rich-text]')) {
    setRichTextEditorValue(el, value ?? '');
    return;
  }
  el.value = value ?? '';
}

function captureUnsavedPageState() {
  const evidenceNotes = {};
  document.querySelectorAll('[data-evidence-notes]').forEach((el) => {
    const id = el.getAttribute('data-evidence-notes');
    if (id) evidenceNotes[id] = syncRichTextEditor(el) || '';
  });

  return {
    fields: {
      auditTitle: fieldValue('auditTitle'),
      auditFramework: fieldValue('auditFramework'),
      auditStatus: fieldValue('auditStatus'),
      auditType: fieldValue('auditType'),
      auditStart: fieldValue('auditStart'),
      auditStartText: fieldValue('auditStartText'),
      auditEnd: fieldValue('auditEnd'),
      auditEndText: fieldValue('auditEndText'),
      tmplDateRule: fieldValue('tmplDateRule'),
      tmplScheduleStart: fieldValue('tmplScheduleStart'),
      tmplScheduleStartText: fieldValue('tmplScheduleStartText'),
      tmplFuzzyMonth: fieldValue('tmplFuzzyMonth'),
      tmplFuzzyYear: fieldValue('tmplFuzzyYear'),
      tmplRecurrence: fieldValue('tmplRecurrence'),
      tmplInterval: fieldValue('tmplInterval'),
      tmplUntil: fieldValue('tmplUntil'),
      tmplUntilText: fieldValue('tmplUntilText'),
      auditExecutiveSummary: fieldValue('auditExecutiveSummary'),
      auditNotes: fieldValue('auditNotes'),
      clauseFilter: fieldValue('clauseFilter'),
      documentFilter: fieldValue('documentFilter'),
      controlFilter: fieldValue('controlFilter'),
      urlEvidence: fieldValue('urlEvidence'),
      attendeeUserId: fieldValue('attendeeUserId'),
      attendeePersonId: fieldValue('attendeePersonId'),
      attendeeName: fieldValue('attendeeName'),
      attendeeEmail: fieldValue('attendeeEmail'),
      attendeeRole: fieldValue('attendeeRole'),
      findingKind: fieldValue('findingKind'),
      findingTitle: fieldValue('findingTitle'),
      findingStatus: fieldValue('findingStatus'),
      findingControl: fieldValue('findingControl'),
      findingDesc: fieldValue('findingDesc'),
    },
    selectedSwotIds: new Set(selectedSwotIds),
    selectedDocumentIds: new Set(selectedDocumentIds),
    selectedControlIds: new Set(selectedControlIds),
    selectedClauseIds: new Set(selectedClauseIds),
    evidenceNotes,
  };
}

function restoreUnsavedPageState(state) {
  if (!state) return;

  for (const [id, value] of Object.entries(state.fields || {})) {
    setFieldValue(id, value);
  }
  auditStartWire?.syncTextFromHidden?.();
  auditEndWire?.syncTextFromHidden?.();
  tmplScheduleStartWire?.syncTextFromHidden?.();
  tmplUntilWire?.syncTextFromHidden?.();
  templateDates.update();

  selectedSwotIds = new Set(state.selectedSwotIds || []);
  document.querySelectorAll('[data-swot-scope]').forEach((box) => {
    box.checked = selectedSwotIds.has(box.dataset.swotScope);
  });
  selectedDocumentIds = new Set(state.selectedDocumentIds || []);
  selectedControlIds = new Set(state.selectedControlIds || []);
  selectedClauseIds = new Set(state.selectedClauseIds || []);
  renderScopeLists();

  for (const [id, value] of Object.entries(state.evidenceNotes || {})) {
    const el = document.querySelector(`[data-evidence-notes="${CSS.escape(id)}"]`);
    if (el) setRichTextEditorValue(el, value);
  }
}

function renderReport(a) {
  const el = document.getElementById('reportInfo');
  const r = a?.final_report;
  if (!r) {
    el.innerHTML = 'No final report uploaded.';
    return;
  }
  el.innerHTML = `<div class="d-flex flex-wrap gap-2 align-items-center">
    <a class="btn btn-sm btn-outline-primary" href="${esc(r.download_url)}">Download final report</a>
    <span class="small-muted">${esc(r.filename || 'audit-report')} · ${esc(r.content_type || '')} · ${esc(r.size_bytes ?? '')} bytes · uploaded ${esc(fmtTs(r.uploaded_at))}</span>
  </div>`;
}

function renderKpis(a) {
  document.getElementById('kpiEvidence').textContent = String(
    a?.evidence_count ?? (a?.evidence || []).length ?? 0,
  );
  const documentCount = Number(a?.document_scope_count ?? (a?.scoped_documents || []).length ?? 0);
  const controlCount = Number(a?.control_scope_count ?? (a?.scoped_controls || []).length ?? 0);
  const clauseCount = Number(a?.clause_scope_count ?? (a?.scoped_clauses || []).length ?? 0);
  const scopeEl = document.getElementById('kpiScope');
  scopeEl.textContent = String(a?.scope_count ?? documentCount + controlCount + clauseCount);
  scopeEl.title = `${documentCount} documents, ${clauseCount} clauses, ${controlCount} controls`;
  document.getElementById('kpiPeople').textContent = String(
    a?.attendee_count ?? (a?.attendees || []).length ?? 0,
  );
  document.getElementById('kpiFindings').textContent = String(
    a?.finding_count ?? (a?.findings || []).length ?? 0,
  );
}

// ------------------------------------------------------------------
// Audit ZIP/PDF export helpers
// ------------------------------------------------------------------

function _sanitizeFilename(s) {
  const str = String(s || '');
  return str
    .replace(/[\\/]+/g, ' ')
    .replace(/[<>"\?\*\|\0]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function _safeZipPathSegment(s, fallback = 'file') {
  const v = _sanitizeFilename(s).replace(/[:]+/g, '-').trim();
  return (v || fallback).slice(0, 180).trim() || fallback;
}

// Audit downloads can also be ZIP archives, which the evidence export does not name.
function _extFromContentType(ct) {
  const ext = extFromContentType(ct);
  if (ext) return ext;
  const type = String(ct || '').toLowerCase();
  return type.includes('application/zip') ? '.zip' : '';
}

function _uniqueZipName(name, seen) {
  // ZIP entry names are relative paths: drop empty segments (a leading "/")
  // and replace "." or ".." so no entry can address a parent directory.
  const segments = String(name || 'file')
    .replace(/\\+/g, '/')
    .split('/')
    .filter((p) => p !== '');
  let n = segments
    .map((p, idx) => {
      const fallback = idx === segments.length - 1 ? 'file' : 'folder';
      const safe = _safeZipPathSegment(p, fallback);
      return /^\.+$/.test(safe) ? fallback : safe;
    })
    .join('/');
  if (!n) n = 'file';
  const key = n.toLowerCase();
  const count = seen.get(key) || 0;
  seen.set(key, count + 1);
  if (count === 0) return n;
  const slash = n.lastIndexOf('/');
  const prefix = slash >= 0 ? n.slice(0, slash + 1) : '';
  const file = slash >= 0 ? n.slice(slash + 1) : n;
  const dot = file.lastIndexOf('.');
  if (dot > 0) return `${prefix}${file.slice(0, dot)} (${count + 1})${file.slice(dot)}`;
  return `${prefix}${file} (${count + 1})`;
}

function auditFileStem(a) {
  const parts = ['Keen Audit'];
  if (a?.id) parts.push(String(a.id));
  if (a?.title) parts.push(String(a.title));
  if (a?.updated_at || a?.created_at) parts.push(fmtTsFilename(a.updated_at || a.created_at));
  const stem = _sanitizeFilename(parts.filter(Boolean).join(' - '));
  return stem.slice(0, 180).trim() || 'Keen Audit';
}

function _lookupOriginalControlValue(cloned) {
  const id = cloned.getAttribute('id');
  if (id) {
    const src = document.getElementById(id);
    if (src)
      return { value: src.value ?? '', checked: !!src.checked, selectedIndex: src.selectedIndex };
  }
  const evidenceNotesId = cloned.getAttribute('data-evidence-notes');
  if (evidenceNotesId) {
    const src = document.querySelector(`[data-evidence-notes="${CSS.escape(evidenceNotesId)}"]`);
    if (src)
      return { value: src.value ?? '', checked: !!src.checked, selectedIndex: src.selectedIndex };
  }
  return {
    value: cloned.value ?? cloned.textContent ?? '',
    checked: !!cloned.checked,
    selectedIndex: cloned.selectedIndex,
  };
}

function _formStaticBlock(text, extraClass = '', { richText = false } = {}) {
  const div = document.createElement('div');
  div.className = `form-control bg-white export-static-field ${extraClass}`.trim();
  div.style.whiteSpace = 'pre-wrap';
  div.style.height = 'auto';
  div.style.minHeight = '2.4rem';
  if (richText) {
    div.classList.add('rich-text-content');
    div.innerHTML = renderRichTextContent(text, '<span class="small-muted">-</span>');
  } else {
    div.textContent = String(text || '').trim() || '-';
  }
  return div;
}

function _scopeExportList(items, emptyText) {
  const arr = Array.isArray(items) ? items : [];
  const wrap = document.createElement('div');
  wrap.className = 'export-scope-list';
  if (!arr.length) {
    wrap.innerHTML = `<div class="small-muted border rounded p-2 bg-white">${esc(emptyText || 'No scoped items.')}</div>`;
    return wrap;
  }
  wrap.innerHTML = arr
    .map(
      (item) => `
    <div class="border rounded p-2 mb-2 bg-white">
      <span class="fw-semibold">${esc(item.ref || '')}</span>
      <span class="small-muted">${esc(item.title || '')}</span>
    </div>`,
    )
    .join('');
  return wrap;
}

function _replaceAuditScopeListsForExport(clone) {
  const swotList = clone.querySelector('#swotScope');
  if (swotList)
    swotList.replaceWith(_scopeExportList(audit?.scoped_swot || [], 'No SWOT items selected.'));
  const documentsList = clone.querySelector('#documentsList');
  const clausesList = clone.querySelector('#clausesList');
  const controlsList = clone.querySelector('#controlsList');
  const documentFilter = clone.querySelector('#documentFilter');
  const clauseFilter = clone.querySelector('#clauseFilter');
  const controlFilter = clone.querySelector('#controlFilter');
  if (documentFilter) documentFilter.remove();
  if (clauseFilter) clauseFilter.remove();
  if (controlFilter) controlFilter.remove();
  if (documentsList)
    documentsList.replaceWith(
      _scopeExportList(audit?.scoped_documents || [], 'No ISMS documents selected for this audit.'),
    );
  if (clausesList)
    clausesList.replaceWith(
      _scopeExportList(audit?.scoped_clauses || [], 'No clauses selected for this audit.'),
    );
  if (controlsList)
    controlsList.replaceWith(
      _scopeExportList(audit?.scoped_controls || [], 'No controls selected for this audit.'),
    );
  const documentCount = clone.querySelector('#documentScopeCount');
  const clauseCount = clone.querySelector('#clauseScopeCount');
  const controlCount = clone.querySelector('#controlScopeCount');
  if (documentCount)
    documentCount.textContent = `${(audit?.scoped_documents || []).length} in scope`;
  if (clauseCount) clauseCount.textContent = `${(audit?.scoped_clauses || []).length} in scope`;
  if (controlCount) controlCount.textContent = `${(audit?.scoped_controls || []).length} in scope`;
}

function _prepareAuditCloneForPdf(clone) {
  clone.querySelectorAll('.collapse').forEach((el) => {
    el.classList.add('show');
    el.style.height = 'auto';
    el.style.display = 'block';
    el.style.visibility = 'visible';
  });
  clone.querySelectorAll('.collapsing').forEach((el) => el.classList.remove('collapsing'));
  clone.querySelectorAll('.d-none').forEach((el) => el.remove());
  clone
    .querySelectorAll(
      '#reportForm, #urlEvidenceForm, #attendeeForm, #findingForm, [data-finding-edit-form]',
    )
    .forEach((el) => el.remove());
  _replaceAuditScopeListsForExport(clone);

  clone.querySelectorAll('textarea').forEach((el) => {
    const { value } = _lookupOriginalControlValue(el);
    const block = _formStaticBlock(value, 'export-static-textarea', {
      richText: el.matches?.('textarea[data-rich-text]'),
    });
    block.style.minHeight = `${Math.max(3, Number(el.getAttribute('rows') || 3)) * 1.55}rem`;
    el.replaceWith(block);
  });

  clone.querySelectorAll('select').forEach((el) => {
    const { selectedIndex } = _lookupOriginalControlValue(el);
    const options = Array.from(el.options || []);
    const opt = options[selectedIndex >= 0 ? selectedIndex : el.selectedIndex];
    el.replaceWith(_formStaticBlock(opt?.textContent || el.value || ''));
  });

  clone.querySelectorAll('input').forEach((el) => {
    const type = String(el.getAttribute('type') || 'text').toLowerCase();
    if (['button', 'submit', 'reset', 'file', 'hidden'].includes(type)) {
      el.remove();
      return;
    }
    const { value, checked } = _lookupOriginalControlValue(el);
    if (type === 'checkbox' || type === 'radio') {
      const span = document.createElement('span');
      span.className = 'export-checkbox';
      span.textContent = checked ? '☑' : '☐';
      span.style.fontSize = '1.05rem';
      span.style.lineHeight = '1.4';
      el.replaceWith(span);
      return;
    }
    el.replaceWith(_formStaticBlock(value));
  });

  clone.querySelectorAll('button, a.btn, .no-print').forEach((el) => el.remove());
  clone.querySelectorAll('#status').forEach((el) => el.remove());
  clone.querySelectorAll('a').forEach((el) => {
    el.style.textDecoration = 'none';
  });
}

function _makeAuditExportContainer(widthPx) {
  syncRichTextEditors(document);
  const main = document.querySelector('main');
  if (!main) throw new Error('Page layout missing');
  const clone = main.cloneNode(true);
  _prepareAuditCloneForPdf(clone);

  const wrap = document.createElement('div');
  wrap.className = 'container container-tight py-4 export-capture';
  wrap.style.position = 'fixed';
  wrap.style.left = '-100000px';
  wrap.style.top = '0';
  wrap.style.width = `${Math.round(widthPx || 1000)}px`;
  wrap.style.background = '#fff';
  wrap.style.color = '#1f2937';
  // html2canvas measures and paints text itself. On macOS Chrome, the
  // system font stack can produce cramped word spacing in the captured
  // canvas, even though the live page looks normal. Use a deterministic
  // export-only font stack and disable font shaping features that vary by
  // OS/browser so the rasterised PDF is consistent across platforms.
  wrap.style.fontFamily = 'Arial, Helvetica, sans-serif';
  wrap.style.fontKerning = 'none';
  wrap.style.fontVariantLigatures = 'none';
  wrap.style.textRendering = 'geometricPrecision';
  wrap.style.letterSpacing = '0.01px';
  wrap.style.wordSpacing = '0.08em';
  wrap.appendChild(clone);
  wrap.querySelectorAll('*').forEach((el) => {
    el.style.fontFamily = 'Arial, Helvetica, sans-serif';
    el.style.fontKerning = 'none';
    el.style.fontVariantLigatures = 'none';
    el.style.textRendering = 'geometricPrecision';
  });
  _maskExportDom(wrap);
  document.body.appendChild(wrap);
  return wrap;
}

async function buildAuditPagePdfBlob(a) {
  const main = document.querySelector('main');
  if (!main) throw new Error('Page layout missing');
  const h2c = window.html2canvas || globalThis.html2canvas;
  if (!h2c) throw new Error('html2canvas is not available');
  // Use a fixed export viewport instead of the current browser window size.
  // This keeps Bootstrap breakpoints and text wrapping consistent between
  // small MacBook windows, external displays, Linux desktops, and browser zooms.
  const exportWindowWidth = 1280;
  const width = 1180;
  const exportedIso = new Date().toISOString();
  const header = _sampleMaskedFilename(
    `Keen audit export ${fmtTsFilename(exportedIso)} - ${a?.title || a?.id || ''}`,
  );
  const footer = (me?.sample_pdf_footer || '').trim();
  const wrap = _makeAuditExportContainer(width);
  try {
    await waitForImages(wrap);
    const canvas = await h2c(wrap, {
      scale: 2,
      backgroundColor: '#fff',
      useCORS: true,
      windowWidth: exportWindowWidth,
      windowHeight: Math.max(900, Math.ceil(wrap.scrollHeight || 0) + 120),
    });
    const pages = await sliceCanvasToA4JpegPages(canvas, header, footer, 'portrait', [], {
      fitText: true,
    });
    return buildPdfFromJpegPages(pages, 'portrait');
  } finally {
    try {
      wrap.remove();
    } catch {}
  }
}

async function _fetchBytes(url, { signal, remainingBytes }) {
  const res = await fetch(url, { credentials: 'include', signal });
  if (!res.ok) {
    await res.body?.cancel();
    throw new Error(`Failed to download ${url} (HTTP ${res.status})`);
  }
  const dispName = parseContentDispositionFilename(res.headers.get('Content-Disposition') || '');
  const contentType = res.headers.get('Content-Type') || '';
  return {
    data: await readBoundedExportResponse(res, remainingBytes),
    filename: dispName,
    contentType,
  };
}

const auditZipButton = document.getElementById('btnAuditZip');
const auditZipCancel = document.getElementById('btnAuditZipCancel');
// The export in progress, so Cancel and leaving the page can abort it.
let auditZipExport = null;

function _stopIfCancelled(signal) {
  if (signal.aborted) throw new Error('Audit ZIP export cancelled.');
}

// Reads each sampled event's attachment list, so an export over the attachment
// limit stops before anything is rendered or downloaded.
async function _sampledEvidence(evidenceItems, signal) {
  const sampled = [];
  let attachments = 0;
  for (let i = 0; i < evidenceItems.length; i++) {
    const item = evidenceItems[i];
    const eventId = item.event_id || item.event?.id;
    if (!eventId) continue;
    toast(status, `Collecting sampled evidence ${i + 1}/${evidenceItems.length}`, 'info');
    const eventData = await apiGet(
      _sampleExportUrl(
        `/api/v1/events/${encodeURIComponent(eventId)}?framework=${encodeURIComponent(audit.framework_slug || getCurrentFramework())}`,
      ),
    );
    _stopIfCancelled(signal);
    const artifacts = Array.isArray(eventData?.artifacts) ? eventData.artifacts : [];
    if (!artifacts.length) continue;
    attachments += artifacts.length;
    if (attachments > MAX_ZIP_EXPORT_ARTIFACTS)
      throw new Error(
        `Audit ZIP export supports at most ${MAX_ZIP_EXPORT_ARTIFACTS} sampled evidence attachments. Download them individually.`,
      );
    sampled.push({ index: i, eventId, eventData, artifacts });
  }
  return sampled;
}

async function exportAuditZip() {
  if (!audit) {
    toast(status, 'Audit not loaded yet', 'warning');
    return;
  }
  if (auditZipExport) return;
  const controller = new AbortController();
  const { signal } = controller;
  auditZipExport = controller;
  const btn = auditZipButton;
  const prevLabel = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Preparing ZIP';
  }
  if (auditZipCancel) {
    auditZipCancel.hidden = false;
    // The disabled export button cannot keep focus; Cancel takes it.
    if (btn && document.activeElement === btn) auditZipCancel.focus();
  }

  try {
    const stem = auditFileStem(audit);
    const files = [];
    const seenNames = new Map();
    const evidenceItems = Array.isArray(audit.evidence) ? audit.evidence : [];
    const sampled = await _sampledEvidence(evidenceItems, signal);

    toast(status, 'Rendering audit page PDF', 'info');
    const pdfBlob = await buildAuditPagePdfBlob(audit);
    _stopIfCancelled(signal);
    if (pdfBlob.size > MAX_ZIP_EXPORT_BYTES)
      throw new Error('Audit page PDF exceeds the 100 MiB ZIP export limit.');
    const pdfBytes = new Uint8Array(await pdfBlob.arrayBuffer());
    let totalBytes = pdfBytes.byteLength;
    files.push({
      name: _uniqueZipName(`${stem}-audit-page.pdf`, seenNames),
      data: pdfBytes,
      mtime: new Date(),
    });

    if (audit.final_report?.download_url) {
      toast(status, 'Adding final report', 'info');
      const finalReport = await _fetchBytes(audit.final_report.download_url, {
        signal,
        remainingBytes: MAX_ZIP_EXPORT_BYTES - totalBytes,
      });
      totalBytes += finalReport.data.byteLength;
      const reportName =
        finalReport.filename ||
        audit.final_report.filename ||
        `final-report${_extFromContentType(finalReport.contentType || audit.final_report.content_type)}`;
      files.push({
        name: _uniqueZipName(`final-report/${reportName}`, seenNames),
        data: finalReport.data,
        mtime: audit.final_report.uploaded_at
          ? new Date(audit.final_report.uploaded_at)
          : new Date(),
      });
    }

    let artifactCount = 0;
    for (const { index: i, eventId, eventData, artifacts } of sampled) {
      const eventStamp = eventData?.timestamp
        ? fmtTsFilename(eventData.timestamp)
        : String(i + 1).padStart(3, '0');
      const eventLabel = _safeZipPathSegment(
        `${String(i + 1).padStart(3, '0')} ${eventStamp} ${eventData?.source || 'event'} ${String(eventId).slice(0, 8)}`,
        `evidence-${i + 1}`,
      );

      for (let j = 0; j < artifacts.length; j++) {
        const art = artifacts[j];
        artifactCount += 1;
        toast(status, `Downloading sampled evidence artifact ${artifactCount}`, 'info');
        const downloaded = await _fetchBytes(
          _sampleExportUrl(`/api/v1/artifacts/${encodeURIComponent(art.id)}/download`),
          { signal, remainingBytes: MAX_ZIP_EXPORT_BYTES - totalBytes },
        );
        totalBytes += downloaded.data.byteLength;
        const ext = downloaded.filename
          ? ''
          : _extFromContentType(downloaded.contentType || art.content_type);
        const artifactName = _sampleMaskedFilename(
          downloaded.filename || `artifact-${art.id}${ext}`,
        );
        files.push({
          name: _uniqueZipName(`sampled-evidence/${eventLabel}/${artifactName}`, seenNames),
          data: downloaded.data,
          mtime: art.captured_at ? new Date(art.captured_at) : new Date(),
        });
      }
    }

    // Nothing is downloaded, and no object URL is made, once the export is cancelled.
    _stopIfCancelled(signal);
    toast(status, 'Building ZIP', 'info');
    const zipBlob = buildZipStore(files);
    downloadBlob(zipBlob, `${stem}.zip`);
    const extra = [];
    if (audit.final_report?.download_url) extra.push('final report');
    if (artifactCount)
      extra.push(`${artifactCount} sampled evidence attachment${artifactCount === 1 ? '' : 's'}`);
    toast(
      status,
      `Audit ZIP download started${extra.length ? ` (${extra.join(', ')})` : ''}`,
      'success',
      3000,
    );
    setTimeout(() => {
      try {
        status.style.display = 'none';
      } catch {}
    }, 3300);
  } catch (err) {
    if (signal.aborted) toast(status, 'Audit ZIP export cancelled.', 'info');
    else toast(status, errorText(err), 'danger');
  } finally {
    auditZipExport = null;
    const cancelHadFocus = !!auditZipCancel && document.activeElement === auditZipCancel;
    if (auditZipCancel) auditZipCancel.hidden = true;
    if (btn) {
      btn.disabled = false;
      btn.textContent = prevLabel || 'Export audit ZIP';
      if (cancelHadFocus) btn.focus();
    }
  }
}

function _graphRawId(value) {
  const s = String(value || '');
  const idx = s.indexOf(':');
  return idx >= 0 ? s.slice(idx + 1) : s;
}

function _buildClauseControlMap(graphData) {
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

function _addControlsLinkedToClause(clauseId) {
  const linked = clauseControlMap.get(String(clauseId)) || new Set();
  let added = 0;
  for (const controlId of linked) {
    if (!selectedControlIds.has(String(controlId))) {
      selectedControlIds.add(String(controlId));
      added += 1;
    }
  }
  return added;
}

function _rebuildClauseHierarchy() {
  clauseChildrenByParentId = new Map();
  for (const clause of allClauses || []) {
    const parentId = clause?.parent_id ? String(clause.parent_id) : '';
    if (!parentId) continue;
    if (!clauseChildrenByParentId.has(parentId)) clauseChildrenByParentId.set(parentId, []);
    clauseChildrenByParentId.get(parentId).push(clause);
  }
}

function _clauseHasChildren(clauseOrId) {
  const id =
    typeof clauseOrId === 'object' ? String(clauseOrId?.id || '') : String(clauseOrId || '');
  if (!id) return false;
  if ((clauseChildrenByParentId.get(id) || []).length > 0) return true;
  if (typeof clauseOrId === 'object') return Number(clauseOrId?.child_count || 0) > 0;
  return false;
}

function _selectableDescendantClauseIds(clauseId) {
  const id = String(clauseId || '');
  const children = clauseChildrenByParentId.get(id) || [];
  if (!children.length) return id ? [id] : [];
  const out = [];
  for (const child of children) out.push(..._selectableDescendantClauseIds(child.id));
  return out;
}

function _selectClauseOrChildren(clauseId) {
  const id = String(clauseId || '');
  if (!id) return { clausesAdded: 0, controlsAdded: 0 };
  const selectableIds = _selectableDescendantClauseIds(id).filter((x) => x && x !== id);
  const targetIds = selectableIds.length ? selectableIds : [id];
  let clausesAdded = 0;
  let controlsAdded = 0;

  // Parent clauses are grouping headings only. Never persist them in scope; use
  // the click as a shortcut for selecting the individual child clauses instead.
  selectedClauseIds.delete(id);
  for (const targetId of targetIds) {
    if (!selectedClauseIds.has(String(targetId))) {
      selectedClauseIds.add(String(targetId));
      clausesAdded += 1;
    }
    controlsAdded += _addControlsLinkedToClause(targetId);
  }
  return { clausesAdded, controlsAdded };
}

function _normaliseSelectedClausesToChildren() {
  const before = Array.from(selectedClauseIds);
  let changed = false;
  for (const id of before) {
    if (!_clauseHasChildren(id)) continue;
    selectedClauseIds.delete(id);
    changed = true;
    for (const childId of _selectableDescendantClauseIds(id).filter((x) => x !== id)) {
      selectedClauseIds.add(String(childId));
      _addControlsLinkedToClause(childId);
    }
  }
  return changed;
}

function _addScopeLinkedToDocument(documentId) {
  const doc = allDocuments.find((d) => String(d.id) === String(documentId));
  let added = 0;
  for (const clause of doc?.clauses || []) {
    const id = String(clause.id || '');
    if (!id) continue;
    if (!selectedClauseIds.has(id)) {
      selectedClauseIds.add(id);
      added += 1;
    }
    added += _addControlsLinkedToClause(id);
  }
  for (const control of doc?.controls || []) {
    const id = String(control.id || '');
    if (id && !selectedControlIds.has(id)) {
      selectedControlIds.add(id);
      added += 1;
    }
  }
  return added;
}

function renderDocuments() {
  const filter = (document.getElementById('documentFilter')?.value || '').trim().toLowerCase();
  const wrap = document.getElementById('documentsList');
  const countEl = document.getElementById('documentScopeCount');
  if (countEl) countEl.textContent = `${selectedDocumentIds.size} selected`;
  updateScopeActionButtons();
  if (!wrap) return;
  const rows = allDocuments.filter((d) =>
    matchesFilter({ ref: d.document_type, title: d.title }, filter),
  );
  wrap.innerHTML =
    rows
      .map((d) => {
        const id = String(d.id);
        const checked = selectedDocumentIds.has(id) ? 'checked' : '';
        const linked = `${(d.clauses || []).length} clauses, ${(d.controls || []).length} controls`;
        return `<div class="col-12">
      <label class="border rounded p-2 d-flex gap-2 align-items-start h-100">
        <input class="form-check-input mt-1" type="checkbox" data-document-id="${esc(id)}" ${checked}>
        <span><span class="fw-semibold">${esc(d.title || 'Document')}</span> <span class="badge text-bg-light ms-1">${esc(d.document_type || 'document')}</span><div class="small-muted">${esc(linked)}</div></span>
      </label>
    </div>`;
      })
      .join('') || `<div class="small-muted">No ISMS documents match.</div>`;
  wrap.querySelectorAll('[data-document-id]').forEach((cb) => {
    cb.disabled = !canEditAudit();
    cb.addEventListener('change', () => {
      if (!canEditAudit()) return;
      const id = cb.getAttribute('data-document-id');
      if (cb.checked) {
        selectedDocumentIds.add(id);
        const added = _addScopeLinkedToDocument(id);
        if (added > 0) {
          renderClauses();
          renderControls();
        }
      } else {
        selectedDocumentIds.delete(id);
      }
      if (countEl) countEl.textContent = `${selectedDocumentIds.size} selected`;
      updateScopeActionButtons();
    });
  });
}

async function loadScopeItems() {
  const fw = audit?.framework_slug || getCurrentFramework();
  const [controlsData, clausesData, graphData, documentsData] = await Promise.all([
    featureEnabled(me, 'controls')
      ? apiGet(`/api/v1/controls?framework=${encodeURIComponent(fw)}&limit=5000`)
      : { items: audit?.scoped_controls || [] },
    featureEnabled(me, 'clauses')
      ? apiGet(`/api/v1/clauses?framework=${encodeURIComponent(fw)}`)
      : { items: audit?.scoped_clauses || [] },
    featureEnabled(me, 'visualisations')
      ? apiGet(`/api/v1/graph/clause_control?framework=${encodeURIComponent(fw)}`)
      : {},
    featureEnabled(me, 'documents')
      ? apiGet(`/api/v1/isms/documents?framework=${encodeURIComponent(fw)}&limit=1000`)
      : { items: audit?.scoped_documents || [] },
  ]);
  allDocuments = documentsData?.items || [];
  allControls = (controlsData?.items || []).filter((c) => String(c.type || '') !== 'clause');
  allClauses = clausesData?.items || [];
  _rebuildClauseHierarchy();
  clauseControlMap = _buildClauseControlMap(graphData);
  await loadSwotScope();
  selectedDocumentIds = new Set((audit?.scoped_documents || []).map((d) => String(d.id)));
  selectedControlIds = new Set((audit?.scoped_controls || []).map((c) => String(c.id)));
  selectedClauseIds = new Set((audit?.scoped_clauses || []).map((c) => String(c.id)));
  _normaliseSelectedClausesToChildren();
  renderScopeLists();
}

function matchesFilter(item, filter) {
  if (!filter) return true;
  return (
    String(item.ref || '')
      .toLowerCase()
      .includes(filter) ||
    String(item.title || '')
      .toLowerCase()
      .includes(filter)
  );
}

function clauseIndent(c) {
  const ref = String(c.ref || '');
  return Math.min(Math.max(ref.split('.').length - 1, 0), 3);
}

function renderClauses() {
  const filter = (document.getElementById('clauseFilter')?.value || '').trim().toLowerCase();
  const wrap = document.getElementById('clausesList');
  const countEl = document.getElementById('clauseScopeCount');
  if (countEl) countEl.textContent = `${selectedClauseIds.size} selected`;
  updateScopeActionButtons();
  const rows = allClauses.filter((c) => matchesFilter(c, filter));
  wrap.innerHTML =
    rows
      .map((c) => {
        const id = String(c.id);
        const hasChildren = _clauseHasChildren(c);
        const descendants = hasChildren
          ? _selectableDescendantClauseIds(id).filter((x) => x !== id)
          : [];
        const allChildrenSelected =
          descendants.length > 0 &&
          descendants.every((childId) => selectedClauseIds.has(String(childId)));
        const checked = !hasChildren && selectedClauseIds.has(id) ? 'checked' : '';
        const pad = clauseIndent(c) * 1.1;
        const hint = hasChildren
          ? `<span class="badge text-bg-secondary ms-2">${allChildrenSelected ? 'child clauses selected' : 'selects child clauses'}</span>`
          : '';
        const rowClass = hasChildren
          ? 'border rounded p-2 d-flex gap-2 align-items-start h-100 bg-body-tertiary text-muted'
          : 'border rounded p-2 d-flex gap-2 align-items-start h-100';
        const title = hasChildren
          ? 'Parent clauses are grouping headings. Selecting this will add the individual child clauses instead.'
          : '';
        return `<div class="col-12">
      <label class="${rowClass}" style="margin-left:${pad}rem" title="${esc(title)}">
        <input class="form-check-input mt-1" type="checkbox" data-clause-id="${esc(id)}" ${checked}>
        <span><span class="fw-semibold">${esc(c.ref || '')}</span> <span class="small-muted">${esc(c.title || '')}</span>${hint}</span>
      </label>
    </div>`;
      })
      .join('') || `<div class="small-muted">No clauses match.</div>`;
  wrap.querySelectorAll('[data-clause-id]').forEach((cb) => {
    cb.disabled = !canEditAudit();
    cb.addEventListener('change', () => {
      if (!canEditAudit()) return;
      const id = cb.getAttribute('data-clause-id');
      if (_clauseHasChildren(id)) {
        cb.checked = false;
        const { clausesAdded, controlsAdded } = _selectClauseOrChildren(id);
        renderScopeLists();
        if (clausesAdded || controlsAdded) {
          toast(
            status,
            `Selected ${clausesAdded} child clause${clausesAdded === 1 ? '' : 's'} and ${controlsAdded} linked control${controlsAdded === 1 ? '' : 's'}.`,
            'info',
            3000,
          );
        }
        return;
      }
      if (cb.checked) {
        selectedClauseIds.add(id);
        const added = _addControlsLinkedToClause(id);
        if (added > 0) renderControls();
      } else {
        selectedClauseIds.delete(id);
      }
      if (countEl) countEl.textContent = `${selectedClauseIds.size} selected`;
      updateScopeActionButtons();
    });
  });
}

function renderControls() {
  const filter = (document.getElementById('controlFilter')?.value || '').trim().toLowerCase();
  const wrap = document.getElementById('controlsList');
  const countEl = document.getElementById('controlScopeCount');
  if (countEl) countEl.textContent = `${selectedControlIds.size} selected`;
  updateScopeActionButtons();
  const rows = allControls.filter((c) => matchesFilter(c, filter));
  wrap.innerHTML =
    rows
      .map((c) => {
        const id = String(c.id);
        const checked = selectedControlIds.has(id) ? 'checked' : '';
        return `<div class="col-12 col-lg-6">
      <label class="border rounded p-2 d-flex gap-2 align-items-start h-100">
        <input class="form-check-input mt-1" type="checkbox" data-control-id="${esc(id)}" ${checked}>
        <span><span class="fw-semibold">${esc(c.ref || '')}</span> <span class="small-muted">${esc(c.title || '')}</span></span>
      </label>
    </div>`;
      })
      .join('') || `<div class="small-muted">No controls match.</div>`;
  wrap.querySelectorAll('[data-control-id]').forEach((cb) => {
    cb.disabled = !canEditAudit();
    cb.addEventListener('change', () => {
      if (!canEditAudit()) return;
      const id = cb.getAttribute('data-control-id');
      if (cb.checked) selectedControlIds.add(id);
      else selectedControlIds.delete(id);
      if (countEl) countEl.textContent = `${selectedControlIds.size} selected`;
      updateScopeActionButtons();
    });
  });
}

function renderScopeLists() {
  renderDocuments();
  renderClauses();
  renderControls();
}

function _scopeApplicabilityLabel(value) {
  const v = String(value || '').trim();
  if (!v) return 'No selected link';
  return v.replaceAll('_', ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function _scopeApplicabilityColor(value) {
  const base =
    getComputedStyle(document.documentElement).getPropertyValue('--bs-primary').trim() || '#0d6efd';
  if (String(value || '') === 'partially_applicable') {
    try {
      const c = d3.color(base);
      if (c) {
        c.opacity = 0.58;
        return c.formatRgb();
      }
    } catch {}
  }
  return base;
}

function _openScopeHref(href, ev) {
  if (!href || href === '#') return;
  if (ev && (ev.metaKey || ev.ctrlKey)) {
    window.open(href, '_blank', 'noopener');
    return;
  }
  location.href = href;
}

function _scopeClauseHref(clause) {
  return withFramework(
    `/clause.html?id=${encodeURIComponent(clause?.id || '')}`,
    audit?.framework_slug,
  );
}

function _scopeControlHref(control) {
  return withFramework(
    `/control.html?id=${encodeURIComponent(control?.id || '')}`,
    audit?.framework_slug,
  );
}

function renderScopeBipartiteGraph(data) {
  const wrap = document.getElementById('scopeHeatmap');
  const summary = document.getElementById('scopeHeatmapSummary');
  if (!wrap) return;
  const clauses = Array.isArray(data?.clauses) ? data.clauses : [];
  const controls = Array.isArray(data?.controls) ? data.controls : [];
  const links = Array.isArray(data?.links) ? data.links : [];
  const stats = data?.summary || {};
  wrap.innerHTML = '';

  if (summary) {
    summary.textContent = `${stats.clause_count ?? clauses.length} clause(s), ${stats.control_count ?? controls.length} control(s), ${stats.relationship_count ?? links.length} clause/control relationship(s) in this audit scope.`;
  }

  if (!clauses.length || !controls.length) {
    wrap.innerHTML =
      '<div class="small-muted p-3">Select at least one clause and one control to show the audit scope graph.</div>';
    return;
  }

  const clauseById = new Map(clauses.map((c) => [String(c.id), c]));
  const controlById = new Map(controls.map((c) => [String(c.id), c]));
  const validLinks = links
    .map((l) => ({
      ...l,
      clause_id: String(l.clause_id || ''),
      control_id: String(l.control_id || ''),
      applicability: l.applicability || 'applicable',
    }))
    .filter((l) => clauseById.has(l.clause_id) && controlById.has(l.control_id));

  const linkedClauseCounts = new Map();
  const linkedControlCounts = new Map();
  for (const l of validLinks) {
    linkedClauseCounts.set(l.clause_id, (linkedClauseCounts.get(l.clause_id) || 0) + 1);
    linkedControlCounts.set(l.control_id, (linkedControlCounts.get(l.control_id) || 0) + 1);
  }

  const orderedClauses = clauses.slice().sort((a, b) => {
    const ac = linkedClauseCounts.get(String(a.id)) || 0;
    const bc = linkedClauseCounts.get(String(b.id)) || 0;
    if (bc !== ac) return bc - ac;
    return String(a.ref || '').localeCompare(String(b.ref || ''), undefined, { numeric: true });
  });
  const orderedControls = controls.slice().sort((a, b) => {
    const ac = linkedControlCounts.get(String(a.id)) || 0;
    const bc = linkedControlCounts.get(String(b.id)) || 0;
    if (bc !== ac) return bc - ac;
    return String(a.ref || '').localeCompare(String(b.ref || ''), undefined, { numeric: true });
  });

  const rowPx = Math.max(
    34,
    Math.min(46, orderedClauses.length > 60 || orderedControls.length > 60 ? 34 : 42),
  );
  const rows = Math.max(orderedClauses.length, orderedControls.length, 6);
  const width = Math.max(760, (wrap.clientWidth || 900) - 18);
  const height = Math.max(340, rows * rowPx + 96);
  const margin = { top: 48, right: 18, bottom: 48, left: 18 };
  const leftW = Math.min(280, Math.max(190, Math.floor(width * 0.28)));
  const rightW = Math.min(280, Math.max(190, Math.floor(width * 0.28)));
  const xL = margin.left;
  const xR = width - margin.right - rightW;
  const linkGap = Math.max(80, xR - (xL + leftW));

  if (summary) {
    const orphanControls =
      stats.controls_in_scope_without_selected_clause_link ??
      controls.length - linkedControlCounts.size;
    const orphanClauses =
      stats.clauses_without_selected_control_link ?? clauses.length - linkedClauseCounts.size;
    const extras = [];
    if (orphanClauses > 0)
      extras.push(`${orphanClauses} selected clause(s) have no selected control link`);
    if (orphanControls > 0)
      extras.push(`${orphanControls} selected control(s) have no selected clause link`);
    if (extras.length) summary.textContent += ` ${extras.join('; ')}.`;
  }

  const yClauses = d3
    .scaleBand()
    .domain(orderedClauses.map((c) => String(c.id)))
    .range([margin.top, height - margin.bottom])
    .padding(0.22);
  const yControls = d3
    .scaleBand()
    .domain(orderedControls.map((c) => String(c.id)))
    .range([margin.top, height - margin.bottom])
    .padding(0.22);

  const svg = d3
    .select(wrap)
    .append('svg')
    .attr('width', width)
    .attr('height', height)
    .attr('role', 'img')
    .attr('aria-label', 'Audit clause and control scope bipartite graph')
    .style('display', 'block')
    .style('min-width', `${width}px`);

  svg
    .append('text')
    .attr('x', margin.left)
    .attr('y', 20)
    .attr('class', 'small-muted')
    .style('font-size', '12px')
    .text(
      'Clauses are on the left; controls are on the right. Click a clause or control to open its detail page.',
    );

  svg
    .append('text')
    .attr('x', xL)
    .attr('y', margin.top - 10)
    .attr('class', 'small-muted')
    .style('font-size', '12px')
    .text('Clauses in scope');
  svg
    .append('text')
    .attr('x', xR)
    .attr('y', margin.top - 10)
    .attr('class', 'small-muted')
    .style('font-size', '12px')
    .text('Controls in scope');

  const defs = svg.append('defs');
  const grad = defs
    .append('linearGradient')
    .attr('id', 'auditScopeLinkGradient')
    .attr('x1', '0%')
    .attr('y1', '0%')
    .attr('x2', '100%')
    .attr('y2', '0%');
  grad
    .append('stop')
    .attr('offset', '0%')
    .attr('stop-color', _scopeApplicabilityColor('applicable'))
    .attr('stop-opacity', 0.62);
  grad
    .append('stop')
    .attr('offset', '100%')
    .attr('stop-color', _scopeApplicabilityColor('applicable'))
    .attr('stop-opacity', 0.3);

  const gLinks = svg.append('g').attr('class', 'audit-scope-links');
  const linkSel = gLinks
    .selectAll('path')
    .data(validLinks)
    .enter()
    .append('path')
    .attr('fill', 'none')
    .attr('stroke-width', (d) => (d.applicability === 'partially_applicable' ? 1.7 : 2.8))
    .attr('stroke-dasharray', (d) => (d.applicability === 'partially_applicable' ? '5 5' : null))
    .style('stroke', (d) =>
      d.applicability === 'partially_applicable'
        ? _scopeApplicabilityColor(d.applicability)
        : 'url(#auditScopeLinkGradient)',
    )
    .style('stroke-opacity', (d) => (d.applicability === 'partially_applicable' ? 0.6 : 0.76))
    .style('stroke-linecap', 'round')
    .attr('d', (d) => {
      const y1 = (yClauses(d.clause_id) ?? margin.top) + yClauses.bandwidth() / 2;
      const y2 = (yControls(d.control_id) ?? margin.top) + yControls.bandwidth() / 2;
      const x1 = xL + leftW;
      const x2 = xR;
      const xm = x1 + linkGap / 2;
      return `M ${x1} ${y1} C ${xm} ${y1}, ${xm} ${y2}, ${x2} ${y2}`;
    });

  linkSel.append('title').text((d) => {
    const clause = clauseById.get(d.clause_id) || {};
    const control = controlById.get(d.control_id) || {};
    return `${clause.ref || 'Clause'} × ${control.ref || 'Control'}\n${_scopeApplicabilityLabel(d.applicability)}`;
  });

  const gNodes = svg.append('g').attr('class', 'audit-scope-nodes');

  const clauseNodes = gNodes
    .selectAll('a.scope-clause')
    .data(orderedClauses)
    .enter()
    .append('a')
    .attr('class', 'scope-clause')
    .attr('href', (d) => _scopeClauseHref(d))
    .each(function (d) {
      const y = yClauses(String(d.id)) ?? 0;
      const group = d3
        .select(this)
        .append('g')
        .attr('class', 'node node-source audit-scope-node audit-scope-clause')
        .attr('transform', `translate(${xL}, ${y})`)
        .style('cursor', 'pointer')
        .on('click', (ev) => {
          ev.preventDefault();
          _openScopeHref(_scopeClauseHref(d), ev);
        });
      group
        .append('rect')
        .attr('rx', 8)
        .attr('ry', 8)
        .attr('width', leftW)
        .attr('height', yClauses.bandwidth())
        .style(
          'fill',
          linkedClauseCounts.has(String(d.id))
            ? 'rgba(var(--keen-accent-rgb),0.86)'
            : 'rgba(108,117,125,0.72)',
        )
        .style('stroke', 'rgba(255,255,255,0.20)')
        .style('stroke-width', 1);
      const label = `${d.ref || 'Clause'}${d.title ? ` - ${d.title}` : ''}`;
      group
        .append('text')
        .attr('x', leftW / 2)
        .attr('y', yClauses.bandwidth() / 2)
        .attr('text-anchor', 'middle')
        .style('font-size', orderedClauses.length > 50 ? '10px' : '12px')
        .style('font-weight', 700)
        .style('fill', 'rgba(255,255,255,0.94)')
        .style('pointer-events', 'none')
        .each(function () {
          // Scope labels sit a pixel above the middle of their tile.
          renderWrappedNodeLabel(d3.select(this), label, {
            width: leftW,
            height: yClauses.bandwidth(),
            x: leftW / 2,
            y: yClauses.bandwidth() / 2 - 1,
            linePx: orderedClauses.length > 50 ? 11 : 13,
            charPx: 6.8,
            maxLines: 3,
          });
        });
      group
        .append('title')
        .text(
          `${label}\n${linkedClauseCounts.get(String(d.id)) || 0} linked control(s) in this audit scope`,
        );
    });

  const controlNodes = gNodes
    .selectAll('a.scope-control')
    .data(orderedControls)
    .enter()
    .append('a')
    .attr('class', 'scope-control')
    .attr('href', (d) => _scopeControlHref(d))
    .each(function (d) {
      const y = yControls(String(d.id)) ?? 0;
      const group = d3
        .select(this)
        .append('g')
        .attr('class', 'node node-control audit-scope-node audit-scope-control')
        .attr('transform', `translate(${xR}, ${y})`)
        .style('cursor', 'pointer')
        .on('click', (ev) => {
          ev.preventDefault();
          _openScopeHref(_scopeControlHref(d), ev);
        });
      group
        .append('rect')
        .attr('rx', 8)
        .attr('ry', 8)
        .attr('width', rightW)
        .attr('height', yControls.bandwidth())
        .style(
          'fill',
          linkedControlCounts.has(String(d.id)) ? 'rgba(31,31,42,0.84)' : 'rgba(108,117,125,0.72)',
        )
        .style('stroke', 'rgba(255,255,255,0.18)')
        .style('stroke-width', 1);
      const label = `${d.ref || 'Control'}${d.title ? ` - ${d.title}` : ''}`;
      group
        .append('text')
        .attr('x', 10)
        .attr('y', yControls.bandwidth() / 2)
        .attr('text-anchor', 'start')
        .style('font-size', orderedControls.length > 50 ? '10px' : '12px')
        .style('font-weight', 700)
        .style('fill', 'rgba(255,255,255,0.94)')
        .style('pointer-events', 'none')
        .each(function () {
          renderWrappedNodeLabel(d3.select(this), label, {
            width: rightW - 18,
            height: yControls.bandwidth(),
            x: 10,
            y: yControls.bandwidth() / 2 - 1,
            linePx: orderedControls.length > 50 ? 11 : 13,
            charPx: 6.4,
            maxLines: 3,
          });
        });
      group
        .append('title')
        .text(
          `${label}\n${linkedControlCounts.get(String(d.id)) || 0} linked clause(s) in this audit scope`,
        );
    });

  const legend = svg.append('g').attr('transform', `translate(${margin.left}, ${height - 26})`);
  const legendItems = [
    { label: 'Applicable relationship', applicability: 'applicable', dashed: false },
    {
      label: 'Partially applicable relationship',
      applicability: 'partially_applicable',
      dashed: true,
    },
    { label: 'Grey node = in scope, no selected relationship', applicability: '', grey: true },
  ];
  const legendG = legend
    .selectAll('g')
    .data(legendItems)
    .enter()
    .append('g')
    .attr('transform', (_, i) => `translate(${i * 235},0)`);
  legendG.each(function (d) {
    const g = d3.select(this);
    if (d.grey) {
      g.append('rect')
        .attr('width', 28)
        .attr('height', 10)
        .attr('rx', 3)
        .attr('fill', 'rgba(108,117,125,0.72)');
    } else {
      g.append('line')
        .attr('x1', 0)
        .attr('x2', 28)
        .attr('y1', 5)
        .attr('y2', 5)
        .attr('stroke-width', d.dashed ? 2 : 3)
        .attr('stroke-dasharray', d.dashed ? '5 5' : null)
        .attr('stroke', _scopeApplicabilityColor(d.applicability))
        .attr('stroke-linecap', 'round');
    }
    g.append('text')
      .attr('x', 36)
      .attr('y', 9)
      .style('font-size', '12px')
      .attr('class', 'small-muted')
      .text(d.label);
  });
}

async function loadScopeVisualisation() {
  const wrap = document.getElementById('scopeHeatmap');
  if (wrap) wrap.innerHTML = '<div class="small-muted p-3">Loading scope visualisation</div>';
  try {
    const data = await apiGet(`/api/v1/audits/${encodeURIComponent(auditId)}/scope-map`);
    renderScopeBipartiteGraph(data);
  } catch (e) {
    if (wrap)
      wrap.innerHTML = `<div class="text-danger small p-3">Failed to load scope visualisation: ${esc(errorText(e))}</div>`;
  }
}

function renderAttendeeUserOptions() {
  const sel = document.getElementById('attendeeUserId');
  if (!sel) return;
  const cur = sel.value || '';
  const rows = (attendeeUsers || [])
    .slice()
    .sort((a, b) => String(a.username || '').localeCompare(String(b.username || '')));
  sel.innerHTML = `<option value="">Custom / external attendee</option>${rows.map((u) => `<option value="${esc(u.id)}">${esc(u.username)}${u.email ? ` · ${esc(u.email)}` : ''}</option>`).join('')}`;
  sel.value = rows.some((u) => String(u.id) === cur) ? cur : '';
}

function renderAttendeePersonOptions() {
  const sel = document.getElementById('attendeePersonId');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML =
    '<option value="">Choose a person or enter a new name below</option>' +
    attendeePeople
      .map(
        (p) =>
          `<option value="${esc(p.id)}">${esc(p.name)}${p.email ? ` · ${esc(p.email)}` : ''}</option>`,
      )
      .join('');
  sel.value = attendeePeople.some((p) => p.id === current) ? current : '';
}

async function loadAttendeePeople() {
  if (!featureEnabled(me, 'people')) return;
  try {
    attendeePeople = (await apiGet('/api/v1/isms/people')).items || [];
    renderAttendeePersonOptions();
  } catch (err) {
    attendeePeople = [];
    renderAttendeePersonOptions();
  }
}

function suggestPersonCreation() {
  const button = document.getElementById('attendeeCreatePerson');
  if (!button) return;
  const name = document.getElementById('attendeeName')?.value.trim() || '';
  const email = document.getElementById('attendeeEmail')?.value.trim().toLowerCase() || '';
  const matching = attendeePeople.find((p) =>
    email
      ? p.email?.trim().toLowerCase() === email
      : p.name?.trim().toLowerCase() === name.toLowerCase(),
  );
  if (matching && !document.getElementById('attendeePersonId').value) {
    document.getElementById('attendeePersonId').value = matching.id;
    document.getElementById('attendeeUserId').value = '';
  }
  button.hidden = !(
    name &&
    !matching &&
    !document.getElementById('attendeePersonId')?.value &&
    (me?.is_admin || me?.can_manage_isms)
  );
}

async function loadAttendeeUsers() {
  if (!canManageAudits()) return;
  try {
    const data = await apiGet('/api/v1/audits/users');
    attendeeUsers = Array.isArray(data?.items) ? data.items : [];
    renderAttendeeUserOptions();
  } catch (e) {
    attendeeUsers = [];
    renderAttendeeUserOptions();
    toast(status, `Failed to load Keen users for attendees: ${errorText(e)}`, 'warning');
  }
}

function selectedAttendeeUser() {
  const id = document.getElementById('attendeeUserId')?.value || '';
  if (!id) return null;
  return (attendeeUsers || []).find((u) => String(u.id) === String(id)) || null;
}

function syncAttendeeUserSelection() {
  const user = selectedAttendeeUser();
  const nameEl = document.getElementById('attendeeName');
  const emailEl = document.getElementById('attendeeEmail');
  if (user && nameEl && !String(nameEl.value || '').trim()) {
    nameEl.value = user.username || '';
  }
  if (user && emailEl && !String(emailEl.value || '').trim()) {
    emailEl.value = user.email || '';
  }
}
document.getElementById('attendeePersonId')?.addEventListener('change', () => {
  const p = attendeePeople.find(
    (person) => person.id === document.getElementById('attendeePersonId').value,
  );
  if (p) {
    document.getElementById('attendeeUserId').value = '';
    document.getElementById('attendeeName').value = p.name;
    document.getElementById('attendeeEmail').value = p.email || '';
  }
  suggestPersonCreation();
});
for (const id of ['attendeeName', 'attendeeEmail'])
  document.getElementById(id)?.addEventListener('input', suggestPersonCreation);
document.getElementById('attendeeCreatePerson')?.addEventListener('click', async () => {
  try {
    const name = document.getElementById('attendeeName').value.trim();
    const email = document.getElementById('attendeeEmail').value.trim();
    const p = await apiPost('/api/v1/isms/people', { name, email });
    await loadAttendeePeople();
    document.getElementById('attendeePersonId').value = p.id;
    suggestPersonCreation();
    toast(status, 'Person saved to the directory. Add them to the audit below.', 'success');
  } catch (err) {
    toast(status, `Could not add Person: ${errorText(err)}`, 'danger');
  }
});

function renderAttendees(items) {
  const el = document.getElementById('attendees');
  const arr = items || [];
  if (!arr.length) {
    el.innerHTML = '<span class="small-muted">No people listed yet.</span>';
    return;
  }
  el.innerHTML = `<div class="list-group">${arr
    .map((x) => {
      const meta = [x.email, x.role].filter(Boolean);
      const who = x.user_id
        ? userPillHtml({ username: x.username || x.name, email: x.email })
        : esc(x.name || x.email || 'Attendee');
      return `<div class="list-group-item d-flex justify-content-between gap-2 align-items-start">
      <div>
        <div class="fw-semibold">${who}</div>
        <div class="small-muted">${esc(meta.join(' · ') || '-')}</div>
      </div>
      ${canEditAudit() ? `<button class="btn btn-sm btn-outline-danger" data-del-attendee="${esc(x.id)}">Remove</button>` : ''}
    </div>`;
    })
    .join('')}</div>`;
  el.querySelectorAll('[data-del-attendee]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      if (!ensureAuditEditable()) return;
      await apiDelete(
        `/api/v1/audits/${encodeURIComponent(auditId)}/attendees/${encodeURIComponent(btn.getAttribute('data-del-attendee'))}`,
      );
      await loadAudit({ preserveUnsaved: true });
    }),
  );
}

function findingKindOptions(current) {
  return `<option value="major_nc" ${selectedAttr('major_nc', current)}>Major non-conformity</option>
    <option value="minor_nc" ${selectedAttr('minor_nc', current)}>Minor non-conformity</option>
    <option value="ofi" ${selectedAttr('ofi', current)}>Opportunity for improvement</option>
    <option value="best_practice" ${selectedAttr('best_practice', current)}>Best practice</option>`;
}

function findingStatusOptions(current) {
  return `<option value="open" ${selectedAttr('open', current || 'open')}>Open</option>
    <option value="accepted" ${selectedAttr('accepted', current)}>Accepted</option>
    <option value="closed" ${selectedAttr('closed', current)}>Closed</option>`;
}

function renderFindings(items) {
  const el = document.getElementById('findings');
  const arr = items || [];
  if (!arr.length) {
    el.innerHTML = '<span class="small-muted">No findings yet.</span>';
    return;
  }
  el.innerHTML = arr
    .map(
      (f) => `<div class="border rounded p-3 mb-2" data-finding-card="${esc(f.id)}">
    <div class="d-flex justify-content-between gap-2 align-items-start">
      <div>
        <span class="badge text-bg-light me-2">${esc(kindLabel(f.kind))}</span>
        <span class="badge badge-soft me-2">${esc(findingStatusLabel(f.status))}</span>
        <span class="fw-semibold">${esc(f.title)}</span>
      </div>
      ${canEditAudit() ? `<div class="d-flex gap-2"><button class="btn btn-sm btn-outline-primary" data-edit-finding="${esc(f.id)}">Edit</button><button class="btn btn-sm btn-outline-danger" data-del-finding="${esc(f.id)}">Remove</button></div>` : ''}
    </div>
    ${f.control ? `<div class="small-muted mt-1">Control: ${esc(f.control.ref)} ${esc(f.control.title || '')}</div>` : ''}
    ${f.description ? `<div class="mt-2 rich-text-content">${renderRichTextContent(f.description, '')}</div>` : ''}
    ${
      canEditAudit()
        ? `<form class="row g-2 mt-3 d-none" data-finding-edit-form="${esc(f.id)}">
      <div class="col-12 col-md-4"><select class="form-select" data-finding-edit-kind="${esc(f.id)}">${findingKindOptions(f.kind)}</select></div>
      <div class="col-12 col-md-4"><input class="form-control" data-finding-edit-title="${esc(f.id)}" value="${esc(f.title || '')}" required /></div>
      <div class="col-12 col-md-4"><select class="form-select" data-finding-edit-status="${esc(f.id)}">${findingStatusOptions(f.status)}</select></div>
      <div class="col-12 col-md-4"><input class="form-control" data-finding-edit-control="${esc(f.id)}" value="${esc(f.control?.ref || '')}" placeholder="Control ref/id (optional)" /></div>
      <div class="col-12"><div class="rich-text-editor"><textarea class="form-control" rows="3" data-rich-text="1" data-finding-edit-desc="${esc(f.id)}">${esc(f.description || '')}</textarea></div></div>
      <div class="col-12 d-flex gap-2">
        <button class="btn btn-sm btn-primary" type="submit">Save finding</button>
        <button class="btn btn-sm btn-outline-secondary" type="button" data-cancel-finding="${esc(f.id)}">Cancel</button>
      </div>
    </form>`
        : ''
    }
  </div>`,
    )
    .join('');
  initRichTextEditors(el);
  refreshRichTextEditorStates(el);
  el.querySelectorAll('[data-edit-finding]').forEach((btn) =>
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-edit-finding');
      el.querySelector(`[data-finding-edit-form="${CSS.escape(id)}"]`)?.classList.remove('d-none');
    }),
  );
  el.querySelectorAll('[data-cancel-finding]').forEach((btn) =>
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-cancel-finding');
      el.querySelector(`[data-finding-edit-form="${CSS.escape(id)}"]`)?.classList.add('d-none');
    }),
  );
  el.querySelectorAll('[data-finding-edit-form]').forEach((form) =>
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!ensureAuditEditable()) return;
      const id = form.getAttribute('data-finding-edit-form');
      await apiPatch(
        `/api/v1/audits/${encodeURIComponent(auditId)}/findings/${encodeURIComponent(id)}`,
        {
          kind: el.querySelector(`[data-finding-edit-kind="${CSS.escape(id)}"]`)?.value,
          title: el.querySelector(`[data-finding-edit-title="${CSS.escape(id)}"]`)?.value,
          status: el.querySelector(`[data-finding-edit-status="${CSS.escape(id)}"]`)?.value,
          control:
            el.querySelector(`[data-finding-edit-control="${CSS.escape(id)}"]`)?.value || null,
          description:
            syncRichTextEditor(el.querySelector(`[data-finding-edit-desc="${CSS.escape(id)}"]`)) ||
            '',
        },
      );
      toast(status, 'Finding saved', 'success');
      await loadAudit({ preserveUnsaved: true });
    }),
  );
  el.querySelectorAll('[data-del-finding]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      if (!ensureAuditEditable()) return;
      await apiDelete(
        `/api/v1/audits/${encodeURIComponent(auditId)}/findings/${encodeURIComponent(btn.getAttribute('data-del-finding'))}`,
      );
      await loadAudit({ preserveUnsaved: true });
    }),
  );
}

function renderEvidence(items) {
  const el = document.getElementById('evidence');
  const arr = items || [];
  const bulk = canEditAudit();
  const selectAllWrap = document.getElementById('evidenceSelectAllWrap');
  if (selectAllWrap) selectAllWrap.hidden = !bulk || !arr.length;
  if (!arr.length) {
    el.innerHTML =
      '<span class="small-muted">No evidence sampled yet. Use the “Sample into audit” button on an event, risk or ISMS page, or paste a URL above.</span>';
    return;
  }
  el.innerHTML = arr
    .map((x) => {
      const ev = x.event;
      const entity = x.entity || null;
      const href =
        safeExternalHref(x.evidence_url) ||
        safeExternalHref(entity?.href) ||
        (ev?.id
          ? withFramework(`/event.html?id=${encodeURIComponent(ev.id)}`, audit?.framework_slug)
          : '#');
      const title = x.title || entity?.title || ev?.summary || x.evidence_url || 'Evidence';
      const controls = (x.controls || ev?.controls || entity?.controls || [])
        .map((c) => `<span class="badge badge-soft me-1">${esc(c.ref)}</span>`)
        .join('');
      const kind = entity?.kind || (ev ? 'Event evidence' : 'Evidence URL');
      const subtitle = entity?.subtitle ? ` · ${entity.subtitle}` : '';
      return `<div class="border rounded p-3 mb-3" data-evidence-card="${esc(x.id)}">
      <div class="d-flex flex-wrap gap-2 justify-content-between">
        <div class="d-flex flex-wrap align-items-start gap-2">
          ${bulk ? evidenceSelection.cellHtml(x.id) : ''}
          <div>
          <div class="mb-1"><span class="badge text-bg-light">${esc(kind)}</span></div>
          <a class="fw-semibold" href="${esc(href || '#')}">${esc(title)}</a>
          <div class="small-muted">Added ${esc(fmtTs(x.added_at))} by ${userPillHtml(x.added_by_username)}${ev?.timestamp ? ` · Event time ${esc(fmtTs(ev.timestamp))}` : ''}${esc(subtitle)}</div>
          <div class="mt-1">${controls}</div>
          </div>
        </div>
        ${canEditAudit() ? `<button class="btn btn-sm btn-outline-danger" data-del-evidence="${esc(x.id)}">Remove</button>` : ''}
      </div>
      <label class="form-label small-muted mb-1 mt-3">Audit notes</label>
      <div class="rich-text-editor"><textarea class="form-control" rows="3" data-rich-text="1" data-evidence-notes="${esc(x.id)}" ${canEditAudit() ? '' : 'readonly'}>${esc(x.notes || '')}</textarea></div>
      ${canEditAudit() ? `<div class="mt-2"><button class="btn btn-sm btn-outline-primary" data-save-evidence="${esc(x.id)}">Save notes</button></div>` : ''}
    </div>`;
    })
    .join('');
  initRichTextEditors(el);
  refreshRichTextEditorStates(el);
  el.querySelectorAll('[data-save-evidence]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      if (!ensureAuditEditable()) return;
      const id = btn.getAttribute('data-save-evidence');
      const notes =
        syncRichTextEditor(el.querySelector(`[data-evidence-notes="${CSS.escape(id)}"]`)) || '';
      await apiPatch(
        `/api/v1/audits/${encodeURIComponent(auditId)}/evidence/${encodeURIComponent(id)}`,
        { notes },
      );
      toast(status, 'Evidence notes saved', 'success');
      await loadAudit({ preserveUnsaved: true });
    }),
  );
  evidenceSelection.rendered();
  el.querySelectorAll('[data-del-evidence]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      if (!ensureAuditEditable()) return;
      await apiDelete(
        `/api/v1/audits/${encodeURIComponent(auditId)}/evidence/${encodeURIComponent(btn.getAttribute('data-del-evidence'))}`,
      );
      await loadAudit({ preserveUnsaved: true });
    }),
  );
}

async function loadAudit({ preserveUnsaved = false } = {}) {
  const unsavedState = preserveUnsaved ? captureUnsavedPageState() : null;
  if (!ensureAuditRead()) return;
  if (!auditId) {
    toast(status, 'Missing audit id', 'danger');
    return;
  }
  audit = await apiGet(`/api/v1/audits/${encodeURIComponent(auditId)}`);
  showMeta(audit);
  setForm(audit);
  renderReport(audit);
  renderKpis(audit);
  renderAttendees(audit.attendees || []);
  renderFindings(audit.findings || []);
  renderEvidence(audit.evidence || []);
  await loadScopeItems();
  await loadScopeVisualisation();
  restoreUnsavedPageState(unsavedState);
  applyAuditEditability();
}

function payloadDate(id) {
  if (id === 'auditStart') {
    const ok = auditStartWire?.syncHiddenFromText?.();
    if (ok === false) return undefined;
  }
  if (id === 'auditEnd') {
    const ok = auditEndWire?.syncHiddenFromText?.();
    if (ok === false) return undefined;
  }
  const v = document.getElementById(id)?.value || '';
  return v || null;
}

document.getElementById('detailsForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    syncRichTextEditors(document);
    const nextStatus = document.getElementById('auditStatus').value;
    const startDate = payloadDate('auditStart');
    const endDate = payloadDate('auditEnd');
    if (startDate === undefined || endDate === undefined) {
      toast(status, 'Enter dates using the configured date format.', 'warning');
      return;
    }
    if (auditLocked() && isAuditLocked(nextStatus)) {
      toast(
        status,
        'Choose Open or In progress to reopen this audit before editing it.',
        'warning',
      );
      return;
    }

    if (auditTemplate() && !auditLocked()) {
      await apiPatch(`/api/v1/audits/scheduled/${encodeURIComponent(auditId)}`, {
        title: document.getElementById('auditTitle').value,
        framework_slug: document.getElementById('auditFramework').value,
        audit_type: document.getElementById('auditType').value,
        start_date: startDate,
        end_date: endDate,
        executive_summary: document.getElementById('auditExecutiveSummary').value,
      });
      toast(status, 'Template details saved', 'success');
      await loadAudit({ preserveUnsaved: true });
      return;
    }

    const body = auditLocked()
      ? { status: nextStatus }
      : {
          title: document.getElementById('auditTitle').value,
          framework_slug: document.getElementById('auditFramework').value,
          status: nextStatus,
          audit_type: document.getElementById('auditType').value,
          start_date: startDate,
          end_date: endDate,
          executive_summary: document.getElementById('auditExecutiveSummary').value,
        };
    await apiPatch(`/api/v1/audits/${encodeURIComponent(auditId)}`, body);
    toast(status, auditLocked() ? 'Audit reopened' : 'Audit details saved', 'success');
    await loadAudit({ preserveUnsaved: !auditLocked() });
  } catch (e) {
    toast(status, `Failed: ${errorText(e)}`, 'danger');
  }
});

document.getElementById('templateScheduleForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    if (!ensureAuditEditable() || !auditTemplate()) return;
    const startDate = templateDates.startForSubmit(tmplScheduleStartWire);
    const untilOk = tmplUntilWire?.syncHiddenFromText?.() !== false;
    if (!startDate || !untilOk) {
      toast(status, 'Enter schedule dates using the configured date format.', 'warning');
      return;
    }
    await apiPatch(`/api/v1/audits/scheduled/${encodeURIComponent(auditId)}`, {
      start_date: startDate,
      schedule_date_rule: document.getElementById('tmplDateRule')?.value || 'exact',
      schedule_anchor_month: templateDates.anchorMonth(),
      schedule_recurrence: document.getElementById('tmplRecurrence')?.value || 'once',
      schedule_interval: Number(document.getElementById('tmplInterval')?.value || 1),
      schedule_until_date: tmplUntil?.value || null,
    });
    toast(status, 'Template schedule saved', 'success');
    await loadAudit({ preserveUnsaved: false });
  } catch (e) {
    toast(status, `Failed: ${errorText(e)}`, 'danger');
  }
});

document.getElementById('reportNotesForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    syncRichTextEditors(document);
    if (!ensureAuditEditable()) return;
    await apiPatch(`/api/v1/audits/${encodeURIComponent(auditId)}`, {
      report_notes: syncRichTextEditor(document.getElementById('auditNotes')),
    });
    toast(status, 'Report notes saved', 'success');
    await loadAudit({ preserveUnsaved: true });
  } catch (e) {
    toast(status, `Failed: ${errorText(e)}`, 'danger');
  }
});

document.getElementById('reportForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    if (!ensureAuditEditable()) return;
    const f = document.getElementById('reportFile')?.files?.[0];
    if (!f) throw new Error('Choose a report file first');
    const fd = new FormData();
    fd.append('file', f, f.name);
    await apiPostForm(`/api/v1/audits/${encodeURIComponent(auditId)}/report`, fd);
    document.getElementById('reportFile').value = '';
    toast(status, 'Final report uploaded', 'success');
    await loadAudit({ preserveUnsaved: true });
  } catch (e) {
    toast(status, `Failed: ${errorText(e)}`, 'danger');
  }
});

document.getElementById('documentFilter')?.addEventListener('input', renderDocuments);
document.getElementById('clauseFilter')?.addEventListener('input', renderClauses);
document.getElementById('controlFilter')?.addEventListener('input', renderControls);
document.getElementById('deselectDocuments')?.addEventListener('click', () => {
  if (!ensureAuditEditable()) return;
  selectedDocumentIds.clear();
  renderDocuments();
  toast(status, 'All ISMS documents deselected. Save scope to apply the change.', 'info');
});

document.getElementById('deselectClauses')?.addEventListener('click', () => {
  if (!ensureAuditEditable()) return;
  selectedClauseIds.clear();
  renderClauses();
  toast(status, 'All clauses deselected. Save scope to apply the change.', 'info');
});
document.getElementById('deselectControls')?.addEventListener('click', () => {
  if (!ensureAuditEditable()) return;
  selectedControlIds.clear();
  renderControls();
  toast(status, 'All controls deselected. Save scope to apply the change.', 'info');
});
document.getElementById('saveScope')?.addEventListener('click', async () => {
  try {
    if (!ensureAuditEditable()) return;
    _normaliseSelectedClausesToChildren();
    await apiPut(`/api/v1/audits/${encodeURIComponent(auditId)}/scope`, {
      ...(swotScopeLoaded ? { swot_ids: Array.from(selectedSwotIds) } : {}),
      ...(featureEnabled(me, 'documents') ? { documents: Array.from(selectedDocumentIds) } : {}),
      clauses: Array.from(selectedClauseIds),
      controls: Array.from(selectedControlIds),
    });
    toast(
      status,
      'Scope saved; linked controls for selected clauses were included automatically.',
      'success',
    );
    await loadAudit({ preserveUnsaved: true });
  } catch (e) {
    toast(status, `Failed: ${errorText(e)}`, 'danger');
  }
});

document.getElementById('attendeeUserId')?.addEventListener('change', syncAttendeeUserSelection);

document.getElementById('attendeeForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    if (!ensureAuditEditable()) return;
    const userId = document.getElementById('attendeeUserId')?.value || '';
    const personId = document.getElementById('attendeePersonId')?.value || '';
    const user = selectedAttendeeUser();
    const name = (document.getElementById('attendeeName')?.value || '').trim();
    const email = (document.getElementById('attendeeEmail')?.value || '').trim();
    if (!userId && !personId && !name && !email) throw new Error('Choose a person or enter a name');
    if (userId && personId) throw new Error('Select either a person or a Keen user');
    await apiPost(`/api/v1/audits/${encodeURIComponent(auditId)}/attendees`, {
      user_id: userId || null,
      person_id: personId || null,
      name: name || null,
      email: email || user?.email || null,
      role: document.getElementById('attendeeRole').value || null,
    });
    document.getElementById('attendeeUserId').value = '';
    document.getElementById('attendeePersonId').value = '';
    document.getElementById('attendeeName').value = '';
    document.getElementById('attendeeEmail').value = '';
    document.getElementById('attendeeRole').value = '';
    toast(status, 'Person added', 'success');
    await loadAudit({ preserveUnsaved: true });
  } catch (e) {
    toast(status, `Failed: ${errorText(e)}`, 'danger');
  }
});

document.getElementById('findingForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    syncRichTextEditors(document);
    if (!ensureAuditEditable()) return;
    await apiPost(`/api/v1/audits/${encodeURIComponent(auditId)}/findings`, {
      kind: document.getElementById('findingKind').value,
      title: document.getElementById('findingTitle').value,
      status: document.getElementById('findingStatus').value,
      control: document.getElementById('findingControl').value || null,
      description: syncRichTextEditor(document.getElementById('findingDesc')) || '',
    });
    document.getElementById('findingTitle').value = '';
    document.getElementById('findingStatus').value = 'open';
    document.getElementById('findingControl').value = '';
    setRichTextEditorValue('findingDesc', '');
    toast(status, 'Finding added', 'success');
    await loadAudit({ preserveUnsaved: true });
  } catch (e) {
    toast(status, `Failed: ${errorText(e)}`, 'danger');
  }
});

function applyManageVisibility() {
  if (canManageAudits()) return;
  [
    'detailsForm',
    'reportForm',
    'reportNotesForm',
    'urlEvidenceForm',
    'attendeeForm',
    'findingForm',
  ].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.add('d-none');
  });
  document.getElementById('saveScope')?.classList.add('d-none');
  document.getElementById('deselectDocuments')?.classList.add('d-none');
  document.getElementById('deselectClauses')?.classList.add('d-none');
  document.getElementById('deselectControls')?.classList.add('d-none');
  document
    .getElementById('status')
    ?.insertAdjacentHTML(
      'beforeend',
      '<div class="alert alert-info mt-2 mb-0">You have read-only access to this audit.</div>',
    );
}

applyManageVisibility();

document.getElementById('urlEvidenceForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    if (!ensureAuditEditable()) return;
    const url = document.getElementById('urlEvidence').value.trim();
    if (!url) return;
    await apiPost(`/api/v1/audits/${encodeURIComponent(auditId)}/evidence`, { evidence_url: url });
    document.getElementById('urlEvidence').value = '';
    toast(status, 'Evidence URL added', 'success');
    await loadAudit({ preserveUnsaved: true });
  } catch (e) {
    toast(status, `Failed: ${errorText(e)}`, 'danger');
  }
});

auditZipButton?.addEventListener('click', () => exportAuditZip());
auditZipCancel?.addEventListener('click', () => auditZipExport?.abort());
// Leaving the page stops the downloads instead of finishing them unseen.
window.addEventListener('pagehide', () => auditZipExport?.abort());
document.getElementById('deleteAudit')?.addEventListener('click', async () => {
  if (!canDeleteAudit()) {
    toast(status, 'Admin role required.', 'danger');
    return;
  }
  const title = audit?.title || 'this audit';
  if (
    !(await confirmAction({
      title: `Delete audit ${title}`,
      message:
        'This permanently removes the audit, its scope, attendees, sample links and findings. Events protected only by this audit become eligible for evidence retention or purge.',
      confirmLabel: 'Delete',
      danger: true,
    }))
  )
    return;
  try {
    await apiDelete(`/api/v1/audits/${encodeURIComponent(auditId)}`);
    toast(status, 'Audit deleted', 'success');
    location.href = withFramework('/audits.html', audit?.framework_slug || getCurrentFramework());
  } catch (e) {
    toast(status, `Failed: ${errorText(e)}`, 'danger');
  }
});

await loadAttendeeUsers();
await loadAttendeePeople();
await loadAudit();
focusRequestedSection();

// The new-audit wizard can open the audit at its report notes.
function focusRequestedSection() {
  if (qs('focus') !== 'notes') return;
  const form = document.getElementById('reportNotesForm');
  form?.scrollIntoView({ block: 'start' });
  const editor = form?.querySelector('[contenteditable]') || document.getElementById('auditNotes');
  editor?.focus?.();
}

async function loadSwotScope() {
  if (!featureEnabled(me, 'swot')) return;
  let host = document.getElementById('swotScope');
  if (!host) {
    host = document.createElement('section');
    host.id = 'swotScope';
    host.className = 'card card-body my-3';
    document.getElementById('scopeBody')?.prepend(host);
  }
  selectedSwotIds = new Set((audit?.scoped_swot || []).map((r) => r.id));
  let rows = audit?.scoped_swot || [];
  try {
    const data = await apiGet('/api/v1/isms/swot?limit=200');
    const byId = new Map([...data.items, ...rows].map((r) => [r.id, r]));
    rows = [...byId.values()];
    swotScopeLoaded = true;
  } catch {
    swotScopeLoaded = false;
  }
  host.innerHTML =
    '<h3 class="h5">SWOT scope</h3><p class="small-muted">Selected titles and quadrants are shared with audit readers. Save scope to apply. Up to 200 candidates are shown.</p>' +
    rows
      .map(
        (r) =>
          `<label class="d-block mb-2"><input type="checkbox" data-swot-scope="${esc(r.id)}" ${selectedSwotIds.has(r.id) ? 'checked' : ''} ${swotScopeLoaded ? '' : 'disabled'}> ${esc(r.title)} <span class="small-muted">${esc(r.kind)}</span></label>`,
      )
      .join('');
  host.querySelectorAll('[data-swot-scope]').forEach((box) =>
    box.addEventListener('change', () => {
      if (box.checked) selectedSwotIds.add(box.dataset.swotScope);
      else selectedSwotIds.delete(box.dataset.swotScope);
    }),
  );
}
