import { enrollmentProfiles } from './agent-enrollment.js';
import { openState, platformIcon } from './platform.js';
import { fmtTs, apiGet, apiPost, apiPatch, initNavbar, confirmAction, esc } from '/app.js';
const root = '/api/v1/admin/agents',
  $ = (id) => document.getElementById('ka-' + id);
let people = [];
const opened = openState();
let showRevoked = false;
function personOptions(selected = '') {
  return (
    '<option value="">Choose a person</option>' +
    people
      .map(
        (p) =>
          `<option value="${esc(p.id)}" ${p.id === selected ? 'selected' : ''}>${esc(p.name)}</option>`,
      )
      .join('')
  );
}
function registrationType() {
  const workstation = $('type').value === 'workstation';
  $('person-field').hidden = !workstation;
  $('person').disabled = !workstation;
  $('person').required = workstation;
  if (!workstation) $('person').value = '';
}
$('type').addEventListener('change', registrationType);
let demo = true,
  loading = false;
const profileHost = document.createElement('section');
$('agents').before(profileHost);
const profiles = enrollmentProfiles(profileHost, () => load());
const endpoint = location.origin + '/api/v1/otlp/logs';
$('endpoint').textContent = endpoint;
function msg(text, tone = 'info') {
  $('status').textContent = text;
  $('status').className = 'alert alert-' + tone;
  $('status').hidden = !text;
}
function fail(e) {
  let text = String(e.message || e);
  try {
    const d = JSON.parse(text).detail;
    text = typeof d === 'string' ? d : 'Request failed; check the entered values.';
  } catch {}
  msg(text, 'danger');
}
function date(v) {
  return v ? fmtTs(v) : 'Never';
}
function dismiss() {
  $('token').value = '';
  $('token').type = 'password';
  $('credential').hidden = true;
  $('reveal').textContent = 'Show credential';
}
function credential(r) {
  dismiss();
  const code = Boolean(r.enrollment_code);
  $('token-label').textContent = code ? 'Enrolment code' : 'Agent credential';
  $('credential-help').textContent = code
    ? 'Keen displays it once. It enrols one Keen Agent within 24 hours and cannot be used again. Issuing a new code leaves the current key working until the new code is used.'
    : "Keen displays it once. Save it to the collector's protected configuration. Rotating it immediately invalidates the previous one.";
  $('token').value = code ? r.enrollment_code : r.token;
  $('credential').hidden = false;
  $('credential').scrollIntoView({ behavior: 'smooth', block: 'center' });
  $('copy').focus();
}
async function guard(button, fn) {
  button.disabled = true;
  try {
    await fn();
  } catch (e) {
    fail(e);
  } finally {
    button.disabled = demo;
  }
}
function button(label, fn) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'btn btn-outline-primary btn-sm';
  b.textContent = label;
  b.disabled = demo;
  b.onclick = () => guard(b, fn);
  return b;
}
const modeLabels = {
  events: 'Recognised events only',
  events_with_lines: 'Events with their source lines',
  all_lines: 'All log lines',
};
const subjectLabels = {
  pairwise: 'Pairwise (differ between devices)',
  public: 'Public (match across devices)',
};
function select(id, label, options, value) {
  const wrap = document.createElement('div');
  wrap.className = 'col-md-4';
  const l = document.createElement('label');
  l.className = 'form-label';
  l.htmlFor = id;
  l.textContent = label;
  const s = document.createElement('select');
  s.className = 'form-select';
  s.id = id;
  for (const [key, text] of Object.entries(options))
    s.add(new Option(text, key, false, key === value));
  wrap.append(l, s);
  return [wrap, s];
}
function evidencePolicy(a) {
  const section = document.createElement('div');
  section.className = 'border-top pt-3 mt-3';
  const help = document.createElement('p');
  help.className = 'small-muted';
  help.textContent =
    'Keen applies the evidence mode limit to everything it receives from now on. The agent learns both settings at its next enrolment or key renewal.';
  const row = document.createElement('div');
  row.className = 'row g-2 align-items-end';
  const [modeWrap, mode] = select(
    `agent-mode-${a.id}`,
    'Evidence mode limit',
    modeLabels,
    a.evidence_mode_ceiling,
  );
  const [subjectWrap, subject] = select(
    `agent-subject-${a.id}`,
    'Account pseudonyms',
    subjectLabels,
    a.subject_type,
  );
  const actions = document.createElement('div');
  actions.className = 'col-12 col-md-auto';
  actions.append(
    button('Save evidence settings', async () => {
      await apiPatch(root + '/' + a.id + '/evidence', {
        evidence_mode_ceiling: mode.value,
        subject_type: subject.value,
      });
      msg('Evidence settings saved.', 'success');
      await load();
    }),
  );
  row.append(modeWrap, subjectWrap, actions);
  section.append(help, row);
  return section;
}
async function load() {
  if (loading) return;
  loading = true;
  try {
    const data = await apiGet(root);
    demo = data.demo_mode;
    if (data.server_key_fingerprint) $('fingerprint').textContent = data.server_key_fingerprint;
    people = data.people || [];
    const selected = $('person').value;
    $('person').innerHTML = personOptions(selected);
    $('type').querySelector('[value="workstation"]').disabled = data.people_enabled === false;
    if (data.people_enabled === false) $('type').value = 'server';
    registrationType();
    $('register-button').disabled = demo;
    if (demo)
      msg(
        'You are running in a demo environment. Agent registration and external log ingestion are disabled. Use your own Keen installation to connect servers.',
      );
    const profileNames = await profiles.refresh(demo);
    $('agents').replaceChildren();
    const revoked = data.agents.filter((a) => !a.enabled).length,
      active = data.agents.length - revoked;
    if (!data.agents.length) {
      const p = document.createElement('p');
      p.textContent = 'No agents registered yet.';
      $('agents').append(p);
    } else {
      const bar = document.createElement('div');
      bar.className = 'd-flex gap-2 justify-content-end align-items-center flex-wrap';
      if (revoked) {
        const label = document.createElement('label');
        label.className = 'form-check form-check-inline me-auto mb-0';
        label.innerHTML = `<input class="form-check-input" type="checkbox" id="show-revoked"> <span class="form-check-label">Show revoked agents (${revoked})</span>`;
        const box = label.querySelector('input');
        box.checked = showRevoked;
        box.addEventListener('change', () => {
          showRevoked = box.checked;
          for (const card of $('agents').querySelectorAll('[data-revoked]'))
            card.hidden = !showRevoked;
        });
        bar.append(label);
      }
      if (data.agents.length > 1) {
        for (const [label, open] of [
          ['Expand all', true],
          ['Collapse all', false],
        ]) {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'btn btn-sm btn-outline-secondary';
          b.textContent = label;
          b.addEventListener('click', () => {
            for (const d of $('agents').querySelectorAll(
              'article:not([hidden]) details[data-agent]',
            ))
              d.open = open;
          });
          bar.append(b);
        }
      }
      $('agents').append(bar);
      if (!active) {
        const p = document.createElement('p');
        p.className = 'small-muted';
        p.textContent = 'No active agents.';
        $('agents').append(p);
      }
    }
    for (const a of data.agents) {
      const expired = new Date(a.expires_at + 'Z') < new Date(),
        stale = !a.last_seen || Date.now() - new Date(a.last_seen + 'Z') > 5 * 60000;
      const h = a.health || {},
        status = !a.enabled
          ? 'Revoked'
          : expired
            ? 'Credential expired'
            : h.delivery_blocked
              ? 'Delivery paused'
              : stale
                ? 'Awaiting contact'
                : 'Connected';
      const bad = !a.enabled || expired || h.delivery_blocked;
      const card = document.createElement('article');
      card.className = 'card';
      card.innerHTML = `<details class="collapsible-card" data-agent="${esc(a.id)}"><summary class="card-header d-flex align-items-center justify-content-between flex-wrap gap-2"><span class="d-flex align-items-center gap-2"><i class="bi bi-chevron-right collapse-chevron" aria-hidden="true"></i>${platformIcon(h.platform)}<h2 class="h5 mb-0">${esc(a.name)}</h2></span><span class="badge d-inline-flex align-items-center justify-content-center py-2 px-3 lh-sm ${bad ? 'text-bg-danger' : stale ? 'text-bg-secondary' : 'text-bg-success'}">${esc(status)}</span></summary><div class="card-body"><div class="small-muted text-break">${esc(a.id)}</div><div class="row g-2 my-2"><div class="col-md-6">Last contact: ${esc(date(a.last_seen))}</div><div class="col-md-6">Credential expiry: ${esc(date(a.expires_at))}</div><div class="col-md-6">Queued: ${Number(h.queued || 0)} · Queue bytes: ${Number(h.queue_bytes || 0)}</div><div class="col-md-6">Dropped records / detected gaps: ${Number(h.rejected || 0)}</div><div class="col-md-6">Credential: ${a.credential_type === 'key' ? 'Signing key' : 'Bearer token'}${a.enrollment_code_expires_at ? ` · enrolment code valid until ${esc(date(a.enrollment_code_expires_at))}` : ''}</div><div class="col-md-6">Evidence chain position: ${Number(h.chain_sequence || 0)}${h.evidence_mode_clamped ? ' · a source asks for more than the limit allows' : ''}</div></div><p class="small-muted">Health report: ${esc(date(h.health_received_at))} · Agent ${esc(h.version || 'version not reported')}</p><div class="agent-sources small mb-3"></div><div class="agent-actions d-flex gap-2 flex-wrap"></div></div></details>`;
      // Revoked agents stay hidden unless asked for.
      if (!a.enabled) {
        card.dataset.revoked = '';
        card.hidden = !showRevoked;
      }
      // A single active agent opens by default; with several, each opens on request.
      opened.apply(card.querySelector('details'), a.id, active === 1 && a.enabled);
      const provenance = document.createElement('p');
      provenance.className = 'small-muted';
      provenance.textContent = a.enrollment_profile_id
        ? 'Enrollment profile: ' +
          (profileNames.get(a.enrollment_profile_id) || a.enrollment_profile_id)
        : 'Individually registered';
      card.querySelector('.card-body').append(provenance);
      const sources = card.querySelector('.agent-sources');
      for (const [name, state] of Object.entries(h.sources || {})) {
        const p = document.createElement('p');
        p.className = 'mb-1 text-break';
        p.textContent = name + ': ' + state;
        sources.append(p);
      }
      if (a.enabled && !demo)
        card.querySelector('.agent-actions').append(
          button(
            a.credential_type === 'key' ? 'Issue enrolment code' : 'Rotate credential',
            async () => {
              if (
                !(await confirmAction(
                  a.credential_type === 'key'
                    ? {
                        title: 'Issue enrolment code',
                        message:
                          'Issue a single-use enrolment code for ' +
                          a.name +
                          '? The current key keeps working until the code enrols a new key, which replaces it.',
                        confirmLabel: 'Issue code',
                      }
                    : {
                        title: 'Rotate agent credential',
                        message:
                          'Invalidate the current credential and issue a replacement for ' +
                          a.name +
                          '? Collectors using the current credential stop reporting until they are re-issued it.',
                        confirmLabel: 'Rotate',
                        typed: 'Rotate',
                      },
                ))
              )
                return;
              const r = await apiPost(root + '/' + a.id + '/rotate', {
                expires_days: Number($('expiry').value),
              });
              credential(r);
              await load();
            },
          ),
          ...(a.credential_type === 'token'
            ? [
                button('Move to key', async () => {
                  if (
                    !(await confirmAction({
                      title: 'Move agent to key enrolment',
                      message:
                        'Issue a single-use enrolment code for ' +
                        a.name +
                        '? Its token stops working now; a Keen Agent 0.2 or later enrols its own key with the code and sends signed evidence.',
                      confirmLabel: 'Issue code',
                      typed: 'Move',
                    }))
                  )
                    return;
                  const r = await apiPost(root + '/' + a.id + '/rotate', {
                    expires_days: Number($('expiry').value),
                    credential_type: 'key',
                  });
                  credential(r);
                  await load();
                }),
              ]
            : []),
          button('Revoke', async () => {
            if (
              !(await confirmAction({
                title: 'Revoke agent',
                message:
                  'Permanently revoke ' +
                  a.name +
                  '? The agent loses access for good; existing evidence remains available.',
                confirmLabel: 'Revoke',
                danger: true,
              }))
            )
              return;
            await apiPost(root + '/' + a.id + '/revoke', {});
            dismiss();
            await load();
          }),
        );
      const filtered = document.createElement('p');
      filtered.className = 'small-muted';
      filtered.textContent =
        'Filtered before delivery: ' +
        Object.entries(h.filtered_by_source || {})
          .map(([source, count]) => source + ': ' + count)
          .join(' · ');
      if (Object.keys(h.filtered_by_source || {}).length)
        card.querySelector('.card-body').append(filtered);
      const association = document.createElement('div');
      association.className = 'border-top pt-3 mt-3';
      const owner = people.find((p) => p.id === a.person_id);
      const label = document.createElement('p');
      label.textContent =
        a.agent_type === 'workstation'
          ? `Workstation · ${owner?.name || 'Person unassigned'}`
          : 'Server';
      association.append(label);
      if (!demo) {
        const typeId = `agent-type-${a.id}`,
          personId = `agent-person-${a.id}`;
        const controls = document.createElement('div');
        controls.className = 'row g-2 align-items-end';
        controls.innerHTML = `<div class="col-md-4"><label class="form-label" for="${esc(typeId)}">Device type</label><select class="form-select" id="${esc(typeId)}"><option value="server">Server</option><option value="workstation" ${data.people_enabled === false ? 'disabled' : ''}>Workstation</option></select></div><div class="col-md-5 agent-person-field"><label class="form-label" for="${esc(personId)}">Person</label><select class="form-select" id="${esc(personId)}">${personOptions(a.person_id)}</select></div>`;
        const type = controls.querySelector(`#${CSS.escape(typeId)}`),
          person = controls.querySelector(`#${CSS.escape(personId)}`);
        type.value = a.agent_type || 'server';
        const change = () => {
          person.disabled = type.value !== 'workstation';
          controls.querySelector('.agent-person-field').hidden = person.disabled;
        };
        type.addEventListener('change', change);
        change();
        const save = button('Save assignment', async () => {
          if (type.value === 'workstation' && !person.value) throw new Error('Choose a person.');
          if (
            type.value === a.agent_type &&
            (person.disabled ? null : person.value) === a.person_id
          )
            return;
          if (
            !(await confirmAction({
              title: 'Change device assignment',
              message:
                'The workstation overview will start with new reports. Existing evidence remains available.',
              confirmLabel: 'Save assignment',
            }))
          )
            return;
          await apiPatch(root + '/' + a.id, {
            agent_type: type.value,
            person_id: person.disabled ? null : person.value,
          });
          await load();
        });
        const actions = document.createElement('div');
        actions.className = 'col-12 col-md-auto';
        actions.append(save);
        controls.append(actions);
        association.append(controls);
      }
      card.querySelector('.card-body').append(association);
      if (!demo && a.enabled) card.querySelector('.card-body').append(evidencePolicy(a));
      $('agents').append(card);
    }
  } finally {
    loading = false;
  }
}
$('register').onsubmit = (e) => {
  e.preventDefault();
  guard($('register-button'), async () => {
    const r = await apiPost(root, {
      name: $('agent-name').value,
      agent_type: $('type').value,
      person_id: $('type').value === 'workstation' ? $('person').value : null,
      expires_days: Number($('expiry').value),
      credential_type: $('credential-type').value,
      evidence_mode_ceiling: $('mode').value,
      subject_type: $('subject').value,
    });
    $('agent-name').value = '';
    credential(r);
    await load();
  });
};
$('copy').onclick = async () => {
  try {
    await navigator.clipboard.writeText($('token').value);
    msg(
      'Credential copied. Save it to the protected file the agent or collector reads.',
      'success',
    );
  } catch {
    msg('Clipboard unavailable. Use Show credential and copy it manually.', 'warning');
  }
};
$('reveal').onclick = () => {
  const show = $('token').type === 'password';
  $('token').type = show ? 'text' : 'password';
  $('reveal').textContent = show ? 'Hide credential' : 'Show credential';
};
$('dismiss').onclick = dismiss;
window.addEventListener('pagehide', dismiss);
document.getElementById('tab-agents')?.addEventListener('hidden.bs.tab', dismiss);
$('refresh').onclick = () => load().catch(fail);
$('yaml').onclick = () => {
  const text = `endpoint: ${JSON.stringify(endpoint)}\nenrollment:\n  code_file: /etc/keen-agent/enrollment-code\nstate_dir: /var/lib/keen-agent\nqueue_mb: 256\npoll_seconds: 5\nretention_days: 0\ninitial_lookback: 24h\n# events, events_with_lines or all_lines; Keen enforces the identity's limit.\nevidence_mode: events\nsources:\n  - name: system\n    kind: journal\n    parser: syslog\n`;
  const url = URL.createObjectURL(new Blob([text], { type: 'application/yaml' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = 'keen-agent.yaml';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
if (!document.getElementById('pane-agents')) await initNavbar();
await load().catch(fail);
setInterval(() => {
  if (!document.hidden) load().catch(fail);
}, 30000);
