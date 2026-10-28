// Provenance of UEU evidence: signature, chain, catalogue checks and the
// changes KEEN made before storing it. All values are rendered as text.
const verification = {
  verified: [
    'Signature verified',
    'success',
    'The producer signed this envelope with a key KEEN holds for it.',
  ],
  unsigned: [
    'Unsigned',
    'secondary',
    'The producer did not sign this batch. Its content is as received.',
  ],
  unknown_key: [
    'Signed by an unknown key',
    'warning',
    'The batch is signed, but by a key KEEN does not hold, so the signature could not be checked.',
  ],
  revoked_key: [
    'Signed by a revoked key',
    'danger',
    'The signature is valid for a key an administrator has since revoked.',
  ],
};
const chain = {
  continuous: ['Continuous', 'success', 'Follows the previous record from this producer.'],
  started: ['Stream started', 'info', 'The first record of this producer stream.'],
  restarted: [
    'Stream restarted',
    'warning',
    'The producer started a new stream; its queue state was reset.',
  ],
  gap: [
    'Gap before this record',
    'warning',
    'Records between the previous one KEEN received and this one did not arrive.',
  ],
  broken: [
    'Chain broken',
    'danger',
    'This record does not name the previous record KEEN received with the same sequence position.',
  ],
  out_of_order: [
    'Received out of order',
    'secondary',
    'A later record from this producer arrived first.',
  ],
  untracked: [
    'Not chained',
    'secondary',
    'The producer stream is not one KEEN tracks for this identity.',
  ],
};
const reasons = {
  sensitive_name: 'redacted: sensitive field name',
  sensitive_value: 'redacted: sensitive value',
  above_evidence_mode_ceiling: 'removed: above the identity’s evidence mode',
};

function badge(map, value) {
  const [label, tone, help] = map[value] || [value || 'Unknown', 'secondary', ''];
  const span = document.createElement('span');
  span.className = `badge text-bg-${tone}`;
  span.textContent = label;
  if (help) span.title = help;
  return span;
}

function row(dl, term, value) {
  const dt = document.createElement('dt');
  dt.className = 'col-sm-3';
  dt.textContent = term;
  const dd = document.createElement('dd');
  dd.className = 'col-sm-9';
  if (value instanceof Node) dd.append(value);
  else {
    dd.textContent = value == null || value === '' ? '-' : String(value);
    dd.classList.add('mono', 'text-break');
  }
  dl.append(dt, dd);
}

export function renderUeuEvidence(host, e) {
  if (!host) return;
  const p = e?.ueu;
  host.replaceChildren();
  if (!p || typeof p !== 'object') {
    host.hidden = true;
    return;
  }
  host.hidden = false;
  const title = document.createElement('div');
  title.className = 'fw-bold mb-2';
  title.textContent = 'Evidence provenance (UEU)';
  const badges = document.createElement('div');
  badges.className = 'd-flex flex-wrap gap-2 mb-2';
  badges.append(badge(verification, p.verification), badge(chain, p.chain_status));
  const schema = document.createElement('span');
  schema.className = `badge text-bg-${p.schema_validation === 'valid' ? 'success' : 'secondary'}`;
  schema.textContent =
    p.schema_validation === 'valid' ? 'Catalogue checked' : 'Catalogue not held; structure checked';
  badges.append(schema);
  const changes = Array.isArray(p.custody?.changes) ? p.custody.changes : [];
  const altered = document.createElement('span');
  altered.className = `badge text-bg-${changes.length ? 'warning' : 'success'}`;
  altered.textContent = changes.length ? `${changes.length} changes by KEEN` : 'Stored as signed';
  badges.append(altered);
  const dl = document.createElement('dl');
  dl.className = 'row small mb-2';
  row(dl, 'Event type', p.event_type);
  row(dl, 'Catalogue', p.catalogue);
  row(dl, 'Evidence ID', p.evidence_id);
  row(dl, 'Evidence hash', p.evidence_hash);
  row(dl, 'Sequence', p.sequence);
  row(dl, 'Signing key', p.key_id);
  row(dl, 'Batch received', p.batch?.received_at);
  if (changes.length) {
    const list = document.createElement('ul');
    list.className = 'mb-0 ps-3';
    for (const change of changes.slice(0, 64)) {
      const li = document.createElement('li');
      li.textContent = `${String(change?.path || '')} — ${reasons[change?.reason] || String(change?.reason || '')}`;
      list.append(li);
    }
    row(dl, 'Changes', list);
  }
  if (p.custody?.event_data_masking)
    row(dl, 'Masking', 'Event data masking was on when this was stored');
  const note = document.createElement('p');
  note.className = 'small-muted';
  note.textContent = changes.length
    ? 'The stored envelope differs from the signed one at the paths listed, so it no longer matches the evidence hash. The batch manifest and signature are kept for verification.'
    : 'The stored envelope artifact is the exact signed bytes; its SHA-256 is the evidence hash.';
  const link = document.createElement('a');
  link.className = 'btn btn-sm btn-outline-primary no-print';
  link.href = `/api/v1/events/${encodeURIComponent(String(e.id || ''))}/ueu-bundle`;
  link.textContent = 'Download verification bundle';
  link.title =
    'The envelope, its signed batch manifest, the public key and the catalogue, for independent checking';
  host.append(title, badges, dl, note, link);
}
