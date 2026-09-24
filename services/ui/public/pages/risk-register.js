import {initNavbar, apiGet, apiPatch, apiPost, apiPostForm, apiDelete, esc, toast, getCurrentFramework, withFramework} from '/app.js';

const me = await initNavbar();
const manage = !!(me?.is_admin || me?.can_manage_risks);
const framework = getCurrentFramework();
const $ = id => document.getElementById(id);
const base = '/api/v1/risks';
let risks = [], library = [], selected = null, thresholds = {low_max:4, moderate_max:9, high_max:16}, offset = 0, total = 0;

function notify(message, kind='success') { toast($('status'), message, kind); }
function severity(score) {
  if (score == null) return {text:'Needs rating', color:'#eef0f4'};
  if (score <= thresholds.low_max) return {text:'Low', color:'#d9efde'};
  if (score <= thresholds.moderate_max) return {text:'Moderate', color:'#fff2bf'};
  if (score <= thresholds.high_max) return {text:'High', color:'#ffd5a7'};
  return {text:'Critical', color:'#f9b9ba'};
}
function badge(score) {
  const s = severity(score);
  return `<span class="badge text-dark" style="background-color:${s.color}">${score == null ? s.text : `${score} · ${s.text}`}</span>`;
}
function options(id) { $(id).innerHTML = '<option value="">Not assessed</option>' + Array.from({length:5}, (_, i) => `<option value="${i+1}">${i+1}</option>`).join(''); }
for (const id of ['inherentLikelihood','inherentImpact','residualLikelihood','residualImpact']) options(id);

