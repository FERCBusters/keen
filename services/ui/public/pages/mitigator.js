// Keen Mitigator: a natural-language starting point for a risk. It analyses
// the described issue and lays out three choices in order - an existing risk
// that already covers it, a library template, or a new risk - and hands the
// chosen draft to the risk wizard, where everything is reviewed before saving.
import {
  initNavbar,
  apiGet,
  apiPost,
  esc,
  toast,
  getCurrentFramework,
  withFramework,
} from '/app.js';
import {
  methodBadgeHtml,
  riskTypeBadgesHtml,
  scoreBadge,
  riskDisplayTitle,
  riskHref,
  controlHref,
  pestleHref,
  interestedPartyHref,
  riskWizardHref,
  storeWizardDraft,
} from '/pages/risk-common.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const canViewRisks = !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
const canManageRisks = !!(me?.is_admin || me?.can_manage_risks);
const canManagePestle = !!(me?.is_admin || me?.can_manage_pestle || me?.can_manage_risks);

const $ = (id) => document.getElementById(id);
const status = $('status');
const form = $('mitigatorForm');
const issueInput = $('mitigatorIssue');
const assetInput = $('mitigatorAsset');
const categorySelect = $('mitigatorCategory');
const subcategorySelect = $('mitigatorSubcategory');
const resultsCard = $('mitigatorResultsCard');
const resultsMeta = $('mitigatorResultsMeta');
const signalsEl = $('mitigatorSignals');
const suggestionsEl = $('mitigatorSuggestions');
const weightOutputs = {
  Confidentiality: $('mitigatorWeightCValue'),
  Integrity: $('mitigatorWeightIValue'),
  Availability: $('mitigatorWeightAValue'),
};

let categories = [];
let analysis = null;

function notify(message, kind = 'danger') {
  toast(status, message, kind);
}

// --- Form -------------------------------------------------------------------------------------

function syncWeightLabels() {
  document.querySelectorAll('.mitigator-weight').forEach((range) => {
    const out = weightOutputs[range.dataset.riskType || ''];
    if (out) out.textContent = String(range.value || 0);
  });
}

function renderCategories() {
  const current = categorySelect.value;
  categorySelect.innerHTML =
    '<option value="">Not sure yet</option>' +
    categories.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  categorySelect.value = categories.some((c) => String(c.id) === current) ? current : '';
  renderSubcategories();
}

function renderSubcategories() {
  const category = categories.find((c) => String(c.id) === String(categorySelect.value));
  const current = subcategorySelect.value;
  const subs = category?.subcategories || [];
  subcategorySelect.innerHTML =
    '<option value="">Not sure yet</option>' +
    subs.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
  subcategorySelect.value = subs.some((s) => String(s.id) === current) ? current : '';
  subcategorySelect.disabled = !category;
}

function selectedRiskTypes() {
  return [...document.querySelectorAll('.mitigator-risk-type')]
    .filter((x) => x.checked)
    .map((x) => x.value);
}

function riskWeights() {
  const out = {};
  document.querySelectorAll('.mitigator-weight').forEach((range) => {
    const rt = range.dataset.riskType || '';
    if (rt) out[rt] = Math.max(0, Math.min(100, Math.floor(Number(range.value || 0))));
  });
  return out;
}

function impactFlags() {
  const out = {};
  document.querySelectorAll('.mitigator-impact').forEach((cb) => {
    out[cb.value] = !!cb.checked;
  });
  return out;
}

function payload() {
  const issue = issueInput.value.trim();
  if (!issue) throw new Error('Describe the potential issue first');
  const category = categories.find((c) => String(c.id) === String(categorySelect.value));
  const sub = (category?.subcategories || []).find(
    (x) => String(x.id) === String(subcategorySelect.value),
  );
  return {
    issue,
    asset_name: assetInput.value.trim(),
    category_id: category?.id || null,
    category_name: category?.name || '',
    subcategory_id: sub?.id || null,
    subcategory_name: sub?.name || '',
    risk_types: selectedRiskTypes(),
    risk_weights: riskWeights(),
    impact_flags: impactFlags(),
    framework,
    limit: 10,
  };
}

