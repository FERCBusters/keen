import { initNavbar, apiGet, apiPost, apiPatch, apiDelete, esc, fmtTs, toast, debounce, getCurrentFramework, withFramework, loadEntityChangelog, shorten, canSampleIntoAudit, openAuditSampleModal } from '/app.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(location.search);
let itemId = params.get('id') || '';
const requestedTab = params.get('tab') || 'details';
const canView = !!(me?.is_admin || me?.can_view_interested_parties || me?.can_manage_interested_parties || me?.can_view_risks || me?.can_manage_risks);
const canManage = !!(me?.is_admin || me?.can_manage_interested_parties || me?.can_manage_risks);

const status = document.getElementById('status');
const title = document.getElementById('partyTitle');
const metaEl = document.getElementById('partyMeta');
const detailsTab = document.getElementById('partyDetailsTab');
const communicationsTab = document.getElementById('partyCommunicationsTab');
const changelogTab = document.getElementById('partyChangelogTab');
const form = document.getElementById('partyForm');
const partyId = document.getElementById('partyId');
const partyName = document.getElementById('partyName');
const partyNature = document.getElementById('partyNature');
const partyNote = document.getElementById('partyNote');
const controlsChecklist = document.getElementById('controlsChecklist');
const controlSearch = document.getElementById('controlSearch');
const saveParty = document.getElementById('saveParty');
const deleteParty = document.getElementById('deleteParty');
const samplePartyAudit = document.getElementById('samplePartyAudit');
const addCommunication = document.getElementById('addCommunication');
const saveCommunications = document.getElementById('saveCommunications');
const communicationsList = document.getElementById('communicationsList');
const changelogEl = document.getElementById('partyChangelog');

let meta = {events: [], when: [], with_whom: [], methods: []};
let names = [];
let natures = [];
let controls = [];
let currentItem = null;
let communications = [];

