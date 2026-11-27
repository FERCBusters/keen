// The risk wizard: one guided path that creates a risk and whatever it needs
// along the way (an asset, a person or vendor it affects), or edits
// an existing register risk. It asks for the method first and shows only the
// fields that method needs.
import { featureEnabled } from '/pages/feature-availability.js';
import { catalogueLabel, controlsOnly } from '/pages/catalogue-options.js';
import {
  AFFECTED_KINDS,
  affectedChoices,
  affectedPayload,
  loadAffectedOptions,
  mountAffectedPicker,
} from '/pages/affected-common.js';
import {
  errorText,
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  esc,
  toast,
  debounce,
  node,
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
  // [{kind, id, label, detail}] people, vendors and assets, or
  // {kind: 'new_person' | 'new_vendor', label} to add by name
  parties: [],
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
  // The controls and clauses it is linked to, by id. A PESTLE(E) item rates
  // each with a relevance code; a register risk links them plainly ('').
  controls: new Map(),
  clauses: new Map(),
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
// The people, vendors and assets the account may name; null without access.
let affectedOptions = null;
let affectedPicker = null;
let pestleMeta = { types: [], lenses: [], relevance_levels: [] };
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
  ['clauses', 'Clauses'],
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
  if (step === 'parties') return affectedOptions !== null;
  // An item belongs to no asset and has no likelihood or impact.
  if (step === 'subject' || step === 'rate') return !isPestle();
  // Most frameworks have controls and no clauses.
  if (step === 'clauses') return clauses.length > 0;
  return true;
}

function visibleSteps() {
  return STEPS.filter(([step]) => stepApplies(step) && !(editingId && step === 'method'));
}

function currentStep() {
  return visibleSteps()[stepIndex][0];
}

