// A Vendor's Cloud posture tab: for each cloud provider linked to the vendor,
// every account's latest checks and identities, the vendor's recent activity,
// and risks recorded against failing checks or one subject within them.
import {
  apiGet,
  apiPost,
  errorText,
  esc,
  fmtTs,
  getCurrentFramework,
  withFramework,
} from '/app.js';
import { openState } from './platform.js';
import {
  linkedRisks,
  openPostureRiskForm,
  postureBadge,
  savedNotice,
} from './posture-risk-form.js';

const KINDS = { user: 'User', service: 'Service', root: 'Root', guest: 'Guest' };
const OUTCOMES = { success: 'Succeeded', failure: 'Failed', info: 'Recorded' };

function hours(seconds) {
  const value = Math.round(Number(seconds) / 3600);
  return value === 1 ? 'hour' : `${value} hours`;
}

function mfa(value) {
  return value === true ? 'On' : value === false ? 'Off' : '—';
}

function statusNotes(provider) {
  const notes = [];
  if (!provider.enabled)
    notes.push(
      `${esc(provider.label)} is turned off in the deployment's settings; the results below are from the last refresh.`,
    );
  if (provider.error)
    notes.push(
      `The last refresh${provider.attempted_at ? ` (${esc(fmtTs(provider.attempted_at))})` : ''} could not read ${esc(provider.label)}: ${esc(provider.error)}`,
    );
  if (provider.stale) notes.push('These results are older than two refresh intervals.');
  return notes.map((n) => `<p class="text-warning mb-1" data-cloud-warning>${n}</p>`).join('');
}

function activityTable(provider, framework) {
  const query = new URLSearchParams({ source: provider.provider });
  if (framework) query.set('framework', framework);
  const link = `<a href="/events.html?${esc(query.toString())}">View all ${esc(provider.label)} activity</a>`;
  const notes = [];
  if (provider.activity_error)
    notes.push(
      `<p class="text-warning mb-1">The last activity read failed: ${esc(provider.activity_error)}</p>`,
    );
  if (provider.activity_truncated)
    notes.push(
      '<p class="text-warning mb-1">The last activity read held more entries than one read keeps; the oldest in that window were skipped.</p>',
    );
  if (!provider.activity?.length)
    return `${notes.join('')}<p class="small-muted mb-0" data-cloud-activity>${provider.reads_activity ? 'No activity recorded yet.' : 'No activity recorded.'} ${link}</p>`;
  const rows = provider.activity
    .map(
      (a) =>
        `<tr><td class="text-nowrap"><a href="/event.html?id=${encodeURIComponent(a.id)}">${esc(fmtTs(a.timestamp))}</a></td><td>${esc(a.action)}</td><td class="text-break">${esc(a.actor || '—')}</td><td>${esc(OUTCOMES[a.outcome] || a.outcome || '—')}</td></tr>`,
    )
    .join('');
  return `${notes.join('')}<div class="table-responsive" data-cloud-activity><table class="table table-sm"><thead><tr><th>When</th><th>Action</th><th>By</th><th>Outcome</th></tr></thead><tbody>${rows}</tbody></table></div><p class="small mb-0">${link}</p>`;
}