function filteredRisks() {
  const q = $('registerSearch').value.trim().toLowerCase();
  return risks.filter(r => `${r.asset} ${r.threat_summary} ${(r.risk_types || []).join(' ')}`.toLowerCase().includes(q));
}
function renderHeatmap() {
  const mode = document.querySelector('input[name="scoreMode"]:checked')?.value || 'inherent';
  const byCell = new Map();
  for (const r of risks) {
    const likelihood = mode === 'residual' ? r.register_residual_likelihood : r.register_likelihood;
    const impact = mode === 'residual' ? r.register_residual_impact : r.register_impact;
    if (likelihood && impact) byCell.set(`${likelihood}:${impact}`, (byCell.get(`${likelihood}:${impact}`) || 0) + 1);
  }
  let html = '<thead><tr><th scope="col">Likelihood \\ Impact</th>' + [1,2,3,4,5].map(i => `<th scope="col">${i}</th>`).join('') + '</tr></thead><tbody>';
  for (let likelihood=5; likelihood>=1; likelihood--) {
    html += `<tr><th scope="row">${likelihood}</th>`;
    for (let impact=1; impact<=5; impact++) {
      const key = `${likelihood}:${impact}`, score = likelihood * impact, amount = byCell.get(key) || 0;
      html += `<td title="Likelihood ${likelihood}, impact ${impact}: ${amount} risks" style="background-color:${severity(score).color}">${amount ? `<strong>${amount}</strong>` : '·'}</td>`;
    }
    html += '</tr>';
  }
  $('heatmap').innerHTML = html + '</tbody>';
}
function render() {
  $('registerRows').innerHTML = filteredRisks().map(r => `
    <tr><td><a href="${withFramework(`/risks.html?edit=${encodeURIComponent(r.id)}`, framework)}">${esc(r.asset)}</a></td>
    <td class="wrap">${esc(r.threat_summary || 'Untitled risk')}<div class="small-muted">${esc((r.risk_types || []).join(' / '))}</div></td>
    <td>${esc(r.risk_owner?.username || 'Unassigned')}</td>
    <td>${badge(r.register_inherent_score)}</td><td>${badge(r.register_residual_score)}</td>
    <td>${esc((r.treatment_strategy || 'Undecided').replaceAll('_',' '))}<div class="small-muted">${esc(r.treatment_status.replaceAll('_',' '))}${r.treatment_due_at ? ` · due ${esc(r.treatment_due_at)}` : ''}</div></td>
    <td>${r.control_count || 0}</td><td><button class="btn btn-sm btn-outline-primary" data-rate="${esc(r.id)}" type="button">${manage ? 'Rate / treat' : 'Details'}</button></td></tr>`).join('') || '<tr><td colspan="8" class="small-muted p-3">No risks match.</td></tr>';
  $('registerMeta').innerHTML = `${risks.length} of ${total} risks shown ${total > risks.length ? '<button class="btn btn-sm btn-outline-primary ms-2" type="button" id="moreRisks">Load more</button>' : ''}`;
  $('moreRisks')?.addEventListener('click', loadMore);
  renderHeatmap();
}
function renderLibrary() {
  $('libraryList').innerHTML = library.length ? library.map(item => `
    <div class="list-group-item d-flex justify-content-between gap-2"><div><strong>${esc(item.name)}</strong><div class="small-muted">${esc(item.threat_summary)}</div></div>
      <div class="d-flex gap-1 align-self-start"><a class="btn btn-sm btn-outline-primary" href="${withFramework(`/risks.html?template=${encodeURIComponent(item.id)}`, framework)}">Use template</a>
      ${manage ? `<button type="button" class="btn btn-sm btn-outline-danger" data-delete-library="${esc(item.id)}">Delete</button>` : ''}</div></div>`).join('') : '<span class="small-muted">No reusable scenarios yet. Select a risk and save its scenario here.</span>';
}
async function loadMore() {
  try {
    const data = await apiGet(`${base}/register?framework=${encodeURIComponent(framework)}&limit=200&offset=${offset}`);
    risks.push(...data.items); offset += data.items.length; total = data.total; thresholds = data.thresholds;
    for (const [key,id] of Object.entries({low_max:'lowMax',moderate_max:'moderateMax',high_max:'highMax'})) $(id).value=thresholds[key];
    render();
  } catch(e) { notify(`Could not load risk register: ${String(e)}`, 'danger'); }
}
async function reload() {
  risks=[]; offset=0; await Promise.all([loadMore(), loadLibrary()]);
}
async function loadLibrary() {
  try { library = (await apiGet(`${base}/library`)).items || []; renderLibrary(); }
  catch(e) { notify(`Could not load risk library: ${String(e)}`, 'danger'); }
}
function selectRisk(r) {
  selected = r; $('ratingCard').style.display = '';
  $('ratingTitle').textContent = `${r.asset} · ${r.threat_summary || 'Risk assessment'}`;
  for (const [id, key] of Object.entries({inherentLikelihood:'register_likelihood', inherentImpact:'register_impact', residualLikelihood:'register_residual_likelihood', residualImpact:'register_residual_impact'})) $(id).value = r[key] || '';
  $('treatmentStrategy').value = r.treatment_strategy || ''; $('treatmentStatus').value = r.treatment_status || 'open';
  $('treatmentDue').value = r.treatment_due_at || ''; $('treatmentPlan').value = r.treatment_plan || '';
  $('editScenario').href = withFramework(`/risks.html?edit=${encodeURIComponent(r.id)}`, framework);
  if (!manage) $('ratingForm').querySelectorAll('input, textarea, select, button').forEach(el => el.disabled = true);
  $('ratingCard').scrollIntoView({behavior:'smooth', block:'start'});
}
$('registerRows').addEventListener('click', e => { const btn = e.target.closest('[data-rate]'); if (btn) { const r = risks.find(item => item.id === btn.dataset.rate); if (r) selectRisk(r); } });
$('registerSearch').addEventListener('input', render);
document.querySelectorAll('input[name="scoreMode"]').forEach(input => input.addEventListener('change', renderHeatmap));
$('ratingForm').addEventListener('submit', async e => {
  e.preventDefault(); if (!selected || !manage) return;
  const val = id => $(id).value ? Number($(id).value) : null;
  const payload = {register_likelihood:val('inherentLikelihood'), register_impact:val('inherentImpact'),
    register_residual_likelihood:val('residualLikelihood'), register_residual_impact:val('residualImpact'),
    treatment_strategy:$('treatmentStrategy').value, treatment_status:$('treatmentStatus').value,
    treatment_due_at:$('treatmentDue').value || null, treatment_plan:$('treatmentPlan').value};
  try {
    await apiPatch(`${base}/${encodeURIComponent(selected.id)}/register`, payload);
    const id=selected.id; await reload(); selectRisk(risks.find(r => r.id===id) || selected); notify('Rating and treatment saved.');
  } catch(err) { notify(`Could not save risk rating: ${String(err)}`, 'danger'); }
});
$('thresholdForm').addEventListener('submit', async e => {
  e.preventDefault(); if (!manage) return;
  try {
    thresholds = await apiPatch(`${base}/register/settings`, {low_max:Number($('lowMax').value), moderate_max:Number($('moderateMax').value), high_max:Number($('highMax').value)});
    render(); notify('Risk thresholds saved.');
  } catch(err) { notify(`Could not save thresholds: ${String(err)}`, 'danger'); }
});
$('addToLibrary').addEventListener('click', async () => {
  if (!selected || !manage) return;
  const name = prompt('Name this reusable scenario', selected.threat_summary?.slice(0, 100) || selected.asset);
  if (!name) return;
  try {
    await apiPost(`${base}/library`, {name, threat_summary:selected.threat_summary,
      risk_types:selected.risk_types, treatment_guidance:selected.treatment_plan || ''});
    await loadLibrary(); notify('Scenario saved to risk library.');
  } catch(err) { notify(`Could not save library item: ${String(err)}`, 'danger'); }
});
$('libraryList').addEventListener('click', async e => {
  const btn=e.target.closest('[data-delete-library]'); if (!btn || !manage || !confirm('Delete this reusable template?')) return;
  try { await apiDelete(`${base}/library/${btn.dataset.deleteLibrary}`); await loadLibrary(); notify('Template deleted.'); }
  catch(err) { notify(`Could not delete template: ${String(err)}`, 'danger'); }
});
$('exportRegister').href = `${base}/register/export.csv?framework=${encodeURIComponent(framework)}`;
$('importForm').addEventListener('submit', async e => {
  e.preventDefault(); if (!manage) return;
  const file=$('importFile').files[0]; if (!file) return;
  const body=new FormData(); body.append('file', file);
  try {
    const response=await apiPostForm(`${base}/register/import.csv?framework=${encodeURIComponent(framework)}`, body);
    $('importForm').reset(); await reload(); notify(`${response.created} risks imported.`);
  } catch(err) { notify(`Could not import CSV: ${String(err)}`, 'danger'); }
});
if (!manage) { $('thresholdForm').style.display='none'; $('importForm').style.display='none'; }
await reload();
