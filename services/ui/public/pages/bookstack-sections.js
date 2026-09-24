import {initNavbar, apiGet, apiPost, apiPatch, esc, toast, getCurrentFramework, canSampleIntoAudit, openAuditSampleModal} from '/app.js';

const me = await initNavbar();
const manage = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const $ = id => document.getElementById(id);
const framework = getCurrentFramework();
let pageOffset = null;
let capturedSections = [];
function notify(message, kind='success') { toast($('status'), message, kind); }
function setOptions(id, items, placeholder) {
  $(id).innerHTML = `<option value="">${esc(placeholder)}</option>` + items.map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');
}
async function loadPages(append=false) {
  if (!$('bookSelect').value) return;
  try {
    const next = append ? pageOffset : 0;
    const data = await apiGet(`/api/v1/admin/bookstack/catalog?kind=pages&book_id=${encodeURIComponent($('bookSelect').value)}&offset=${next || 0}`);
    if (!append) setOptions('pageSelect', [], 'Choose page…');
    $('pageSelect').insertAdjacentHTML('beforeend', (data.items || []).map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join(''));
    pageOffset = data.next_offset; $('morePages').style.display = pageOffset == null ? 'none' : '';
  } catch(e) { notify(`Could not load BookStack pages: ${String(e)}`, 'danger'); }
}
async function loadTargets() {
  const slug=$('targetFramework').value;
  setOptions('targetRequirement', [], 'Choose requirement…');
  if (!slug) return;
  try {
    const [controls, clauses] = await Promise.all([
      apiGet(`/api/v1/controls?framework=${encodeURIComponent(slug)}&limit=5000`),
      apiGet(`/api/v1/clauses?framework=${encodeURIComponent(slug)}&limit=5000`),
    ]);
    $('targetRequirement').insertAdjacentHTML('beforeend', (controls.items || []).map(c => `<option value="control:${esc(c.id)}">Control ${esc(c.ref)} · ${esc(c.title || '')}</option>`).join('') +
      (clauses.items || []).map(c => `<option value="clause:${esc(c.id)}">Clause ${esc(c.ref)} · ${esc(c.title || '')}</option>`).join(''));
  } catch(e) { notify(`Could not load framework requirements: ${String(e)}`, 'danger'); }
}
async function loadSnapshots() {
  try {
    const data=await apiGet('/api/v1/isms/bookstack-sections');
    capturedSections = data.items || [];
    $('sectionRows').innerHTML=(data.items || []).map(s => `<tr class="${s.archived ? 'text-muted' : ''}">
      <td>${esc(s.document_title)}</td><td><a href="${esc(s.permalink)}" target="_blank" rel="noopener noreferrer">${esc(s.page_title)}${s.anchor ? ` #${esc(s.anchor)}` : ''}</a></td>
      <td>${esc(s.target_framework || 'Deleted framework')} · ${esc(s.target_ref || 'Deleted requirement')}</td><td>${esc(s.captured_at)}</td>
      <td>${s.revision_count ?? '—'} <small>${esc(s.page_updated_at || '')}</small></td>
      <td><a href="/api/v1/isms/bookstack-sections/${encodeURIComponent(s.id)}/snapshot">Download JSON</a><div class="small-muted" title="SHA-256 ${esc(s.sha256)}">SHA-256 ${esc(s.sha256.slice(0,12))}…</div></td>
      <td>${canSampleIntoAudit(me) ? `<button type="button" class="btn btn-sm btn-outline-primary" data-sample="${esc(s.id)}">Sample into audit</button>` : ''}
      ${manage ? `<button type="button" class="btn btn-sm btn-outline-secondary" data-archive="${esc(s.id)}" data-archived="${s.archived}">${s.archived ? 'Restore' : 'Archive'}</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="7" class="small-muted">No policy versions captured yet.</td></tr>';
  } catch(e) { notify(`Could not load policy evidence: ${String(e)}`, 'danger'); }
}
$('bookSelect').addEventListener('change', () => loadPages());
$('morePages').addEventListener('click', () => loadPages(true));
$('targetFramework').addEventListener('change', loadTargets);
$('sectionForm').addEventListener('submit', async e => {
  e.preventDefault(); if (!manage) return;
  const [type, id] = $('targetRequirement').value.split(':');
  const body={document_id:$('policyDocument').value, page_id:Number($('pageSelect').value), anchor:$('pageAnchor').value,
    target_control_id:type==='control' ? id : null, target_clause_id:type==='clause' ? id : null};
  try {
    await apiPost('/api/v1/isms/bookstack-sections', body);
    await loadSnapshots(); notify('BookStack page version captured and linked.');
  } catch(err) { notify(`Could not capture policy version: ${String(err)}`, 'danger'); }
});
$('sectionRows').addEventListener('click', async e => {
  const sample=e.target.closest('[data-sample]');
  if (sample && canSampleIntoAudit(me)) {
    const section = capturedSections.find(item => item.id === sample.dataset.sample);
    openAuditSampleModal({me, entityType:'bookstack_section', entityId:sample.dataset.sample,
      title:'BookStack policy snapshot', evidenceUrl:`/api/v1/isms/bookstack-sections/${encodeURIComponent(sample.dataset.sample)}/snapshot`,
      framework:section?.target_framework || framework, statusEl:$('status')});
    return;
  }
  const button=e.target.closest('[data-archive]'); if (!button || !manage) return;
  try {
    await apiPatch(`/api/v1/isms/bookstack-sections/${encodeURIComponent(button.dataset.archive)}?archived=${button.dataset.archived === 'true' ? 'false' : 'true'}`, {});
    await loadSnapshots(); notify('Policy evidence status updated.');
  } catch(err) { notify(`Could not update policy evidence: ${String(err)}`, 'danger'); }
});
async function init() {
  try {
    const [docs, books, frameworks] = await Promise.all([
      apiGet(`/api/v1/isms/documents?framework=${encodeURIComponent(framework)}&limit=1000`),
      apiGet('/api/v1/admin/bookstack/catalog?kind=books'),
      apiGet('/api/v1/frameworks'),
    ]);
    setOptions('policyDocument', (docs.items || []).map(d => ({id:d.id,name:d.title})), 'Choose policy or process…');
    setOptions('bookSelect', books.items || [], 'Choose book…');
    setOptions('targetFramework', (frameworks.items || []).map(f => ({id:f.slug,name:f.name || f.slug})), 'Choose framework…');
    $('targetFramework').value = framework; await loadTargets(); await loadSnapshots();
  } catch(e) { notify(`Could not load BookStack catalogue: ${String(e)}`, 'danger'); }
}
if (!manage) $('sectionForm').style.display='none';
await init();
