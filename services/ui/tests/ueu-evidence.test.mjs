import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const hostile = '<img src=x onerror=alert(1)>';

async function panel(t) {
  const ui = await loadUi('pages/ueu-evidence.js', { html: '<section id="host"></section>' });
  t.after(ui.close);
  return { ui, host: ui.document.getElementById('host') };
}

test('provenance shows signature, chain, catalogue and custody as text', async (t) => {
  const { ui, host } = await panel(t);
  ui.api.renderUeuEvidence(host, {
    id: 'event/1',
    ueu: {
      verification: 'verified',
      chain_status: 'gap',
      schema_validation: 'valid',
      event_type: hostile,
      catalogue: 'keen@2026.10',
      evidence_id: 'e1',
      evidence_hash: 'sha256:' + 'a'.repeat(64),
      sequence: 7,
      key_id: 'agent:0011223344556677',
      batch: { received_at: '2026-10-27T00:00:00' },
      custody: {
        changes: [
          { path: 'evidence.payload', reason: 'sensitive_value' },
          { path: hostile, reason: 'above_evidence_mode_ceiling' },
          { path: 'x', reason: hostile },
        ],
        event_data_masking: true,
      },
    },
  });
  assert.equal(host.hidden, false);
  assert.equal(host.querySelector('img'), null);
  const badges = [...host.querySelectorAll('.badge')].map((b) => b.textContent);
  assert.deepEqual(badges, [
    'Signature verified',
    'Gap before this record',
    'Catalogue checked',
    '3 changes by KEEN',
  ]);
  assert.match(host.textContent, /evidence\.payload — redacted: sensitive value/);
  assert.match(host.textContent, /removed: above the identity’s evidence mode/);
  assert.match(host.textContent, /Event data masking was on/);
  assert.match(host.textContent, /no longer matches the evidence hash/);
  assert.equal(host.querySelector('a').getAttribute('href'), '/api/v1/events/event%2F1/ueu-bundle');
});

test('unsigned, unknown and unchanged evidence read clearly; other events hide the panel', async (t) => {
  const { ui, host } = await panel(t);
  ui.api.renderUeuEvidence(host, {
    id: 'e',
    ueu: {
      verification: 'unknown_key',
      chain_status: 'mystery',
      schema_validation: 'catalogue_unknown',
      custody: {},
      batch: null,
    },
  });
  const badges = [...host.querySelectorAll('.badge')].map((b) => b.textContent);
  assert.deepEqual(badges, [
    'Signed by an unknown key',
    'mystery',
    'Catalogue not held; structure checked',
    'Stored as signed',
  ]);
  assert.match(host.textContent, /exact signed bytes/);
  ui.api.renderUeuEvidence(host, { id: 'e', ueu: { custody: { changes: [] } } });
  assert.match(host.textContent, /Unknown/);
  ui.api.renderUeuEvidence(host, { id: 'e', ueu: null });
  assert.equal(host.hidden, true);
  assert.equal(host.children.length, 0);
  ui.api.renderUeuEvidence(null, {});
  for (const verification of ['unsigned', 'revoked_key']) {
    ui.api.renderUeuEvidence(host, { ueu: { verification, chain_status: 'continuous' } });
    assert.ok(host.querySelector('.badge').title.length > 0);
  }
});
