// The risk wizard: one guided path that creates a risk and whatever it needs
// along the way (an asset, interested parties, a business process), or edits
// an existing register risk. It asks for the method first and shows only the
// fields that method needs.
import { featureEnabled } from '/pages/feature-availability.js';
import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  esc,
  toast,
  debounce,
  getCurrentFramework,
  getFrameworkCatalog,
  withFramework,
} from '/app.js';
import {
  RISK_TYPES,
  RISK_METHODS,
  TREATMENT_STRATEGIES,
  TREATMENT_STATUSES,
  LIKELIHOOD_SCALE,
  IMPACT_SCALE,
  clampScore,
  scorePreviewClass,
  severityFor,
  riskHref,
  pestleHref,
  riskDashboardHref,
  takeWizardDraft,
} from '/pages/risk-common.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const canManageRisks = !!(me?.is_admin || me?.can_manage_risks);
const canManagePestle = !!(me?.is_admin || me?.can_manage_pestle || me?.can_manage_risks);
const params = new URLSearchParams(location.search);
const editingId = String(params.get('id') || '').trim();
const requestedMethod = String(params.get('method') || '').trim();
const requestedTemplate = String(params.get('template') || '').trim();

const $ = (id) => document.getElementById(id);
const status = $('status');
const form = $('wizardForm');
const CIA_HELP = {
  Confidentiality: 'Information could be seen, taken or leaked by someone who should not have it.',
  Integrity: 'Information could be changed, corrupted or made unreliable.',
  Availability: 'Information or a service could become unavailable when it is needed.',
};

// Everything the wizard knows so far. Saving turns this into API calls.
const state = {
  method: 'general',
  template: null,
  asset: null, // {id, name} for an existing asset
  newAsset: null, // {name, category, subcategory} for one to create
  processes: [], // [{id, name}] existing business processes
  newProcesses: [], // [name]
  parties: [], // [{kind, id, label, detail}] existing, or {kind:'new', label, detail}
  title: '',
  description: '',
  note: '',
  riskTypes: [],
  pestleType: '',
  lens: 'External',
  relevance: '',
  rationale: '',
  inherent: { likelihood: 1, impact: 1 },
  residual: { likelihood: 1, impact: 1 },
  owner: '',
  treatment: { strategy: '', status: 'open', due: '', plan: '' },
  controls: new Set(),
  clauseRelevance: new Map(), // clause id -> relevance code
  mitigatorContext: null,
  suggestedControlIds: [],
  existing: null, // the risk being edited
};

let categories = [];
let assets = [];
let users = [];
let roles = [];
let allControls = [];
let library = [];
let partyOptions = { interested_parties: [], people: [], vendors: [], natures: [] };
let pestleMeta = { types: [], lenses: [], relevance_levels: [] };
let businessProcesses = [];
let clauses = [];
let keenAfEnabled = false;
let thresholds = { low_max: 4, moderate_max: 9, high_max: 16 };

const STEPS = [
  ['method', 'Kind of risk'],
  ['subject', 'What is affected'],
  ['parties', 'Who is affected'],
  ['describe', 'Describe it'],
  ['rate', 'Likelihood and impact'],
  ['controls', 'Controls'],
  ['review', 'Check and save'],
];
let stepIndex = 0;

function notify(message, kind = 'danger') {
  toast(status, message, kind);
}

function isPestle() {
  return state.method === 'pestle';
}

// Steps that do not apply to the chosen method are skipped in both directions.
function stepApplies(step) {
  if (state.method === 'pestle') return !['parties', 'controls'].includes(step);
  return true;
}

function visibleSteps() {
  return STEPS.filter(([step]) => stepApplies(step) && !(editingId && step === 'method'));
}

function currentStep() {
  return visibleSteps()[stepIndex]?.[0] || 'review';
}

function renderStepper() {
  const steps = visibleSteps();
  $('wizardSteps').innerHTML = steps
    .map(([step, stepLabel], index) => {
      // A PESTLE(E) factor is rated against clauses, so its rating step is named for them.
      const label = step === 'rate' && isPestle() ? 'Relevant clauses' : stepLabel;
      const cls = index < stepIndex ? 'done' : index === stepIndex ? 'current' : '';
      return `<li class="risk-wizard-step-pill ${cls}"><button class="btn btn-sm ${index === stepIndex ? 'btn-primary' : index < stepIndex ? 'btn-outline-success' : 'btn-outline-secondary'}" type="button" data-goto-step="${esc(step)}" ${index > stepIndex ? 'disabled' : ''}>${index + 1}. ${esc(label)}</button></li>`;
    })
    .join('');
}

function showStep(step) {
  for (const section of document.querySelectorAll('.risk-wizard-step'))
    section.hidden = section.dataset.step !== step;
  for (const el of document.querySelectorAll('[data-method-only]'))
    el.hidden = el.dataset.methodOnly !== state.method;
  for (const el of document.querySelectorAll('[data-method-not]'))
    el.hidden = el.dataset.methodNot === state.method;
  for (const el of document.querySelectorAll('[data-subject]'))
    el.hidden = el.dataset.subject !== (isPestle() ? 'pestle' : 'asset');
  const steps = visibleSteps();
  const last = stepIndex >= steps.length - 1;
  $('backStep').hidden = stepIndex === 0 || step === 'done';
  $('nextStep').innerHTML = last
    ? `<i class="bi bi-check2 me-1" aria-hidden="true"></i>${editingId ? 'Save changes' : isPestle() ? 'Create PESTLE(E) factor' : 'Create risk'}`
    : 'Next<i class="bi bi-arrow-right ms-1" aria-hidden="true"></i>';
  $('skipStep').hidden =
    !['parties', 'controls', 'subject'].includes(step) || isPestleSubjectRequired(step);
  $('wizardFooter').hidden = step === 'done';
  $('stepHint').textContent = stepHintFor(step);
  renderStepper();
  if (step === 'review') renderReview();
  const heading = document.querySelector(`.risk-wizard-step[data-step="${step}"] h2`);
  heading?.setAttribute('tabindex', '-1');
  heading?.focus?.();
}

function isPestleSubjectRequired(step) {
  return step === 'subject' && !isPestle();
}

function stepHintFor(step) {
  switch (step) {
    case 'method':
      return 'Step 1 of ' + visibleSteps().length;
    case 'subject':
      return isPestle()
        ? 'Processes are optional for a PESTLE(E) factor.'
        : 'Pick or add one asset.';
    case 'parties':
      return 'You can skip this and add parties later.';
    case 'describe':
      return 'A short name and a few sentences are enough.';
    case 'rate':
      return isPestle()
        ? 'Clause relevance is optional.'
        : 'Use the words beside each scale as a guide.';
    case 'controls':
      return 'Controls can be added later from the risk page too.';
    default:
      return '';
  }
}

