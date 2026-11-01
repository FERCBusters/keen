import { apiGet, apiPost, esc, fmtTs, getCurrentFramework } from '/app.js';
import { openState, platformIcon } from './platform.js';
import {
  OPERATORS,
  POSTURE_LABELS as labels,
  linkedRisks,
  openPostureRiskForm,
  postureBadge,
  savedNotice,
} from './posture-risk-form.js';
const targetLabels = { screen_lock: 'Screen lock', antivirus: 'Antivirus signature age' };
function targets(value) {
  const entries = Object.entries(value || {});
  if (!entries.length)
    return 'No organisation targets are set, so configured protections count as met.';
  return `Organisation targets: ${entries
    .map(
      ([check, t]) =>
        `${targetLabels[check] || check} ${OPERATORS[t.operator] || t.operator} ${t.value} ${t.unit}`,
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
export function workstationPosture(host, allowed) {
  const opened = openState();
  let person = null,
    generation = 0,
    offset = 0,
    owners = null,
    // Revoked devices keep their evidence; they are left out of the list unless asked for.
    showRevoked = false,
    toggled = false;
  async function ownerOptions() {
    owners ??= apiGet('/api/v1/risks/users').then((d) => d?.items || []);
    return owners;
  }
  // An inline editor below the device's checks, as other Person tabs use.
  function riskForm(article, device, check, trigger) {
    openPostureRiskForm({
      container: article,
      heading: `Record a risk for ${check.label} on ${device.hostname}`,
      prefix: `posture-risk-${device.agent_id}-${check.check}`,
      title: `${device.hostname}: ${check.label} needs attention`,
      summary: [
        `The ${check.label} check needs attention on ${device.hostname}.`,
        ...(check.observations || [])
          .filter((o) => o.status === 'attention')
          .map((o) =>
            [o.provider, o.scope, String(o.reason || '').replaceAll('_', ' '), o.detail]
              .filter(Boolean)
              .join(' · '),
          ),
      ].join('\n'),
      owners: ownerOptions,
      trigger,
      save: (fields) =>
        apiPost(`/api/v1/isms/people/${encodeURIComponent(person.id)}/workstation-posture/risks`, {
          agent_id: device.agent_id,
          check: check.check,
          ...fields,
        }),
      saved: async (created, framework) => {
        await load();
        savedNotice(host.querySelector('[data-posture-notice]'), created.risk_id, framework);
      },
    });
  }
  async function load() {
    const request = ++generation;
    host.hidden = false;
    host.innerHTML =
      '<div class="card"><div class="card-body" role="status">Loading workstation posture…</div></div>';
    try {
      const data = await apiGet(
        `/api/v1/isms/people/${encodeURIComponent(person.id)}/workstation-posture?offset=${offset}&include_revoked=${showRevoked}`,
      );
      if (request !== generation) return;
      const devices = data.devices || [];
      const met = devices.reduce((n, d) => n + d.met, 0),
        target = devices.reduce((n, d) => n + d.target, 0);
      const revoked = Number(data.revoked_total) || 0,
        hidden = !showRevoked && revoked;
      const hiddenNote = hidden
        ? ` · ${revoked} revoked ${revoked === 1 ? 'device' : 'devices'} hidden`
        : '';
      host.innerHTML = `<div class="card"><div class="card-header d-flex justify-content-between align-items-center gap-2"><h2 class="h5 mb-0">Workstation posture · ${esc(person.name)}</h2><button type="button" class="btn btn-sm btn-outline-primary" data-posture-refresh>Refresh</button></div><div class="card-body"><p><strong>${Number(met)} / ${Number(target)} checks met</strong> across ${devices.length} devices on this page · ${Number(data.total)} devices linked${hiddenNote}.</p><p class="small-muted">Targets: encrypted disks, automatic screen locking, an antivirus component, an enabled firewall and automatic update installation. Reports stay current for ${Number(data.freshness_hours)} hours. Results describe the collected scopes; they are reported by the device. ${esc(targets(data.targets))} Rules: ${esc(data.rules_version || '')}.</p>${revoked ? `<div class="form-check mb-2"><input class="form-check-input" type="checkbox" id="posture-show-revoked" data-posture-show-revoked${showRevoked ? ' checked' : ''}><label class="form-check-label" for="posture-show-revoked">Show revoked agents (${revoked})</label></div>` : ''}<div class="alert alert-success" role="status" data-posture-notice hidden></div><div data-posture-devices></div><div class="d-flex gap-2 mt-3" data-posture-pages></div></div></div>`;
      const container = host.querySelector('[data-posture-devices]');
      if (!devices.length)
        container.textContent = hidden
          ? 'Every device linked to this person is revoked. Tick Show revoked agents to review them.'
          : 'No workstation agents linked. Choose Workstation and this person when registering an agent in Admin.';
      for (const device of devices) {
        const article = document.createElement('article');
        article.className = 'border-top pt-3 mt-3';
        const framework = getCurrentFramework();
        const showRisks =
          data.can_create_risk || device.checks.some((check) => check.risks?.length);
        const riskColumn = (check, index) =>
          showRisks
            ? `<td>${linkedRisks(check.risks, framework)}${data.can_create_risk && check.status === 'attention' ? `<button type="button" class="btn btn-sm btn-outline-primary" data-posture-risk="${index}">Create a risk</button>` : ''}</td>`
            : '';
        article.innerHTML = `<details class="collapsible-card" data-posture-device><summary class="d-flex align-items-center gap-2"><i class="bi bi-chevron-right collapse-chevron" aria-hidden="true"></i>${platformIcon(device.platform)}<h3 class="h6 mb-0">${esc(device.hostname)}</h3><span class="badge badge-soft">${Number(device.met)} / ${Number(device.target)}</span></summary><p class="small-muted mt-2">${device.active ? 'Active credential' : device.revoked ? 'Credential revoked' : 'Credential expired'} · Last contact: ${esc(device.last_seen ? fmtTs(device.last_seen) : 'Never')}</p>${device.overflow ? '<p class="text-warning">Too many observation scopes to establish device completion. Review the agent configuration.</p>' : ''}<div class="table-responsive"><table class="table table-sm"><thead><tr><th>Check</th><th>Result</th><th>Latest evidence by scope</th>${showRisks ? '<th>Risks</th>' : ''}</tr></thead><tbody>${device.checks.map((check, index) => `<tr><th scope="row">${esc(check.label)}</th><td>${postureBadge(check.status)}</td><td>${evidence(check)}</td>${riskColumn(check, index)}</tr>`).join('')}</tbody></table></div></details>`;
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
      const show = host.querySelector('[data-posture-show-revoked]');
      show?.addEventListener('change', () => {
        showRevoked = show.checked;
        toggled = true;
        offset = 0;
        load();
      });
      // The list is drawn afresh, so focus returns to the box that redrew it.
      if (toggled) show?.focus();
      toggled = false;
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
      toggled = false;
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