export function cloudPosture(host, allowed) {
  const opened = openState();
  let vendor = null,
    generation = 0,
    owners = null;
  const ownerOptions = () => {
    owners ??= apiGet('/api/v1/risks/users').then((d) => d?.items || []);
    return owners;
  };
  const base = () => `/api/v1/isms/vendors/${encodeURIComponent(vendor.id)}/cloud-posture`;
  const notice = () => host.querySelector('[data-cloud-notice]');

  function riskForm({ container, provider, scope, check, subject = '', trigger }) {
    const where = `${provider.label} ${scope.kind || provider.scope_noun} ${scope.label} (${scope.key})`;
    openPostureRiskForm({
      container,
      heading: subject
        ? `Record a risk for ${subject} in ${check.label}`
        : `Record a risk for ${check.label} in ${scope.label}`,
      prefix:
        `cloud-risk-${provider.provider}-${scope.key}-${check.key}${subject ? '-subject' : ''}`.replace(
          /[^A-Za-z0-9_-]/g,
          '-',
        ),
      title: `${subject || scope.label}: ${check.label} needs attention`,
      summary: [
        subject
          ? `${subject} is listed by the ${check.label} check in ${where}.`
          : `The ${check.label} check needs attention in ${where}.`,
        check.detail,
      ]
        .filter(Boolean)
        .join('\n'),
      owners: ownerOptions,
      trigger,
      save: (fields) =>
        apiPost(base() + '/risks', {
          provider: provider.provider,
          scope_key: scope.key,
          check: check.key,
          subject_key: subject,
          ...fields,
        }),
      saved: async (created, framework) => {
        await load();
        savedNotice(notice(), created.risk_id, framework);
      },
    });
  }

  // The subjects one check lists, each with its own risks.
  function subjectList(cell, data, provider, scope, check) {
    const framework = getCurrentFramework();
    const more =
      check.subjects_total > check.subjects.length
        ? `<p class="small-muted mb-1">Showing ${check.subjects.length} of ${Number(check.subjects_total)}.</p>`
        : '';
    cell.innerHTML = `${more}<ul class="list-unstyled mb-0">${check.subjects
      .map((s, index) => {
        const accepted = s.risks?.some((r) => r.accepted);
        const action =
          data.can_create_risk && check.status !== 'met' && !accepted
            ? ` <button type="button" class="btn btn-sm btn-outline-primary ms-2" data-cloud-subject-risk="${index}">Create a risk</button>`
            : '';
        return `<li class="mb-1"><span class="mono text-break">${esc(s.name)}</span>${action}${linkedRisks(s.risks, framework)}</li>`;
      })
      .join('')}</ul>`;
    for (const button of cell.querySelectorAll('[data-cloud-subject-risk]'))
      button.addEventListener('click', () =>
        riskForm({
          container: cell,
          provider,
          scope,
          check,
          subject: check.subjects[Number(button.dataset.cloudSubjectRisk)].name,
          trigger: button,
        }),
      );
  }

  function identitiesTable(scope, framework) {
    if (!scope.identities?.length) return '';
    const rows = scope.identities
      .map((i) => {
        const person = i.person
          ? `<div><a href="${esc(withFramework('/person.html?id=' + encodeURIComponent(i.person.id), framework))}">${esc(i.person.name)}</a></div>`
          : '';
        const email =
          i.email && i.email !== i.name ? `<div class="small-muted">${esc(i.email)}</div>` : '';
        return `<tr><th scope="row" class="text-break">${esc(i.name)}${email}${person}</th><td>${esc(KINDS[i.kind] || 'User')}</td><td>${i.admin ? 'Yes' : 'No'}</td><td>${mfa(i.mfa)}</td><td class="text-nowrap">${esc(i.last_used ? fmtTs(i.last_used) : 'Never')}</td><td>${esc(i.detail || '')}</td></tr>`;
      })
      .join('');
    return `<details class="mt-2" data-cloud-identities><summary>${Number(scope.identities.length)} identities listed by the vendor</summary><div class="table-responsive mt-2"><table class="table table-sm"><thead><tr><th>Identity</th><th>Kind</th><th>Administrator</th><th>MFA</th><th>Last used</th><th>Access</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
  }

  function renderScope(data, provider, scope, single) {
    const framework = getCurrentFramework();
    const article = document.createElement('article');
    article.className = 'border-top pt-3 mt-3';
    const showRisks = data.can_create_risk || scope.checks.some((c) => c.risks?.length);
    const rows = scope.checks
      .map((check, index) => {
        const evidence = check.event_id
          ? `<a href="/event.html?id=${encodeURIComponent(check.event_id)}">${esc(fmtTs(check.observed_at))}</a>`
          : '—';
        const detail = check.detail || check.reason || '—';
        const listed = check.subjects.length
          ? `<div><button type="button" class="btn btn-sm btn-link p-0" aria-expanded="false" data-cloud-subjects="${index}">${Number(check.subjects_total)} listed</button></div>`
          : '';
        const risks = showRisks
          ? `<td>${linkedRisks(check.risks, framework)}${data.can_create_risk && check.status === 'attention' ? `<button type="button" class="btn btn-sm btn-outline-primary" data-cloud-risk="${index}">Create a risk</button>` : ''}</td>`
          : '';
        return `<tr><th scope="row">${esc(check.label)}</th><td>${postureBadge(check.status)}</td><td>${esc(detail)}${listed}</td><td class="text-nowrap">${evidence}</td>${risks}</tr><tr data-cloud-subject-row="${index}" hidden><td colspan="${showRisks ? 5 : 4}"></td></tr>`;
      })
      .join('');
    const key =
      scope.label !== scope.key ? `<span class="mono small-muted">${esc(scope.key)}</span>` : '';
    article.innerHTML = `<details class="collapsible-card" data-cloud-scope><summary class="d-flex align-items-center gap-2"><i class="bi bi-chevron-right collapse-chevron" aria-hidden="true"></i><i class="bi bi-cloud" aria-hidden="true"></i><h4 class="h6 mb-0">${esc(scope.label)}</h4>${key}<span class="badge badge-soft">${Number(scope.met)} / ${Number(scope.judged)} met</span></summary><p class="small-muted mt-2">Checked ${esc(fmtTs(scope.checked_at))}</p><div class="table-responsive"><table class="table table-sm"><thead><tr><th>Check</th><th>Result</th><th>Detail</th><th>Evidence</th>${showRisks ? '<th>Risks</th>' : ''}</tr></thead><tbody>${rows}</tbody></table></div>${identitiesTable(scope, framework)}</details>`;
    const details = article.querySelector('details');
    opened.apply(details, `${provider.provider}:${scope.key}`, single);
    for (const button of article.querySelectorAll('[data-cloud-risk]'))
      button.addEventListener('click', () =>
        riskForm({
          container: details,
          provider,
          scope,
          check: scope.checks[Number(button.dataset.cloudRisk)],
          trigger: button,
        }),
      );
    for (const button of article.querySelectorAll('[data-cloud-subjects]'))
      button.addEventListener('click', () => {
        const index = button.dataset.cloudSubjects;
        const row = article.querySelector(`[data-cloud-subject-row="${index}"]`);
        const open = row.hidden;
        row.hidden = !open;
        button.setAttribute('aria-expanded', String(open));
        if (open) subjectList(row.firstElementChild, data, provider, scope, scope.checks[index]);
      });
    return article;
  }

  async function refresh(button) {
    button.disabled = true;
    const box = notice();
    try {
      await apiPost(base() + '/refresh', {});
      box.className = 'alert alert-info';
      box.textContent =
        'Refresh queued. The results update when the read finishes; reload this tab in a few minutes.';
    } catch (error) {
      box.className = 'alert alert-warning';
      box.textContent = errorText(error) || 'The refresh could not be queued.';
    }
    box.hidden = false;
    button.disabled = false;
  }

  async function load() {
    const request = ++generation;
    host.hidden = false;
    host.innerHTML =
      '<div class="card"><div class="card-body" role="status">Loading cloud posture…</div></div>';
    let data;
    try {
      data = await apiGet(base());
    } catch {
      if (request !== generation) return;
      host.innerHTML =
        '<div class="alert alert-warning" role="status">Cloud posture could not be loaded. <button type="button" class="btn btn-sm btn-outline-primary">Retry</button></div>';
      host.querySelector('button').addEventListener('click', load);
      return;
    }
    if (request !== generation) return;
    const framework = getCurrentFramework();
    const providers = data.providers || [];
    const scopes = providers.flatMap((p) => p.scopes);
    const button = data.can_refresh
      ? '<button type="button" class="btn btn-sm btn-outline-primary" data-cloud-refresh>Refresh now</button>'
      : '';
    host.innerHTML = `<div class="card"><div class="card-header d-flex justify-content-between align-items-center gap-2"><h2 class="h5 mb-0">Cloud posture · ${esc(vendor.name)}</h2>${button}</div><div class="card-body"><p class="small-muted">KEEN reads these settings with read-only access every ${hours(data.interval_seconds)} and records each result, and the vendor's activity, as evidence that mapping rules can attach to controls.</p><div class="alert alert-success" role="status" data-cloud-notice hidden></div><div data-cloud-providers></div></div></div>`;
    host
      .querySelector('[data-cloud-refresh]')
      ?.addEventListener('click', (event) => refresh(event.currentTarget));
    const container = host.querySelector('[data-cloud-providers]');
    if (!providers.length)
      container.textContent = 'No cloud provider shows its posture on this vendor.';
    for (const provider of providers) {
      const section = document.createElement('section');
      section.className = 'mt-3';
      section.dataset.cloudProvider = provider.provider;
      const checked = provider.checked_at
        ? `Last refreshed ${esc(fmtTs(provider.checked_at))}`
        : 'Not refreshed yet';
      section.innerHTML = `<h3 class="h6 mb-1">${esc(provider.label)}</h3><p class="small-muted mb-1">${checked}${provider.scopes.length ? '' : ` · No ${esc(provider.scope_noun)}s read yet`}</p>${statusNotes(provider)}<div data-cloud-scopes></div><h4 class="h6 mt-4">Recent activity</h4>${activityTable(provider, framework)}`;
      const list = section.querySelector('[data-cloud-scopes]');
      for (const scope of provider.scopes)
        list.append(renderScope(data, provider, scope, scopes.length === 1));
      container.append(section);
    }
  }

  return {
    select(value) {
      if (!allowed || !value) {
        vendor = null;
        generation++;
        host.hidden = true;
        host.replaceChildren();
        return;
      }
      if (vendor?.id === value.id) {
        vendor = value;
        return;
      }
      vendor = value;
      load();
    },
  };
}