function goTo(step) {
  const index = visibleSteps().findIndex(([s]) => s === step);
  if (index >= 0) {
    stepIndex = index;
    showStep(step);
  }
}

// --- Step 1: method and templates -------------------------------------------------------

function renderMethodChoices() {
  const choices = Object.entries(RISK_METHODS).filter(
    ([key]) => key !== 'pestle' || (canManagePestle && featureEnabled(me, 'pestle')),
  );
  $('methodChoices').innerHTML = choices
    .map(
      ([key, meta]) => `<div class="col-12 col-md-4">
      <label class="risk-method-card border rounded-3 p-3 h-100 d-block ${state.method === key ? 'selected' : ''}">
        <input class="form-check-input me-2" type="radio" name="riskMethod" value="${esc(key)}" ${state.method === key ? 'checked' : ''}>
        <span class="fw-semibold"><i class="bi ${esc(meta.icon)} me-1" aria-hidden="true"></i>${esc(meta.label)}</span>
        <div class="small-muted mt-1">${esc(meta.description)}</div>
      </label>
    </div>`,
    )
    .join('');
  renderTemplates();
}

function templatesForMethod() {
  const wanted = isPestle() ? 'pestle' : 'risk';
  const q = String($('templateSearch')?.value || '')
    .trim()
    .toLowerCase();
  return library.filter(
    (item) =>
      (item.method || 'risk') === wanted &&
      (!q || `${item.name} ${item.threat_summary}`.toLowerCase().includes(q)),
  );
}

function renderTemplates() {
  const items = templatesForMethod();
  $('templateList').innerHTML = items.length
    ? items
        .slice(0, 50)
        .map(
          (
            item,
          ) => `<button class="list-group-item list-group-item-action ${state.template?.id === item.id ? 'active' : ''}" type="button" data-template="${esc(item.id)}">
        <div class="d-flex flex-wrap justify-content-between gap-2">
          <span class="fw-semibold">${esc(item.name)}</span>
          <span class="small">${esc(item.method === 'pestle' ? `${item.pestle_type} · ${item.lens}` : (item.risk_types || []).join(', ') || 'General risk')}</span>
        </div>
        <div class="small ${state.template?.id === item.id ? '' : 'small-muted'}">${esc(item.threat_summary || '')}</div>
      </button>`,
        )
        .join('')
    : `<div class="list-group-item small-muted">No ${isPestle() ? 'PESTLE(E)' : 'risk'} templates${$('templateSearch')?.value ? ' match' : ' yet'}. Carry on without one.</div>`;
}

function applyTemplate(item) {
  if (!item) {
    state.template = null;
    renderTemplates();
    return;
  }
  state.template = item;
  // A description already typed (for example in the Mitigator) wins over the
  // template's wording; the template supplies what is still blank.
  if (item.method === 'pestle') {
    state.method = 'pestle';
    state.pestleType = item.pestle_type || '';
    state.lens = item.lens || 'External';
    if (!state.description) state.description = item.threat_summary || '';
    if (!state.rationale) state.rationale = item.treatment_guidance || '';
  } else {
    state.method = (item.risk_types || []).length ? 'cia' : 'general';
    state.riskTypes = [...(item.risk_types || [])];
    if (!state.title) state.title = item.name || '';
    if (!state.description) state.description = item.threat_summary || '';
    if (!state.note) state.note = item.treatment_guidance || '';
    const example = item.suggested_assessment || {};
    for (const [target, key] of [
      [state.inherent, 'likelihood', 'inherent_likelihood'],
      [state.inherent, 'impact', 'inherent_impact'],
      [state.residual, 'likelihood', 'residual_likelihood'],
      [state.residual, 'impact', 'residual_impact'],
    ].map(([obj, field, k]) => [[obj, field], k])) {
      const value = Number(example[key]);
      if (Number.isInteger(value) && value >= 1 && value <= 5) target[0][target[1]] = value;
    }
    const suggestedAsset = String(example.asset || '').trim();
    if (suggestedAsset && !state.asset && !state.newAsset) {
      const match = assets.find(
        (a) => String(a.name || '').toLowerCase() === suggestedAsset.toLowerCase(),
      );
      if (match) state.asset = { id: match.id, name: match.name };
      else $('assetSearch').value = suggestedAsset;
    }
  }
  renderMethodChoices();
  notify('Template loaded. Everything it filled in can be changed before you save.', 'info');
}

// --- Step 2: subject -----------------------------------------------------------------------

function assetMatches(query) {
  const q = query.toLowerCase();
  return assets
    .filter(
      (a) =>
        !q ||
        `${a.name} ${a.category?.name || ''} ${a.subcategory?.name || ''}`
          .toLowerCase()
          .includes(q),
    )
    .slice(0, 30);
}

function renderAssets() {
  const query = String($('assetSearch').value || '').trim();
  const matches = assetMatches(query);
  const exact = matches.find((a) => String(a.name || '').toLowerCase() === query.toLowerCase());
  $('assetList').innerHTML =
    matches
      .map(
        (
          a,
        ) => `<button class="list-group-item list-group-item-action d-flex justify-content-between gap-2 ${state.asset?.id === a.id ? 'active' : ''}" type="button" data-asset="${esc(a.id)}">
      <span><span class="fw-semibold">${esc(a.name)}</span> <span class="small ${state.asset?.id === a.id ? '' : 'small-muted'}">${esc([a.category?.name, a.subcategory?.name].filter(Boolean).join(' › '))}</span></span>
      ${state.asset?.id === a.id ? '<i class="bi bi-check2" aria-hidden="true"></i>' : ''}
    </button>`,
      )
      .join('') ||
    (query
      ? ''
      : '<div class="list-group-item small-muted">No assets yet. Type a name to add the first one.</div>');
  const panel = $('newAssetPanel');
  const offerNew =
    !!query && !exact && !(state.asset && state.asset.name.toLowerCase() === query.toLowerCase());
  panel.hidden = !offerNew;
  if (offerNew) {
    $('newAssetName').textContent = query;
    state.newAsset = {
      name: query,
      category: $('newAssetCategory').value.trim(),
      subcategory: $('newAssetSubcategory').value.trim(),
    };
    if (state.asset) state.asset = null;
  } else if (!exact) {
    state.newAsset = null;
  }
  renderSelectedAsset();
}

function renderSelectedAsset() {
  const box = $('selectedAssetSummary');
  if (state.asset) {
    box.hidden = false;
    box.innerHTML = `<i class="bi bi-check-circle text-success me-1" aria-hidden="true"></i>Using existing asset <strong>${esc(state.asset.name)}</strong>. <button class="btn btn-link btn-sm p-0 align-baseline" type="button" id="clearAsset">Choose another</button>`;
  } else if (state.newAsset?.name) {
    box.hidden = false;
    box.innerHTML = `<i class="bi bi-plus-circle text-primary me-1" aria-hidden="true"></i>A new asset <strong>${esc(state.newAsset.name)}</strong> will be added${state.newAsset.category ? ` under <strong>${esc(state.newAsset.category)}</strong>${state.newAsset.subcategory ? ` › <strong>${esc(state.newAsset.subcategory)}</strong>` : ''}` : ''}.`;
  } else {
    box.hidden = true;
    box.innerHTML = '';
  }
}

