// Start an audit one step at a time: its details, its scope and its
// attendees, then a choice of where to begin. Nothing is saved until the
// last step, when the audit and its attendees are created together.
import { featureEnabled } from '/pages/feature-availability.js';
import {
  initNavbar,
  apiGet,
  apiPost,
  esc,
  toast,
  getCurrentFramework,
  frameworkLabel,
  withFramework,
  rememberSamplingAudit,
} from '/app.js';

const $ = (id) => document.getElementById(id);

// The details step works while the navigation is still loading: nothing
// submits the form early, and the highlighted card follows the chosen type.
$('wizardForm').addEventListener('submit', (ev) => ev.preventDefault());
function auditType() {
  return document.querySelector('input[name="auditType"]:checked')?.value || 'internal';
}
function showAuditType() {
  for (const card of document.querySelectorAll('[data-audit-type]'))
    card.classList.toggle('selected', card.dataset.auditType === auditType());
}
for (const input of document.querySelectorAll('input[name="auditType"]'))
  input.addEventListener('change', showAuditType);
showAuditType();

const me = await initNavbar();
const framework = getCurrentFramework();
const status = $('status');

const STEPS = [
  ['details', 'Details'],
  ['scope', 'Scope'],
  ['people', 'Attendees'],
  ['review', 'Check and create'],
];
const HINTS = {
  details: 'A title is enough to begin; dates can wait.',
  scope: 'Scope can be added or changed on the audit later.',
  people: 'Attendees can be added on the audit later too.',
  review: '',
};
// Scope lists, in the order the step shows them.
const SCOPE = [
  { key: 'controls', label: 'Controls', single: 'control', plural: 'controls' },
  { key: 'clauses', label: 'Clauses', single: 'clause', plural: 'clauses' },
  {
    key: 'documents',
    label: 'Policies and procedures',
    single: 'policy or procedure',
    plural: 'policies and procedures',
    feature: 'documents',
  },
  { key: 'swot', label: 'SWOT items', single: 'SWOT item', plural: 'SWOT items', feature: 'swot' },
];
const ROLES = { person: 'Person', user: 'Keen account', guest: 'Guest' };

const canManage = Boolean(me?.is_admin || me?.can_manage_audits || me?.can_admin_audits);
const canReadIsms = Boolean(me?.is_admin || me?.can_view_isms || me?.can_manage_isms);
const scopeLists = SCOPE.filter(
  (list) =>
    (!list.feature || featureEnabled(me, list.feature)) && (list.key !== 'swot' || canReadIsms),
);

let stepIndex = 0;
let scopeLoaded = null;
let peopleLoaded = null;
let created = null;
let activeScope = scopeLists[0].key;
const chosen = Object.fromEntries(SCOPE.map((list) => [list.key, new Set()]));
const catalogue = { controls: [], clauses: [], documents: [], swot: [] };
const failures = {};
let clauseControls = new Map();
let attendees = [];
let people = [];
let users = [];

function notify(message, kind = 'danger') {
  toast(status, message, kind);
}

// --- Steps -------------------------------------------------------------------

function currentStep() {
  return STEPS[stepIndex][0];
}

function renderStepper() {
  $('wizardSteps').innerHTML = STEPS.map(([step, label], index) => {
    const tone =
      index === stepIndex
        ? 'btn-primary'
        : index < stepIndex
          ? 'btn-outline-success'
          : 'btn-outline-secondary';
    const current = index === stepIndex ? ' aria-current="step"' : '';
    return `<li><button class="btn btn-sm ${tone}" type="button" data-goto-step="${esc(step)}"${current} ${index > stepIndex ? 'disabled' : ''}>${index + 1}. ${esc(label)}</button></li>`;
  }).join('');
}

function showStep(step) {
  for (const section of document.querySelectorAll('.risk-wizard-step'))
    section.hidden = section.dataset.step !== step;
  const done = step === 'done';
  $('wizardSteps').hidden = done;
  $('wizardFooter').hidden = done;
  $('backStep').hidden = stepIndex === 0;
  $('skipStep').hidden = !['scope', 'people'].includes(step);
  $('nextStep').innerHTML =
    step === 'review'
      ? '<i class="bi bi-check2 me-1" aria-hidden="true"></i>Create audit'
      : 'Next<i class="bi bi-arrow-right ms-1" aria-hidden="true"></i>';
  $('stepHint').textContent = HINTS[step] || '';
  if (!done) renderStepper();
  if (step === 'scope') loadScope();
  if (step === 'people') loadPeople();
  if (step === 'review') renderReview();
  const heading = document.querySelector(`.risk-wizard-step[data-step="${step}"] h2`);
  heading?.setAttribute('tabindex', '-1');
  heading?.focus?.();
}

