// Who may change an audit from its page: the audit creator and audit
// administrators edit it; other audit managers only read it, even when they
// re-enable its controls; people are added to the directory only by those
// who manage the ISMS.
import test from 'node:test';
import assert from 'node:assert/strict';
import { settle } from './support/page-fixture.mjs';
import { apiAudit, auditPage, change, submit } from './support/audit-pages.mjs';

const colleague = { can_manage_audits: true, can_view_events: true, user_id: 'u2' };
const refusal = 'Only the audit creator or an audit administrator can edit this audit.';
const documents = {
  items: [
    { id: 'd1', title: 'Policy', document_type: 'policy', clauses: [], controls: [{ id: 'c2' }] },
  ],
};

test('another audit manager cannot save notes, reports, people or evidence on the audit', async (t) => {
  const { ui, state } = await auditPage(t, { me: colleague });
  const refused = async (act) => {
    ui.el('status').textContent = '';
    act();
    await settle(() => ui.el('status').textContent === refusal);
    assert.equal(ui.el('status').dataset.alert, 'danger');
  };
  ui.el('auditNotes').value = '<p>Mine</p>';
  await refused(() => submit(ui, 'reportNotesForm'));
  Object.defineProperty(ui.el('reportFile'), 'files', {
    value: [new File(['%PDF'], 'final.pdf')],
    configurable: true,
  });
  await refused(() => submit(ui, 'reportForm'));
  ui.el('attendeeName').value = 'Erin';
  await refused(() => submit(ui, 'attendeeForm'));
  ui.el('urlEvidence').value = 'https://evidence.example/item';
  await refused(() => submit(ui, 'urlEvidenceForm'));
  // Re-enabled buttons are refused the same way.
  for (const id of ['deselectDocuments', 'deselectClauses']) {
    ui.el(id).disabled = false;
    await refused(() => ui.el(id).click());
  }
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual([state.patched, state.posted, state.put], [[], [], null]);
});

test('re-enabled scope boxes do not change the scope for another audit manager', async (t) => {
  const { ui } = await auditPage(t, {
    me: colleague,
    responses: { '/api/v1/isms/documents': documents },
  });
  for (const [attr, id, count] of [
    ['data-document-id', 'd1', 'documentScopeCount'],
    ['data-clause-id', 'cl41', 'clauseScopeCount'],
    ['data-control-id', 'c1', 'controlScopeCount'],
  ]) {
    const box = ui.document.querySelector(`[${attr}="${id}"]`);
    assert.equal(box.disabled, true, id);
    box.disabled = false;
    change(ui, box, { checked: true });
    assert.equal(ui.el(count).textContent, '0 selected', id);
  }
  // A refused tick adds no linked controls either.
  assert.equal(ui.el('controlScopeCount').textContent, '0 selected');
  assert.equal(ui.el('saveScope').disabled, true);
});

test('the creator of the audit can tick a document and its linked controls', async (t) => {
  const { ui } = await auditPage(t, {
    me: { can_manage_audits: true, can_view_events: true, user_id: 'u1' },
    responses: { '/api/v1/isms/documents': documents },
  });
  change(ui, ui.document.querySelector('[data-document-id="d1"]'), { checked: true });
  assert.equal(ui.el('documentScopeCount').textContent, '1 selected');
  assert.equal(ui.document.querySelector('[data-control-id="c2"]').checked, true);
});

test('the template schedule form of an ordinary audit saves nothing', async (t) => {
  const { ui, state } = await auditPage(t);
  assert.ok(ui.el('templateScheduleCard').classList.contains('d-none'));
  ui.el('tmplScheduleStart').value = '2026-09-09';
  submit(ui, 'templateScheduleForm');
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(state.patched, []);
  assert.equal(ui.el('status').textContent, '');
});

test('only ISMS managers are offered to save a new attendee to the people directory', async (t) => {
  const offered = async (me) => {
    const { ui } = await auditPage(t, {
      me,
      audit: apiAudit(),
      responses: { '/api/v1/isms/people': { items: [{ id: 'p1', name: 'Dana', email: '' }] } },
    });
    ui.el('attendeeName').value = 'Erin';
    ui.el('attendeeName').dispatchEvent(new ui.window.Event('input'));
    // An audit administrator edits the audit either way.
    assert.equal(ui.el('attendeeName').disabled, false);
    return !ui.el('attendeeCreatePerson').hidden;
  };
  assert.equal(await offered({ can_admin_audits: true, user_id: 'u3' }), false);
  assert.equal(
    await offered({ can_admin_audits: true, can_manage_isms: true, user_id: 'u3' }),
    true,
  );
});