function renderCategoryOptions() {
  $('assetCategoryOptions').innerHTML = categories
    .map((c) => `<option value="${esc(c.name)}"></option>`)
    .join('');
  renderSubcategoryOptions();
}

function renderSubcategoryOptions() {
  const chosen = $('newAssetCategory').value.trim().toLowerCase();
  const category = categories.find((c) => String(c.name || '').toLowerCase() === chosen);
  const subs = category
    ? category.subcategories || []
    : categories.flatMap((c) => c.subcategories || []);
  $('assetSubcategoryOptions').innerHTML = subs
    .map((s) => `<option value="${esc(s.name)}"></option>`)
    .join('');
}

function renderProcesses() {
  const query = String($('processSearch').value || '').trim();
  const q = query.toLowerCase();
  const chosen = new Set(state.processes.map((p) => p.id));
  const rows = businessProcesses
    .filter(
      (p) =>
        !q ||
        String(p.name || '')
          .toLowerCase()
          .includes(q),
    )
    .slice(0, 50)
    .map(
      (p) => `<label class="list-group-item d-flex gap-2 align-items-center">
      <input class="form-check-input mt-0" type="checkbox" data-process="${esc(p.id)}" ${chosen.has(p.id) ? 'checked' : ''}>
      <span>${esc(p.name)}</span>
    </label>`,
    );
  for (const name of state.newProcesses)
    rows.push(
      `<label class="list-group-item d-flex gap-2 align-items-center"><input class="form-check-input mt-0" type="checkbox" data-new-process="${esc(name)}" checked><span>${esc(name)} <span class="badge text-bg-light border">new</span></span></label>`,
    );
  const exact =
    businessProcesses.some((p) => String(p.name || '').toLowerCase() === q) ||
    state.newProcesses.some((n) => n.toLowerCase() === q);
  if (query && !exact)
    rows.push(
      `<button class="list-group-item list-group-item-action" type="button" data-add-process="${esc(query)}"><i class="bi bi-plus-lg me-1" aria-hidden="true"></i>Add “${esc(query)}” as a new business process</button>`,
    );
  $('processList').innerHTML =
    rows.join('') ||
    '<div class="list-group-item small-muted">No business processes yet. Type a name to add one.</div>';
}

// --- Step 3: parties ------------------------------------------------------------------------

function partyKey(p) {
  return `${p.kind}:${p.id || p.label}`;
}

function allPartyOptions() {
  return [...partyOptions.interested_parties, ...partyOptions.people, ...partyOptions.vendors];
}

function renderParties() {
  const query = String($('partySearch').value || '').trim();
  const q = query.toLowerCase();
  const chosen = new Set(state.parties.map(partyKey));
  const kindLabel = { interested_party: 'Interested party', person: 'Person', vendor: 'Vendor' };
  const rows = allPartyOptions()
    .filter((p) => !q || `${p.label} ${p.detail || ''}`.toLowerCase().includes(q))
    .slice(0, 60)
    .map(
      (p) => `<label class="list-group-item d-flex gap-2 align-items-center">
      <input class="form-check-input mt-0" type="checkbox" data-party="${esc(partyKey(p))}" ${chosen.has(partyKey(p)) ? 'checked' : ''}>
      <span class="flex-grow-1"><span class="fw-semibold">${esc(p.label)}</span>${p.detail ? ` <span class="small-muted">- ${esc(p.detail)}</span>` : ''}</span>
      <span class="badge text-bg-light border">${esc(kindLabel[p.kind] || p.kind)}</span>
    </label>`,
    );
  $('partyList').innerHTML =
    rows.join('') ||
    `<div class="list-group-item small-muted">${query ? 'Nobody by that name yet.' : 'No interested parties, people or vendors recorded yet. Type a name to add one.'}</div>`;
  const exact = allPartyOptions().some((p) => String(p.label || '').toLowerCase() === q);
  const alreadyNew = state.parties.some((p) => p.kind === 'new' && p.label.toLowerCase() === q);
  $('newPartyPanel').hidden = !query || exact || alreadyNew;
  $('newPartyName').textContent = query;
  renderSelectedParties();
}

function renderSelectedParties() {
  $('selectedParties').innerHTML = state.parties.length
    ? state.parties
        .map(
          (p) =>
            `<span class="badge badge-soft d-inline-flex align-items-center gap-1">${esc(p.label)}${p.detail ? ` <span class="small-muted">(${esc(p.detail)})</span>` : ''}${p.kind === 'new' ? ' <span class="small">new</span>' : ''}<button class="btn btn-sm btn-link p-0 ms-1" type="button" data-remove-party="${esc(partyKey(p))}" aria-label="Remove ${esc(p.label)}"><i class="bi bi-x" aria-hidden="true"></i></button></span>`,
        )
        .join('')
    : '<span class="small-muted">Nobody selected yet.</span>';
}

// --- Step 4: describe -----------------------------------------------------------------------

function renderDescribe() {
  $('riskTitle').value = state.title;
  $('riskTitle').required = !isPestle();
  $('riskTitle').closest('.col-12').hidden = isPestle();
  $('riskDescription').value = state.description;
  $('riskDescription').required = isPestle();
  $('riskNote').value = state.note;
  $('ciaChoices').innerHTML = RISK_TYPES.map(
    (type) => `<div class="col-12 col-md-4"><label class="border rounded-3 p-2 d-block h-100">
      <input class="form-check-input me-1" type="checkbox" data-cia="${esc(type)}" ${state.riskTypes.includes(type) ? 'checked' : ''}>
      <span class="fw-semibold">${esc(type)}</span>
      <div class="small-muted">${esc(CIA_HELP[type])}</div>
    </label></div>`,
  ).join('');
  $('pestleTypeChoices').innerHTML = (pestleMeta.types || [])
    .map(
      (type) =>
        `<label class="btn btn-sm ${state.pestleType === type ? 'btn-info' : 'btn-outline-secondary'}"><input class="visually-hidden" type="radio" name="pestleType" value="${esc(type)}" ${state.pestleType === type ? 'checked' : ''}>${esc(type)}</label>`,
    )
    .join('');
  $('pestleLens').innerHTML = (pestleMeta.lenses || [])
    .map(
      (lens) =>
        `<option value="${esc(lens)}" ${state.lens === lens ? 'selected' : ''}>${esc(lens)}</option>`,
    )
    .join('');
  $('pestleRelevance').innerHTML = (pestleMeta.relevance_levels || [])
    .map(
      (level) =>
        `<option value="${esc(level.code)}" ${state.relevance === level.code ? 'selected' : ''}>${esc(level.label)}</option>`,
    )
    .join('');
  $('pestleRationale').value = state.rationale;
}