function goTo(step) {
  const index = STEPS.findIndex(([name]) => name === step);
  if (index < 0) return;
  stepIndex = index;
  showStep(step);
}

function detailsProblem() {
  if (!$('auditTitle').value.trim()) return ['auditTitle', 'Give the audit a title.'];
  const start = $('auditStart').value;
  const end = $('auditEnd').value;
  if (start && end && end < start) return ['auditEnd', 'The audit cannot end before it begins.'];
  return null;
}

async function next() {
  const step = currentStep();
  if (step === 'details') {
    const problem = detailsProblem();
    if (problem) {
      notify(problem[1], 'warning');
      $(problem[0]).focus();
      return;
    }
  }
  if (step === 'review') {
    await create();
    return;
  }
  status.style.display = 'none';
  goTo(STEPS[stepIndex + 1][0]);
}

// --- Scope -------------------------------------------------------------------

function rawId(value) {
  const text = String(value || '');
  const at = text.indexOf(':');
  return at >= 0 ? text.slice(at + 1) : text;
}

async function fetchList(key, url, pick = (data) => data?.items || []) {
  try {
    catalogue[key] = pick(await apiGet(url));
  } catch (error) {
    failures[key] = String(error?.message || error);
  }
}

function loadScope() {
  scopeLoaded ||= (async () => {
    renderScopeTabs();
    const fw = encodeURIComponent(framework);
    const keys = new Set(scopeLists.map((list) => list.key));
    await Promise.all([
      fetchList('controls', `/api/v1/controls?framework=${fw}&limit=5000`, (data) =>
        (data?.items || []).filter((c) => String(c.type || '') !== 'clause'),
      ),
      fetchList('clauses', `/api/v1/clauses?framework=${fw}`),
      apiGet(`/api/v1/graph/clause_control?framework=${fw}`)
        .then((graph) => {
          clauseControls = new Map();
          for (const link of graph?.links || []) {
            const clause = rawId(link.source);
            const control = rawId(link.target);
            if (!clause || !control) continue;
            if (!clauseControls.has(clause)) clauseControls.set(clause, new Set());
            clauseControls.get(clause).add(control);
          }
        })
        .catch(() => {
          // Without the graph, clauses bring no controls with them.
        }),
      keys.has('documents')
        ? fetchList('documents', `/api/v1/isms/documents?framework=${fw}&limit=1000`)
        : null,
      keys.has('swot') ? fetchList('swot', '/api/v1/isms/swot?limit=200') : null,
    ]);
    renderScopePanes();
  })();
  return scopeLoaded;
}

function scopeCount(key) {
  return chosen[key].size;
}

function renderScopeTabs() {
  $('scopeTabs').innerHTML = scopeLists
    .map(
      (list) =>
        `<button class="btn btn-sm ${list.key === activeScope ? 'btn-primary' : 'btn-outline-secondary'}" type="button" role="tab" aria-selected="${list.key === activeScope}" data-scope-tab="${esc(list.key)}">${esc(list.label)}${scopeCount(list.key) ? ` (${scopeCount(list.key)})` : ''}</button>`,
    )
    .join('');
}

function itemLabel(key, item) {
  if (key === 'documents')
    return `<span class="fw-semibold">${esc(item.title || 'Document')}</span> <span class="badge text-bg-light">${esc(item.document_type || 'document')}</span><span class="small-muted d-block">${(item.clauses || []).length} clauses, ${(item.controls || []).length} controls</span>`;
  if (key === 'swot')
    return `<span class="fw-semibold">${esc(item.title || 'SWOT item')}</span> <span class="small-muted">${esc(item.kind || '')}</span>`;
  return `<span class="fw-semibold">${esc(item.ref || '')}</span> <span class="small-muted">${esc(item.title || '')}</span>`;
}

function matches(key, item, filter) {
  if (!filter) return true;
  const text =
    key === 'documents'
      ? `${item.title || ''} ${item.document_type || ''}`
      : key === 'swot'
        ? `${item.title || ''} ${item.kind || ''}`
        : `${item.ref || ''} ${item.title || ''}`;
  return text.toLowerCase().includes(filter);
}

