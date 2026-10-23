import { apiGet, apiPost, apiPut, esc, toast } from '/app.js';

export function initEvidenceRetention(isAdmin) {
  if (!isAdmin) return;
  const $ = (id) => document.getElementById(`retention-${id}`);
  const endpoint = '/api/v1/admin/evidence-retention';
  let policyUpdatedAt = null;
  let choices = { source: [], rule: [] };
  let savedOverrides = [];
  $('add').disabled = true;
  let loaded = false,
    fetching = false,
    busy = false,
    activeJob = false;
  function explain() {
    const mode = $('mode').value;
    $('value-wrap').hidden = mode === 'disabled';
    $('value').required = mode !== 'disabled';
    $('value').max = mode === 'age' ? '36500' : '1000000000';
    $('value-label').textContent =
      mode === 'age' ? 'Maximum age (days)' : 'Newest unsampled events to keep';
    $('explanation').textContent =
      mode === 'age'
        ? 'Age is measured from the event timestamp in UTC. Late-arriving old events are eligible at the next cleanup. Audit-protected events are always kept.'
        : mode === 'count'
          ? 'Keeps the newest N unsampled events by event timestamp, plus all audit-protected events. Retained artifact references can also temporarily keep older events.'
          : 'The global group keeps everything. Source and rule overrides can still delete events. Pending stored-file cleanup continues.';
    $('preview-result').textContent = '';
  }
  function addOverride(item = { scope: 'source', key: '', mode: 'disabled', value: null }) {
    const row = document.createElement('div');
    row.className = 'row g-2 mb-2';
    row.innerHTML = `<div class="col-md-2"><select class="form-select" aria-label="Override scope"><option value="source">Source type</option><option value="rule">Rule ID</option></select></div>
      <div class="col-md-3"><select class="form-select" aria-label="Source type or rule" required></select></div>
      <div class="col-md-3"><select class="form-select" aria-label="Retention policy"><option value="disabled">Keep everything</option><option value="age">Maximum age (days)</option><option value="count">Newest events to keep</option></select></div>
      <div class="col-md-2"><input class="form-control" type="number" min="1" step="1" aria-label="Retention limit"></div>
      <div class="col-md-2"><button type="button" class="btn btn-outline-danger">Remove</button></div>`;
    const [scope, key, mode] = row.querySelectorAll('select');
    const value = row.querySelector('input');
    scope.value = item.scope;
    function populate(selected = '') {
      key.replaceChildren(
        new Option('Choose ' + (scope.value === 'rule' ? 'a rule' : 'a source type'), ''),
      );
      const options = new Map(
        (choices[scope.value] || []).map((option) => [option.key, option.label]),
      );
      for (const saved of savedOverrides)
        if (saved.scope === scope.value && !options.has(saved.key))
          options.set(saved.key, 'Unavailable - saved override');
      for (const [id, label] of options)
        key.add(new Option(label === id ? id : `${label} (${id})`, id));
      key.value = selected;
      $('preview-result').textContent = '';
    }
    populate(item.key);
    scope.addEventListener('change', () => populate());
    key.addEventListener('change', () => {
      $('preview-result').textContent = '';
    });
    mode.value = item.mode;
    value.value = item.value ?? '';
    function update() {
      value.hidden = mode.value === 'disabled';
      value.required = !value.hidden;
      value.max = mode.value === 'age' ? '36500' : '1000000000';
      $('preview-result').textContent = '';
    }
    mode.addEventListener('change', update);
    row.querySelector('button').addEventListener('click', () => {
      row.remove();
      $('preview-result').textContent = '';
    });
    row.readPolicy = () => ({
      scope: scope.value,
      key: key.value,
      mode: mode.value,
      value: mode.value === 'disabled' ? null : Number(value.value),
    });
    $('overrides').appendChild(row);
    update();
  }
  $('add').addEventListener('click', () => addOverride());
  function payload() {
    return {
      expected_updated_at: policyUpdatedAt,
      overrides: [...$('overrides').children].map((row) => row.readPolicy()),
      mode: $('mode').value,
      value: $('mode').value === 'disabled' ? null : Number($('value').value),
    };
  }
  function summary(p) {
    return `${p.eligible_events.toLocaleString()} events currently eligible; ${p.audit_protected_events.toLocaleString()} audit-protected; ${p.total_events.toLocaleString()} events in total.`;
  }
  async function refresh(reset = false) {
    if (fetching) return;
    fetching = true;
    try {
      const data = await apiGet(endpoint);
      if (!loaded || reset) {
        choices = data.choices || { source: [], rule: [] };
        savedOverrides = data.overrides || [];
        $('add').disabled = false;
        policyUpdatedAt = data.policy.updated_at;
        $('mode').value = data.policy.mode;
        $('value').value = data.policy.value ?? '';
        explain();
        $('overrides').replaceChildren();
        for (const item of data.overrides || []) addOverride(item);
        const params = new URLSearchParams(location.search);
        const rule = params.get('retention_rule'),
          source = params.get('retention_source');
        const key = rule || source,
          scope = rule ? 'rule' : 'source';
        if (
          !loaded &&
          key &&
          (choices[scope] || []).some((item) => item.key === key) &&
          !(data.overrides || []).some((item) => item.scope === scope && item.key === key)
        )
          addOverride({ scope, key, mode: 'disabled', value: null });
        loaded = true;
      }
      const active = data.jobs.find((j) => ['queued', 'running'].includes(j.status));
      activeJob = !!active;
      const latest = active || data.jobs[0];
      $('cancel').hidden = !active;
      $('retry').hidden = !data.storage.pending_objects;
      $('purge').disabled = !!active || busy || $('confirm').value !== 'PURGE EVIDENCE';
      $('progress').innerHTML =
        `<p><strong>${data.total_events.toLocaleString()}</strong> events remain · <strong>${data.audit_protected_events.toLocaleString()}</strong> audit-protected</p>` +
        (data.ingestion_pause?.paused
          ? '<p class="text-warning">Ingestion paused for purge or its five-minute cooldown.</p>'
          : '') +
        (latest
          ? `<p class="${latest.last_error ? 'text-danger' : active ? '' : 'text-success'}">${active ? '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>' : ''}${esc(latest.status)} · ${Number(latest.deleted).toLocaleString()} events removed from the database${latest.automatic ? ' · automatic retention' : ''}</p>${latest.last_error ? `<p class="text-danger">${esc(latest.last_error)}</p>` : ''}`
          : '<p>No purge has run yet.</p>') +
        `<p>${data.storage.pending_objects.toLocaleString()} stored files awaiting cleanup${data.storage.waiting_objects ? ` · ${data.storage.waiting_objects.toLocaleString()} waiting for references, permissions or retention locks` : ''}.</p>` +
        (data.storage.errors || [])
          .map((e) => `<p class="text-warning-emphasis">${esc(e)}</p>`)
          .join('');
    } catch (e) {
      toast($('status'), String(e), 'danger');
    } finally {
      fetching = false;
    }
  }
  async function act(fn) {
    if (busy) return;
    busy = true;
    try {
      await fn();
    } catch (e) {
      toast($('status'), String(e), 'danger');
    } finally {
      busy = false;
      await refresh();
    }
  }
  $('mode').addEventListener('change', explain);
  $('value').addEventListener('input', () => {
    $('preview-result').textContent = '';
  });
  $('confirm').addEventListener('input', () => {
    $('purge').disabled = activeJob || busy || $('confirm').value !== 'PURGE EVIDENCE';
  });
  $('preview').addEventListener('click', () =>
    act(async () => {
      if (!$('form').reportValidity()) return;
      $('preview-result').textContent = summary(await apiPost(`${endpoint}/preview`, payload()));
    }),
  );
  $('form').addEventListener('submit', (e) => {
    e.preventDefault();
    act(async () => {
      if (!loaded || !$('form').reportValidity()) return;
      const p = payload();
      const result = await apiPost(`${endpoint}/preview`, p);
      if (
        (p.mode !== 'disabled' || p.overrides.some((item) => item.mode !== 'disabled')) &&
        !confirm(
          `${summary(result)}\nEnable automatic deletion across all frameworks? Deletion is permanent. Eligibility changes as events arrive and audits are deleted.`,
        )
      )
        return;
      const saved = await apiPut(endpoint, p);
      policyUpdatedAt = saved.updated_at;
      toast($('status'), 'Retention policy saved. The worker checks it every minute.', 'success');
    });
  });
  $('purge').addEventListener('click', () =>
    act(async () => {
      if ($('confirm').value !== 'PURGE EVIDENCE') return;
      const p = await apiPost(`${endpoint}/preview-all`, {});
      if (
        !confirm(
          `${summary(p)}\nPermanently purge all eligible evidence across all frameworks? Audit-protected events remain. Older events held only by other purged events may become eligible during cleanup.`,
        )
      )
        return;
      await apiPost(`${endpoint}/purge`, { mode: 'all', confirmation: $('confirm').value });
      $('confirm').value = '';
      toast($('status'), 'Purge queued. This screen refreshes while cleanup runs.', 'success');
    }),
  );
  $('cancel').addEventListener('click', () =>
    act(async () => {
      if (
        !confirm(
          'Cancel the active purge? Completed deletions cannot be undone. Stored-file cleanup continues. To stop automatic retention, set the global policy and every override to Keep everything.',
        )
      )
        return;
      await apiPost(`${endpoint}/cancel`, {});
    }),
  );
  $('retry').addEventListener('click', () =>
    act(async () => {
      await apiPost(`${endpoint}/retry-storage`, {});
      toast($('status'), 'Stored-file cleanup will retry on the next worker pass.', 'success');
    }),
  );
  $('refresh').addEventListener('click', () => refresh());
  document.getElementById('tab-retention').addEventListener('shown.bs.tab', () => refresh());
  setInterval(() => {
    if (document.getElementById('pane-retention').classList.contains('active') && !document.hidden)
      refresh();
  }, 5000);
  explain();
  if (location.hash === '#retention') refresh();
}