// --- Step 5: rating -------------------------------------------------------------------------

function ratingHtml(kind) {
  const rating = state[kind];
  const score = rating.likelihood * rating.impact;
  const sev = severityFor(score, thresholds);
  const scale = (field, items) =>
    `<div class="mb-3">
      <div class="d-flex justify-content-between"><label class="form-label mb-1" for="${kind}-${field}">${field === 'likelihood' ? 'How likely is it?' : 'How much would it hurt?'}</label><span class="fw-semibold" data-scale-word="${kind}-${field}">${esc(items[rating[field] - 1][1])}</span></div>
      <input id="${kind}-${field}" class="form-range" type="range" min="1" max="5" step="1" value="${rating[field]}" data-rating-kind="${kind}" data-rating-field="${field}" aria-describedby="${kind}-${field}-help">
      <div class="small-muted" id="${kind}-${field}-help" data-scale-help="${kind}-${field}">${esc(items[rating[field] - 1][2])}</div>
    </div>`;
  return `${scale('likelihood', LIKELIHOOD_SCALE)}${scale('impact', IMPACT_SCALE)}
    <div class="d-flex align-items-center gap-2"><span class="small-muted">Score</span><span class="${scorePreviewClass(score)}" data-score="${kind}">${score}</span><span class="badge text-dark" style="background-color:${esc(sev.color)}" data-severity="${kind}">${esc(sev.text)}</span></div>`;
}

function renderRatings() {
  for (const kind of ['inherent', 'residual'])
    document.querySelector(`[data-rating="${kind}"]`).innerHTML = ratingHtml(kind);
  const owner = $('riskOwner');
  owner.innerHTML =
    '<option value="">Nobody yet</option>' +
    (users.length
      ? `<optgroup label="People">${users.map((u) => `<option value="user:${esc(u.id)}">${esc(u.username)}</option>`).join('')}</optgroup>`
      : '') +
    (roles.length
      ? `<optgroup label="Roles">${roles.map((r) => `<option value="role:${esc(r.id)}">${esc(r.name)}</option>`).join('')}</optgroup>`
      : '');
  owner.value = state.owner;
  if (owner.value !== state.owner) owner.value = '';
  $('treatmentStrategy').innerHTML = TREATMENT_STRATEGIES.map(
    ([value, label]) =>
      `<option value="${esc(value)}" ${state.treatment.strategy === value ? 'selected' : ''}>${esc(label)}</option>`,
  ).join('');
  $('treatmentStatus').innerHTML = TREATMENT_STATUSES.map(
    ([value, label]) =>
      `<option value="${esc(value)}" ${state.treatment.status === value ? 'selected' : ''}>${esc(label)}</option>`,
  ).join('');
  $('treatmentDue').value = state.treatment.due;
  $('treatmentPlan').value = state.treatment.plan;
  renderClauses();
}

function updateRating(kind, field, value) {
  state[kind][field] = clampScore(value);
  const rating = state[kind];
  const items = field === 'likelihood' ? LIKELIHOOD_SCALE : IMPACT_SCALE;
  document.querySelector(`[data-scale-word="${kind}-${field}"]`).textContent =
    items[rating[field] - 1][1];
  document.querySelector(`[data-scale-help="${kind}-${field}"]`).textContent =
    items[rating[field] - 1][2];
  const score = rating.likelihood * rating.impact;
  const pill = document.querySelector(`[data-score="${kind}"]`);
  pill.textContent = String(score);
  pill.className = scorePreviewClass(score);
  const sev = severityFor(score, thresholds);
  const badge = document.querySelector(`[data-severity="${kind}"]`);
  badge.textContent = sev.text;
  badge.style.backgroundColor = sev.color;
}

function renderClauses() {
  const q = String($('clauseSearch').value || '')
    .trim()
    .toLowerCase();
  const levels = (pestleMeta.relevance_levels || []).filter((l) => l.code !== 'na');
  const defaultCode =
    state.relevance && state.relevance !== 'na' ? state.relevance : levels[0]?.code || 'high';
  $('clauseList').innerHTML =
    clauses
      .filter((c) => !q || `${c.ref} ${c.title}`.toLowerCase().includes(q))
      .slice(0, 300)
      .map((c) => {
        const chosen = state.clauseRelevance.get(c.id);
        return `<div class="d-flex gap-2 align-items-center py-1">
        <input class="form-check-input mt-0" type="checkbox" data-clause="${esc(c.id)}" ${chosen ? 'checked' : ''} aria-label="${esc(c.ref)}">
        <span class="flex-grow-1"><span class="fw-semibold">${esc(c.ref)}</span> <span class="small-muted">${esc(c.title || '')}</span></span>
        <select class="form-select form-select-sm w-auto" data-clause-level="${esc(c.id)}" ${chosen ? '' : 'disabled'} aria-label="Relevance of ${esc(c.ref)}">${levels.map((l) => `<option value="${esc(l.code)}" ${(chosen || defaultCode) === l.code ? 'selected' : ''}>${esc(l.label)}</option>`).join('')}</select>
      </div>`;
      })
      .join('') || '<div class="small-muted">No clauses match.</div>';
}

// --- Step 6: controls -----------------------------------------------------------------------

function renderControls() {
  $('controlsFramework').textContent = framework || 'this framework';
  const q = String($('controlSearch').value || '')
    .trim()
    .toLowerCase();
  const suggested = new Set(state.suggestedControlIds);
  const rows = allControls
    .filter((c) => !q || `${c.ref} ${c.title}`.toLowerCase().includes(q))
    .sort((a, b) => Number(suggested.has(b.id)) - Number(suggested.has(a.id)))
    .slice(0, 400)
    .map(
      (c) => `<label class="d-flex gap-2 align-items-start py-1">
      <input class="form-check-input mt-1" type="checkbox" data-control="${esc(c.id)}" ${state.controls.has(c.id) ? 'checked' : ''}>
      <span><span class="fw-semibold">${esc(c.ref || 'Control')}</span> ${esc(c.title || '')}${suggested.has(c.id) ? ' <span class="badge text-bg-info">suggested</span>' : ''}${c.in_scope === false ? ' <span class="badge text-bg-secondary">out of scope</span>' : ''}</span>
    </label>`,
    );
  $('controlList').innerHTML = rows.join('') || '<div class="small-muted">No controls match.</div>';
  $('controlCount').textContent = String(state.controls.size);
  const note = $('suggestedControlsNote');
  const templateRefs = (state.template?.suggested_assessment?.keen_af_control_refs || []).filter(
    Boolean,
  );
  const bits = [];
  if (state.suggestedControlIds.length)
    bits.push(
      `The Mitigator suggested ${state.suggestedControlIds.length} control${state.suggestedControlIds.length === 1 ? '' : 's'} as mitigations for this scenario; they are pre-ticked.`,
    );
  if (templateRefs.length && !isPestle())
    bits.push(
      keenAfEnabled
        ? `The template links Keen Assurance Framework controls ${templateRefs.join(', ')}; Keen saves those links under KEEN-AF:1.0 when you save.`
        : `The template's control links (${templateRefs.join(', ')}) belong to the Keen Assurance Framework, which is not enabled here.`,
    );
  note.hidden = !bits.length;
  note.textContent = bits.join(' ');
}