function reset() {
  analysis = null;
  issueInput.value = '';
  assetInput.value = '';
  document.querySelectorAll('.mitigator-risk-type').forEach((cb) => {
    cb.checked = cb.value === 'Confidentiality';
  });
  const defaults = { Confidentiality: 60, Integrity: 40, Availability: 40 };
  document.querySelectorAll('.mitigator-weight').forEach((range) => {
    range.value = String(defaults[range.dataset.riskType] || 40);
  });
  document.querySelectorAll('.mitigator-impact').forEach((cb) => {
    cb.checked = false;
  });
  resultsCard.style.display = 'none';
  signalsEl.innerHTML = '';
  suggestionsEl.innerHTML = '';
  syncWeightLabels();
  renderCategories();
}

// --- Results ------------------------------------------------------------------------------------

function confidenceBadge(confidence) {
  const c = String(confidence || '').toLowerCase();
  const cls =
    c === 'strong' ? 'text-bg-success' : c === 'medium' ? 'text-bg-warning' : 'text-bg-secondary';
  return `<span class="badge ${cls}">${esc(c ? c[0].toUpperCase() + c.slice(1) : 'Suggested')} match</span>`;
}

function termBadges(terms) {
  return (terms || [])
    .slice(0, 8)
    .map((t) => `<span class="badge text-bg-light mitigator-term-badge">${esc(t)}</span>`)
    .join('');
}

function sectionHtml(number, title, lead, body, { tone = '' } = {}) {
  return `<section class="mitigator-choice border rounded-3 p-3 ${tone}">
    <div class="d-flex gap-2 align-items-start">
      <span class="mitigator-choice-number">${number}</span>
      <div class="flex-grow-1" style="min-width:0">
        <h2 class="h6 mb-1">${title}</h2>
        <p class="small-muted mb-2">${lead}</p>
        ${body}
      </div>
    </div>
  </section>`;
}

function renderSignals(data) {
  const rows = data?.signals || [];
  signalsEl.innerHTML = rows.length
    ? `<div class="small-muted mb-1">What I noticed in your description</div><div class="d-flex flex-wrap gap-2">${rows.map((sig) => `<span class="badge text-bg-light border" title="${esc(sig.reason || '')}">${esc(sig.label || sig.kind || 'Signal')}</span>`).join('')}</div>`
    : '<div class="small-muted">I found no particular signals in the description beyond your answers above.</div>';
}

function existingSection(data) {
  const existing = data?.existing_matches || {};
  const risks = existing.cia_risks || [];
  const pestle = existing.pestle_items || [];
  const parties = existing.interested_parties || [];
  const riskCards = risks
    .map(
      (r) => `<div class="mitigator-suggestion-card d-flex flex-wrap justify-content-between gap-2">
      <div style="min-width:0">
        <div class="fw-semibold">${esc(riskDisplayTitle(r))}</div>
        <div class="small-muted">${esc(r.asset_name || r.asset || '')}${(r.risk_types || []).length ? ` · ${esc(r.risk_types.join(', '))}` : ''}${r.residual_risk_score != null ? ` · after controls ${scoreBadge(r.residual_risk_score)}` : ''}</div>
        ${r.threat_summary && r.threat_summary !== riskDisplayTitle(r) ? `<div class="small-muted">${esc(r.threat_summary)}</div>` : ''}
      </div>
      <a class="btn btn-sm btn-outline-primary align-self-start" href="${esc(riskHref(r.id, framework))}">Open this risk</a>
    </div>`,
    )
    .join('');
  const pestleCards = pestle
    .map(
      (p) => `<div class="mitigator-suggestion-card d-flex flex-wrap justify-content-between gap-2">
      <div style="min-width:0">
        <div class="fw-semibold">${methodBadgeHtml('pestle')} ${esc(p.item || 'PESTLE(E) factor')}</div>
        <div class="small-muted">${esc(p.type || '')} · ${esc(p.lens || '')}${
          (p.clauses || []).length
            ? ` · clauses ${esc(
                p.clauses
                  .map((c) => c?.clause?.ref || '')
                  .filter(Boolean)
                  .join(', '),
              )}`
            : ''
        } ${confidenceBadge(p.confidence)}</div>
        ${termBadges(p.matched_terms) ? `<div class="d-flex flex-wrap gap-1 mt-1">${termBadges(p.matched_terms)}</div>` : ''}
      </div>
      <a class="btn btn-sm btn-outline-primary align-self-start" href="${esc(pestleHref(p.id, framework))}">Open this factor</a>
    </div>`,
    )
    .join('');
  const partyHtml = parties.length
    ? `<div class="small mt-2">Interested parties already recorded that match: ${parties.map((p) => `<a href="${esc(interestedPartyHref(p.id, framework))}">${esc(p.label || p.name || 'Interested party')}</a>`).join(', ')}.</div>`
    : '';
  const any = risks.length || pestle.length;
  return sectionHtml(
    1,
    any ? 'Is it already covered?' : 'Nothing like this exists yet',
    any
      ? 'These existing risks and factors use similar words. If one of them is the same thing, open it and update it instead of creating a duplicate.'
      : 'No existing risk, PESTLE(E) factor or interested party matched your description, so a new one is likely needed.',
    `${riskCards}${pestleCards}${partyHtml}` || '',
    { tone: any ? 'border-warning-subtle' : '' },
  );
}