function renderScopeList(key) {
  const host = document.querySelector(`[data-scope-items="${key}"]`);
  if (!host) return;
  if (failures[key]) {
    host.innerHTML = `<div class="text-danger small p-1">Could not load this list: ${esc(failures[key])}</div>`;
    return;
  }
  const filter = (document.querySelector(`[data-scope-filter="${key}"]`)?.value || '')
    .trim()
    .toLowerCase();
  const items = catalogue[key].filter((item) => matches(key, item, filter));
  host.innerHTML =
    items
      .map((item) => {
        const id = String(item.id);
        const indent =
          key === 'clauses'
            ? ` style="margin-left:${Math.min(String(item.ref || '').split('.').length - 1, 3)}rem"`
            : '';
        return `<label class="d-flex gap-2 align-items-start border-bottom py-2"${indent}><input class="form-check-input mt-1" type="checkbox" data-scope-key="${esc(key)}" value="${esc(id)}" ${chosen[key].has(id) ? 'checked' : ''}><span>${itemLabel(key, item)}</span></label>`;
      })
      .join('') ||
    `<div class="small-muted p-1">${catalogue[key].length ? 'Nothing matches the filter.' : `No ${esc(SCOPE.find((list) => list.key === key).plural)} in this framework yet.`}</div>`;
}

function renderScopePanes() {
  $('scopePanes').innerHTML = scopeLists
    .map(
      (list) =>
        `<div data-scope-pane="${esc(list.key)}" role="tabpanel" ${list.key === activeScope ? '' : 'hidden'}>
          <input class="form-control form-control-sm mb-2" type="search" data-scope-filter="${esc(list.key)}" aria-label="Filter ${esc(list.plural)}" placeholder="Filter ${esc(list.plural)}">
          <div class="border rounded px-2 risk-wizard-scroll" data-scope-items="${esc(list.key)}"></div>
        </div>`,
    )
    .join('');
  for (const list of scopeLists) renderScopeList(list.key);
  renderScopeTabs();
}

// Choosing a document or clause also chooses what it links to.
function addLinked(key, id) {
  let added = 0;
  const add = (target, value) => {
    const text = String(value || '');
    if (text && !chosen[target].has(text)) {
      chosen[target].add(text);
      added += 1;
    }
  };
  const clauseLinks = (clauseId) => {
    for (const control of clauseControls.get(String(clauseId)) || []) add('controls', control);
  };
  if (key === 'documents') {
    const doc = catalogue.documents.find((d) => String(d.id) === id);
    for (const clause of doc?.clauses || []) {
      add('clauses', clause.id);
      clauseLinks(clause.id);
    }
    for (const control of doc?.controls || []) add('controls', control.id);
  }
  if (key === 'clauses') clauseLinks(id);
  return added;
}

$('scopeTabs').addEventListener('click', (ev) => {
  const tab = ev.target.closest('[data-scope-tab]');
  if (!tab) return;
  activeScope = tab.dataset.scopeTab;
  for (const pane of document.querySelectorAll('[data-scope-pane]'))
    pane.hidden = pane.dataset.scopePane !== activeScope;
  renderScopeTabs();
});
$('scopePanes').addEventListener('input', (ev) => {
  const filter = ev.target.closest('[data-scope-filter]');
  if (filter) renderScopeList(filter.dataset.scopeFilter);
});
$('scopePanes').addEventListener('change', (ev) => {
  const box = ev.target.closest('[data-scope-key]');
  if (!box) return;
  const key = box.dataset.scopeKey;
  if (!box.checked) {
    chosen[key].delete(box.value);
    $('scopeNote').textContent = '';
  } else {
    chosen[key].add(box.value);
    const added = addLinked(key, box.value);
    $('scopeNote').textContent = added
      ? `Added ${added} linked ${added === 1 ? 'clause or control' : 'clauses and controls'} as well.`
      : '';
    if (added) for (const list of scopeLists) renderScopeList(list.key);
  }
  renderScopeTabs();
});

// --- Attendees ---------------------------------------------------------------

