import { apiGet, esc, fmtTs } from '/app.js';
const labels = {
  met: 'Met',
  attention: 'Needs attention',
  unknown: 'Not established',
  stale: 'Stale',
  external: 'Managed externally',
  not_collected: 'Not collected',
};
const tones = {
  met: 'success',
  attention: 'warning',
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
export function workstationPosture(host, allowed) {
  let person = null,
    generation = 0,
    offset = 0;
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
      host.innerHTML = `<div class="card"><div class="card-header d-flex justify-content-between align-items-center gap-2"><h2 class="h5 mb-0">Workstation posture · ${esc(person.name)}</h2><button type="button" class="btn btn-sm btn-outline-primary" data-posture-refresh>Refresh</button></div><div class="card-body"><p><strong>${Number(met)} / ${Number(target)} checks met</strong> across ${devices.length} devices on this page · ${Number(data.total)} devices linked.</p><p class="small-muted">Targets: encrypted disks, automatic screen locking, an antivirus component, an enabled firewall and automatic update installation. Reports stay current for ${Number(data.freshness_hours)} hours. Results describe the collected scopes; they are reported by the device. ${esc(targets(data.targets))} Rules: ${esc(data.rules_version || '')}.</p><div data-posture-devices></div><div class="d-flex gap-2 mt-3" data-posture-pages></div></div></div>`;
      const container = host.querySelector('[data-posture-devices]');
      if (!devices.length)
        container.textContent =
          'No workstation agents linked. Choose Workstation and this person when registering an agent in Admin.';
      for (const device of devices) {
        const article = document.createElement('article');
        article.className = 'border-top pt-3 mt-3';
        article.innerHTML = `<h3 class="h6">${esc(device.hostname)} <span class="badge badge-soft">${Number(device.met)} / ${Number(device.target)}</span></h3><p class="small-muted">${device.active ? 'Active credential' : 'Credential revoked or expired'} · Last contact: ${esc(device.last_seen ? fmtTs(device.last_seen) : 'Never')}</p>${device.overflow ? '<p class="text-warning">Too many observation scopes to establish device completion. Review the agent configuration.</p>' : ''}<div class="table-responsive"><table class="table table-sm"><thead><tr><th>Check</th><th>Result</th><th>Latest evidence by scope</th></tr></thead><tbody>${device.checks.map((check) => `<tr><th scope="row">${esc(check.label)}</th><td><span class="badge text-bg-${tones[check.status] || 'secondary'}">${esc(labels[check.status] || 'Not established')}</span></td><td>${check.observations.length ? check.observations.map((o) => `<div class="mb-1"><a href="/event.html?id=${encodeURIComponent(o.event_id)}">${esc(o.provider)} · ${esc(o.scope)}</a> · ${esc(fmtTs(o.observed_at))} · ${esc(labels[o.status] || 'Not established')}<div class="small-muted">${esc(o.reason.replaceAll('_', ' '))}${o.detail ? ` · ${esc(o.detail)}` : ''}</div></div>`).join('') : 'No current evidence'}</td></tr>`).join('')}</tbody></table></div>`;
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