// --- Step 7: review -------------------------------------------------------------------------

function reviewRow(label, value, step) {
  return `<dt class="col-sm-4">${esc(label)}</dt><dd class="col-sm-8 d-flex justify-content-between gap-2"><span>${value}</span>${step ? `<button class="btn btn-link btn-sm p-0 align-baseline flex-shrink-0" type="button" data-goto-step="${esc(step)}">Change</button>` : ''}</dd>`;
}

function renderReview() {
  const rows = [];
  rows.push(
    reviewRow('Kind of risk', esc(RISK_METHODS[state.method].label), editingId ? '' : 'method'),
  );
  if (state.template) rows.push(reviewRow('Template', esc(state.template.name), 'method'));
  if (isPestle()) {
    const names = [
      ...state.processes.map((p) => p.name),
      ...state.newProcesses.map((n) => `${n} (new)`),
    ];
    rows.push(
      reviewRow(
        'Business processes',
        names.length ? esc(names.join(', ')) : '<span class="small-muted">None</span>',
        'subject',
      ),
    );
    rows.push(reviewRow('Factor', esc(state.description || '-'), 'describe'));
    rows.push(
      reviewRow('Type and lens', esc(`${state.pestleType || '-'} · ${state.lens}`), 'describe'),
    );
    const level = (pestleMeta.relevance_levels || []).find((l) => l.code === state.relevance);
    rows.push(
      reviewRow('Overall relevance', esc(level?.label || state.relevance || '-'), 'describe'),
    );
    if (state.rationale) rows.push(reviewRow('Rationale', esc(state.rationale), 'describe'));
    const picked = [...state.clauseRelevance.keys()].map(
      (id) => clauses.find((c) => c.id === id)?.ref || id,
    );
    rows.push(
      reviewRow(
        'Relevant clauses',
        picked.length ? esc(picked.join(', ')) : '<span class="small-muted">None yet</span>',
        'rate',
      ),
    );
  } else {
    rows.push(
      reviewRow(
        'Asset',
        state.asset
          ? esc(state.asset.name)
          : state.newAsset?.name
            ? `${esc(state.newAsset.name)} <span class="badge text-bg-light border">new</span>${state.newAsset.category ? ` <span class="small-muted">${esc(state.newAsset.category)}${state.newAsset.subcategory ? ` › ${esc(state.newAsset.subcategory)}` : ''}</span>` : ''}`
            : '<span class="text-danger">Not chosen</span>',
        'subject',
      ),
    );
    rows.push(
      reviewRow(
        'Affected parties',
        state.parties.length
          ? state.parties
              .map(
                (p) =>
                  `${esc(p.label)}${p.kind === 'new' ? ' <span class="badge text-bg-light border">new</span>' : ''}`,
              )
              .join(', ')
          : '<span class="small-muted">None</span>',
        'parties',
      ),
    );
    rows.push(reviewRow('Name', esc(state.title || '-'), 'describe'));
    rows.push(reviewRow('What could happen', esc(state.description || '-'), 'describe'));
    if (state.method === 'cia')
      rows.push(
        reviewRow(
          'Security properties',
          state.riskTypes.length
            ? esc(state.riskTypes.join(', '))
            : '<span class="text-danger">None chosen</span>',
          'describe',
        ),
      );
    const i = state.inherent.likelihood * state.inherent.impact;
    const r = state.residual.likelihood * state.residual.impact;
    rows.push(
      reviewRow(
        'Rating',
        `Today ${i} (${esc(severityFor(i, thresholds).text)}), after controls ${r} (${esc(severityFor(r, thresholds).text)})`,
        'rate',
      ),
    );
    const ownerLabel = ownerLabelFor(state.owner);
    rows.push(reviewRow('Owner', esc(ownerLabel || 'Nobody yet'), 'rate'));
    const strategy =
      TREATMENT_STRATEGIES.find(([v]) => v === state.treatment.strategy)?.[1] || 'Not decided yet';
    rows.push(
      reviewRow(
        'Treatment',
        esc(strategy.split(' - ')[0]) +
          ` <span class="small-muted">(${esc(TREATMENT_STATUSES.find(([v]) => v === state.treatment.status)?.[1] || 'Open')}${state.treatment.due ? `, due ${esc(state.treatment.due)}` : ''})</span>`,
        'rate',
      ),
    );
    const picked = [...state.controls].map((id) => allControls.find((c) => c.id === id)?.ref || id);
    rows.push(
      reviewRow(
        'Controls',
        picked.length ? esc(picked.join(', ')) : '<span class="small-muted">None yet</span>',
        'controls',
      ),
    );
  }
  if (state.note) rows.push(reviewRow('Notes', esc(state.note), 'describe'));
  $('reviewSummary').innerHTML = rows.join('');
}

function ownerLabelFor(value) {
  const [kind, id] = String(value || '').split(':');
  if (kind === 'user') return users.find((u) => u.id === id)?.username || '';
  if (kind === 'role') return roles.find((r) => r.id === id)?.name || '';
  return '';
}

// --- Validation per step ---------------------------------------------------------------------

