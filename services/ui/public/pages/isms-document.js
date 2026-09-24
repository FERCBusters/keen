import {
  initNavbar,
  apiGet,
  esc,
  fmtTs,
  getCurrentFramework,
  toast,
  withFramework,
  userPillHtml,
  canSampleIntoAudit,
  openAuditSampleModal,
  loadEntityChangelog,
  renderRichTextContent,
} from '/app.js';
import {activateTabFromHashOrQuery, clauseBadges, controlBadges} from '/pages/isms-detail-common.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search || '');
const documentId = params.get('id') || params.get('document') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);
const canManageIsms = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me);
let documentRow = null;

function documentHref(id = documentId) {
  return withFramework(`/isms-document.html?id=${encodeURIComponent(id || '')}`, framework);
}

function fileHref(row) {
  return row?.has_file ? `/api/v1/isms/documents/${encodeURIComponent(row.id)}/file` : '';
}

function renderDocument(row) {
  documentRow = row || {};
  const title = documentRow.title || 'Policy / Process';
  document.title = `Keen – ${title}`;
  $('documentTitle').textContent = title;
  $('documentMeta').textContent = [documentRow.document_type || 'document', documentRow.has_file ? 'uploaded file' : '', documentRow.external_url ? 'external URL' : '', `updated ${fmtTs(documentRow.updated_at) || '—'}`].filter(Boolean).join(' • ');
  $('documentDescription').textContent = documentRow.description || 'No description recorded.';
  $('documentContentCard').hidden = !documentRow.content_html;
  $('documentContent').innerHTML = renderRichTextContent(documentRow.content_html);
  $('documentTags').textContent = (documentRow.tags || []).join(', ') || '—';
  $('documentType').textContent = documentRow.document_type || '—';
  $('documentUpdated').textContent = fmtTs(documentRow.updated_at) || '—';
  $('documentUploadedAt').textContent = fmtTs(documentRow.uploaded_at) || '—';
  $('documentUploadedBy').innerHTML = userPillHtml(documentRow.uploaded_by);
  $('documentControls').innerHTML = controlBadges(documentRow.controls, framework);
  $('documentClauses').innerHTML = clauseBadges(documentRow.clauses, framework);

  const external = documentRow.external_url || '';
  const file = fileHref(documentRow);
  $('documentUrl').innerHTML = external ? `<a href="${esc(external)}" target="_blank" rel="noopener noreferrer">${esc(external)}</a>` : '<span class="small-muted">—</span>';
  $('documentFile').innerHTML = file ? `<a href="${esc(file)}" target="_blank" rel="noopener noreferrer">${esc(documentRow.filename || 'Uploaded file')}</a>` : '<span class="small-muted">—</span>';

  const open = $('openDocumentLink');
  if (open) {
    const href = external || file;
    open.href = href || documentHref(documentRow.id);
    open.style.display = href ? '' : 'none';
  }
  const edit = $('editDocumentLink');
  if (edit) {
    edit.href = withFramework(`/isms.html?tab=documents&edit=document:${encodeURIComponent(documentRow.id || '')}`, framework);
    edit.style.display = canManageIsms ? '' : 'none';
  }
  const sample = $('sampleDocumentAudit');
  if (sample) sample.style.display = canSampleAudits ? '' : 'none';
}

async function loadChangelog() {
  await loadEntityChangelog(
    $('documentChangelog'),
    `/api/v1/isms/document/${encodeURIComponent(documentId)}/changelog?limit=100`,
    {empty: 'No document changes have been recorded yet.'}
  );
}

async function load() {
  if (!documentId) {
    toast(status, 'Missing document id.', 'danger');
    return;
  }
  try {
    const row = await apiGet(`/api/v1/isms/documents/${encodeURIComponent(documentId)}?framework=${encodeURIComponent(framework)}`);
    renderDocument(row);
    await loadChangelog();
    activateTabFromHashOrQuery(params, {changelog: $('documentChangelogTab')});
  } catch (e) {
    toast(status, `Failed to load document: ${String(e)}`, 'danger');
  }
}

$('sampleDocumentAudit')?.addEventListener('click', () => {
  if (!documentRow) return;
  openAuditSampleModal({
    me,
    entityType: 'isms_document',
    entityId: documentRow.id,
    title: documentRow.title || 'ISMS document',
    evidenceUrl: documentHref(documentRow.id),
    framework,
    statusEl: status,
  });
});

window.addEventListener('hashchange', () => activateTabFromHashOrQuery(params, {changelog: $('documentChangelogTab')}));
load();
