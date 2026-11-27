// Start an audit one step at a time: its details, its scope and its
// attendees, then a choice of where to begin. Nothing is saved until the
// last step, when the audit and its attendees are created together.
import { featureEnabled } from '/pages/feature-availability.js';
import { catalogueLabel } from '/pages/catalogue-options.js';
import {
  isoDateToDisplay,
  errorText,
  initNavbar,
  apiGet,
  apiPost,
  enhancePickers,
  esc,
  node,
  toast,
  getCurrentFramework,
  frameworkLabel,
  withFramework,
  rememberSamplingAudit,
  unsavedGuard,
} from '/app.js';

const $ = (id) => document.getElementById(id);

// Leaving the wizard with unsaved details warns first and shows a hint.
const unsaved = unsavedGuard({ root: $('wizardForm'), hint: $('unsaved') });

// The details step works while the navigation is still loading: nothing
// submits the form early, and the highlighted card follows the chosen type.
$('wizardForm').addEventListener('submit', (ev) => ev.preventDefault());
// One type is always chosen: the page starts with Internal checked.
function auditType() {
  return document.querySelector('input[name="auditType"]:checked').value;
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
  stepIndex = STEPS.findIndex(([name]) => name === step);
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

async function fetchList(key, url, pick = (data) => data.items) {
  try {
    catalogue[key] = pick(await apiGet(url));
  } catch (error) {
    failures[key] = errorText(error);
  }
}

function loadScope() {
  scopeLoaded ||= (async () => {
    renderScopeTabs();
    const fw = encodeURIComponent(framework);
    const keys = new Set(scopeLists.map((list) => list.key));
    await Promise.all([
      fetchList('controls', `/api/v1/controls?framework=${fw}&limit=5000`, (data) =>
        data.items.filter((c) => String(c.type || '') !== 'clause'),
      ),
      fetchList('clauses', `/api/v1/clauses?framework=${fw}`),
      apiGet(`/api/v1/graph/clause_control?framework=${fw}`)
        .then((graph) => {
          clauseControls = new Map();
          for (const link of graph.links) {
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

// One entry of a scope list, as an option built from text.
function scopeOption(key, item) {
  const id = String(item.id);
  let option;
  if (key === 'documents') {
    option = node(
      'option',
      {
        value: id,
        title: `${(item.clauses || []).length} clauses, ${(item.controls || []).length} controls`,
      },
      item.title,
    );
    option.dataset.badge = item.document_type || 'document';
  } else if (key === 'swot') {
    option = node('option', { value: id }, item.title);
    option.dataset.badge = item.kind;
  } else option = node('option', { value: id }, catalogueLabel(item));
  option.selected = chosen[key].has(id);
  return option;
}

function renderScopeList(key) {
  const select = document.querySelector(`[data-scope-list="${key}"]`);
  const note = document.querySelector(`[data-scope-items="${key}"]`);
  note.className = failures[key] ? 'text-danger small p-1' : 'small-muted p-1';
  if (failures[key]) {
    select.replaceChildren();
    note.textContent = `Could not load this list: ${failures[key]}`;
    return;
  }
  select.replaceChildren(...catalogue[key].map((item) => scopeOption(key, item)));
  note.textContent = catalogue[key].length
    ? ''
    : `No ${SCOPE.find((list) => list.key === key).plural} in this framework yet.`;
}

// Each list is a multiple select, which the design system presents as its
// picker: a search box, a short list to browse and the chosen entries as chips.
function renderScopePanes() {
  $('scopePanes').replaceChildren(
    ...scopeLists.map((list) => {
      const pane = node(
        'div',
        { 'data-scope-pane': list.key, role: 'tabpanel' },
        node('select', {
          class: 'form-select',
          multiple: '',
          size: '10',
          'data-scope-list': list.key,
          'aria-label': list.label,
        }),
        node('div', { 'data-scope-items': list.key }),
      );
      pane.hidden = list.key !== activeScope;
      return pane;
    }),
  );
  enhancePickers($('scopePanes'));
  for (const list of scopeLists) renderScopeList(list.key);
  renderScopeTabs();
}

// Choosing a document or clause also chooses what it links to.
function addLinked(key, id) {
  let added = 0;
  const add = (target, value) => {
    const text = String(value);
    if (!chosen[target].has(text)) {
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
$('scopePanes').addEventListener('change', (ev) => {
  const select = ev.target.closest('[data-scope-list]');
  if (!select) return;
  const key = select.dataset.scopeList;
  let added = 0;
  for (const option of select.options) {
    if (!option.selected) chosen[key].delete(option.value);
    else if (!chosen[key].has(option.value)) {
      chosen[key].add(option.value);
      added += addLinked(key, option.value);
    }
  }
  $('scopeNote').textContent = added
    ? `Added ${added} linked ${added === 1 ? 'clause or control' : 'clauses and controls'} as well.`
    : '';
  if (added) for (const list of scopeLists) renderScopeList(list.key);
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
        notify(`Could not list Keen accounts: ${errorText(error)}`, 'warning');
        return { items: [] };
      }),
    ]);
    people = peopleData.items;
    users = userData.items;
    // A person attends through the Keen account linked to their record, so
    // the choice names that account.
    const group = (label, kind, rows, name) =>
      rows.length
        ? `<optgroup label="${esc(label)}">${rows
            .map(
              (row) =>
                `<option value="${kind}:${esc(row.id)}">${esc(name(row))}${row.email ? ` · ${esc(row.email)}` : ''}${kind === 'person' && row.username ? ` · account ${esc(row.username)}` : ''}</option>`,
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
  $('attendeeName').value = record ? record.row?.name || record.row?.username : '';
  $('attendeeEmail').value = record ? record.row?.email : '';
});

function renderAttendees() {
  $('attendeeList').innerHTML = attendees
    .map(
      (
        a,
        index,
      ) => `<li class="list-group-item d-flex flex-wrap gap-2 justify-content-between align-items-center">
        <span class="text-break"><span class="fw-semibold">${esc(a.name)}</span>${a.role ? ` <span class="badge badge-soft">${esc(a.role)}</span>` : ''}<span class="small-muted d-block">${esc(ROLES[a.kind])}${a.email ? ` · ${esc(a.email)}` : ''}${a.account ? ` · account ${esc(a.account)}` : ''}</span></span>
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
    name: name || record.row?.name || record.row?.username,
    email,
    role: $('attendeeRole').value.trim(),
    account: record?.kind === 'person' ? record.row?.username : '',
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
  return isoDateToDisplay(value) || 'not set';
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
      name: a.name,
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
    notify(`Could not create the audit: ${errorText(error)}`);
    return;
  } finally {
    button.disabled = false;
  }
  unsaved.markClean();
  rememberSamplingAudit(created.id);
  status.style.display = 'none';
  renderDone();
  showStep('done');
}

function renderDone() {
  const fw = created.framework_slug;
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
