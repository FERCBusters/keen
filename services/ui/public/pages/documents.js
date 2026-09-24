import {initNavbar, apiGet, apiPost, apiPatch, esc, toast, initRichTextEditors, setRichTextEditorValue, syncRichTextEditor, renderRichTextContent, getCurrentFramework} from '/app.js';

const me = await initNavbar();
const manage = !!(me?.is_admin || me?.can_manage_isms);
const $ = id => document.getElementById(id);
const base = '/api/v1/isms';
let folders = [], documents = [], selected = null, activeFolder = '';
const status = (message, kind='success') => toast($('status'), message, kind);
const folderLabel = folder => folder ? `${folder.name}` : 'Unfiled';

function draw() {
  $('folderParent').innerHTML = $('folder').innerHTML = '<option value="">Top level / unfiled</option>' + folders.map(f => `<option value="${esc(f.id)}">${esc(folderLabel(f))}</option>`).join('');
  if (selected) $('folder').value = selected.folder_id || '';
  $('folderList').innerHTML = `<button class="list-group-item list-group-item-action ${activeFolder === '' ? 'active' : ''}" data-folder="">All folders</button>` + folders.map(f => `<button class="list-group-item list-group-item-action ${activeFolder === f.id ? 'active' : ''}" data-folder="${esc(f.id)}">${esc(f.name)}</button>`).join('');
  const q = $('search').value.trim().toLowerCase();
  const found = documents.filter(d => (!activeFolder || d.folder_id === activeFolder) && `${d.title} ${(d.tags || []).join(' ')} ${d.description || ''}`.toLowerCase().includes(q));
  $('documentList').innerHTML = found.map(d => `<button class="list-group-item list-group-item-action ${selected?.id === d.id ? 'active' : ''}" data-document="${esc(d.id)}"><strong>${esc(d.title)}</strong><br><small>${esc(d.document_type)} · ${esc((d.tags || []).join(', '))}</small></button>`).join('') || '<div class="small-muted">No documents found.</div>';
}
async function reload() {
  const [f, d] = await Promise.all([apiGet(`${base}/document-folders`), apiGet(`${base}/documents?framework=${encodeURIComponent(getCurrentFramework())}&limit=1000`)]);
  folders = f; documents = d.items || [];
  if (selected) selected = documents.find(d => d.id === selected.id) || null;
  draw();
}
async function openDocument(id) {
  selected = documents.find(d => d.id === id) || null;
  if (!selected) return;
  $('editorTitle').textContent = selected.title;
  for (const [key, field] of Object.entries({title:'title',type:'document_type',folder:'folder_id',tags:'tags',description:'description'})) {
    $(key).value = key === 'tags' ? (selected.tags || []).join(', ') : (selected[field] || '');
  }
  setRichTextEditorValue('content', selected.content_html || '');
  $('historyCard').style.display = '';
  $('revisionPreview').innerHTML = '';
  await loadHistory(); draw();
}
async function loadHistory() {
  if (!selected) return;
  const [revisions, comments] = await Promise.all([apiGet(`${base}/documents/${selected.id}/revisions`), apiGet(`${base}/documents/${selected.id}/comments`)]);
  $('revisionList').innerHTML = revisions.map(r => `<button type="button" class="btn btn-outline-secondary btn-sm me-2 mb-2" data-revision="${r.version}">Version ${r.version} · ${esc(r.created_at.slice(0,10))}</button>`).join('') || '<p class="small-muted">No edited content yet.</p>';
  $('commentList').innerHTML = comments.map(c => `<div class="border-bottom py-2"><strong>${esc(c.author?.username || c.author?.display_name || 'User')}</strong> <small>${esc(c.created_at)}</small><div>${esc(c.body)}</div></div>`).join('') || '<p class="small-muted">No comments yet.</p>';
}
$('newDocument').addEventListener('click', () => {
  selected = null; $('documentForm').reset(); $('folder').value = activeFolder;
  setRichTextEditorValue('content', ''); $('historyCard').style.display='none';
  $('editorTitle').textContent = 'New document'; draw();
});
$('documentList').addEventListener('click', e => { const row=e.target.closest('[data-document]'); if (row) openDocument(row.dataset.document).catch(err => status(String(err), 'danger')); });
$('folderList').addEventListener('click', e => { const row=e.target.closest('[data-folder]'); if (row) { activeFolder=row.dataset.folder; draw(); } });
$('search').addEventListener('input', draw);
$('folderForm').addEventListener('submit', async e => {
  e.preventDefault(); if (!manage) return;
  try { await apiPost(`${base}/document-folders`, {name:$('newFolder').value, parent_id:$('folderParent').value || null}); $('newFolder').value=''; await reload(); status('Folder added.'); }
  catch(err) { status(String(err), 'danger'); }
});
$('documentForm').addEventListener('submit', async e => {
  e.preventDefault(); if (!manage) return;
  const body = {title:$('title').value, document_type:$('type').value, folder_id:$('folder').value || null,
    tags:$('tags').value.split(',').map(s=>s.trim()).filter(Boolean), description:$('description').value,
    content_html:syncRichTextEditor($('content'))};
  try {
    const row = selected ? await apiPatch(`${base}/documents/${selected.id}`, {...body, expected_content_version:selected.content_version}) : await apiPost(`${base}/documents`, body);
    await reload(); await openDocument(row.id); status('Document saved.');
  } catch(err) { status(String(err), 'danger'); }
});
$('revisionList').addEventListener('click', async e => {
  const button=e.target.closest('[data-revision]'); if (!button || !selected) return;
  try {
    const revision=await apiGet(`${base}/documents/${selected.id}/revisions/${button.dataset.revision}`);
    $('revisionPreview').innerHTML = `<div class="alert alert-secondary"><strong>Version ${revision.version}</strong> · SHA-256 ${esc(revision.sha256)}<div id="revisionContent" class="mt-2"></div></div>`;
    $('revisionContent').innerHTML = renderRichTextContent(revision.content_html);
  } catch(err) { status(String(err), 'danger'); }
});
$('commentForm').addEventListener('submit', async e => {
  e.preventDefault(); if (!selected || !manage) return;
  try { await apiPost(`${base}/documents/${selected.id}/comments`, {body:$('comment').value}); $('comment').value=''; await loadHistory(); status('Comment added.'); }
  catch(err) { status(String(err), 'danger'); }
});
if (!manage) { $('folderForm').hidden=true; $('documentForm').querySelectorAll('input, textarea, select, button').forEach(el=>el.disabled=true); $('commentForm').hidden=true; $('newDocument').hidden=true; }
initRichTextEditors(document);
try { await reload(); } catch(err) { status(`Could not load documents: ${String(err)}`, 'danger'); }