function loadPeople() {
  peopleLoaded ||= (async () => {
    const [peopleData, userData] = await Promise.all([
      canReadIsms && featureEnabled(me, 'people')
        ? apiGet('/api/v1/isms/people').catch(() => ({ items: [] }))
        : { items: [] },
      apiGet('/api/v1/audits/users').catch((error) => {
        notify(`Could not list Keen accounts: ${String(error?.message || error)}`, 'warning');
        return { items: [] };
      }),
    ]);
    people = peopleData?.items || [];
    users = userData?.items || [];
    const group = (label, kind, rows, name) =>
      rows.length
        ? `<optgroup label="${esc(label)}">${rows
            .map(
              (row) =>
                `<option value="${kind}:${esc(row.id)}">${esc(name(row))}${row.email ? ` · ${esc(row.email)}` : ''}</option>`,
            )
            .join('')}</optgroup>`
        : '';
    $('attendeeWho').innerHTML =
      '<option value="">Someone else (enter their details)</option>' +
      group('People', 'person', people, (p) => p.name) +
      group('Keen accounts', 'user', users, (u) => u.username);
  })();
  return peopleLoaded;
}

function chosenRecord() {
  const [kind, id] = $('attendeeWho').value.split(':');
  if (kind === 'person') return { kind, id, row: people.find((p) => String(p.id) === id) };
  if (kind === 'user') return { kind, id, row: users.find((u) => String(u.id) === id) };
  return null;
}

$('attendeeWho').addEventListener('change', () => {
  const record = chosenRecord();
  $('attendeeName').value = record ? record.row?.name || record.row?.username || '' : '';
  $('attendeeEmail').value = record ? record.row?.email || '' : '';
});

function renderAttendees() {
  $('attendeeList').innerHTML = attendees
    .map(
      (
        a,
        index,
      ) => `<li class="list-group-item d-flex flex-wrap gap-2 justify-content-between align-items-center">
        <span class="text-break"><span class="fw-semibold">${esc(a.name)}</span>${a.role ? ` <span class="badge badge-soft">${esc(a.role)}</span>` : ''}<span class="small-muted d-block">${esc(ROLES[a.kind])}${a.email ? ` · ${esc(a.email)}` : ''}</span></span>
        <button class="btn btn-sm btn-outline-danger" type="button" data-remove-attendee="${index}" aria-label="Remove ${esc(a.name)}">Remove</button>
      </li>`,
    )
    .join('');
}

$('addAttendee').addEventListener('click', () => {
  const record = chosenRecord();
  const name = $('attendeeName').value.trim();
  const email = $('attendeeEmail').value.trim();
  if (!record && !name) {
    notify('Choose a person or enter a name.', 'warning');
    $('attendeeName').focus();
    return;
  }
  if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    notify('Enter a valid email address, or leave it blank.', 'warning');
    $('attendeeEmail').focus();
    return;
  }
  if (record && attendees.some((a) => a.kind === record.kind && a.id === record.id)) {
    notify(`${name || 'This person'} is already an attendee.`, 'warning');
    return;
  }
  attendees.push({
    kind: record?.kind || 'guest',
    id: record?.id || '',
    name: name || record.row?.name || record.row?.username || '',
    email,
    role: $('attendeeRole').value.trim(),
  });
  for (const id of ['attendeeWho', 'attendeeName', 'attendeeEmail', 'attendeeRole'])
    $(id).value = '';
  status.style.display = 'none';
  renderAttendees();
});
$('attendeeList').addEventListener('click', (ev) => {
  const button = ev.target.closest('[data-remove-attendee]');
  if (!button) return;
  attendees.splice(Number(button.dataset.removeAttendee), 1);
  renderAttendees();
});

// --- Review and create -------------------------------------------------------

function dateText(value) {
  return value || 'not set';
}

function scopeSummary() {
  const parts = scopeLists
    .filter((list) => scopeCount(list.key))
    .map((list) => {
      const count = scopeCount(list.key);
      return `${count} ${count === 1 ? list.single : list.plural}`;
    });
  return parts.length ? parts.join(', ') : 'Nothing chosen yet';
}

