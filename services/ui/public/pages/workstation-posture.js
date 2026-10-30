import { apiGet, apiPost, errorText, esc, fmtTs, getCurrentFramework } from '/app.js';
import { openState, platformIcon } from './platform.js';
import { TREATMENT_STRATEGIES, riskHref } from './risk-common.js';
const labels = {
  met: 'Met',
  attention: 'Needs attention',
  accepted: 'Risk accepted',
  unknown: 'Not established',
  stale: 'Stale',
  external: 'Managed externally',
  not_collected: 'Not collected',
};
const tones = {
  met: 'success',
  attention: 'warning',
  accepted: 'info',
  unknown: 'secondary',
  stale: 'secondary',
  external: 'info',
  not_collected: 'secondary',
};
const units = { lt: '<', lte: '≤', eq: '=', gte: '≥', gt: '>' };
const targetLabels = { screen_lock: 'Screen lock', antivirus: 'Antivirus signature age' };
function targets(value) {
  const entries = Object.entries(value || {});
  if (!entries.length)
    return 'No organisation targets are set, so configured protections count as met.';
  return `Organisation targets: ${entries
    .map(
      ([check, t]) =>
        `${targetLabels[check] || check} ${units[t.operator] || t.operator} ${t.value} ${t.unit}`,
    )
    .join('; ')}.`;
}
function evidence(check) {
  const rows = check.observations.map(
    (o) =>
      `<div class="mb-1"><a href="/event.html?id=${encodeURIComponent(o.event_id)}">${esc(o.provider)} · ${esc(o.scope)}</a> · ${esc(fmtTs(o.observed_at))} · ${esc(labels[o.status] || 'Not established')}<div class="small-muted">${esc(o.reason.replaceAll('_', ' '))}${o.detail ? ` · ${esc(o.detail)}` : ''}</div></div>`,
  );
  if (!rows.length) return 'No current evidence';
  if (rows.length === 1) return rows[0];
  return `<details data-posture-evidence><summary>${rows.length} observations</summary>${rows.join('')}</details>`;
}
// Linked risks as text: an accepted risk names its review date, and a lapsed
// acceptance says so. Titles arrive only for viewers with risk access.
function risks(check, framework) {
  return (check.risks || [])
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
export function workstationPosture(host, allowed) {
  const opened = openState();
  let person = null,
    generation = 0,
    offset = 0,
    owners = null;
  async function ownerOptions() {
    owners ??= apiGet('/api/v1/risks/users').then((d) => d?.items || []);
    return owners;
  }
  // An inline editor below the device's checks, as other Person tabs use.
  function riskForm(article, device, check, trigger) {
    article.querySelector('[data-posture-risk-form]')?.remove();
    const framework = getCurrentFramework();
    const form = document.createElement('form');
    form.className = 'row g-3 border-top pt-3 mt-3';
    form.dataset.postureRiskForm = '';
    form.noValidate = true;
    const heading = document.createElement('h4');
    heading.className = 'h6 col-12 mb-0';
    heading.textContent = `Record a risk for ${check.label} on ${device.hostname}`;
    const prefix = `posture-risk-${device.agent_id}-${check.check}`;
    const title = document.createElement('input');
    title.className = 'form-control';
    title.maxLength = 256;
    title.required = true;
    title.value = `${device.hostname}: ${check.label} needs attention`;
    const summary = document.createElement('textarea');
    summary.className = 'form-control';
    summary.rows = 3;
    summary.maxLength = 12000;
    summary.value = [
      `The ${check.label} check needs attention on ${device.hostname}.`,
      ...(check.observations || [])
        .filter((o) => o.status === 'attention')
        .map((o) =>
          [o.provider, o.scope, String(o.reason || '').replaceAll('_', ' '), o.detail]
            .filter(Boolean)
            .join(' · '),
        ),
    ].join('\n');
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
    const save = document.createElement('button');
    save.type = 'submit';
    save.className = 'btn btn-sm btn-primary';
    save.textContent = 'Save risk';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn-sm btn-outline-secondary';
    cancel.textContent = 'Cancel';
    actions.append(save, cancel);
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
    article.append(form);
    title.focus();
    ownerOptions()
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
      save.disabled = true;
      try {
        const created = await apiPost(
          `/api/v1/isms/people/${encodeURIComponent(person.id)}/workstation-posture/risks`,
          {
            agent_id: device.agent_id,
            check: check.check,
            title: title.value.trim(),
            threat_summary: summary.value,
            treatment_strategy: strategy.value,
            treatment_due_at: due.value || null,
            risk_owner_user_id: owner.value || null,
            framework,
          },
        );
        await load();
        const notice = host.querySelector('[data-posture-notice]');
        notice.hidden = false;
        notice.innerHTML = `Risk recorded. <a href="${esc(riskHref(created.risk_id, framework))}">Open the risk</a> to rate it or change its treatment.`;
      } catch (error) {
        save.disabled = false;
        status.className = 'col-12 small text-danger';
        status.textContent = errorText(error) || 'The risk could not be saved.';
      }
    });
  }
  async function load() {
    const request = ++generation;
    host.hidden = false;
    host.innerHTML =
      '<div class="card"><div class="card-body" role="status">Loading workstation posture…</div></div>';
    try {
      const data = await apiGet(
        `/api/v1/isms/people/${encodeURIComponent(person.id)}/workstation-posture?offset=${offset}`,
      );
      if (request !== generation) return;
      const devices = data.devices || [];
      const met = devices.reduce((n, d) => n + d.met, 0),
        target = devices.reduce((n, d) => n + d.target, 0);
      host.innerHTML = `<div class="card"><div class="card-header d-flex justify-content-between align-items-center gap-2"><h2 class="h5 mb-0">Workstation posture · ${esc(person.name)}</h2><button type="button" class="btn btn-sm btn-outline-primary" data-posture-refresh>Refresh</button></div><div class="card-body"><p><strong>${Number(met)} / ${Number(target)} checks met</strong> across ${devices.length} devices on this page · ${Number(data.total)} devices linked.</p><p class="small-muted">Targets: encrypted disks, automatic screen locking, an antivirus component, an enabled firewall and automatic update installation. Reports stay current for ${Number(data.freshness_hours)} hours. Results describe the collected scopes; they are reported by the device. ${esc(targets(data.targets))} Rules: ${esc(data.rules_version || '')}.</p><div class="alert alert-success" role="status" data-posture-notice hidden></div><div data-posture-devices></div><div class="d-flex gap-2 mt-3" data-posture-pages></div></div></div>`;
      const container = host.querySelector('[data-posture-devices]');
      if (!devices.length)
        container.textContent =
          'No workstation agents linked. Choose Workstation and this person when registering an agent in Admin.';
      for (const device of devices) {
        const article = document.createElement('article');
        article.className = 'border-top pt-3 mt-3';
        const framework = getCurrentFramework();
        const showRisks =
          data.can_create_risk || device.checks.some((check) => check.risks?.length);
        const riskColumn = (check, index) =>
          showRisks
            ? `<td>${risks(check, framework)}${data.can_create_risk && check.status === 'attention' ? `<button type="button" class="btn btn-sm btn-outline-primary" data-posture-risk="${index}">Create a risk</button>` : ''}</td>`
            : '';
        article.innerHTML = `<details class="collapsible-card" data-posture-device><summary class="d-flex align-items-center gap-2"><i class="bi bi-chevron-right collapse-chevron" aria-hidden="true"></i>${platformIcon(device.platform)}<h3 class="h6 mb-0">${esc(device.hostname)}</h3><span class="badge badge-soft">${Number(device.met)} / ${Number(device.target)}</span></summary><p class="small-muted mt-2">${device.active ? 'Active credential' : 'Credential revoked or expired'} · Last contact: ${esc(device.last_seen ? fmtTs(device.last_seen) : 'Never')}</p>${device.overflow ? '<p class="text-warning">Too many observation scopes to establish device completion. Review the agent configuration.</p>' : ''}<div class="table-responsive"><table class="table table-sm"><thead><tr><th>Check</th><th>Result</th><th>Latest evidence by scope</th>${showRisks ? '<th>Risks</th>' : ''}</tr></thead><tbody>${device.checks.map((check, index) => `<tr><th scope="row">${esc(check.label)}</th><td><span class="badge text-bg-${tones[check.status] || 'secondary'}">${esc(labels[check.status] || 'Not established')}</span></td><td>${evidence(check)}</td>${riskColumn(check, index)}</tr>`).join('')}</tbody></table></div></details>`;
        // One device opens by default; with several, each opens on request.
        opened.apply(
          article.querySelector('details'),
          String(device.agent_id),
          devices.length === 1,
        );
        for (const button of article.querySelectorAll('[data-posture-risk]'))
          button.addEventListener('click', () =>
            riskForm(
              article.querySelector('details'),
              device,
              device.checks[Number(button.dataset.postureRisk)],
              button,
            ),
          );
        container.append(article);
      }
      host.querySelector('[data-posture-refresh]').addEventListener('click', load);
      const pages = host.querySelector('[data-posture-pages]');
      for (const [title, next] of [
        ['Previous devices', offset ? Math.max(0, offset - 20) : null],
        ['More devices', data.next_offset],
      ]) {
        if (next == null) continue;
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn-sm btn-outline-secondary';
        b.textContent = title;
        b.addEventListener('click', () => {
          offset = next;
          load();
        });
        pages.append(b);
      }
    } catch {
      if (request !== generation) return;
      host.innerHTML =
        '<div class="alert alert-warning" role="status">Workstation posture could not be loaded. <button type="button" class="btn btn-sm btn-outline-primary">Retry</button></div>';
      host.querySelector('button').addEventListener('click', load);
    }
  }
  return {
    select(value) {
      if (!allowed || !value) {
        person = null;
        generation++;
        host.hidden = true;
        host.replaceChildren();
        return;
      }
      if (person?.id === value.id) {
        person = value;
        return;
      }
      person = value;
      offset = 0;
      load();
    },
  };
}