function renderStepper() {
  const steps = visibleSteps();
  $('wizardSteps').innerHTML = steps
    .map(([step, label], index) => {
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
  const steps = visibleSteps();
  const last = stepIndex >= steps.length - 1;
  $('backStep').hidden = stepIndex === 0 || step === 'done';
  $('nextStep').innerHTML = last
    ? `<i class="bi bi-check2 me-1" aria-hidden="true"></i>${editingId ? 'Save changes' : isPestle() ? 'Create PESTLE(E) item' : 'Create risk'}`
    : 'Next<i class="bi bi-arrow-right ms-1" aria-hidden="true"></i>';
  $('skipStep').hidden = !['parties', 'controls', 'clauses'].includes(step);
  $('wizardFooter').hidden = step === 'done';
  $('stepHint').textContent = stepHintFor(step);
  renderStepper();
  if (step === 'review') renderReview();
  const heading = document.querySelector(`.risk-wizard-step[data-step="${step}"] h2`);
  heading?.setAttribute('tabindex', '-1');
  heading?.focus?.();
}

function stepHintFor(step) {
  switch (step) {
    case 'method':
      return 'Step 1 of ' + visibleSteps().length;
    case 'subject':
      return 'Pick or add one asset.';
    case 'parties':
      return 'You can skip this and name them later.';
    case 'describe':
      return 'A short name and a few sentences are enough.';
    case 'rate':
      return 'Use the words beside each scale as a guide.';
    case 'controls':
    case 'clauses':
      return isPestle()
        ? `Relevance to ${step} is optional, and can be set later on the item's page.`
        : `You can link ${step} later, by editing the risk.`;
    default:
      return '';
  }
}

// Draws a step's choices from the state before it is shown, however the
// wizard arrives at it: Next, Skip or a step's Change link.
function prepareStep(step) {
  if (step === 'subject') renderAssets();
  if (step === 'parties') renderParties();
  if (step === 'describe') renderDescribe();
  if (step === 'rate') renderRatings();
  if (step === 'controls' || step === 'clauses') renderLinks(step);
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
      item.method === wanted &&
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
          <span class="small">${esc(item.method === 'pestle' ? `${item.pestle_type} · ${item.lens}` : item.risk_types.join(', ') || 'General risk')}</span>
        </div>
        <div class="small ${state.template?.id === item.id ? '' : 'small-muted'}">${esc(item.threat_summary)}</div>
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
    state.pestleType = item.pestle_type;
    state.lens = item.lens;
    if (!state.description) state.description = item.threat_summary;
    if (!state.rationale) state.rationale = item.treatment_guidance;
  } else {
    state.method = item.risk_types.length ? 'cia' : 'general';
    state.riskTypes = [...item.risk_types];
    if (!state.title) state.title = item.name;
    if (!state.description) state.description = item.threat_summary;
    if (!state.note) state.note = item.treatment_guidance;
    const example = item.suggested_assessment;
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
      const match = assets.find((a) => a.name.toLowerCase() === suggestedAsset.toLowerCase());
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
        !q || `${a.name} ${a.category.name} ${a.subcategory?.name || ''}`.toLowerCase().includes(q),
    )
    .slice(0, 30);
}

function renderAssets() {
  const query = String($('assetSearch').value || '').trim();
  const matches = assetMatches(query);
  const exact = matches.find((a) => a.name.toLowerCase() === query.toLowerCase());
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
  const category = categories.find((c) => c.name.toLowerCase() === chosen);
  const subs = category ? category.subcategories : categories.flatMap((c) => c.subcategories);
  $('assetSubcategoryOptions').innerHTML = subs
    .map((s) => `<option value="${esc(s.name)}"></option>`)
    .join('');
}

// --- Step 3: who and what is affected ------------------------------------------------------

function renderParties() {
  affectedPicker.set(state.parties);
}

function affectedSummary() {
  return state.parties.length
    ? state.parties
        .map((choice) =>
          AFFECTED_KINDS[choice.kind]
            ? esc(choice.label)
            : `${esc(choice.label)} <span class="badge text-bg-light border">new</span>`,
        )
        .join(', ')
    : '<span class="small-muted">None</span>';
}

// "1 new person", "2 new vendors / 3rd parties": what saving adds by name.
function addedByName() {
  const people = state.parties.filter((choice) => choice.kind === 'new_person').length;
  const vendors = state.parties.filter((choice) => choice.kind === 'new_vendor').length;
  return [
    people && `${people} new ${people === 1 ? 'person' : 'people'}`,
    vendors && `${vendors} new ${vendors === 1 ? 'vendor / 3rd party' : 'vendors / 3rd parties'}`,
  ].filter(Boolean);
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
  $('pestleTypeChoices').innerHTML = pestleMeta.types
    .map(
      (type) =>
        `<label class="btn btn-sm ${state.pestleType === type ? 'btn-info' : 'btn-outline-secondary'}"><input class="visually-hidden" type="radio" name="pestleType" value="${esc(type)}" ${state.pestleType === type ? 'checked' : ''}>${esc(type)}</label>`,
    )
    .join('');
  $('pestleLens').innerHTML = pestleMeta.lenses
    .map(
      (lens) =>
        `<option value="${esc(lens)}" ${state.lens === lens ? 'selected' : ''}>${esc(lens)}</option>`,
    )
    .join('');
  $('pestleRelevance').innerHTML = pestleMeta.relevance_levels
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

// --- Steps 6 and 7: controls and clauses --------------------------------------------------

// Each step chooses one kind of record in a multiple select, which the design
// system presents as its picker. A PESTLE(E) item also says how relevant each
// chosen record is, as the value the picker keeps beside it. What is chosen
// lives in state, as record id -> relevance ('' for a risk).
const LINKS = {
  controls: { list: 'controlList', all: () => allControls },
  clauses: { list: 'clauseList', all: () => clauses },
};

function renderLinks(kind) {
  const select = $(LINKS[kind].list);
  const chosen = state[kind];
  $(`${kind}Framework`).textContent = framework || 'this framework';
  for (const el of document.querySelectorAll(`[data-links-intro="${kind}"]`))
    el.hidden = el.dataset.linksFor !== (isPestle() ? 'pestle' : 'risk');
  const levels = pestleMeta.relevance_levels.filter((l) => l.code !== 'na');
  if (isPestle())
    select.dataset.pickerExtra = JSON.stringify({
      label: 'Relevance',
      choices: levels.map((l) => [l.code, l.label]),
      // A record chosen before its relevance was set takes the overall relevance.
      default: state.relevance,
    });
  else delete select.dataset.pickerExtra;
  const suggested = new Set(kind === 'controls' ? state.suggestedControlIds : []);
  select.replaceChildren(
    ...LINKS[kind]
      .all()
      .toSorted((a, b) => Number(suggested.has(b.id)) - Number(suggested.has(a.id)))
      .map((row) => {
        const option = node('option', { value: row.id }, catalogueLabel(row));
        const badges = [
          suggested.has(row.id) ? 'suggested' : '',
          row.in_scope === false ? 'out of scope' : '',
        ].filter(Boolean);
        if (badges.length) option.dataset.badge = badges.join(' · ');
        option.selected = chosen.has(row.id);
        if (option.selected && chosen.get(row.id)) option.dataset.extra = chosen.get(row.id);
        return option;
      }),
  );
  if (kind === 'controls') renderSuggestedControlsNote();
}

function renderSuggestedControlsNote() {
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

// The references of the ticked records, for the review step.
function pickedRefs(kind) {
  const picked = [...state[kind].keys()].map(
    (id) => LINKS[kind].all().find((row) => row.id === id)?.ref || id,
  );
  return picked.length ? esc(picked.join(', ')) : '<span class="small-muted">None yet</span>';
}

// --- Step 8: review -------------------------------------------------------------------------

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
    if (affectedOptions) rows.push(reviewRow('Affects', affectedSummary(), 'parties'));
    rows.push(reviewRow('Item', esc(state.description), 'describe'));
    rows.push(reviewRow('Type and lens', esc(`${state.pestleType} · ${state.lens}`), 'describe'));
    const level = pestleMeta.relevance_levels.find((l) => l.code === state.relevance);
    rows.push(reviewRow('Overall relevance', esc(level.label), 'describe'));
    if (state.rationale) rows.push(reviewRow('Rationale', esc(state.rationale), 'describe'));
    rows.push(reviewRow('Relevant controls', pickedRefs('controls'), 'controls'));
    if (clauses.length) rows.push(reviewRow('Relevant clauses', pickedRefs('clauses'), 'clauses'));
  } else {
    rows.push(
      reviewRow(
        'Asset',
        state.asset
          ? esc(state.asset.name)
          : `${esc(state.newAsset.name)} <span class="badge text-bg-light border">new</span> <span class="small-muted">${esc(state.newAsset.category)} › ${esc(state.newAsset.subcategory)}</span>`,
        'subject',
      ),
    );
    if (affectedOptions) rows.push(reviewRow('Affects', affectedSummary(), 'parties'));
    rows.push(reviewRow('Name', esc(state.title), 'describe'));
    rows.push(reviewRow('What could happen', esc(state.description || '-'), 'describe'));
    if (state.method === 'cia')
      rows.push(reviewRow('Security properties', esc(state.riskTypes.join(', ')), 'describe'));
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
    const [, strategy] = TREATMENT_STRATEGIES.find(([v]) => v === state.treatment.strategy);
    rows.push(
      reviewRow(
        'Treatment',
        esc(strategy.split(' - ')[0]) +
          ` <span class="small-muted">(${esc(TREATMENT_STATUSES.find(([v]) => v === state.treatment.status)[1])}${state.treatment.due ? `, due ${esc(state.treatment.due)}` : ''})</span>`,
        'rate',
      ),
    );
    rows.push(reviewRow('Controls', pickedRefs('controls'), 'controls'));
    if (clauses.length) rows.push(reviewRow('Clauses', pickedRefs('clauses'), 'clauses'));
  }
  if (state.note) rows.push(reviewRow('Notes', esc(state.note), 'describe'));
  $('reviewSummary').innerHTML = rows.join('');
}

function ownerLabelFor(value) {
  const [kind, id] = String(value || '').split(':');
  if (kind === 'user') return users.find((u) => u.id === id).username;
  if (kind === 'role') return roles.find((r) => r.id === id).name;
  return '';
}

// --- Validation per step ---------------------------------------------------------------------

function collectStep(step) {
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
      if (!state.description) return 'Describe the item in a sentence or two.';
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
  if (step === 'rate') {
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
    controls: [...state.controls.keys()],
  };
  // A framework without clauses has none to link, and none to clear.
  if (clauses.length) payload.clauses = [...state.clauses.keys()];
  // Without access to the registers, the links a risk holds stay as they are.
  if (affectedOptions) payload.affected = affectedPayload(state.parties);
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
  if (affectedOptions) created.push(...addedByName());
  return { saved, created };
}

async function savePestle() {
  const created = [];
  const body = {
    framework,
    type: state.pestleType,
    lens: state.lens,
    item: state.description,
    overall_relevance_code: state.relevance,
    rationale: [state.rationale, state.note].filter(Boolean).join('\n\n'),
  };
  if (affectedOptions) {
    body.affected = affectedPayload(state.parties);
    created.push(...addedByName());
  }
  const item = await apiPost('/api/v1/pestle/items', body);
  // A record ticked while the wizard was on another kind of risk has no
  // relevance of its own yet.
  const fallback = state.relevance !== 'na' ? state.relevance : 'high';
  for (const [kind, field] of [
    ['controls', 'control_id'],
    ['clauses', 'clause_id'],
  ])
    if (state[kind].size)
      await apiPatch(`/api/v1/pestle/items/${encodeURIComponent(item.id)}/${kind}`, {
        items: [...state[kind]].map(([id, level]) => ({
          [field]: id,
          relevance_code: level || fallback,
        })),
      });
  return { saved: item, created };
}

function showDone({ saved, created }) {
  $('doneTitle').textContent = editingId
    ? 'Risk updated'
    : isPestle()
      ? 'PESTLE(E) item recorded'
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
    notify(`Could not save: ${errorText(e)}`);
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
    affectedData,
    catalog,
    registerData,
    clauseData,
  ] = await Promise.all([
    apiGet('/api/v1/risks/categories'),
    apiGet('/api/v1/risks/assets'),
    apiGet('/api/v1/risks/users'),
    featureEnabled(me, 'controls')
      ? apiGet(`/api/v1/controls?framework=${encodeURIComponent(framework)}&limit=5000`)
      : { items: [] },
    apiGet('/api/v1/risks/library'),
    // Without read access to People, Vendors and Assets the step is left out.
    loadAffectedOptions().catch(() => null),
    getFrameworkCatalog().catch(() => ({ items: [] })),
    // The heatmap carries the severity thresholds the rating step bands with.
    apiGet('/api/v1/risks/register/heatmap').catch(() => null),
    featureEnabled(me, 'clauses')
      ? apiGet(`/api/v1/clauses?framework=${encodeURIComponent(framework)}&limit=5000`).catch(
          () => ({ items: [] }),
        )
      : { items: [] },
  ]);
  categories = categoryData.items;
  assets = assetData.items;
  users = userData.items;
  roles = userData.roles;
  allControls = controlsOnly(controlData.items);
  clauses = clauseData.items;
  library = libraryData.items;
  affectedOptions = affectedData;
  keenAfEnabled = catalog.items.some((item) => item.slug === 'KEEN-AF:1.0');
  if (registerData?.thresholds) thresholds = registerData.thresholds;
  if (affectedOptions)
    affectedPicker = mountAffectedPicker($('affectedPicker'), {
      prefix: 'affected',
      options: affectedOptions,
      onChange: (chosen) => {
        state.parties = chosen;
      },
    });
  if (canManagePestle && featureEnabled(me, 'pestle')) {
    pestleMeta = await apiGet('/api/v1/pestle/meta').catch(() => pestleMeta);
    if (!state.relevance)
      state.relevance = pestleMeta.relevance_levels.find((l) => l.code !== 'na')?.code;
  }
}