function renderReview() {
  const rows = [
    ['Title', esc($('auditTitle').value.trim()), 'details'],
    ['Type', auditType() === 'external' ? 'External audit' : 'Internal audit', 'details'],
    [
      'Dates',
      `${esc(dateText($('auditStart').value))} to ${esc(dateText($('auditEnd').value))}`,
      'details',
    ],
    ['Framework', esc(frameworkLabel(framework)), ''],
    ['Scope', esc(scopeSummary()), 'scope'],
    [
      'Attendees',
      attendees.length
        ? attendees.map((a) => `${esc(a.name)}${a.role ? ` (${esc(a.role)})` : ''}`).join(', ')
        : 'None yet',
      'people',
    ],
  ];
  $('reviewSummary').innerHTML = rows
    .map(
      ([label, value, step]) =>
        `<dt class="col-sm-3">${esc(label)}</dt><dd class="col-sm-9 d-flex gap-3 justify-content-between align-items-start"><span class="text-break">${value}</span>${step ? `<button class="btn btn-link btn-sm p-0" type="button" data-goto-step="${esc(step)}">Change</button>` : ''}</dd>`,
    )
    .join('');
}

function payload() {
  const body = {
    title: $('auditTitle').value.trim(),
    framework_slug: framework,
    audit_type: auditType(),
    start_date: $('auditStart').value || null,
    end_date: $('auditEnd').value || null,
    controls: [...chosen.controls],
    clauses: [...chosen.clauses],
    documents: [...chosen.documents],
    attendees: attendees.map((a) => ({
      user_id: a.kind === 'user' ? a.id : null,
      person_id: a.kind === 'person' ? a.id : null,
      name: a.name || null,
      email: a.email || null,
      role: a.role || null,
    })),
  };
  // SWOT scope is sent only when chosen; it needs ISMS access to set.
  if (chosen.swot.size) body.swot_ids = [...chosen.swot];
  return body;
}

async function create() {
  const button = $('nextStep');
  button.disabled = true;
  try {
    created = await apiPost('/api/v1/audits', payload());
  } catch (error) {
    notify(`Could not create the audit: ${String(error?.message || error)}`);
    return;
  } finally {
    button.disabled = false;
  }
  rememberSamplingAudit(created.id);
  status.style.display = 'none';
  renderDone();
  showStep('done');
}

function renderDone() {
  const fw = created.framework_slug || framework;
  const audit = (extra = '') =>
    withFramework(`/audit.html?id=${encodeURIComponent(created.id)}${extra}`, fw);
  $('doneTitle').textContent = `${created.title} is ready`;
  $('doneDetail').textContent =
    'Its scope and attendees are saved. Findings, notes and the executive summary are recorded on the audit page.';
  const choices = [
    [
      'bi-activity',
      'Look for evidence by event',
      'Search collected events and sample them into the audit from each event.',
      withFramework('/events.html', fw),
    ],
    [
      'bi-shield-check',
      'Look for evidence by control',
      'Start from a control and follow its evidence and linked records.',
      withFramework('/controls.html', fw),
    ],
    [
      'bi-diagram-3',
      'Look for evidence in the ISMS',
      'Sample documents, effectiveness measures, objectives and other ISMS records.',
      withFramework('/isms.html', fw),
    ],
    [
      'bi-journal-text',
      'Start writing notes',
      'Open the audit at its report notes and record what you find as you go.',
      audit('&focus=notes'),
    ],
  ];
  $('startChoices').innerHTML =
    choices
      .map(
        ([icon, title, text, href]) => `<div class="col-12 col-md-6">
          <a class="card h-100 p-3 text-decoration-none risk-method-card" href="${esc(href)}">
            <span class="fw-semibold"><i class="bi ${esc(icon)} me-2" aria-hidden="true"></i>${esc(title)}</span>
            <span class="small-muted mt-1">${esc(text)}</span>
          </a>
        </div>`,
      )
      .join('') +
    `<div class="col-12 text-center"><a class="btn btn-outline-secondary" href="${esc(audit())}">Open the audit</a></div>`;
}

// --- Wiring ------------------------------------------------------------------

$('wizardForm').addEventListener('submit', (ev) => {
  ev.preventDefault();
  next();
});
$('backStep').addEventListener('click', () => {
  if (stepIndex > 0) goTo(STEPS[stepIndex - 1][0]);
});
$('skipStep').addEventListener('click', () => goTo(STEPS[stepIndex + 1][0]));
document.addEventListener('click', (ev) => {
  const target = ev.target.closest('[data-goto-step]');
  if (target) goTo(target.dataset.gotoStep);
});

// The audit belongs to the framework chosen in the top navigation.
$('auditFramework').textContent = frameworkLabel(framework);
if (!canManage) {
  notify('You need audits.manage permission to start an audit.');
  $('wizardForm').hidden = true;
  $('wizardSteps').hidden = true;
} else {
  showStep('details');
}
