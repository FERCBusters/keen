// UEU provenance where a recorded custody change lacks its path or reason:
// the change is still listed, without placeholder words.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

test('a custody change without a path or reason is listed with blanks', async (t) => {
  const ui = await loadUi('pages/ueu-evidence.js', { html: '<section id="host"></section>' });
  t.after(ui.close);
  const host = ui.document.getElementById('host');
  ui.api.renderUeuEvidence(host, {
    id: 'e1',
    ueu: {
      verification: 'verified',
      chain_status: 'continuous',
      schema_validation: 'valid',
      custody: { changes: [{}, { path: 'evidence.token' }, { reason: 'sensitive_name' }] },
    },
  });
  assert.deepEqual(
    [...host.querySelectorAll('li')].map((item) => item.textContent),
    [' — ', 'evidence.token — ', ' — redacted: sensitive field name'],
  );
  assert.match(host.textContent, /3 changes by KEEN/);
});