function templateSection(data) {
  const items = data?.library_suggestions || [];
  const body = items.length
    ? items
        .map((item) => {
          const example = item.suggested_assessment || {};
          return `<div class="mitigator-suggestion-card d-flex flex-wrap justify-content-between gap-2">
        <div style="min-width:0">
          <div class="fw-semibold">${esc(item.name)} ${riskTypeBadgesHtml(item.risk_types)}</div>
          <div class="small-muted">${esc(item.threat_summary || '')}</div>
          <div class="small-muted">${esc(item.reason || '')}${(item.control_refs || []).length ? ` Suggested controls here: ${esc(item.control_refs.join(', '))}.` : ''}</div>
          ${example.inherent_likelihood && example.inherent_impact ? `<div class="small-muted">Example rating: ${esc(String(example.inherent_likelihood))} × ${esc(String(example.inherent_impact))}; review before saving.</div>` : ''}
        </div>
        ${canManageRisks ? `<button class="btn btn-sm btn-outline-primary align-self-start" type="button" data-use-template="${esc(item.id)}">Use this template</button>` : ''}
      </div>`;
        })
        .join('')
    : '<div class="small-muted">No library template matched. You can still start from scratch below, and save the result as a template afterwards.</div>';
  return sectionHtml(
    2,
    'Start from a template',
    'Templates from your reusable risk library fill in the wizard with a scenario, its CIA type and example ratings. Your description comes along too.',
    body,
  );
}