async function loadExisting() {
  const risk = await apiGet(
    `/api/v1/risks/${encodeURIComponent(editingId)}?framework=${encodeURIComponent(framework)}`,
  );
  state.existing = risk;
  state.method = risk.risk_types.length ? 'cia' : 'general';
  state.asset = { id: risk.asset_id, name: risk.asset };
  state.parties = affectedChoices(risk.affected);
  state.title = risk.title;
  state.description = risk.threat_summary;
  state.note = risk.note;
  state.riskTypes = [...risk.risk_types];
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
    strategy: risk.treatment_strategy,
    status: risk.treatment_status,
    due: risk.treatment_due_at,
    plan: risk.treatment_plan,
  };
  state.controls = new Map(risk.controls.map((c) => [c.id, '']));
  state.clauses = new Map(risk.clauses.map((c) => [c.id, '']));
  $('wizardTitle').textContent = 'Edit risk';
  $('wizardIntro').textContent =
    `Change anything about “${risk.display}”. The register updates when you save.`;
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
      (a) => a.name.toLowerCase() === String(draft.asset_name).toLowerCase(),
    );
    if (match) state.asset = { id: match.id, name: match.name };
    else {
      $('assetSearch').value = String(draft.asset_name);
      $('newAssetCategory').value = String(draft.category_name ?? '');
      $('newAssetSubcategory').value = String(draft.subcategory_name ?? '');
    }
  }
  if (Array.isArray(draft.controls)) {
    const known = new Set(allControls.map((c) => c.id));
    state.suggestedControlIds = draft.controls.filter((id) => known.has(id));
    for (const id of state.suggestedControlIds) state.controls.set(id, '');
  }
  state.parties.push(...affectedChoices(draft.affected));
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
  prepareStep(next);
  showStep(next);
});

