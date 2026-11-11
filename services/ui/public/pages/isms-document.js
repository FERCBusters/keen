import {
  errorText,
  safeExternalHref,
  initNavbar,
  apiGet,
  apiPost,
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
  userDisplayName,
} from '/app.js';

import {
  activateTabFromHashOrQuery,
  clauseBadges,
  controlBadges,
} from '/pages/isms-detail-common.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search);
const documentId = params.get('id') || params.get('document') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);
const canManageIsms = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me, 'isms');
let documentRow = null;

function documentHref(id = documentId) {
  return withFramework(`/isms-document.html?id=${encodeURIComponent(id)}`, framework);
}

function fileHref(row) {
  return row?.has_file ? `/api/v1/isms/documents/${encodeURIComponent(row.id)}/file` : '';
}

function renderDocument(row) {
  documentRow = row;
  const title = documentRow.title || 'Policy / Process';
  document.title = `Keen - ${title}`;
  $('documentTitle').textContent = title;
  $('documentMeta').textContent = [
    documentRow.document_type || 'document',
    documentRow.has_file ? 'uploaded file' : '',
    documentRow.external_url ? 'external URL' : '',
    `updated ${fmtTs(documentRow.updated_at)}`,
  ]
    .filter(Boolean)
    .join(' · ');
  $('documentDescription').textContent = documentRow.description || 'No description recorded.';
  $('documentContentCard').hidden = !documentRow.content_html;
  $('documentContent').innerHTML = renderRichTextContent(documentRow.content_html);
  $('documentTags').textContent = (documentRow.tags || []).join(', ') || '-';
  $('documentType').textContent = documentRow.document_type || '-';
  $('documentUpdated').textContent = fmtTs(documentRow.updated_at);
  $('documentUploadedAt').textContent = fmtTs(documentRow.uploaded_at) || '-';
  $('documentUploadedBy').innerHTML = userPillHtml(documentRow.uploaded_by);
  $('documentControls').innerHTML = controlBadges(documentRow.controls, framework, { tab: '' });
  $('documentClauses').innerHTML = clauseBadges(documentRow.clauses, framework);

  const external = documentRow.external_url || '';
  const file = fileHref(documentRow);
  $('documentUrl').innerHTML = external
    ? `<a href="${esc(safeExternalHref(external) || '#')}" target="_blank" rel="noopener noreferrer">${esc(external)}</a>`
    : '<span class="small-muted">-</span>';
  $('documentFile').innerHTML = file
    ? `<a href="${esc(file)}" target="_blank" rel="noopener noreferrer">${esc(documentRow.filename || 'Uploaded file')}</a>`
    : '<span class="small-muted">-</span>';

  const open = $('openDocumentLink');
  if (open) {
    const href = safeExternalHref(external) || file;
    open.href = href || documentHref(documentRow.id);
    open.style.display = href ? '' : 'none';
  }
  const edit = $('editDocumentLink');
  if (edit) {
    edit.href = withFramework(
      `/isms.html?tab=documents&edit=document:${encodeURIComponent(documentRow.id)}`,
      framework,
    );
    edit.style.display = canManageIsms ? '' : 'none';
  }
  const sample = $('sampleDocumentAudit');
  if (sample) sample.style.display = canSampleAudits ? '' : 'none';
}

async function loadChangelog() {
  await loadEntityChangelog(
    $('documentChangelog'),
    `/api/v1/isms/document/${encodeURIComponent(documentId)}/changelog?limit=100`,
    { empty: 'No document changes have been recorded yet.' },
  );
}

function documentApi(path) {
  return `/api/v1/isms/documents/${encodeURIComponent(documentId)}/${path}`;
}

function element(tag, className, text = '') {
  const el = document.createElement(tag);
  el.className = className;
  el.textContent = text;
  return el;
}

// Saved versions of the text written in Keen, newest first.
async function loadRevisions() {
  const revisions = await apiGet(documentApi('revisions'));
  const list = $('revisionList');
  if (!revisions.length) {
    list.textContent = 'No text written in Keen has been saved yet.';
    return;
  }
  list.className = 'd-flex flex-wrap gap-2';
  list.replaceChildren(
    ...revisions.map((revision) => {
      const button = element(
        'button',
        'btn btn-outline-secondary btn-sm',
        `Version ${revision.version} · ${fmtTs(revision.created_at)}`,
      );
      button.type = 'button';
      button.dataset.revision = String(revision.version);
      button.setAttribute('aria-pressed', 'false');
      return button;
    }),
  );
}

