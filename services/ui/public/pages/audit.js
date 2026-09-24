import {initNavbar, apiGet, apiPatch, apiPost, apiPut, apiDelete, apiPostForm, esc, fmtTs, fmtTsFilename, toast, qs, getCurrentFramework, withFramework, isAuditLocked, auditStatusLabel, wireIsoDateTextField, isoDateToDisplay, dateFormatPlaceholder, initRichTextEditors, refreshRichTextEditorStates, renderRichTextContent, setRichTextEditorValue, syncRichTextEditor, syncRichTextEditors, userPillHtml} from '/app.js';

const me = await initNavbar();

const _eventDataMaskingMode = String(me?.event_data_masking || 'false').trim().toLowerCase();
function _maskSampleExportsEnabled() {
  return _eventDataMaskingMode === 'samples' || _eventDataMaskingMode === 'true';
}
function _isValidIpv4Literal(value) {
  return /^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/.test(String(value || ''));
}
function _isMaskableIpv6Candidate(raw) {
  const candidate = String(raw || '').split('%', 1)[0];
  if (!candidate || !candidate.includes(':')) return false;
  if (/[^0-9A-Fa-f:.]/.test(candidate)) return false;
  const compressionMatches = candidate.match(/::/g) || [];
  if (compressionMatches.length > 1) return false;
  const hasCompression = compressionMatches.length === 1;
  const colonCount = (candidate.match(/:/g) || []).length;
  // Avoid treating ordinary times like 13:40:19 as IPv6 addresses.
  if (!hasCompression && colonCount !== 7) return false;
  if (hasCompression && colonCount < 2) return false;

  const parts = candidate.split(':');
  const last = parts[parts.length - 1] || '';
  const hasIpv4Tail = last.includes('.');
  if (parts.some((part, idx) => part.includes('.') && idx !== parts.length - 1)) return false;
  if (hasIpv4Tail && !_isValidIpv4Literal(last)) return false;

  const hexParts = hasIpv4Tail ? parts.slice(0, -1) : parts;
  if (!hasCompression && hexParts.some((part) => part === '')) return false;
  for (const part of hexParts) {
    if (!part) continue;
    if (!/^[0-9A-Fa-f]{1,4}$/.test(part)) return false;
  }

  const groupCount = hexParts.filter(Boolean).length + (hasIpv4Tail ? 2 : 0);
  return hasCompression ? groupCount < 8 : groupCount === 8;
}
function maskEventDataString(value) {
  if (value == null) return value;
  let txt = String(value);
  txt = txt.replace(/(^|[^A-Za-z0-9._%+-])([A-Za-z0-9._%+-]{1,128})@([A-Za-z0-9.-]+\.[A-Za-z]{2,63})(?![A-Za-z0-9._%+-])/g, '$1[MASKED_EMAIL]');
  txt = txt.replace(/(^|[^\w:.])([A-Fa-f0-9:.]+(?::[A-Fa-f0-9:.]*)+(?:%[A-Za-z0-9_.-]+)?)(?![\w:])/g, (match, prefix, raw) => {
    if (!_isMaskableIpv6Candidate(raw)) return match;
    return `${prefix}[MASKED_IP]`;
  });
  txt = txt.replace(/(^|[^\w.])((?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})(?![\w.])/g, '$1[MASKED_IP]');
  return txt;
}
function _maskExportDom(root) {
  if (!root || !_maskSampleExportsEnabled()) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node?.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  for (const node of textNodes) node.nodeValue = maskEventDataString(node.nodeValue);
  root.querySelectorAll('input, textarea').forEach((el) => {
    if ('value' in el) el.value = maskEventDataString(el.value);
  });
  root.querySelectorAll('[title], [alt], [aria-label]').forEach((el) => {
    for (const attr of ['title', 'alt', 'aria-label']) {
      if (el.hasAttribute(attr)) el.setAttribute(attr, maskEventDataString(el.getAttribute(attr) || ''));
    }
  });
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

const auditStart = document.getElementById('auditStart');
const auditEnd = document.getElementById('auditEnd');
const auditStartWire = wireIsoDateTextField(auditStart, document.getElementById('auditStartText'), document.getElementById('auditStartPick'), {dispatchChangeOnText: true});
const auditEndWire = wireIsoDateTextField(auditEnd, document.getElementById('auditEndText'), document.getElementById('auditEndPick'), {dispatchChangeOnText: true});
const tmplScheduleStart = document.getElementById('tmplScheduleStart');
const tmplUntil = document.getElementById('tmplUntil');
const tmplScheduleStartWire = wireIsoDateTextField(tmplScheduleStart, document.getElementById('tmplScheduleStartText'), document.getElementById('tmplScheduleStartPick'), {dispatchChangeOnText: true});
const tmplUntilWire = wireIsoDateTextField(tmplUntil, document.getElementById('tmplUntilText'), document.getElementById('tmplUntilPick'), {dispatchChangeOnText: true});
for (const el of ['auditStartText', 'auditEndText', 'tmplScheduleStartText', 'tmplUntilText'].map((id) => document.getElementById(id))) {
  if (el) el.setAttribute('placeholder', dateFormatPlaceholder());
}

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

function _todayYear() {
  return new Date().getFullYear();
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
  const y = Number(year) || _todayYear();
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

function _isoYear(iso, fallback = _todayYear()) {
  const m = String(iso || '').match(/^(\d{4})-/);
  return m ? Number(m[1]) : fallback;
}

function _isoMonth(iso, fallback = new Date().getMonth() + 1) {
  const m = String(iso || '').match(/^\d{4}-(\d{2})-/);
  return m ? Number(m[1]) : fallback;
}

function updateTemplateScheduleDateMode() {
  const mode = document.getElementById('tmplDateRule')?.value || 'exact';
  const fuzzy = _isFuzzyScheduleDateRule(mode);
  const exactFields = document.getElementById('tmplExactDateFields');
  const fuzzyFields = document.getElementById('tmplFuzzyDateFields');
  if (exactFields) exactFields.style.display = fuzzy ? 'none' : '';
  if (fuzzyFields) fuzzyFields.style.display = fuzzy ? '' : 'none';

  const recurrence = document.getElementById('tmplRecurrence');
  const weekly = recurrence?.querySelector('option[value="weekly"]');
  if (weekly) weekly.disabled = fuzzy;
  if (fuzzy && recurrence?.value === 'weekly') recurrence.value = 'yearly';

  if (fuzzy) {
    const year = Number(document.getElementById('tmplFuzzyYear')?.value || _isoYear(tmplScheduleStart?.value));
    const month = Number(document.getElementById('tmplFuzzyMonth')?.value || _isoMonth(tmplScheduleStart?.value));
    const iso = _firstScheduleRuleDateIso(year, month, mode);
    const label = _fuzzyScheduleRuleLabel(mode);
    const monthLabel = document.getElementById('tmplFuzzyMonthLabel');
    if (monthLabel) monthLabel.textContent = `${label.charAt(0).toUpperCase()}${label.slice(1)} of`;
    if (tmplScheduleStart) tmplScheduleStart.value = iso;
    if (document.getElementById('tmplScheduleStartText')) document.getElementById('tmplScheduleStartText').value = isoDateToDisplay(iso);
    const preview = document.getElementById('tmplSchedulePreview');
    if (preview) preview.textContent = `Next run will be ${isoDateToDisplay(iso)}; future runs keep the ${label} rule.`;
  } else {
    const preview = document.getElementById('tmplSchedulePreview');
    if (preview) preview.textContent = '';
  }
}

function _templateScheduleStartForSubmit() {
  updateTemplateScheduleDateMode();
  if (_isFuzzyScheduleDateRule(document.getElementById('tmplDateRule')?.value || 'exact')) {
    return tmplScheduleStart?.value || '';
  }
  const ok = tmplScheduleStartWire?.syncHiddenFromText?.();
  return ok === false ? '' : (tmplScheduleStart?.value || '');
}

function canViewAudits() {
  return Boolean(me?.can_view_audits || me?.can_manage_audits || me?.is_admin);
}

function canManageAudits() {
  return Boolean(me?.can_manage_audits || me?.is_admin);
}

function canDeleteAudit() {
  return Boolean(me?.is_admin && audit && !auditTemplate());
}

function auditLocked() {
  return isAuditLocked(audit);
}

function auditTemplate() {
  return String(audit?.status || '').trim().toLowerCase() === 'template';
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
  else toast(status, 'audits.manage permission required.', 'danger');
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
  const notice = document.getElementById('auditLockNotice');
  if (notice) {
    notice.style.display = locked ? '' : 'none';
    notice.textContent = locked ? auditLockedMessage() : '';
  }

  ['auditTitle', 'auditFramework', 'auditType', 'auditStart', 'auditEnd', 'auditStartText', 'auditStartPick', 'auditEndText', 'auditEndPick', 'auditExecutiveSummary'].forEach((id) => _setDisabled(id, !canEdit));
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
  if (v === 'major_nc') return 'Major Non-Conformity';
  if (v === 'minor_nc') return 'Minor Non-Conformity';
  if (v === 'ofi') return 'Opportunity for Improvement';
  if (v === 'best_practice') return 'Best Practice';
  return v || 'Finding';
}

function statusLabel(s) {
  const v = String(s || 'open');
  if (v === 'in_progress') return 'In progress';
  return v.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase());
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
  const dates = [a?.start_date, a?.end_date].filter(Boolean).map((d) => isoDateToDisplay(d)).join(' → ') || 'No dates set';
  document.getElementById('meta').innerHTML = `${esc(a?.framework_slug || '')} · ${esc(auditTypeLabel(a?.audit_type))} · ${esc(statusLabel(a?.status))} · ${esc(dates)} · Author: ${userPillHtml(a?.created_by_username)}`;
  document.getElementById('sampleEventLink').href = withFramework('/events.html', a?.framework_slug || getCurrentFramework());
}

function setTemplateScheduleForm(a) {
  const start = a?.schedule_next_run_date || a?.start_date || '';
  const dateRule = a?.schedule_date_rule || 'exact';
  setFieldValue('tmplDateRule', dateRule);
  setFieldValue('tmplScheduleStart', start);
  setFieldValue('tmplRecurrence', a?.schedule_recurrence || 'once');
  setFieldValue('tmplInterval', String(a?.schedule_interval || 1));
  setFieldValue('tmplUntil', a?.schedule_until_date || '');
  setFieldValue('tmplFuzzyMonth', String(a?.schedule_anchor_month || _isoMonth(start)));
  setFieldValue('tmplFuzzyYear', String(_isoYear(start)));
  tmplScheduleStartWire?.syncTextFromHidden?.();
  tmplUntilWire?.syncTextFromHidden?.();
  updateTemplateScheduleDateMode();

  const info = document.getElementById('tmplScheduleInfo');
  if (info) {
    const bits = [];
    if (a?.schedule_last_run_date) bits.push(`Last materialised: ${isoDateToDisplay(a.schedule_last_run_date)}`);
    if (a?.schedule_next_run_date) bits.push(`Next run: ${isoDateToDisplay(a.schedule_next_run_date)}`);
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
  updateTemplateScheduleDateMode();

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
  document.getElementById('kpiEvidence').textContent = String(a?.evidence_count ?? (a?.evidence || []).length ?? 0);
  const documentCount = Number(a?.document_scope_count ?? (a?.scoped_documents || []).length ?? 0);
  const controlCount = Number(a?.control_scope_count ?? (a?.scoped_controls || []).length ?? 0);
  const clauseCount = Number(a?.clause_scope_count ?? (a?.scoped_clauses || []).length ?? 0);
  const scopeEl = document.getElementById('kpiScope');
  scopeEl.textContent = String(a?.scope_count ?? (documentCount + controlCount + clauseCount));
  scopeEl.title = `${documentCount} documents, ${clauseCount} clauses, ${controlCount} controls`;
  document.getElementById('kpiPeople').textContent = String(a?.attendee_count ?? (a?.attendees || []).length ?? 0);
  document.getElementById('kpiFindings').textContent = String(a?.finding_count ?? (a?.findings || []).length ?? 0);
}


// ------------------------------------------------------------------
// Audit ZIP/PDF export helpers
// ------------------------------------------------------------------

const _A4_PORTRAIT = {w: 595.28, h: 841.89};
const _utf8 = new TextEncoder();

function _pageSize() {
  return _A4_PORTRAIT;
}

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

function _extFromContentType(ct) {
  const t = String(ct || '').toLowerCase();
  if (t.includes('application/pdf')) return '.pdf';
  if (t.includes('application/json')) return '.json';
  if (t.includes('text/plain')) return '.txt';
  if (t.includes('text/html')) return '.html';
  if (t.includes('text/csv')) return '.csv';
  if (t.includes('image/jpeg')) return '.jpg';
  if (t.includes('image/png')) return '.png';
  if (t.includes('image/gif')) return '.gif';
  if (t.includes('image/webp')) return '.webp';
  if (t.includes('application/zip')) return '.zip';
  return '';
}

function _parseContentDispositionFilename(header) {
  const h = String(header || '');
  if (!h) return '';
  const mStar = h.match(/filename\*\s*=\s*([^;]+)/i);
  if (mStar) {
    let v = mStar[1].trim().replace(/^"|"$/g, '');
    const parts = v.split("''");
    if (parts.length === 2) {
      try { return decodeURIComponent(parts[1]); } catch { return parts[1]; }
    }
    return v;
  }
  const m = h.match(/filename\s*=\s*"?([^";]+)"?/i);
  return m ? m[1].trim() : '';
}

function _uniqueZipName(name, seen) {
  let n = String(name || 'file').replace(/\\+/g, '/').replace(/\/+/g, '/');
  n = n.split('/').map((p, idx) => _safeZipPathSegment(p, idx === 0 ? 'folder' : 'file')).join('/');
  if (!n || n === '/') n = 'file';
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

async function _waitForImages(root, timeoutMs = 15_000) {
  if (!root) return;
  const imgs = Array.from(root.querySelectorAll('img'));
  const pending = imgs.filter((img) => img && img.src).map((img) => {
    if (img.complete && img.naturalWidth > 0) return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => {
        img.removeEventListener('load', done);
        img.removeEventListener('error', done);
        resolve();
      };
      img.addEventListener('load', done);
      img.addEventListener('error', done);
    });
  });
  if (!pending.length) return;
  await Promise.race([Promise.all(pending), new Promise((r) => setTimeout(r, timeoutMs))]);
}