function newRiskSection(data) {
  const controls = data?.control_suggestions || data?.suggestions || [];
  const parties = data?.interested_party_suggestions || [];
  const draftPestle = data?.draft_pestle_item;
  const controlRows = controls
    .map((item, idx) => {
      const c = item.control || {};
      const reasons = (item.reasons || []).map((r) => `<li>${esc(r)}</li>`).join('');
      return `<div class="mitigator-suggestion-card">
      <div class="d-flex flex-wrap gap-2 align-items-start justify-content-between">
        <div class="form-check">
          <input class="form-check-input mitigator-suggestion-select" type="checkbox" value="${esc(c.id || '')}" id="mitigatorSuggestion${idx}" ${idx < 8 ? 'checked' : ''}>
          <label class="form-check-label" for="mitigatorSuggestion${idx}"><span class="fw-semibold"><a href="${esc(controlHref(c.id || '', framework))}">${esc(c.ref || 'Control')}</a> - ${esc(c.title || '')}</span></label>
        </div>
        <div class="d-flex flex-wrap gap-2 align-items-center">${c.in_scope === false ? '<span class="badge text-bg-warning">Out of scope - review applicability</span>' : ''}${confidenceBadge(item.confidence)}</div>
      </div>
      ${reasons ? `<ul class="small mt-2 mb-1">${reasons}</ul>` : ''}
      ${termBadges(item.matched_terms) ? `<div class="d-flex flex-wrap gap-1">${termBadges(item.matched_terms)}</div>` : ''}
    </div>`;
    })
    .join('');
  const partyRows = parties
    .map(
      (item, idx) => `<div class="form-check">
      <input class="form-check-input mitigator-interested-select" type="checkbox" value="${esc(String(idx))}" id="mitigatorParty${idx}" ${item.duplicate_exact ? 'disabled' : 'checked'}>
      <label class="form-check-label" for="mitigatorParty${idx}"><span class="fw-semibold">${esc(item.name || 'Interested party')}</span> - ${esc(item.nature || '')}${item.duplicate_exact ? ' <span class="small-muted">(already recorded)</span>' : ''}</label>
      <div class="small-muted">${esc(item.reason || '')}</div>
    </div>`,
    )
    .join('');
  const pestleHits = (data?.pestle_hits || [])
    .map((h) => `<span class="badge text-bg-info">${esc(h.type || 'PESTLE(E)')}</span>`)
    .join(' ');
  const actions = canManageRisks
    ? `<div class="d-flex flex-wrap gap-2 mt-3">
        <button class="btn btn-primary" type="button" id="mitigatorToWizard"><i class="bi bi-magic me-1" aria-hidden="true"></i>Create a new risk in the wizard</button>
        ${draftPestle && canManagePestle ? `<button class="btn btn-outline-primary" type="button" id="mitigatorToPestleWizard"><i class="bi bi-globe2 me-1" aria-hidden="true"></i>Record it as a PESTLE(E) factor instead</button>` : ''}
      </div>
      <div class="small-muted mt-2">The wizard opens with your description, the asset, the CIA types, the ticked controls and parties filled in. Nothing is saved until you reach the last step.</div>`
    : '<div class="small-muted mt-3">You need risk.manage permission to create risks.</div>';
  const body = `
    <div class="mb-3">
      <div class="fw-semibold">Safeguards the new risk could link to <span class="badge text-bg-secondary ms-1">framework controls</span></div>
      <div class="small-muted mb-2">Each of these is a framework control that would reduce the risk. Untick any that do not apply; you can change them again in the wizard.</div>
      ${controlRows ? `<div class="vstack gap-2">${controlRows}</div>` : '<div class="alert alert-warning mb-0">No controls matched strongly enough. Add more detail or select more impact prompts, or choose controls in the wizard.</div>'}
    </div>
    ${partyRows ? `<div class="mb-2"><div class="fw-semibold">People and parties who might be affected</div><div class="small-muted mb-2">Ticked parties are added to the new risk as interested parties.</div>${partyRows}</div>` : ''}
    ${pestleHits ? `<div class="small mt-2">This also reads like a PESTLE(E) influence: ${pestleHits}</div>` : ''}
    ${actions}`;
  return sectionHtml(
    3,
    'Or create something new',
    'If nothing above fits, start a new risk. The wizard walks you through it one question at a time.',
    body,
  );
}

function render(data) {
  analysis = data || null;
  resultsCard.style.display = '';
  const controls = data?.control_suggestions || data?.suggestions || [];
  const existing = data?.existing_matches || {};
  const existingCount = (existing.cia_risks || []).length + (existing.pestle_items || []).length;
  resultsMeta.textContent = `${existingCount} existing match${existingCount === 1 ? '' : 'es'}, ${(data?.library_suggestions || []).length} template${(data?.library_suggestions || []).length === 1 ? '' : 's'} and ${controls.length} suggested control${controls.length === 1 ? '' : 's'} for ${data?.framework || framework || 'the current framework'}.`;
  renderSignals(data);
  suggestionsEl.innerHTML = [
    existingSection(data),
    templateSection(data),
    newRiskSection(data),
  ].join('');
}

// --- Hand-off to the wizard -------------------------------------------------------------------