async function showRevision(button) {
  const revision = await apiGet(
    documentApi(`revisions/${encodeURIComponent(button.dataset.revision)}`),
  );
  for (const other of $('revisionList').querySelectorAll('[data-revision]'))
    other.setAttribute('aria-pressed', String(other === button));
  const heading = element('div', 'fw-semibold', `Version ${revision.version}`);
  const checksum = element('div', 'small-muted text-break', 'SHA-256 ');
  checksum.append(element('code', '', revision.sha256));
  const content = element('div', 'mt-2');
  content.innerHTML = renderRichTextContent(revision.content_html);
  const preview = element('div', 'alert alert-secondary mb-0');
  preview.append(heading, checksum, content);
  $('revisionPreview').replaceChildren(preview);
}

async function loadComments() {
  const comments = await apiGet(documentApi('comments'));
  const list = $('commentList');
  if (!comments.length) {
    list.className = 'small-muted';
    list.textContent = 'No comments yet.';
    return;
  }
  list.className = '';
  list.replaceChildren(
    ...comments.map((comment) => {
      const row = element('div', 'border-bottom py-2');
      row.append(
        element('strong', '', userDisplayName(comment.author, 'Former user')),
        ' ',
        element('small', 'small-muted', fmtTs(comment.created_at)),
        element('div', 'whitespace-pre-wrap', comment.body),
      );
      return row;
    }),
  );
}

// Revisions and comments each report their own failure, so one that cannot
// load leaves the other readable.
async function loadHistory() {
  const results = await Promise.allSettled([loadRevisions(), loadComments()]);
  const [revisions, comments] = results;
  if (revisions.status === 'rejected') {
    $('revisionList').textContent = 'Revisions could not be loaded.';
    toast(status, `Could not load revisions: ${errorText(revisions.reason)}`, 'warning');
  }
  if (comments.status === 'rejected') {
    $('commentList').textContent = 'Comments could not be loaded.';
    toast(status, `Could not load comments: ${errorText(comments.reason)}`, 'warning');
  }
}

const TABS = {
  revisions: $('documentRevisionsTab'),
  comments: $('documentCommentsTab'),
  changelog: $('documentChangelogTab'),
};

async function load() {
  if (!documentId) {
    toast(status, 'Missing document id.', 'danger');
    return;
  }
  try {
    const row = await apiGet(
      `/api/v1/isms/documents/${encodeURIComponent(documentId)}?framework=${encodeURIComponent(framework)}`,
    );
    renderDocument(row);
    await Promise.all([loadChangelog(), loadHistory()]);
    activateTabFromHashOrQuery(params, TABS);
  } catch (e) {
    toast(status, `Failed to load document: ${errorText(e)}`, 'danger');
  }
}

$('sampleDocumentAudit')?.addEventListener('click', () => {
  openAuditSampleModal({
    me,
    entityType: 'isms_document',
    entityId: documentRow.id,
    title: documentRow.title,
    evidenceUrl: documentHref(documentRow.id),
    framework,
    statusEl: status,
  });
});

$('revisionList').addEventListener('click', async (ev) => {
  const button = ev.target.closest('[data-revision]');
  if (!button) return;
  try {
    await showRevision(button);
  } catch (err) {
    toast(status, `Could not open the revision: ${errorText(err)}`, 'danger');
  }
});

// Managers discuss a document in its comments; readers see them.
$('commentForm').hidden = !canManageIsms;
$('commentForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  try {
    await apiPost(documentApi('comments'), { body: $('commentBody').value });
    $('commentBody').value = '';
    await loadComments();
    toast(status, 'Comment added.', 'success');
  } catch (err) {
    toast(status, `Could not add the comment: ${errorText(err)}`, 'danger');
  }
});

window.addEventListener('hashchange', () => activateTabFromHashOrQuery(params, TABS));
load();

import { mountSwotRelated } from '/pages/swot-related.js';
import { mountStoryRelated } from '/pages/story-related.js';
mountSwotRelated('document', new URLSearchParams(location.search).get('id'));
mountStoryRelated({ me, kind: 'document', id: new URLSearchParams(location.search).get('id') });