$('backStep').addEventListener('click', () => {
  stepIndex = Math.max(0, stepIndex - 1);
  showStep(currentStep());
});

$('skipStep').addEventListener('click', () => {
  const step = currentStep();
  if (step === 'parties') {
    state.parties = [];
    renderParties();
  }
  if (step === 'controls' || step === 'clauses') state[step] = new Map();
  stepIndex = Math.min(stepIndex + 1, visibleSteps().length - 1);
  const next = currentStep();
  prepareStep(next);
  showStep(next);
});

document.addEventListener('click', (ev) => {
  const goto = ev.target.closest('[data-goto-step]');
  if (goto) {
    const step = goto.dataset.gotoStep;
    const current = currentStep();
    if (current === 'describe' || current === 'rate') collectStep(current);
    prepareStep(step);
    goTo(step);
  }
});

$('methodChoices').addEventListener('change', (ev) => {
  state.method = ev.target.value;
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

$('pestleTypeChoices').addEventListener('change', (ev) => {
  state.pestleType = ev.target.value;
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

for (const [kind, spec] of Object.entries(LINKS))
  $(spec.list).addEventListener('change', (ev) => {
    const select = ev.currentTarget;
    const listed = new Set(Array.from(select.options, (option) => option.value));
    state[kind] = new Map([
      // A record the list does not offer, such as one linked before controls
      // were switched off, stays chosen.
      ...[...state[kind]].filter(([id]) => !listed.has(id)),
      ...Array.from(select.selectedOptions, (option) => [
        option.value,
        (isPestle() && option.dataset.extra) || '',
      ]),
    ]);
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
    if (first === 'subject') renderAssets();
    showStep(first);
  } catch (e) {
    notify(`Could not start the risk wizard: ${errorText(e)}`);
  }
}