function setTabQuery(tabName) { const p = new URLSearchParams(location.search); if (itemId) p.set('id', itemId); else p.delete('id'); if (!tabName || tabName === 'details') p.delete('tab'); else p.set('tab', tabName); history.replaceState({}, '', `${location.pathname}${p.toString() ? `?${p.toString()}` : ''}`); }
function activateTab(tabName) { const tab = {details: detailsTab, communications: communicationsTab, changelog: changelogTab}[tabName || 'details'] || detailsTab; if (tab && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(tab).show(); }
function wireTabs() { detailsTab?.addEventListener('shown.bs.tab', () => setTabQuery('details')); communicationsTab?.addEventListener('shown.bs.tab', () => setTabQuery('communications')); changelogTab?.addEventListener('shown.bs.tab', async () => { setTabQuery('changelog'); await loadChangelog(); }); activateTab(requestedTab); }
function optionHtml(rows, selected = '') { return rows.map((r) => `<option value="${esc(r.id)}" ${String(selected) === String(r.id) ? 'selected' : ''}>${esc(r.name || '')}</option>`).join(''); }
function fillSelects() { if (partyName) partyName.innerHTML = optionHtml(names, currentItem?.name_id || currentItem?.name?.id); if (partyNature) partyNature.innerHTML = optionHtml(natures, currentItem?.nature_id || currentItem?.nature?.id); }
function controlHref(id) { return withFramework(`/control.html?id=${encodeURIComponent(id || '')}&tab=interested-parties`, framework); }
function renderChrome() { const label = currentItem ? `${currentItem.name?.name || ''} — ${currentItem.nature?.name || ''}` : 'New interested party'; if (title) title.textContent = label; if (metaEl) metaEl.textContent = currentItem ? `Framework ${currentItem.framework || framework} • Updated ${fmtTs(currentItem.updated_at) || '—'}` : `Framework ${framework}`; if (partyId) partyId.value = itemId || ''; if (partyNote) partyNote.value = currentItem?.note || ''; if (samplePartyAudit) samplePartyAudit.style.display = canSampleIntoAudit(me) && itemId ? '' : 'none'; if (deleteParty) deleteParty.style.display = canManage && itemId ? '' : 'none'; if (saveParty) saveParty.style.display = canManage ? '' : 'none'; }
function renderControls() { if (!controlsChecklist) return; const q = (controlSearch?.value || '').trim().toLowerCase(); const selected = new Set((currentItem?.controls || []).map((c) => String(c.id))); const rows = controls.filter((c) => !q || `${c.ref} ${c.title}`.toLowerCase().includes(q)); if (!rows.length) { controlsChecklist.innerHTML = '<div class="small-muted p-2">No controls match.</div>'; return; } controlsChecklist.innerHTML = rows.map((c) => `<label class="d-flex gap-2 align-items-start py-1 border-bottom"><input class="form-check-input mt-1" type="checkbox" value="${esc(c.id)}" data-control-check ${selected.has(String(c.id)) ? 'checked' : ''} ${canManage ? '' : 'disabled'}><span><a href="${esc(controlHref(c.id))}" class="fw-semibold">${esc(c.ref || '')}</a> ${esc(c.title || '')}</span></label>`).join(''); }
function methodCheckboxes(selected = []) { const set = new Set(selected || []); return (meta.methods || []).map((m) => `<label class="form-check form-check-inline"><input class="form-check-input" type="checkbox" value="${esc(m)}" ${set.has(m) ? 'checked' : ''} ${canManage ? '' : 'disabled'}> <span class="form-check-label">${esc(m)}</span></label>`).join(''); }
function selectOptions(rows, selected = '') { return (rows || []).map((x) => `<option value="${esc(x)}" ${String(x) === String(selected) ? 'selected' : ''}>${esc(x)}</option>`).join(''); }
function renderCommunications() { if (!communicationsList) return; if (!communications.length) { communicationsList.innerHTML = '<div class="border rounded p-3 bg-light small-muted">No communications have been added yet.</div>'; return; } communicationsList.innerHTML = communications.map((c, idx) => `<div class="border rounded p-3" data-comm-row="${idx}"><div class="row g-2"><div class="col-12 col-lg-3"><label class="form-label">Event</label><select class="form-select" data-comm-event ${canManage ? '' : 'disabled'}>${selectOptions(meta.events, c.event)}</select></div><div class="col-12 col-lg-3"><label class="form-label">When</label><select class="form-select" data-comm-when ${canManage ? '' : 'disabled'}>${selectOptions(meta.when, c.when)}</select></div><div class="col-12 col-lg-3"><label class="form-label">With whom</label><select class="form-select" data-comm-with-whom ${canManage ? '' : 'disabled'}>${selectOptions(meta.with_whom, c.with_whom)}</select></div><div class="col-12 col-lg-3 no-print text-lg-end"><label class="form-label d-block">Actions</label>${canManage ? `<button class="btn btn-sm btn-outline-danger" type="button" data-remove-comm="${idx}">Remove</button>` : ''}</div><div class="col-12"><label class="form-label d-block">Method</label><div data-comm-methods>${methodCheckboxes(c.methods || [])}</div></div></div></div>`).join(''); }
function readCommunicationsFromDom() { return Array.from(document.querySelectorAll('[data-comm-row]')).map((row) => ({ event: row.querySelector('[data-comm-event]')?.value || '', when: row.querySelector('[data-comm-when]')?.value || '', with_whom: row.querySelector('[data-comm-with-whom]')?.value || '', methods: Array.from(row.querySelectorAll('[data-comm-methods] input:checked')).map((x) => x.value) })); }
function formPayload() { const selectedControls = Array.from(document.querySelectorAll('[data-control-check]:checked')).map((x) => x.value); return { framework, name_id: partyName?.value || null, nature_id: partyNature?.value || null, note: partyNote?.value || '', controls: selectedControls }; }
function setReadonly() { for (const el of [partyName, partyNature, partyNote, controlSearch]) if (el) el.disabled = !canManage; if (addCommunication) addCommunication.style.display = canManage ? '' : 'none'; if (saveCommunications) saveCommunications.style.display = canManage ? '' : 'none'; }
async function loadLookupsAndControls() { const [m, nameData, natureData, controlData] = await Promise.all([apiGet(`/api/v1/interested-parties/meta?framework=${encodeURIComponent(framework)}`), apiGet('/api/v1/interested-parties/names'), apiGet('/api/v1/interested-parties/natures'), apiGet(`/api/v1/controls?framework=${encodeURIComponent(framework)}&limit=5000`)]); meta = m; names = nameData.items || []; natures = natureData.items || []; controls = controlData.items || []; fillSelects(); renderControls(); }
async function loadItem() { if (!itemId) { currentItem = null; communications = []; renderChrome(); fillSelects(); renderControls(); renderCommunications(); return; } currentItem = await apiGet(`/api/v1/interested-parties/${encodeURIComponent(itemId)}`); communications = (currentItem.communications || []).map((x) => ({...x})); fillSelects(); renderChrome(); renderControls(); renderCommunications(); }
async function saveItem(ev) { ev?.preventDefault(); if (!canManage) return; const payload = formPayload(); const res = itemId ? await apiPatch(`/api/v1/interested-parties/${encodeURIComponent(itemId)}`, payload) : await apiPost('/api/v1/interested-parties', payload); currentItem = res; itemId = res.id; communications = res.communications || communications; renderChrome(); setTabQuery('details'); toast(status, 'Interested party saved.', 'success'); }
async function saveComms() { if (!itemId) { await saveItem(); } communications = readCommunicationsFromDom(); const res = await apiPatch(`/api/v1/interested-parties/${encodeURIComponent(itemId)}/communications`, {items: communications}); currentItem = res; communications = res.communications || []; renderCommunications(); toast(status, 'Communications saved.', 'success'); }
async function deleteCurrent() { if (!itemId || !canManage) return; if (!confirm('Delete this interested party?')) return; await apiDelete(`/api/v1/interested-parties/${encodeURIComponent(itemId)}`); location.href = withFramework('/interested_parties.html', framework); }
async function loadChangelog() { if (!itemId) { if (changelogEl) changelogEl.textContent = 'Save the interested party to start its changelog.'; return; } await loadEntityChangelog(changelogEl, `/api/v1/interested-parties/${encodeURIComponent(itemId)}/changelog`, {empty: 'No changelog entries yet.'}); }
function wireEvents() { form?.addEventListener('submit', (ev) => saveItem(ev).catch(e => toast(status, String(e), 'danger'))); controlSearch?.addEventListener('input', debounce(renderControls, 180)); addCommunication?.addEventListener('click', () => { communications = readCommunicationsFromDom(); communications.push({event: meta.events?.[0] || 'Business as Usual', when: meta.when?.[0] || 'As Required', with_whom: meta.with_whom?.[0] || 'Individual member', methods: [meta.methods?.[0] || 'Email']}); renderCommunications(); }); saveCommunications?.addEventListener('click', () => saveComms().catch(e => toast(status, String(e), 'danger'))); deleteParty?.addEventListener('click', () => deleteCurrent().catch(e => toast(status, String(e), 'danger'))); document.addEventListener('click', (ev) => { const btn = ev.target.closest('[data-remove-comm]'); if (!btn) return; communications = readCommunicationsFromDom(); communications.splice(Number(btn.dataset.removeComm || 0), 1); renderCommunications(); }); }


samplePartyAudit?.addEventListener('click', () => {
  const label = currentItem ? `${currentItem.name?.name || ''} — ${currentItem.nature?.name || ''}` : 'Interested party';
  openAuditSampleModal({
    me,
    entityType: 'interested_party',
    entityId: currentItem?.id || itemId,
    title: label,
    framework: currentItem?.framework || framework,
    statusEl: status,
  });
});

try {
  if (!canView) toast(status, 'You do not have permission to view Interested Parties.', 'warning');
  wireTabs();
  wireEvents();
  setReadonly();
  await loadLookupsAndControls();
  await loadItem();
  if (requestedTab === 'changelog') await loadChangelog();
} catch (e) { toast(status, `Failed to load Interested Party: ${String(e)}`, 'danger'); }