function collectStep(step) {
  if (step === 'subject' && isPestle()) return '';
  if (step === 'subject') {
    if (state.asset) return '';
    const name = String($('assetSearch').value || '').trim();
    if (!name) return 'Pick an asset, or type a name to add a new one.';
    const category = $('newAssetCategory').value.trim();
    const subcategory = $('newAssetSubcategory').value.trim();
    if (!category)
      return 'Say what kind of asset it is, so it lands in the right place in the asset register.';
    if (!subcategory) return 'Add a subcategory too; an existing one from the list is fine.';
    state.newAsset = { name, category, subcategory };
    return '';
  }
  if (step === 'describe') {
    state.title = $('riskTitle').value.trim();
    state.description = $('riskDescription').value.trim();
    state.note = $('riskNote').value.trim();
    if (isPestle()) {
      state.pestleType = document.querySelector('input[name="pestleType"]:checked')?.value || '';
      state.lens = $('pestleLens').value;
      state.relevance = $('pestleRelevance').value;
      state.rationale = $('pestleRationale').value.trim();
      if (!state.description) return 'Describe the factor in a sentence or two.';
      if (!state.pestleType) return 'Choose which kind of influence it is.';
      return '';
    }
    state.riskTypes = [...document.querySelectorAll('[data-cia]:checked')].map(
      (el) => el.dataset.cia,
    );
    if (!state.title) return 'Give the risk a short name.';
    if (state.method === 'cia' && !state.riskTypes.length)
      return 'Tick at least one of confidentiality, integrity or availability, or go back and choose “Simple risk”.';
    return '';
  }
  if (step === 'rate' && !isPestle()) {
    state.owner = $('riskOwner').value;
    state.treatment = {
      strategy: $('treatmentStrategy').value,
      status: $('treatmentStatus').value,
      due: $('treatmentDue').value,
      plan: $('treatmentPlan').value.trim(),
    };
    return '';
  }
  return '';
}

// --- Saving -----------------------------------------------------------------------------------

function riskPayload() {
  const payload = {
    framework,
    title: state.title,
    threat_summary: state.description,
    note: state.note,
    risk_types: state.method === 'cia' ? state.riskTypes : [],
    register_likelihood: state.inherent.likelihood,
    register_impact: state.inherent.impact,
    register_residual_likelihood: state.residual.likelihood,
    register_residual_impact: state.residual.impact,
    treatment_strategy: state.treatment.strategy,
    treatment_status: state.treatment.status,
    treatment_plan: state.treatment.plan,
    treatment_due_at: state.treatment.due || null,
    controls: [...state.controls],
    interested_parties: state.parties.map((p) =>
      p.kind === 'new' ? { name: p.label, nature: p.detail } : { [`${p.kind}_id`]: p.id },
    ),
  };
  if (state.asset) payload.asset_id = state.asset.id;
  else if (state.newAsset) {
    payload.asset_name = state.newAsset.name;
    const category = categories.find(
      (c) => c.name.toLowerCase() === state.newAsset.category.toLowerCase(),
    );
    if (category) payload.category_id = category.id;
    else payload.category_name = state.newAsset.category;
    const sub = (category?.subcategories || []).find(
      (s) => s.name.toLowerCase() === state.newAsset.subcategory.toLowerCase(),
    );
    if (sub) payload.subcategory_id = sub.id;
    else payload.subcategory_name = state.newAsset.subcategory;
  }
  const [kind, id] = String(state.owner || '').split(':');
  payload.risk_owner_user_id = kind === 'user' ? id : null;
  payload.risk_owner_role_id = kind === 'role' ? id : null;
  if (state.template && !editingId) payload.library_template_id = state.template.id;
  if (state.mitigatorContext) payload.mitigator_context = state.mitigatorContext;
  return payload;
}

async function saveRisk() {
  const payload = riskPayload();
  const saved = editingId
    ? await apiPatch(`/api/v1/risks/${encodeURIComponent(editingId)}`, payload)
    : await apiPost('/api/v1/risks', payload);
  const created = [];
  if (!editingId && state.newAsset) created.push(`the asset “${state.newAsset.name}”`);
  const newParties = state.parties.filter((p) => p.kind === 'new').length;
  if (newParties)
    created.push(`${newParties} new interested part${newParties === 1 ? 'y' : 'ies'}`);
  return { saved, created };
}

async function savePestle() {
  const created = [];
  const processIds = state.processes.map((p) => p.id);
  for (const name of state.newProcesses) {
    const process = await apiPost('/api/v1/pestle/business-processes', { name });
    processIds.push(process.id);
    created.push(`the business process “${name}”`);
  }
  const item = await apiPost('/api/v1/pestle/items', {
    framework,
    type: state.pestleType,
    lens: state.lens,
    item: state.description,
    overall_relevance_code: state.relevance || 'na',
    rationale: [state.rationale, state.note].filter(Boolean).join('\n\n'),
  });
  const code = state.relevance && state.relevance !== 'na' ? state.relevance : 'high';
  if (processIds.length)
    await apiPatch(`/api/v1/pestle/items/${encodeURIComponent(item.id)}/business-processes`, {
      items: processIds.map((id) => ({ business_process_id: id, relevance_code: code })),
    });
  if (state.clauseRelevance.size)
    await apiPatch(`/api/v1/pestle/items/${encodeURIComponent(item.id)}/clauses`, {
      items: [...state.clauseRelevance].map(([id, level]) => ({
        clause_id: id,
        relevance_code: level,
      })),
    });
  return { saved: item, created };
}

function showDone({ saved, created }) {
  $('doneTitle').textContent = editingId
    ? 'Risk updated'
    : isPestle()
      ? 'PESTLE(E) factor recorded'
      : 'Risk recorded';
  $('doneDetail').textContent = created.length
    ? `Keen also added ${created.join(' and ')} for you.`
    : 'Everything is in the register.';
  const openHref = isPestle() ? pestleHref(saved.id, framework) : riskHref(saved.id, framework);
  $('doneActions').innerHTML = `
    <a class="btn btn-primary" href="${esc(openHref)}">Open it</a>
    <a class="btn btn-outline-primary" href="${esc(withFramework('/risk-wizard.html', framework))}">Add another</a>
    <a class="btn btn-outline-secondary" href="${esc(riskDashboardHref(framework, isPestle() ? 'pestle' : ''))}">Back to Risks</a>`;
  for (const section of document.querySelectorAll('.risk-wizard-step'))
    section.hidden = section.dataset.step !== 'done';
  $('wizardFooter').hidden = true;
  $('wizardSteps').hidden = true;
  $('wizardTitle').textContent = $('doneTitle').textContent;
  $('wizardIntro').textContent = '';
}

async function finish() {
  const next = $('nextStep');
  next.disabled = true;
  try {
    const result = isPestle() ? await savePestle() : await saveRisk();
    showDone(result);
  } catch (e) {
    notify(`Could not save: ${String(e)}`);
  } finally {
    next.disabled = false;
  }
}

// --- Loading ----------------------------------------------------------------------------------