function selectedControls() {
  return [...document.querySelectorAll('.mitigator-suggestion-select')]
    .filter((cb) => cb.checked)
    .map((cb) => cb.value)
    .filter(Boolean);
}

function selectedPartyDrafts() {
  const suggestions = analysis?.interested_party_suggestions || [];
  return [...document.querySelectorAll('.mitigator-interested-select')]
    .filter((cb) => cb.checked && !cb.disabled)
    .map((cb) => suggestions[Number(cb.value)])
    .filter(Boolean)
    .map((s) => ({ name: s.name, nature: s.nature }));
}

function draftForWizard({ templateId = '' } = {}) {
  const draft = analysis?.draft_risk || {};
  const controls = selectedControls();
  const types = draft.risk_types || selectedRiskTypes();
  return {
    method: types.length ? 'cia' : 'general',
    title: String(issueInput.value.trim())
      .split(/[.!?\n]/)[0]
      .slice(0, 120),
    threat_summary: draft.threat_summary || issueInput.value.trim(),
    asset_name: assetInput.value.trim() || draft.asset_name || '',
    category_name: draft.category_name || '',
    subcategory_name: draft.subcategory_name || '',
    risk_types: types,
    note: draft.note || '',
    controls,
    interested_parties: selectedPartyDrafts(),
    mitigator_context: { ...(draft.mitigator_context || {}), selected_control_ids: controls },
    template_id: templateId,
  };
}

function openWizard(draft, extra = {}) {
  storeWizardDraft(draft);
  location.href = riskWizardHref(framework, extra);
}

// --- Events -------------------------------------------------------------------------------------

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  if (!canViewRisks) return;
  let body;
  try {
    body = payload();
  } catch (e) {
    notify(String(e.message || e), 'warning');
    return;
  }
  $('mitigatorAnalyse').disabled = true;
  $('mitigatorThinking').hidden = false;
  form.setAttribute('aria-busy', 'true');
  try {
    render(await apiPost('/api/v1/risks/mitigator/analyse', body));
    notify('Keen Mitigator analysis complete', 'success');
  } catch (e) {
    notify(`Failed to analyse mitigations: ${String(e)}`);
  } finally {
    $('mitigatorAnalyse').disabled = false;
    $('mitigatorThinking').hidden = true;
    form.removeAttribute('aria-busy');
  }
});
$('mitigatorClear').addEventListener('click', reset);
categorySelect.addEventListener('change', renderSubcategories);
document
  .querySelectorAll('.mitigator-weight')
  .forEach((range) => range.addEventListener('input', syncWeightLabels));
suggestionsEl.addEventListener('click', (ev) => {
  const template = ev.target.closest('[data-use-template]');
  if (template && canManageRisks) {
    openWizard(draftForWizard({ templateId: template.dataset.useTemplate }), {
      template: template.dataset.useTemplate,
    });
    return;
  }
  if (ev.target.closest('#mitigatorToWizard') && canManageRisks) {
    openWizard(draftForWizard());
    return;
  }
  if (ev.target.closest('#mitigatorToPestleWizard') && canManagePestle) {
    const draft = analysis?.draft_pestle_item || {};
    openWizard(
      {
        method: 'pestle',
        threat_summary: draft.item || issueInput.value.trim(),
        pestle_type: draft.type || '',
        lens: draft.lens || '',
      },
      { method: 'pestle' },
    );
  }
});

// --- Start --------------------------------------------------------------------------------------

if (!canViewRisks) {
  notify('You need risk.read permission to use Keen Mitigator.');
  form.querySelectorAll('input, select, textarea, button').forEach((el) => {
    el.disabled = true;
  });
} else {
  try {
    categories = (await apiGet('/api/v1/risks/categories'))?.items || [];
  } catch (e) {
    notify(`Could not load asset categories: ${String(e)}`);
  }
  reset();
  issueInput.focus();
  const wizardLink = document.querySelector('a[href="/risk-wizard.html"]');
  if (wizardLink) wizardLink.href = withFramework('/risk-wizard.html', framework);
}