function _lookupOriginalControlValue(cloned) {
  const id = cloned.getAttribute('id');
  if (id) {
    const src = document.getElementById(id);
    if (src) return {value: src.value ?? '', checked: !!src.checked, selectedIndex: src.selectedIndex};
  }
  const evidenceNotesId = cloned.getAttribute('data-evidence-notes');
  if (evidenceNotesId) {
    const src = document.querySelector(`[data-evidence-notes="${CSS.escape(evidenceNotesId)}"]`);
    if (src) return {value: src.value ?? '', checked: !!src.checked, selectedIndex: src.selectedIndex};
  }
  return {value: cloned.value ?? cloned.textContent ?? '', checked: !!cloned.checked, selectedIndex: cloned.selectedIndex};
}

function _formStaticBlock(text, extraClass = '', {richText = false} = {}) {
  const div = document.createElement('div');
  div.className = `form-control bg-white export-static-field ${extraClass}`.trim();
  div.style.whiteSpace = 'pre-wrap';
  div.style.height = 'auto';
  div.style.minHeight = '2.4rem';
  if (richText) {
    div.classList.add('rich-text-content');
    div.innerHTML = renderRichTextContent(text, '<span class="small-muted">—</span>');
  } else {
    div.textContent = String(text || '').trim() || '—';
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
  wrap.innerHTML = arr.map((item) => `
    <div class="border rounded p-2 mb-2 bg-white">
      <span class="fw-semibold">${esc(item.ref || '')}</span>
      <span class="small-muted">${esc(item.title || '')}</span>
    </div>`).join('');
  return wrap;
}

function _replaceAuditScopeListsForExport(clone) {
  const documentsList = clone.querySelector('#documentsList');
  const clausesList = clone.querySelector('#clausesList');
  const controlsList = clone.querySelector('#controlsList');
  const documentFilter = clone.querySelector('#documentFilter');
  const clauseFilter = clone.querySelector('#clauseFilter');
  const controlFilter = clone.querySelector('#controlFilter');
  if (documentFilter) documentFilter.remove();
  if (clauseFilter) clauseFilter.remove();
  if (controlFilter) controlFilter.remove();
  if (documentsList) documentsList.replaceWith(_scopeExportList(audit?.scoped_documents || [], 'No ISMS documents selected for this audit.'));
  if (clausesList) clausesList.replaceWith(_scopeExportList(audit?.scoped_clauses || [], 'No clauses selected for this audit.'));
  if (controlsList) controlsList.replaceWith(_scopeExportList(audit?.scoped_controls || [], 'No controls selected for this audit.'));
  const documentCount = clone.querySelector('#documentScopeCount');
  const clauseCount = clone.querySelector('#clauseScopeCount');
  const controlCount = clone.querySelector('#controlScopeCount');
  if (documentCount) documentCount.textContent = `${(audit?.scoped_documents || []).length} in scope`;
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
  clone.querySelectorAll('#reportForm, #urlEvidenceForm, #attendeeForm, #findingForm, [data-finding-edit-form]').forEach((el) => el.remove());
  _replaceAuditScopeListsForExport(clone);

  clone.querySelectorAll('textarea').forEach((el) => {
    const {value} = _lookupOriginalControlValue(el);
    const block = _formStaticBlock(value, 'export-static-textarea', {richText: el.matches?.('textarea[data-rich-text]')});
    block.style.minHeight = `${Math.max(3, Number(el.getAttribute('rows') || 3)) * 1.55}rem`;
    el.replaceWith(block);
  });

  clone.querySelectorAll('select').forEach((el) => {
    const {selectedIndex} = _lookupOriginalControlValue(el);
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
    const {value, checked} = _lookupOriginalControlValue(el);
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
  clone.querySelectorAll('#clausesList, #controlsList').forEach((el) => {
    el.style.maxHeight = 'none';
    el.style.overflow = 'visible';
  });
  clone.querySelectorAll('[style]').forEach((el) => {
    const st = el.getAttribute('style') || '';
    if (/max-height\s*:\s*340px/i.test(st)) {
      el.style.maxHeight = 'none';
      el.style.overflow = 'visible';
    }
  });
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

async function _canvasToJpegBytes(canvas, quality = 0.92) {
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
  if (!blob) throw new Error('Failed to encode PDF page image');
  return new Uint8Array(await blob.arrayBuffer());
}

async function _sliceCanvasToA4JpegPages(canvas, header, footerText) {
  const {w: pageW, h: pageH} = _pageSize();
  const pages = [];
  const w = canvas.width;
  const ratio = pageW / w;
  const pageHeightPx = Math.max(1, Math.round(pageH / ratio));
  const headerPt = header ? 24 : 0;
  const footerPt = 24;
  const headerPx = Math.round(headerPt / ratio);
  const footerPx = Math.round(footerPt / ratio);
  const contentHeightPx = Math.max(1, pageHeightPx - headerPx - footerPx);
  const fontPx = Math.max(10, Math.round(10 / ratio));

  const cuts = [];
  for (let y = 0; y < canvas.height; y += contentHeightPx) {
    cuts.push({y, h: Math.min(contentHeightPx, canvas.height - y)});
  }
  const totalPages = cuts.length || 1;

  for (let i = 0; i < cuts.length; i++) {
    const {y: y0, h: sliceH} = cuts[i];
    const page = document.createElement('canvas');
    page.width = w;
    page.height = pageHeightPx;
    const ctx = page.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, w, pageHeightPx);

    if (headerPx > 0) {
      ctx.strokeStyle = '#ddd';
      ctx.beginPath();
      ctx.moveTo(0, headerPx - 0.5);
      ctx.lineTo(w, headerPx - 0.5);
      ctx.stroke();
      ctx.fillStyle = '#666';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.font = `${fontPx}px system-ui, -apple-system, Segoe UI, Roboto, sans-serif`;
      ctx.fillText(String(header), w / 2, headerPx / 2, w - 20);
    }

    ctx.drawImage(canvas, 0, y0, w, sliceH, 0, headerPx, w, sliceH);

    const top = pageHeightPx - footerPx;
    ctx.strokeStyle = '#ddd';
    ctx.beginPath();
    ctx.moveTo(0, top + 0.5);
    ctx.lineTo(w, top + 0.5);
    ctx.stroke();
    ctx.fillStyle = '#666';
    ctx.textBaseline = 'middle';
    ctx.font = `${fontPx}px system-ui, -apple-system, Segoe UI, Roboto, sans-serif`;
    ctx.textAlign = 'left';
    ctx.fillText(String(footerText || ''), 8, top + footerPx / 2, Math.floor(w * 0.72));
    ctx.textAlign = 'right';
    ctx.fillText(`Page ${i + 1} of ${totalPages}`, w - 8, top + footerPx / 2);

    pages.push({bytes: await _canvasToJpegBytes(page, 0.92), w, h: pageHeightPx});
  }
  return pages;
}

function _buildPdfFromJpegPages(pages) {
  if (!pages.length) throw new Error('No pages to export');
  const {w: pageW, h: pageH} = _pageSize();
  const parts = [];
  let offset = 0;
  const offsets = [0];
  const push = (u8) => { parts.push(u8); offset += u8.length; };
  const pushStr = (str) => push(_utf8.encode(str));

  pushStr('%PDF-1.4\n');
  push(new Uint8Array([0x25, 0xE2, 0xE3, 0xCF, 0xD3, 0x0A]));

  let nextObj = 1;
  const beginObj = (id) => { offsets[id] = offset; pushStr(`${id} 0 obj\n`); };
  const endObj = () => pushStr('\nendobj\n');
  const catalogObj = nextObj++;
  const pagesObj = nextObj++;
  const pageObjs = [];
  const imgObjs = [];
  const contentObjs = [];
  for (let i = 0; i < pages.length; i++) {
    pageObjs.push(nextObj++);
    imgObjs.push(nextObj++);
    contentObjs.push(nextObj++);
  }

  beginObj(catalogObj);
  pushStr(`<< /Type /Catalog /Pages ${pagesObj} 0 R >>`);
  endObj();

  beginObj(pagesObj);
  pushStr(`<< /Type /Pages /Count ${pages.length} /Kids [`);
  for (const po of pageObjs) pushStr(` ${po} 0 R`);
  pushStr(' ] >>');
  endObj();

  for (let i = 0; i < pages.length; i++) {
    const p = pages[i];
    const pageObj = pageObjs[i];
    const imgObj = imgObjs[i];
    const contentObj = contentObjs[i];
    const imgName = `Im${i + 1}`;

    beginObj(imgObj);
    pushStr(`<< /Type /XObject /Subtype /Image /Width ${p.w} /Height ${p.h} `);
    pushStr(`/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${p.bytes.length} >>\n`);
    pushStr('stream\n');
    push(p.bytes);
    pushStr('\nendstream');
    endObj();

    const content = `q ${pageW} 0 0 ${pageH} 0 0 cm /${imgName} Do Q`;
    const contentBytes = _utf8.encode(content);
    beginObj(contentObj);
    pushStr(`<< /Length ${contentBytes.length} >>\nstream\n`);
    push(contentBytes);
    pushStr('\nendstream');
    endObj();

    beginObj(pageObj);
    pushStr(`<< /Type /Page /Parent ${pagesObj} 0 R `);
    pushStr(`/Resources << /XObject << /${imgName} ${imgObj} 0 R >> >> `);
    pushStr(`/MediaBox [0 0 ${pageW} ${pageH}] /Contents ${contentObj} 0 R >>`);
    endObj();
  }

  const xrefOffset = offset;
  pushStr('xref\n');
  pushStr(`0 ${nextObj}\n`);
  pushStr('0000000000 65535 f \n');
  for (let i = 1; i < nextObj; i++) {
    pushStr(`${String(offsets[i] || 0).padStart(10, '0')} 00000 n \n`);
  }
  pushStr('trailer\n');
  pushStr(`<< /Size ${nextObj} /Root ${catalogObj} 0 R >>\n`);
  pushStr('startxref\n');
  pushStr(`${xrefOffset}\n%%EOF`);
  return new Blob(parts, {type: 'application/pdf'});
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
  const header = _sampleMaskedFilename(`Keen audit export ${fmtTsFilename(exportedIso)} - ${a?.title || a?.id || ''}`);
  const footer = (me?.sample_pdf_footer || '').trim();
  const wrap = _makeAuditExportContainer(width);
  try {
    await _waitForImages(wrap);
    const canvas = await h2c(wrap, {
      scale: 2,
      backgroundColor: '#fff',
      useCORS: true,
      windowWidth: exportWindowWidth,
      windowHeight: Math.max(900, Math.ceil(wrap.scrollHeight || 0) + 120),
    });
    const pages = await _sliceCanvasToA4JpegPages(canvas, header, footer);
    return _buildPdfFromJpegPages(pages);
  } finally {
    try { wrap.remove(); } catch {}
  }
}

const _crcTable = (() => {
  const tbl = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    tbl[n] = c >>> 0;
  }
  return tbl;
})();

function _crc32(buf) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < buf.length; i++) c = _crcTable[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function _dosTimeDate(d) {
  const dt = (d instanceof Date && !Number.isNaN(d.valueOf())) ? d : new Date();
  let year = dt.getFullYear();
  if (year < 1980) year = 1980;
  const month = dt.getMonth() + 1;
  const day = dt.getDate();
  const hours = dt.getHours();
  const minutes = dt.getMinutes();
  const seconds = Math.floor(dt.getSeconds() / 2);
  return {time: (hours << 11) | (minutes << 5) | seconds, date: ((year - 1980) << 9) | (month << 5) | day};
}

function _u8(viewBuf) { return new Uint8Array(viewBuf); }

function _zipLocalHeader(nameLen, crc, size, time, date) {
  const buf = new ArrayBuffer(30);
  const dv = new DataView(buf);
  dv.setUint32(0, 0x04034b50, true);
  dv.setUint16(4, 20, true);
  dv.setUint16(6, 0x0800, true);
  dv.setUint16(8, 0, true);
  dv.setUint16(10, time, true);
  dv.setUint16(12, date, true);
  dv.setUint32(14, crc, true);
  dv.setUint32(18, size, true);
  dv.setUint32(22, size, true);
  dv.setUint16(26, nameLen, true);
  dv.setUint16(28, 0, true);
  return _u8(buf);
}

function _zipCentralHeader(nameLen, crc, size, time, date, localOffset) {
  const buf = new ArrayBuffer(46);
  const dv = new DataView(buf);
  dv.setUint32(0, 0x02014b50, true);
  dv.setUint16(4, 20, true);
  dv.setUint16(6, 20, true);
  dv.setUint16(8, 0x0800, true);
  dv.setUint16(10, 0, true);
  dv.setUint16(12, time, true);
  dv.setUint16(14, date, true);
  dv.setUint32(16, crc, true);
  dv.setUint32(20, size, true);
  dv.setUint32(24, size, true);
  dv.setUint16(28, nameLen, true);
  dv.setUint16(30, 0, true);
  dv.setUint16(32, 0, true);
  dv.setUint16(34, 0, true);
  dv.setUint16(36, 0, true);
  dv.setUint32(38, 0, true);
  dv.setUint32(42, localOffset, true);
  return _u8(buf);
}

function _zipEndRecord(count, cdSize, cdOffset) {
  const buf = new ArrayBuffer(22);
  const dv = new DataView(buf);
  dv.setUint32(0, 0x06054b50, true);
  dv.setUint16(4, 0, true);
  dv.setUint16(6, 0, true);
  dv.setUint16(8, count, true);
  dv.setUint16(10, count, true);
  dv.setUint32(12, cdSize, true);
  dv.setUint32(16, cdOffset, true);
  dv.setUint16(20, 0, true);
  return _u8(buf);
}

function buildZipStore(files) {
  const parts = [];
  const central = [];
  let offset = 0;
  let cdSize = 0;

  for (const f of files) {
    const nameBytes = _utf8.encode(String(f.name || 'file'));
    const data = (f.data instanceof Uint8Array) ? f.data : new Uint8Array(f.data || []);
    const crc = _crc32(data);
    const {time, date} = _dosTimeDate(f.mtime);
    const local = _zipLocalHeader(nameBytes.length, crc, data.length, time, date);
    parts.push(local, nameBytes, data);
    const localOffset = offset;
    offset += local.length + nameBytes.length + data.length;
    const cd = _zipCentralHeader(nameBytes.length, crc, data.length, time, date, localOffset);
    central.push(cd, nameBytes);
    cdSize += cd.length + nameBytes.length;
  }

  const cdOffset = offset;
  parts.push(...central);
  parts.push(_zipEndRecord(files.length, cdSize, cdOffset));
  return new Blob(parts, {type: 'application/zip'});
}

async function _fetchBytes(url) {
  const res = await fetch(url, {credentials: 'include'});
  if (!res.ok) throw new Error(`Failed to download ${url} (HTTP ${res.status})`);
  const dispName = _parseContentDispositionFilename(res.headers.get('Content-Disposition') || '');
  const contentType = res.headers.get('Content-Type') || '';
  return {
    data: new Uint8Array(await res.arrayBuffer()),
    filename: dispName,
    contentType,
  };
}

function _downloadBlob(blob, filename) {
  const obj = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = obj;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => {
    try { URL.revokeObjectURL(obj); } catch {}
  }, 15_000);
}

async function exportAuditZip() {
  if (!audit) {
    toast(status, 'Audit not loaded yet', 'warning');
    return;
  }
  const btn = document.getElementById('btnAuditZip');
  const prevLabel = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Preparing ZIP…';
  }

  try {
    const stem = auditFileStem(audit);
    const files = [];
    const seenNames = new Map();

    toast(status, 'Rendering audit page PDF…', 'info');
    const pdfBlob = await buildAuditPagePdfBlob(audit);
    files.push({
      name: _uniqueZipName(`${stem}-audit-page.pdf`, seenNames),
      data: new Uint8Array(await pdfBlob.arrayBuffer()),
      mtime: new Date(),
    });

    if (audit.final_report?.download_url) {
      toast(status, 'Adding final report…', 'info');
      const finalReport = await _fetchBytes(audit.final_report.download_url);
      const reportName = finalReport.filename || audit.final_report.filename || `final-report${_extFromContentType(finalReport.contentType || audit.final_report.content_type)}`;
      files.push({
        name: _uniqueZipName(`final-report/${reportName}`, seenNames),
        data: finalReport.data,
        mtime: audit.final_report.uploaded_at ? new Date(audit.final_report.uploaded_at) : new Date(),
      });
    }

    const evidenceItems = Array.isArray(audit.evidence) ? audit.evidence : [];
    let artifactCount = 0;
    for (let i = 0; i < evidenceItems.length; i++) {
      const item = evidenceItems[i];
      const eventId = item.event_id || item.event?.id;
      if (!eventId) continue;
      toast(status, `Collecting sampled evidence ${i + 1}/${evidenceItems.length}…`, 'info');
      const eventData = await apiGet(_sampleExportUrl(`/api/v1/events/${encodeURIComponent(eventId)}?framework=${encodeURIComponent(audit.framework_slug || getCurrentFramework())}`));
      const artifacts = Array.isArray(eventData?.artifacts) ? eventData.artifacts : [];
      if (!artifacts.length) continue;
      const eventStamp = eventData?.timestamp ? fmtTsFilename(eventData.timestamp) : String(i + 1).padStart(3, '0');
      const eventLabel = _safeZipPathSegment(`${String(i + 1).padStart(3, '0')} ${eventStamp} ${eventData?.source || 'event'} ${String(eventId).slice(0, 8)}`, `evidence-${i + 1}`);

      for (let j = 0; j < artifacts.length; j++) {
        const art = artifacts[j];
        artifactCount += 1;
        toast(status, `Downloading sampled evidence artifact ${artifactCount}…`, 'info');
        const downloaded = await _fetchBytes(_sampleExportUrl(`/api/v1/artifacts/${encodeURIComponent(art.id)}/download`));
        const ext = downloaded.filename ? '' : _extFromContentType(downloaded.contentType || art.content_type);
        const artifactName = _sampleMaskedFilename(downloaded.filename || `artifact-${art.id}${ext}`);
        files.push({
          name: _uniqueZipName(`sampled-evidence/${eventLabel}/${artifactName}`, seenNames),
          data: downloaded.data,
          mtime: art.captured_at ? new Date(art.captured_at) : new Date(),
        });
      }
    }

    toast(status, 'Building ZIP…', 'info');
    const zipBlob = buildZipStore(files);
    _downloadBlob(zipBlob, `${stem}.zip`);
    const extra = [];
    if (audit.final_report?.download_url) extra.push('final report');
    if (artifactCount) extra.push(`${artifactCount} sampled evidence attachment${artifactCount === 1 ? '' : 's'}`);
    toast(status, `Audit ZIP download started${extra.length ? ` (${extra.join(', ')})` : ''}`, 'success', 3000);
    setTimeout(() => {
      try { status.style.display = 'none'; } catch {}
    }, 3300);
  } catch (err) {
    toast(status, String(err), 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prevLabel || 'Export audit ZIP';
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
  const id = typeof clauseOrId === 'object' ? String(clauseOrId?.id || '') : String(clauseOrId || '');
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
  if (!id) return {clausesAdded: 0, controlsAdded: 0};
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
  return {clausesAdded, controlsAdded};
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
    if (!selectedClauseIds.has(id)) { selectedClauseIds.add(id); added += 1; }
    added += _addControlsLinkedToClause(id);
  }
  for (const control of doc?.controls || []) {
    const id = String(control.id || '');
    if (id && !selectedControlIds.has(id)) { selectedControlIds.add(id); added += 1; }
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
  const rows = allDocuments.filter((d) => matchesFilter({ref: d.document_type, title: d.title}, filter));
  wrap.innerHTML = rows.map((d) => {
    const id = String(d.id);
    const checked = selectedDocumentIds.has(id) ? 'checked' : '';
    const linked = `${(d.clauses || []).length} clauses, ${(d.controls || []).length} controls`;
    return `<div class="col-12">
      <label class="border rounded p-2 d-flex gap-2 align-items-start h-100">
        <input class="form-check-input mt-1" type="checkbox" data-document-id="${esc(id)}" ${checked}>
        <span><span class="fw-semibold">${esc(d.title || 'Document')}</span> <span class="badge text-bg-light ms-1">${esc(d.document_type || 'document')}</span><div class="small-muted">${esc(linked)}</div></span>
      </label>
    </div>`;
  }).join('') || `<div class="small-muted">No ISMS documents match.</div>`;
  wrap.querySelectorAll('[data-document-id]').forEach((cb) => {
    cb.disabled = !canEditAudit();
    cb.addEventListener('change', () => {
      if (!canEditAudit()) return;
      const id = cb.getAttribute('data-document-id');
      if (cb.checked) {
        selectedDocumentIds.add(id);
        const added = _addScopeLinkedToDocument(id);
        if (added > 0) { renderClauses(); renderControls(); }
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
    apiGet(`/api/v1/controls?framework=${encodeURIComponent(fw)}&limit=5000`),
    apiGet(`/api/v1/clauses?framework=${encodeURIComponent(fw)}`),
    apiGet(`/api/v1/graph/clause_control?framework=${encodeURIComponent(fw)}`),
    apiGet(`/api/v1/isms/documents?framework=${encodeURIComponent(fw)}&limit=1000`),
  ]);
  allDocuments = documentsData?.items || [];
  allControls = (controlsData?.items || []).filter((c) => String(c.type || '') !== 'clause');
  allClauses = clausesData?.items || [];
  _rebuildClauseHierarchy();
  clauseControlMap = _buildClauseControlMap(graphData);
  selectedDocumentIds = new Set((audit?.scoped_documents || []).map((d) => String(d.id)));
  selectedControlIds = new Set((audit?.scoped_controls || []).map((c) => String(c.id)));
  selectedClauseIds = new Set((audit?.scoped_clauses || []).map((c) => String(c.id)));
  _normaliseSelectedClausesToChildren();
  renderScopeLists();
}

function matchesFilter(item, filter) {
  if (!filter) return true;
  return String(item.ref || '').toLowerCase().includes(filter) || String(item.title || '').toLowerCase().includes(filter);
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
  wrap.innerHTML = rows.map((c) => {
    const id = String(c.id);
    const hasChildren = _clauseHasChildren(c);
    const descendants = hasChildren ? _selectableDescendantClauseIds(id).filter((x) => x !== id) : [];
    const allChildrenSelected = descendants.length > 0 && descendants.every((childId) => selectedClauseIds.has(String(childId)));
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
  }).join('') || `<div class="small-muted">No clauses match.</div>`;
  wrap.querySelectorAll('[data-clause-id]').forEach((cb) => {
    cb.disabled = !canEditAudit();
    cb.addEventListener('change', () => {
      if (!canEditAudit()) return;
      const id = cb.getAttribute('data-clause-id');
      if (_clauseHasChildren(id)) {
        cb.checked = false;
        const {clausesAdded, controlsAdded} = _selectClauseOrChildren(id);
        renderScopeLists();
        if (clausesAdded || controlsAdded) {
          toast(status, `Selected ${clausesAdded} child clause${clausesAdded === 1 ? '' : 's'} and ${controlsAdded} linked control${controlsAdded === 1 ? '' : 's'}.`, 'info', 3000);
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
  wrap.innerHTML = rows.map((c) => {
    const id = String(c.id);
    const checked = selectedControlIds.has(id) ? 'checked' : '';
    return `<div class="col-12 col-lg-6">
      <label class="border rounded p-2 d-flex gap-2 align-items-start h-100">
        <input class="form-check-input mt-1" type="checkbox" data-control-id="${esc(id)}" ${checked}>
        <span><span class="fw-semibold">${esc(c.ref || '')}</span> <span class="small-muted">${esc(c.title || '')}</span></span>
      </label>
    </div>`;
  }).join('') || `<div class="small-muted">No controls match.</div>`;
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
  const base = getComputedStyle(document.documentElement).getPropertyValue('--bs-primary').trim() || '#0d6efd';
  if (String(value || '') === 'partially_applicable') {
    try {
      const c = d3.color(base);
      if (c) { c.opacity = 0.58; return c.formatRgb(); }
    } catch {}
  }
  return base;
}

function _truncateText(value, maxChars = 64) {
  const s = String(value || '').trim();
  const n = Math.max(4, Number(maxChars || 0));
  if (s.length <= n) return s;
  return `${s.slice(0, Math.max(1, n - 1))}…`;
}

function _splitLongToken(token, maxChars) {
  const out = [];
  let s = String(token || '');
  const n = Math.max(4, Number(maxChars || 12));
  while (s.length > n) {
    out.push(s.slice(0, n));
    s = s.slice(n);
  }
  if (s) out.push(s);
  return out;
}

function _scopeLabelLines(value, maxCharsPerLine, maxLines = 3) {
  const raw = String(value || '').replace(/\s+/g, ' ').trim();
  const perLine = Math.max(4, Number(maxCharsPerLine || 18));
  const lineLimit = Math.max(1, Number(maxLines || 1));
  if (!raw) return [];

  const tokens = raw.split(' ').flatMap((w) => w.length > perLine ? _splitLongToken(w, perLine) : [w]);
  const lines = [];
  let line = '';
  for (const token of tokens) {
    const next = line ? `${line} ${token}` : token;
    if (next.length <= perLine) {
      line = next;
      continue;
    }
    if (line) lines.push(line);
    line = token;
    if (lines.length >= lineLimit) break;
  }
  if (line && lines.length < lineLimit) lines.push(line);

  if (raw.length > lines.join(' ').length && lines.length) {
    const last = _truncateText(lines[lines.length - 1], Math.max(4, perLine - 1));
    lines[lines.length - 1] = last.endsWith('…') ? last : `${last}…`;
  }
  return lines;
}

function _renderScopeNodeLabel(textSel, value, opts = {}) {
  if (!textSel) return;
  const width = Number(opts.width || 180);
  const height = Number(opts.height || 32);
  const x = Number(opts.x ?? width / 2);
  const y = Number(opts.y ?? height / 2);
  const linePx = Number(opts.linePx || 13);
  const charPx = Number(opts.charPx || 7);
  const maxLines = Math.max(1, Math.min(Number(opts.maxLines || 3), Math.floor(Math.max(1, height - 8) / linePx) || 1));
  const maxChars = Math.max(4, Math.floor(Math.max(20, width - 16) / charPx));
  const lines = _scopeLabelLines(value, maxChars, maxLines);
  const totalHeight = Math.max(linePx, lines.length * linePx);
  const startY = y - (totalHeight / 2) + (linePx / 2) - 1;

  textSel.text(null);
  lines.forEach((line, idx) => {
    textSel
      .append('tspan')
      .attr('x', x)
      .attr('y', startY + (idx * linePx))
      .text(line);
  });
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
  return withFramework(`/clause.html?id=${encodeURIComponent(clause?.id || '')}`, audit?.framework_slug);
}

function _scopeControlHref(control) {
  return withFramework(`/control.html?id=${encodeURIComponent(control?.id || '')}`, audit?.framework_slug);
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
    wrap.innerHTML = '<div class="small-muted p-3">Select at least one clause and one control to show the audit scope graph.</div>';
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
    return String(a.ref || '').localeCompare(String(b.ref || ''), undefined, {numeric: true});
  });
  const orderedControls = controls.slice().sort((a, b) => {
    const ac = linkedControlCounts.get(String(a.id)) || 0;
    const bc = linkedControlCounts.get(String(b.id)) || 0;
    if (bc !== ac) return bc - ac;
    return String(a.ref || '').localeCompare(String(b.ref || ''), undefined, {numeric: true});
  });

  const rowPx = Math.max(34, Math.min(46, orderedClauses.length > 60 || orderedControls.length > 60 ? 34 : 42));
  const rows = Math.max(orderedClauses.length, orderedControls.length, 6);
  const width = Math.max(760, (wrap.clientWidth || 900) - 18);
  const height = Math.max(340, rows * rowPx + 96);
  const margin = {top: 48, right: 18, bottom: 48, left: 18};
  const leftW = Math.min(280, Math.max(190, Math.floor(width * 0.28)));
  const rightW = Math.min(280, Math.max(190, Math.floor(width * 0.28)));
  const xL = margin.left;
  const xR = width - margin.right - rightW;
  const linkGap = Math.max(80, xR - (xL + leftW));

  if (summary) {
    const orphanControls = stats.controls_in_scope_without_selected_clause_link ?? (controls.length - linkedControlCounts.size);
    const orphanClauses = stats.clauses_without_selected_control_link ?? (clauses.length - linkedClauseCounts.size);
    const extras = [];
    if (orphanClauses > 0) extras.push(`${orphanClauses} selected clause(s) have no selected control link`);
    if (orphanControls > 0) extras.push(`${orphanControls} selected control(s) have no selected clause link`);
    if (extras.length) summary.textContent += ` ${extras.join('; ')}.`;
  }

  const yClauses = d3.scaleBand()
    .domain(orderedClauses.map((c) => String(c.id)))
    .range([margin.top, height - margin.bottom])
    .padding(0.22);
  const yControls = d3.scaleBand()
    .domain(orderedControls.map((c) => String(c.id)))
    .range([margin.top, height - margin.bottom])
    .padding(0.22);

  const svg = d3.select(wrap).append('svg')
    .attr('width', width)
    .attr('height', height)
    .attr('role', 'img')
    .attr('aria-label', 'Audit clause and control scope bipartite graph')
    .style('display', 'block')
    .style('min-width', `${width}px`);

  svg.append('text')
    .attr('x', margin.left)
    .attr('y', 20)
    .attr('class', 'small-muted')
    .style('font-size', '12px')
    .text('Clauses are on the left; controls are on the right. Click a clause or control to open its detail page.');

  svg.append('text')
    .attr('x', xL)
    .attr('y', margin.top - 10)
    .attr('class', 'small-muted')
    .style('font-size', '12px')
    .text('Clauses in scope');
  svg.append('text')
    .attr('x', xR)
    .attr('y', margin.top - 10)
    .attr('class', 'small-muted')
    .style('font-size', '12px')
    .text('Controls in scope');

  const defs = svg.append('defs');
  const grad = defs.append('linearGradient')
    .attr('id', 'auditScopeLinkGradient')
    .attr('x1', '0%')
    .attr('y1', '0%')
    .attr('x2', '100%')
    .attr('y2', '0%');
  grad.append('stop')
    .attr('offset', '0%')
    .attr('stop-color', _scopeApplicabilityColor('applicable'))
    .attr('stop-opacity', 0.62);
  grad.append('stop')
    .attr('offset', '100%')
    .attr('stop-color', _scopeApplicabilityColor('applicable'))
    .attr('stop-opacity', 0.30);

  const gLinks = svg.append('g').attr('class', 'audit-scope-links');
  const linkSel = gLinks.selectAll('path')
    .data(validLinks)
    .enter()
    .append('path')
    .attr('fill', 'none')
    .attr('stroke-width', (d) => d.applicability === 'partially_applicable' ? 1.7 : 2.8)
    .attr('stroke-dasharray', (d) => d.applicability === 'partially_applicable' ? '5 5' : null)
    .style('stroke', (d) => d.applicability === 'partially_applicable' ? _scopeApplicabilityColor(d.applicability) : 'url(#auditScopeLinkGradient)')
    .style('stroke-opacity', (d) => d.applicability === 'partially_applicable' ? 0.60 : 0.76)
    .style('stroke-linecap', 'round')
    .attr('d', (d) => {
      const y1 = (yClauses(d.clause_id) ?? margin.top) + yClauses.bandwidth() / 2;
      const y2 = (yControls(d.control_id) ?? margin.top) + yControls.bandwidth() / 2;
      const x1 = xL + leftW;
      const x2 = xR;
      const xm = x1 + linkGap / 2;
      return `M ${x1} ${y1} C ${xm} ${y1}, ${xm} ${y2}, ${x2} ${y2}`;
    });

  linkSel.append('title')
    .text((d) => {
      const clause = clauseById.get(d.clause_id) || {};
      const control = controlById.get(d.control_id) || {};
      return `${clause.ref || 'Clause'} → ${control.ref || 'Control'}\n${_scopeApplicabilityLabel(d.applicability)}`;
    });

  const gNodes = svg.append('g').attr('class', 'audit-scope-nodes');

  const clauseNodes = gNodes.selectAll('a.scope-clause')
    .data(orderedClauses)
    .enter()
    .append('a')
    .attr('class', 'scope-clause')
    .attr('href', (d) => _scopeClauseHref(d))
    .each(function(d) {
      const y = yClauses(String(d.id)) ?? 0;
      const group = d3.select(this)
        .append('g')
        .attr('class', 'node node-source audit-scope-node audit-scope-clause')
        .attr('transform', `translate(${xL}, ${y})`)
        .style('cursor', 'pointer')
        .on('click', (ev) => {
          ev.preventDefault();
          _openScopeHref(_scopeClauseHref(d), ev);
        });
      group.append('rect')
        .attr('rx', 8)
        .attr('ry', 8)
        .attr('width', leftW)
        .attr('height', yClauses.bandwidth())
        .style('fill', linkedClauseCounts.has(String(d.id)) ? 'rgba(var(--keen-accent-rgb),0.86)' : 'rgba(108,117,125,0.72)')
        .style('stroke', 'rgba(255,255,255,0.20)')
        .style('stroke-width', 1);
      const label = `${d.ref || 'Clause'}${d.title ? ` — ${d.title}` : ''}`;
      group.append('text')
        .attr('x', leftW / 2)
        .attr('y', yClauses.bandwidth() / 2)
        .attr('text-anchor', 'middle')
        .style('font-size', orderedClauses.length > 50 ? '10px' : '12px')
        .style('font-weight', 700)
        .style('fill', 'rgba(255,255,255,0.94)')
        .style('pointer-events', 'none')
        .each(function() {
          _renderScopeNodeLabel(d3.select(this), label, {
            width: leftW,
            height: yClauses.bandwidth(),
            x: leftW / 2,
            y: yClauses.bandwidth() / 2,
            linePx: orderedClauses.length > 50 ? 11 : 13,
            charPx: 6.8,
            maxLines: 3,
          });
        });
      group.append('title').text(`${label}\n${linkedClauseCounts.get(String(d.id)) || 0} linked control(s) in this audit scope`);
    });

  const controlNodes = gNodes.selectAll('a.scope-control')
    .data(orderedControls)
    .enter()
    .append('a')
    .attr('class', 'scope-control')
    .attr('href', (d) => _scopeControlHref(d))
    .each(function(d) {
      const y = yControls(String(d.id)) ?? 0;
      const group = d3.select(this)
        .append('g')
        .attr('class', 'node node-control audit-scope-node audit-scope-control')
        .attr('transform', `translate(${xR}, ${y})`)
        .style('cursor', 'pointer')
        .on('click', (ev) => {
          ev.preventDefault();
          _openScopeHref(_scopeControlHref(d), ev);
        });
      group.append('rect')
        .attr('rx', 8)
        .attr('ry', 8)
        .attr('width', rightW)
        .attr('height', yControls.bandwidth())
        .style('fill', linkedControlCounts.has(String(d.id)) ? 'rgba(31,31,42,0.84)' : 'rgba(108,117,125,0.72)')
        .style('stroke', 'rgba(255,255,255,0.18)')
        .style('stroke-width', 1);
      const label = `${d.ref || 'Control'}${d.title ? ` — ${d.title}` : ''}`;
      group.append('text')
        .attr('x', 10)
        .attr('y', yControls.bandwidth() / 2)
        .attr('text-anchor', 'start')
        .style('font-size', orderedControls.length > 50 ? '10px' : '12px')
        .style('font-weight', 700)
        .style('fill', 'rgba(255,255,255,0.94)')
        .style('pointer-events', 'none')
        .each(function() {
          _renderScopeNodeLabel(d3.select(this), label, {
            width: rightW - 18,
            height: yControls.bandwidth(),
            x: 10,
            y: yControls.bandwidth() / 2,
            linePx: orderedControls.length > 50 ? 11 : 13,
            charPx: 6.4,
            maxLines: 3,
          });
        });
      group.append('title').text(`${label}\n${linkedControlCounts.get(String(d.id)) || 0} linked clause(s) in this audit scope`);
    });

  const legend = svg.append('g').attr('transform', `translate(${margin.left}, ${height - 26})`);
  const legendItems = [
    {label: 'Applicable relationship', applicability: 'applicable', dashed: false},
    {label: 'Partially applicable relationship', applicability: 'partially_applicable', dashed: true},
    {label: 'Grey node = in scope, no selected relationship', applicability: '', grey: true},
  ];
  const legendG = legend.selectAll('g').data(legendItems).enter().append('g')
    .attr('transform', (_, i) => `translate(${i * 235},0)`);
  legendG.each(function(d) {
    const g = d3.select(this);
    if (d.grey) {
      g.append('rect').attr('width', 28).attr('height', 10).attr('rx', 3).attr('fill', 'rgba(108,117,125,0.72)');
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
    g.append('text').attr('x', 36).attr('y', 9).style('font-size', '12px').attr('class', 'small-muted').text(d.label);
  });
}

async function loadScopeVisualisation() {
  const wrap = document.getElementById('scopeHeatmap');
  if (wrap) wrap.innerHTML = '<div class="small-muted p-3">Loading scope visualisation…</div>';
  try {
    const data = await apiGet(`/api/v1/audits/${encodeURIComponent(auditId)}/scope-map`);
    renderScopeBipartiteGraph(data);
  } catch (e) {
    if (wrap) wrap.innerHTML = `<div class="text-danger small p-3">Failed to load scope visualisation: ${esc(String(e))}</div>`;
  }
}

function renderAttendeeUserOptions() {
  const sel = document.getElementById('attendeeUserId');
  if (!sel) return;
  const cur = sel.value || '';
  const rows = (attendeeUsers || []).slice().sort((a, b) => String(a.username || '').localeCompare(String(b.username || '')));
  sel.innerHTML = `<option value="">Custom / external attendee</option>${rows.map((u) => `<option value="${esc(u.id)}">${esc(u.username)}${u.email ? ` · ${esc(u.email)}` : ''}</option>`).join('')}`;
  sel.value = rows.some((u) => String(u.id) === cur) ? cur : '';
}

function renderAttendeePersonOptions() {
  const sel=document.getElementById('attendeePersonId'); if (!sel) return;
  const current=sel.value;
  sel.innerHTML='<option value="">Choose a Person or enter a new name below</option>'+
    attendeePeople.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}${p.email ? ` · ${esc(p.email)}` : ''}</option>`).join('');
  sel.value=attendeePeople.some(p=>p.id===current) ? current : '';
}

async function loadAttendeePeople() {
  try { attendeePeople=(await apiGet('/api/v1/isms/people')).items || []; renderAttendeePersonOptions(); }
  catch (err) { attendeePeople=[]; renderAttendeePersonOptions(); }
}

function suggestPersonCreation() {
  const button=document.getElementById('attendeeCreatePerson'); if (!button) return;
  const name=document.getElementById('attendeeName')?.value.trim() || '';
  const email=document.getElementById('attendeeEmail')?.value.trim().toLowerCase() || '';
  const matching=attendeePeople.find(p=>email ? p.email?.trim().toLowerCase()===email : p.name?.trim().toLowerCase()===name.toLowerCase());
  if (matching && !document.getElementById('attendeePersonId').value) {
    document.getElementById('attendeePersonId').value=matching.id;
    document.getElementById('attendeeUserId').value='';
  }
  button.hidden=!(name && !matching && !document.getElementById('attendeePersonId')?.value && (me?.is_admin || me?.can_manage_isms));
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
    toast(status, `Failed to load Keen users for attendees: ${String(e)}`, 'warning');
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
  const p=attendeePeople.find(person=>person.id===document.getElementById('attendeePersonId').value);
  if (p) {
    document.getElementById('attendeeUserId').value='';
    document.getElementById('attendeeName').value=p.name;
    document.getElementById('attendeeEmail').value=p.email || '';
  }
  suggestPersonCreation();
});
for (const id of ['attendeeName','attendeeEmail']) document.getElementById(id)?.addEventListener('input', suggestPersonCreation);
document.getElementById('attendeeCreatePerson')?.addEventListener('click', async () => {
  try {
    const name=document.getElementById('attendeeName').value.trim();
    const email=document.getElementById('attendeeEmail').value.trim();
    const p=await apiPost('/api/v1/isms/people', {name,email});
    await loadAttendeePeople(); document.getElementById('attendeePersonId').value=p.id;
    suggestPersonCreation(); toast(status,'Person saved to the directory. Add them to the audit below.','success');
  } catch (err) { toast(status,`Could not add Person: ${String(err)}`,'danger'); }
});

function renderAttendees(items) {
  const el = document.getElementById('attendees');
  const arr = items || [];
  if (!arr.length) { el.innerHTML = '<span class="small-muted">No people listed yet.</span>'; return; }
  el.innerHTML = `<div class="list-group">${arr.map((x) => {
    const meta = [x.email, x.role].filter(Boolean);
    const who = x.user_id ? userPillHtml({username: x.username || x.name, email: x.email}) : esc(x.name || x.email || 'Attendee');
    return `<div class="list-group-item d-flex justify-content-between gap-2 align-items-start">
      <div>
        <div class="fw-semibold">${who}</div>
        <div class="small-muted">${esc(meta.join(' · ') || '—')}</div>
      </div>
      ${canEditAudit() ? `<button class="btn btn-sm btn-outline-danger" data-del-attendee="${esc(x.id)}">Remove</button>` : ''}
    </div>`;
  }).join('')}</div>`;
  el.querySelectorAll('[data-del-attendee]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!ensureAuditEditable()) return;
    await apiDelete(`/api/v1/audits/${encodeURIComponent(auditId)}/attendees/${encodeURIComponent(btn.getAttribute('data-del-attendee'))}`);
    await loadAudit({preserveUnsaved: true});
  }));
}

function findingKindOptions(current) {
  return `<option value="major_nc" ${selectedAttr('major_nc', current)}>Major Non-Conformity</option>
    <option value="minor_nc" ${selectedAttr('minor_nc', current)}>Minor Non-Conformity</option>
    <option value="ofi" ${selectedAttr('ofi', current)}>Opportunity for Improvement</option>
    <option value="best_practice" ${selectedAttr('best_practice', current)}>Best Practice</option>`;
}

function findingStatusOptions(current) {
  return `<option value="open" ${selectedAttr('open', current || 'open')}>Open</option>
    <option value="accepted" ${selectedAttr('accepted', current)}>Accepted</option>
    <option value="closed" ${selectedAttr('closed', current)}>Closed</option>`;
}

function renderFindings(items) {
  const el = document.getElementById('findings');
  const arr = items || [];
  if (!arr.length) { el.innerHTML = '<span class="small-muted">No findings yet.</span>'; return; }
  el.innerHTML = arr.map((f) => `<div class="border rounded p-3 mb-2" data-finding-card="${esc(f.id)}">
    <div class="d-flex justify-content-between gap-2 align-items-start">
      <div>
        <span class="badge text-bg-light me-2">${esc(kindLabel(f.kind))}</span>
        <span class="badge badge-soft me-2">${esc(statusLabel(f.status))}</span>
        <span class="fw-semibold">${esc(f.title)}</span>
      </div>
      ${canEditAudit() ? `<div class="d-flex gap-2"><button class="btn btn-sm btn-outline-primary" data-edit-finding="${esc(f.id)}">Edit</button><button class="btn btn-sm btn-outline-danger" data-del-finding="${esc(f.id)}">Remove</button></div>` : ''}
    </div>
    ${f.control ? `<div class="small-muted mt-1">Control: ${esc(f.control.ref)} ${esc(f.control.title || '')}</div>` : ''}
    ${f.description ? `<div class="mt-2 rich-text-content">${renderRichTextContent(f.description, '')}</div>` : ''}
    ${canEditAudit() ? `<form class="row g-2 mt-3 d-none" data-finding-edit-form="${esc(f.id)}">
      <div class="col-12 col-md-4"><select class="form-select" data-finding-edit-kind="${esc(f.id)}">${findingKindOptions(f.kind)}</select></div>
      <div class="col-12 col-md-4"><input class="form-control" data-finding-edit-title="${esc(f.id)}" value="${esc(f.title || '')}" required /></div>
      <div class="col-12 col-md-4"><select class="form-select" data-finding-edit-status="${esc(f.id)}">${findingStatusOptions(f.status)}</select></div>
      <div class="col-12 col-md-4"><input class="form-control" data-finding-edit-control="${esc(f.id)}" value="${esc(f.control?.ref || '')}" placeholder="Control ref/id (optional)" /></div>
      <div class="col-12"><div class="rich-text-editor"><textarea class="form-control" rows="3" data-rich-text="1" data-finding-edit-desc="${esc(f.id)}">${esc(f.description || '')}</textarea></div></div>
      <div class="col-12 d-flex gap-2">
        <button class="btn btn-sm btn-primary" type="submit">Save finding</button>
        <button class="btn btn-sm btn-outline-secondary" type="button" data-cancel-finding="${esc(f.id)}">Cancel</button>
      </div>
    </form>` : ''}
  </div>`).join('');
  initRichTextEditors(el);
  refreshRichTextEditorStates(el);
  el.querySelectorAll('[data-edit-finding]').forEach((btn) => btn.addEventListener('click', () => {
    const id = btn.getAttribute('data-edit-finding');
    el.querySelector(`[data-finding-edit-form="${CSS.escape(id)}"]`)?.classList.remove('d-none');
  }));
  el.querySelectorAll('[data-cancel-finding]').forEach((btn) => btn.addEventListener('click', () => {
    const id = btn.getAttribute('data-cancel-finding');
    el.querySelector(`[data-finding-edit-form="${CSS.escape(id)}"]`)?.classList.add('d-none');
  }));
  el.querySelectorAll('[data-finding-edit-form]').forEach((form) => form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (!ensureAuditEditable()) return;
    const id = form.getAttribute('data-finding-edit-form');
    await apiPatch(`/api/v1/audits/${encodeURIComponent(auditId)}/findings/${encodeURIComponent(id)}`, {
      kind: el.querySelector(`[data-finding-edit-kind="${CSS.escape(id)}"]`)?.value,
      title: el.querySelector(`[data-finding-edit-title="${CSS.escape(id)}"]`)?.value,
      status: el.querySelector(`[data-finding-edit-status="${CSS.escape(id)}"]`)?.value,
      control: el.querySelector(`[data-finding-edit-control="${CSS.escape(id)}"]`)?.value || null,
      description: syncRichTextEditor(el.querySelector(`[data-finding-edit-desc="${CSS.escape(id)}"]`)) || '',
    });
    toast(status, 'Finding saved', 'success');
    await loadAudit({preserveUnsaved: true});
  }));
  el.querySelectorAll('[data-del-finding]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!ensureAuditEditable()) return;
    await apiDelete(`/api/v1/audits/${encodeURIComponent(auditId)}/findings/${encodeURIComponent(btn.getAttribute('data-del-finding'))}`);
    await loadAudit({preserveUnsaved: true});
  }));
}

function renderEvidence(items) {
  const el = document.getElementById('evidence');
  const arr = items || [];
  if (!arr.length) { el.innerHTML = '<span class="small-muted">No evidence sampled yet. Use the “Sample into audit” button on an event, risk or ISMS page, or paste a URL above.</span>'; return; }
  el.innerHTML = arr.map((x) => {
    const ev = x.event;
    const entity = x.entity || null;
    const href = x.evidence_url || entity?.href || (ev?.id ? withFramework(`/event.html?id=${encodeURIComponent(ev.id)}`, audit?.framework_slug) : '#');
    const title = x.title || entity?.title || ev?.summary || x.evidence_url || 'Evidence';
    const controls = (x.controls || ev?.controls || entity?.controls || []).map((c) => `<span class="badge badge-soft me-1">${esc(c.ref)}</span>`).join('');
    const kind = entity?.kind || (ev ? 'Event evidence' : 'Evidence URL');
    const subtitle = entity?.subtitle ? ` · ${entity.subtitle}` : '';
    return `<div class="border rounded p-3 mb-3" data-evidence-card="${esc(x.id)}">
      <div class="d-flex flex-wrap gap-2 justify-content-between">
        <div>
          <div class="mb-1"><span class="badge text-bg-light">${esc(kind)}</span></div>
          <a class="fw-semibold" href="${esc(href || '#')}">${esc(title)}</a>
          <div class="small-muted">Added ${esc(fmtTs(x.added_at))} by ${userPillHtml(x.added_by_username)}${ev?.timestamp ? ` · Event time ${esc(fmtTs(ev.timestamp))}` : ''}${esc(subtitle)}</div>
          <div class="mt-1">${controls}</div>
        </div>
        ${canEditAudit() ? `<button class="btn btn-sm btn-outline-danger" data-del-evidence="${esc(x.id)}">Remove</button>` : ''}
      </div>
      <label class="form-label small-muted mb-1 mt-3">Audit notes</label>
      <div class="rich-text-editor"><textarea class="form-control" rows="3" data-rich-text="1" data-evidence-notes="${esc(x.id)}" ${canEditAudit() ? '' : 'readonly'}>${esc(x.notes || '')}</textarea></div>
      ${canEditAudit() ? `<div class="mt-2"><button class="btn btn-sm btn-outline-primary" data-save-evidence="${esc(x.id)}">Save notes</button></div>` : ''}
    </div>`;
  }).join('');
  initRichTextEditors(el);
  refreshRichTextEditorStates(el);
  el.querySelectorAll('[data-save-evidence]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!ensureAuditEditable()) return;
    const id = btn.getAttribute('data-save-evidence');
    const notes = syncRichTextEditor(el.querySelector(`[data-evidence-notes="${CSS.escape(id)}"]`)) || '';
    await apiPatch(`/api/v1/audits/${encodeURIComponent(auditId)}/evidence/${encodeURIComponent(id)}`, {notes});
    toast(status, 'Evidence notes saved', 'success');
    await loadAudit({preserveUnsaved: true});
  }));
  el.querySelectorAll('[data-del-evidence]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!ensureAuditEditable()) return;
    await apiDelete(`/api/v1/audits/${encodeURIComponent(auditId)}/evidence/${encodeURIComponent(btn.getAttribute('data-del-evidence'))}`);
    await loadAudit({preserveUnsaved: true});
  }));
}

async function loadAudit({preserveUnsaved = false} = {}) {
  const unsavedState = preserveUnsaved ? captureUnsavedPageState() : null;
  if (!ensureAuditRead()) return;
  if (!auditId) { toast(status, 'Missing audit id', 'danger'); return; }
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
      toast(status, 'Choose Open or In progress to reopen this audit before editing it.', 'warning');
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
      await loadAudit({preserveUnsaved: true});
      return;
    }

    const body = auditLocked() ? {status: nextStatus} : {
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
    await loadAudit({preserveUnsaved: !auditLocked()});
  } catch (e) { toast(status, `Failed: ${String(e)}`, 'danger'); }
});

document.getElementById('tmplDateRule')?.addEventListener('change', updateTemplateScheduleDateMode);
document.getElementById('tmplFuzzyMonth')?.addEventListener('change', updateTemplateScheduleDateMode);
document.getElementById('tmplFuzzyYear')?.addEventListener('input', updateTemplateScheduleDateMode);
document.getElementById('tmplRecurrence')?.addEventListener('change', updateTemplateScheduleDateMode);

document.getElementById('templateScheduleForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    if (!ensureAuditEditable() || !auditTemplate()) return;
    const startDate = _templateScheduleStartForSubmit();
    const untilOk = tmplUntilWire?.syncHiddenFromText?.() !== false;
    if (!startDate || !untilOk) {
      toast(status, 'Enter schedule dates using the configured date format.', 'warning');
      return;
    }
    await apiPatch(`/api/v1/audits/scheduled/${encodeURIComponent(auditId)}`, {
      start_date: startDate,
      schedule_date_rule: document.getElementById('tmplDateRule')?.value || 'exact',
      schedule_anchor_month: _isFuzzyScheduleDateRule(document.getElementById('tmplDateRule')?.value || 'exact') ? Number(document.getElementById('tmplFuzzyMonth')?.value || 1) : null,
      schedule_recurrence: document.getElementById('tmplRecurrence')?.value || 'once',
      schedule_interval: Number(document.getElementById('tmplInterval')?.value || 1),
      schedule_until_date: tmplUntil?.value || null,
    });
    toast(status, 'Template schedule saved', 'success');
    await loadAudit({preserveUnsaved: false});
  } catch (e) { toast(status, `Failed: ${String(e)}`, 'danger'); }
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
    await loadAudit({preserveUnsaved: true});
  } catch (e) { toast(status, `Failed: ${String(e)}`, 'danger'); }
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
    await loadAudit({preserveUnsaved: true});
  } catch (e) { toast(status, `Failed: ${String(e)}`, 'danger'); }
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
      documents: Array.from(selectedDocumentIds),
      clauses: Array.from(selectedClauseIds),
      controls: Array.from(selectedControlIds),
    });
    toast(status, 'Scope saved; linked controls for selected clauses were included automatically.', 'success');
    await loadAudit({preserveUnsaved: true});
  } catch (e) { toast(status, `Failed: ${String(e)}`, 'danger'); }
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
    if (!userId && !personId && !name && !email) throw new Error('Choose a Person or enter a name');
    if (userId && personId) throw new Error('Select a Person or KEEN user');
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
    await loadAudit({preserveUnsaved: true});
  } catch (e) { toast(status, `Failed: ${String(e)}`, 'danger'); }
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
    await loadAudit({preserveUnsaved: true});
  } catch (e) { toast(status, `Failed: ${String(e)}`, 'danger'); }
});

function applyManageVisibility() {
  if (canManageAudits()) return;
  ['detailsForm', 'reportForm', 'reportNotesForm', 'urlEvidenceForm', 'attendeeForm', 'findingForm'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.add('d-none');
  });
  document.getElementById('saveScope')?.classList.add('d-none');
  document.getElementById('deselectDocuments')?.classList.add('d-none');
  document.getElementById('deselectClauses')?.classList.add('d-none');
  document.getElementById('deselectControls')?.classList.add('d-none');
  document.getElementById('status')?.insertAdjacentHTML('beforeend', '<div class="alert alert-info mt-2 mb-0">You have read-only access to this audit.</div>');
}

applyManageVisibility();

document.getElementById('urlEvidenceForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    if (!ensureAuditEditable()) return;
    const url = document.getElementById('urlEvidence').value.trim();
    if (!url) return;
    await apiPost(`/api/v1/audits/${encodeURIComponent(auditId)}/evidence`, {evidence_url: url});
    document.getElementById('urlEvidence').value = '';
    toast(status, 'Evidence URL added', 'success');
    await loadAudit({preserveUnsaved: true});
  } catch (e) { toast(status, `Failed: ${String(e)}`, 'danger'); }
});

document.getElementById('btnAuditZip')?.addEventListener('click', () => exportAuditZip());
document.getElementById('deleteAudit')?.addEventListener('click', async () => {
  if (!canDeleteAudit()) {
    toast(status, 'Admin role required.', 'danger');
    return;
  }
  const title = audit?.title || 'this audit';
  if (!confirm(`Delete audit "${title}"? This permanently removes the audit, its scope, attendees, sampled evidence and findings.`)) return;
  try {
    await apiDelete(`/api/v1/audits/${encodeURIComponent(auditId)}`);
    toast(status, 'Audit deleted', 'success');
    location.href = withFramework('/audits.html', audit?.framework_slug || getCurrentFramework());
  } catch (e) {
    toast(status, `Failed: ${String(e)}`, 'danger');
  }
});

await loadAttendeeUsers();
await loadAttendeePeople();
await loadAudit();