async function loadOptions() {
  const [
    categoryData,
    assetData,
    userData,
    controlData,
    libraryData,
    partyData,
    catalog,
    registerData,
  ] = await Promise.all([
    apiGet('/api/v1/risks/categories'),
    apiGet('/api/v1/risks/assets'),
    apiGet('/api/v1/risks/users'),
    featureEnabled(me, 'controls')
      ? apiGet(`/api/v1/controls?framework=${encodeURIComponent(framework)}&limit=5000`)
      : { items: [] },
    apiGet('/api/v1/risks/library'),
    apiGet(`/api/v1/risks/parties?framework=${encodeURIComponent(framework)}`),
    getFrameworkCatalog().catch(() => ({ items: [] })),
    // The heatmap carries the severity thresholds the rating step bands with.
    apiGet('/api/v1/risks/register/heatmap').catch(() => null),
  ]);
  categories = categoryData?.items || [];
  assets = assetData?.items || [];
  users = userData?.items || [];
  roles = userData?.roles || [];
  allControls = controlData?.items || [];
  library = libraryData?.items || [];
  partyOptions = { ...partyOptions, ...(partyData || {}) };
  keenAfEnabled = (catalog?.items || []).some((item) => item.slug === 'KEEN-AF:1.0');
  if (registerData?.thresholds) thresholds = registerData.thresholds;
  $('partyNatureOptions').innerHTML = (partyOptions.natures || [])
    .map((n) => `<option value="${esc(n)}"></option>`)
    .join('');
  if (canManagePestle && featureEnabled(me, 'pestle')) {
    const [meta, processes, clauseData] = await Promise.all([
      apiGet('/api/v1/pestle/meta').catch(() => pestleMeta),
      apiGet('/api/v1/pestle/business-processes').catch(() => ({ items: [] })),
      apiGet(`/api/v1/clauses?framework=${encodeURIComponent(framework)}&limit=5000`).catch(() => ({
        items: [],
      })),
    ]);
    pestleMeta = meta || pestleMeta;
    businessProcesses = processes?.items || [];
    clauses = clauseData?.items || [];
    if (!state.relevance)
      state.relevance =
        (pestleMeta.relevance_levels || []).find((l) => l.code !== 'na')?.code || 'na';
  }
}

async function loadExisting() {
  const risk = await apiGet(
    `/api/v1/risks/${encodeURIComponent(editingId)}?framework=${encodeURIComponent(framework)}`,
  );
  state.existing = risk;
  state.method = (risk.risk_types || []).length ? 'cia' : 'general';
  state.asset = risk.asset_id
    ? { id: risk.asset_id, name: risk.asset || risk.asset_entity?.name || '' }
    : null;
  state.parties = (risk.interested_parties || []).map((p) => ({
    kind: p.kind,
    id: p.id,
    label: p.label,
    detail: p.detail,
  }));
  state.title = risk.title || '';
  state.description = risk.threat_summary || '';
  state.note = risk.note || '';
  state.riskTypes = [...(risk.risk_types || [])];
  state.inherent = {
    likelihood: clampScore(risk.register_likelihood),
    impact: clampScore(risk.register_impact),
  };
  state.residual = {
    likelihood: clampScore(risk.register_residual_likelihood),
    impact: clampScore(risk.register_residual_impact),
  };
  const owner = risk.risk_owner;
  state.owner = owner?.id ? `${owner.type === 'role' ? 'role' : 'user'}:${owner.id}` : '';
  state.treatment = {
    strategy: risk.treatment_strategy || '',
    status: risk.treatment_status || 'open',
    due: risk.treatment_due_at || '',
    plan: risk.treatment_plan || '',
  };
  state.controls = new Set((risk.controls || []).map((c) => c.id));
  $('wizardTitle').textContent = 'Edit risk';
  $('wizardIntro').textContent =
    `Change anything about “${risk.display || risk.title || 'this risk'}”. The register updates when you save.`;
}

function applyDraft(draft) {
  if (!draft) return;
  if (draft.method && RISK_METHODS[draft.method]) state.method = draft.method;
  if (Array.isArray(draft.risk_types) && draft.risk_types.length) {
    state.riskTypes = draft.risk_types.filter((t) => RISK_TYPES.includes(t));
    if (state.method === 'general' && state.riskTypes.length) state.method = 'cia';
  }
  if (draft.title) state.title = String(draft.title).slice(0, 256);
  if (draft.threat_summary) state.description = String(draft.threat_summary);
  if (draft.note) state.note = String(draft.note);
  if (draft.asset_name) {
    const match = assets.find(
      (a) => String(a.name || '').toLowerCase() === String(draft.asset_name).toLowerCase(),
    );
    if (match) state.asset = { id: match.id, name: match.name };
    else {
      $('assetSearch').value = String(draft.asset_name);
      $('newAssetCategory').value = String(draft.category_name || '');
      $('newAssetSubcategory').value = String(draft.subcategory_name || '');
    }
  }
  if (Array.isArray(draft.controls)) {
    const known = new Set(allControls.map((c) => c.id));
    state.suggestedControlIds = draft.controls.filter((id) => known.has(id));
    for (const id of state.suggestedControlIds) state.controls.add(id);
  }
  for (const party of draft.interested_parties || []) {
    if (party?.id && party?.kind)
      state.parties.push({
        kind: party.kind,
        id: party.id,
        label: party.label || '',
        detail: party.detail || '',
      });
    else if (party?.name)
      state.parties.push({
        kind: 'new',
        label: String(party.name),
        detail: String(party.nature || ''),
      });
  }
  if (draft.mitigator_context && typeof draft.mitigator_context === 'object')
    state.mitigatorContext = draft.mitigator_context;
  if (draft.pestle_type) state.pestleType = String(draft.pestle_type);
  if (draft.lens) state.lens = String(draft.lens);
  notify(
    "The Mitigator's analysis has been carried into the wizard. Review each step before saving.",
    'info',
  );
}

// --- Events -----------------------------------------------------------------------------------

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const step = currentStep();
  const problem = collectStep(step);
  if (problem) {
    notify(problem, 'warning');
    return;
  }
  if (step === 'review') {
    await finish();
    return;
  }
  stepIndex = Math.min(stepIndex + 1, visibleSteps().length - 1);
  const next = currentStep();
  if (next === 'subject') {
    renderAssets();
    renderProcesses();
  }
  if (next === 'parties') renderParties();
  if (next === 'describe') renderDescribe();
  if (next === 'rate') renderRatings();
  if (next === 'controls') renderControls();
  showStep(next);
});

$('backStep').addEventListener('click', () => {
  stepIndex = Math.max(0, stepIndex - 1);
  showStep(currentStep());
});

$('skipStep').addEventListener('click', () => {
  const step = currentStep();
  if (step === 'parties') state.parties = [];
  if (step === 'controls') state.controls = new Set();
  stepIndex = Math.min(stepIndex + 1, visibleSteps().length - 1);
  const next = currentStep();
  if (next === 'describe') renderDescribe();
  if (next === 'rate') renderRatings();
  if (next === 'controls') renderControls();
  showStep(next);
});

document.addEventListener('click', (ev) => {
  const goto = ev.target.closest('[data-goto-step]');
  if (goto) {
    const step = goto.dataset.gotoStep;
    const current = currentStep();
    if (current === 'describe' || current === 'rate') collectStep(current);
    if (step === 'subject') {
      renderAssets();
      renderProcesses();
    }
    if (step === 'parties') renderParties();
    if (step === 'describe') renderDescribe();
    if (step === 'rate') renderRatings();
    if (step === 'controls') renderControls();
    goTo(step);
  }
});

