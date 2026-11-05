// Removing several sampled evidence entries from an audit at once.
import test from 'node:test';
import assert from 'node:assert/strict';
import { settle } from './support/page-fixture.mjs';
import { apiAudit, auditPage, eventEvidence, reloaded } from './support/audit-pages.mjs';

const audit = () =>
  apiAudit({ evidence: [eventEvidence('e1', 'ev1'), eventEvidence('e2', 'ev2')] });
const selectRow = (ui, id) =>
  ui.document
    .querySelector(`[data-select-evidence][value="${id}"]`)
    .dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
const removeSelected = (ui) =>
  ui.el('evidenceSelectionBar').querySelector('[data-remove-selected-evidence]').click();
const selectedCount = (ui) =>
  ui.el('evidenceSelectionBar').querySelector('[data-evidence-selection-count]').textContent;

test('removing selected evidence with nothing selected asks nothing and sends nothing', async (t) => {
  const { ui, state } = await auditPage(t, { audit: audit() });
  removeSelected(ui);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(ui.confirmations, []);
  assert.deepEqual(state.removed, []);
});

test('one selected entry is named in the singular, and cancelling keeps it selected', async (t) => {
  const { ui, state } = await auditPage(t, { audit: audit(), confirm: false });
  selectRow(ui, 'e2');
  removeSelected(ui);
  await settle(() => ui.confirmations.length === 1);
  assert.equal(
    ui.confirmations[0].message,
    'Remove 1 sampled evidence item from this audit? The evidence itself stays in Keen.',
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(state.removed, []);
  assert.equal(selectedCount(ui), '1 selected');
  assert.equal(ui.el('evidenceSelectionBar').hidden, false);
});

test('a refused removal is reported and the selection stays for another try', async (t) => {
  const { ui, state } = await auditPage(t, {
    audit: audit(),
    remove: () => {
      throw new Error('Delete <b>rejected</b>');
    },
  });
  selectRow(ui, 'e1');
  removeSelected(ui);
  await settle(() => state.removed.length === 1);
  await reloaded(ui);
  assert.deepEqual(state.removed, ['/api/v1/audits/a1/evidence/e1']);
  assert.equal(
    ui.el('status').textContent,
    'Failed to remove sampled evidence: Delete <b>rejected</b>',
  );
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.equal(selectedCount(ui), '1 selected');
  assert.equal(ui.document.querySelector('[data-select-evidence][value="e1"]').checked, true);
});
