// Shared by workstation and cloud posture: result badges, linked risks and
// the inline form that records a risk against a failing result.
import { errorText, esc, getCurrentFramework } from '/app.js';
import { TREATMENT_STRATEGIES, riskHref } from './risk-common.js';

export const POSTURE_LABELS = {
  met: 'Met',
  attention: 'Needs attention',
  accepted: 'Risk accepted',
  unknown: 'Not established',
  stale: 'Stale',
  external: 'Managed externally',
  not_collected: 'Not collected',
  info: 'Information',
};
const tones = {
  met: 'success',
  attention: 'warning',
  accepted: 'info',
  unknown: 'secondary',
  stale: 'secondary',
  external: 'info',
  not_collected: 'secondary',
  info: 'light',
};
export const OPERATORS = { lt: '<', lte: '≤', eq: '=', gte: '≥', gt: '>' };

export function postureBadge(status) {
  return `<span class="badge text-bg-${tones[status] || 'secondary'}">${esc(POSTURE_LABELS[status] || 'Not established')}</span>`;
}

// Linked risks as text: an accepted risk names its review date, and a lapsed
// acceptance says so. Titles arrive only for viewers with risk access.
export function linkedRisks(list, framework) {
  return (list || [])
    .map((r) => {
      const state = r.accepted
        ? `Risk accepted until ${esc(r.due || '')}`
        : r.acceptance_expired
          ? 'Acceptance expired'
          : 'Risk recorded';
      const title = r.title
        ? ` · <a href="${esc(riskHref(r.id, framework))}">${esc(r.title)}</a>`
        : '';
      return `<div class="mb-1" data-posture-linked-risk>${state}${title}</div>`;
    })
    .join('');
}

export function savedNotice(notice, riskId, framework) {
  notice.hidden = false;
  notice.innerHTML = `Risk recorded. <a href="${esc(riskHref(riskId, framework))}">Open the risk</a> to rate it or change its treatment.`;
}

function field(id, text, control, help = '') {
  const wrap = document.createElement('div');
  wrap.className = 'col-md-6';
  const label = document.createElement('label');
  label.className = 'form-label';
  label.htmlFor = id;
  label.textContent = text;
  control.id = id;
  wrap.append(label, control);
  if (help) {
    const note = document.createElement('div');
    note.className = 'form-text';
    note.id = id + '-help';
    note.textContent = help;
    control.setAttribute('aria-describedby', note.id);
    wrap.append(note);
  }
  return wrap;
}

/**
 * Open the inline risk editor at the end of `container`, replacing any open one.
 * `save(fields)` sends the risk and resolves with the created record; `saved`
 * then refreshes the page. `owners()` resolves with the users a risk may name.
 */
export function openPostureRiskForm({
  container,
  heading: headingText,
  prefix,
  title: titleText,
  summary: summaryText,
  owners,
  trigger,
  save,
  saved,
}) {
  container.querySelector('[data-posture-risk-form]')?.remove();
  const framework = getCurrentFramework();
  const form = document.createElement('form');
  form.className = 'row g-3 border-top pt-3 mt-3';
  form.dataset.postureRiskForm = '';
  form.noValidate = true;
  const heading = document.createElement('h4');
  heading.className = 'h6 col-12 mb-0';
  heading.textContent = headingText;
  const title = document.createElement('input');
  title.className = 'form-control';
  title.maxLength = 256;
  title.required = true;
  title.value = titleText;
  const summary = document.createElement('textarea');
  summary.className = 'form-control';
  summary.rows = 3;
  summary.maxLength = 12000;
  summary.value = summaryText;
  const strategy = document.createElement('select');
  strategy.className = 'form-select';
  for (const [value, text] of TREATMENT_STRATEGIES) strategy.add(new Option(text, value));
  const owner = document.createElement('select');
  owner.className = 'form-select';
  owner.add(new Option('Nobody yet', ''));
  const due = document.createElement('input');
  due.type = 'date';
  due.className = 'form-control';
  const status = document.createElement('div');
  status.className = 'col-12 small';
  status.setAttribute('role', 'status');
  const actions = document.createElement('div');
  actions.className = 'col-12 d-flex gap-2';
  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.className = 'btn btn-sm btn-primary';
  submit.textContent = 'Save risk';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-sm btn-outline-secondary';
  cancel.textContent = 'Cancel';
  actions.append(submit, cancel);
  const full = (node) => {
    node.className = 'col-12';
    return node;
  };
  form.append(
    heading,
    full(field(prefix + '-title', 'Title', title)),
    full(field(prefix + '-summary', 'Threat summary', summary)),
    field(prefix + '-strategy', 'Treatment', strategy),
    field(prefix + '-owner', 'Owner', owner),
    field(
      prefix + '-due',
      'Review date',
      due,
      'Accepting needs an owner and a review date; the acceptance lapses after that date.',
    ),
    status,
    actions,
  );
  container.append(form);
  title.focus();
  owners()
    .then((items) => {
      for (const u of items) owner.add(new Option(u.username, u.id));
    })
    .catch(() => {
      status.textContent = 'Owners could not be loaded; the risk can be saved without one.';
    });
  cancel.addEventListener('click', () => {
    form.remove();
    trigger.focus();
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    status.className = 'col-12 small';
    const accept = strategy.value === 'accept';
    const problem = !title.value.trim()
      ? 'Give the risk a title.'
      : accept && (!owner.value || !due.value)
        ? 'Accepting a risk needs an owner and a review date.'
        : '';
    if (problem) {
      status.className = 'col-12 small text-danger';
      status.textContent = problem;
      return;
    }
    submit.disabled = true;
    let created;
    try {
      created = await save({
        title: title.value.trim(),
        threat_summary: summary.value,
        treatment_strategy: strategy.value,
        treatment_due_at: due.value || null,
        risk_owner_user_id: owner.value || null,
        framework,
      });
    } catch (error) {
      submit.disabled = false;
      status.className = 'col-12 small text-danger';
      status.textContent = errorText(error) || 'The risk could not be saved.';
      return;
    }
    await saved(created, framework);
  });
  return form;
}