$('methodChoices').addEventListener('change', (ev) => {
  const input = ev.target.closest('input[name="riskMethod"]');
  if (!input) return;
  state.method = input.value;
  if (state.template && (state.template.method === 'pestle') !== isPestle()) state.template = null;
  renderMethodChoices();
  // The number of steps depends on the method.
  showStep(currentStep());
});

$('templateSearch').addEventListener('input', debounce(renderTemplates, 150));
$('templateList').addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-template]');
  if (!btn) return;
  const item = library.find((x) => x.id === btn.dataset.template);
  applyTemplate(state.template?.id === item?.id ? null : item);
});

$('assetSearch').addEventListener('input', debounce(renderAssets, 120));
$('assetList').addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-asset]');
  if (!btn) return;
  const row = assets.find((a) => a.id === btn.dataset.asset);
  if (!row) return;
  state.asset = state.asset?.id === row.id ? null : { id: row.id, name: row.name };
  state.newAsset = null;
  if (state.asset) $('assetSearch').value = row.name;
  renderAssets();
});
$('selectedAssetSummary').addEventListener('click', (ev) => {
  if (!ev.target.closest('#clearAsset')) return;
  state.asset = null;
  $('assetSearch').value = '';
  renderAssets();
});
$('newAssetCategory').addEventListener('input', () => {
  renderSubcategoryOptions();
  renderAssets();
});
$('newAssetSubcategory').addEventListener('input', renderAssets);

$('processSearch').addEventListener('input', debounce(renderProcesses, 120));
$('processList').addEventListener('click', (ev) => {
  const add = ev.target.closest('[data-add-process]');
  if (add) {
    state.newProcesses.push(add.dataset.addProcess);
    $('processSearch').value = '';
    renderProcesses();
  }
});
$('processList').addEventListener('change', (ev) => {
  const existing = ev.target.closest('[data-process]');
  if (existing) {
    const row = businessProcesses.find((p) => p.id === existing.dataset.process);
    state.processes = state.processes.filter((p) => p.id !== existing.dataset.process);
    if (existing.checked && row) state.processes.push({ id: row.id, name: row.name });
  }
  const added = ev.target.closest('[data-new-process]');
  if (added && !added.checked) {
    state.newProcesses = state.newProcesses.filter((n) => n !== added.dataset.newProcess);
    renderProcesses();
  }
});

$('partySearch').addEventListener('input', debounce(renderParties, 120));
$('partyList').addEventListener('change', (ev) => {
  const box = ev.target.closest('[data-party]');
  if (!box) return;
  const option = allPartyOptions().find((p) => partyKey(p) === box.dataset.party);
  if (!option) return;
  state.parties = state.parties.filter((p) => partyKey(p) !== box.dataset.party);
  if (box.checked)
    state.parties.push({
      kind: option.kind,
      id: option.id,
      label: option.label,
      detail: option.detail || '',
    });
  renderSelectedParties();
});
$('addNewParty').addEventListener('click', () => {
  const name = $('partySearch').value.trim();
  const nature = $('newPartyNature').value.trim();
  if (!name) return;
  if (!nature) {
    notify('Say what their relationship to you is, for example Customer or Regulator.', 'warning');
    $('newPartyNature').focus();
    return;
  }
  state.parties.push({ kind: 'new', label: name, detail: nature });
  $('partySearch').value = '';
  $('newPartyNature').value = '';
  renderParties();
});
$('selectedParties').addEventListener('click', (ev) => {
  const remove = ev.target.closest('[data-remove-party]');
  if (!remove) return;
  state.parties = state.parties.filter((p) => partyKey(p) !== remove.dataset.removeParty);
  renderParties();
});

$('pestleTypeChoices').addEventListener('change', (ev) => {
  const input = ev.target.closest('input[name="pestleType"]');
  if (!input) return;
  state.pestleType = input.value;
  for (const label of $('pestleTypeChoices').querySelectorAll('label')) {
    const checked = label.querySelector('input').checked;
    label.classList.toggle('btn-info', checked);
    label.classList.toggle('btn-outline-secondary', !checked);
  }
});
$('pestleRelevance').addEventListener('change', () => {
  state.relevance = $('pestleRelevance').value;
});

document.addEventListener('input', (ev) => {
  const range = ev.target.closest('[data-rating-kind]');
  if (range) updateRating(range.dataset.ratingKind, range.dataset.ratingField, range.value);
});

$('clauseSearch').addEventListener('input', debounce(renderClauses, 120));
$('clauseList').addEventListener('change', (ev) => {
  const box = ev.target.closest('[data-clause]');
  if (box) {
    const select = $('clauseList').querySelector(`[data-clause-level="${box.dataset.clause}"]`);
    if (box.checked) {
      state.clauseRelevance.set(box.dataset.clause, select.value);
      select.disabled = false;
    } else {
      state.clauseRelevance.delete(box.dataset.clause);
      select.disabled = true;
    }
  }
  const level = ev.target.closest('[data-clause-level]');
  if (level && state.clauseRelevance.has(level.dataset.clauseLevel))
    state.clauseRelevance.set(level.dataset.clauseLevel, level.value);
});

$('controlSearch').addEventListener('input', debounce(renderControls, 120));
$('controlList').addEventListener('change', (ev) => {
  const box = ev.target.closest('[data-control]');
  if (!box) return;
  if (box.checked) state.controls.add(box.dataset.control);
  else state.controls.delete(box.dataset.control);
  $('controlCount').textContent = String(state.controls.size);
});

// --- Start ------------------------------------------------------------------------------------

if (!canManageRisks) {
  notify('You need risk.manage permission to add or edit risks.');
  form.hidden = true;
} else {
  try {
    await loadOptions();
    $('wizardBreadcrumb').href = riskDashboardHref(framework);
    $('wizardMitigatorLink').href = withFramework('/mitigator.html', framework);
    if (!featureEnabled(me, 'mitigator')) $('wizardMitigatorLink').hidden = true;
    if (editingId) {
      await loadExisting();
    } else {
      if (requestedMethod && RISK_METHODS[requestedMethod]) state.method = requestedMethod;
      applyDraft(takeWizardDraft());
      if (requestedTemplate) {
        const template = library.find((item) => item.id === requestedTemplate);
        if (template) applyTemplate(template);
        else notify('That template no longer exists; starting from a blank risk.', 'warning');
      }
    }
    renderMethodChoices();
    renderCategoryOptions();
    stepIndex = 0;
    const first = currentStep();
    if (first === 'subject') {
      renderAssets();
      renderProcesses();
    }
    showStep(first);
  } catch (e) {
    notify(`Could not start the risk wizard: ${String(e)}`);
  }
}
