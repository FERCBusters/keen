// The shared confirmation dialog: promise semantics, text handling, focus,
// typed confirmation and cancellation paths.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// jsdom has no modal dialog focus machinery; the browser-native parts are
// reduced to their observable state.
function dialogSetup(window) {
  window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
}

async function openModal(t, options, setup = dialogSetup) {
  const ui = await loadUi('pages/confirm-modal.js', { setup });
  t.after(ui.close);
  const settled = [];
  const result = ui.api.confirmAction(options).then((value) => (settled.push(value), value));
  await new Promise((r) => setTimeout(r, 0));
  return { ui, result, settled };
}

const dlg = (ui) => ui.document.querySelector('dialog.keen-confirm');
const btn = (ui, name) => dlg(ui).querySelector(`[data-${name}]`);

test('confirm click resolves true, closes the dialog and renders text as text', async (t) => {
  const { ui, result, settled } = await openModal(t, {
    title: 'Delete <b>group</b>',
    message: 'Members <img src=x> lose access.\nSecond line',
    confirmLabel: 'Delete',
  });
  const dialog = dlg(ui);
  assert.equal(dialog.open, true);
  assert.equal(btn(ui, 'title').textContent, 'Delete <b>group</b>');
  assert.equal(btn(ui, 'confirm').textContent, 'Delete');
  assert.equal(dialog.querySelectorAll('[data-title] *').length, 0);
  assert.equal(dialog.querySelectorAll('[data-message] *').length, 0);
  assert.match(btn(ui, 'message').textContent, /Members <img src=x> lose access\.[^]*Second line/);
  btn(ui, 'confirm').click();
  assert.equal(await result, true);
  assert.deepEqual(settled, [true]);
  assert.equal(dialog.open, false);
});

test('cancel click resolves false', async (t) => {
  const { ui, result, settled } = await openModal(t, { title: 'Archive quiz' });
  btn(ui, 'cancel').click();
  assert.equal(await result, false);
  assert.deepEqual(settled, [false]);
  assert.equal(dlg(ui).open, false);
});

test('Escape (cancel event) resolves false and closes', async (t) => {
  const { ui, result, settled } = await openModal(t, { title: 'Delete audit' });
  dlg(ui).dispatchEvent(new ui.window.Event('cancel', { cancelable: true }));
  assert.equal(await result, false);
  assert.deepEqual(settled, [false]);
  assert.equal(dlg(ui).open, false);
});

test('backdrop click resolves false', async (t) => {
  const { ui, result } = await openModal(t, { title: 'Purge evidence' });
  dlg(ui).dispatchEvent(new ui.window.Event('click', { bubbles: false }));
  assert.equal(await result, false);
  assert.equal(dlg(ui).open, false);
});

test('a click inside the dialog content does not cancel', async (t) => {
  const { ui, result } = await openModal(t, { title: 'Reset cursors' });
  btn(ui, 'message').dispatchEvent(new ui.window.Event('click', { bubbles: true }));
  let settled = false;
  result.then(() => (settled = true));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(settled, false);
  assert.equal(dlg(ui).open, true);
  btn(ui, 'confirm').click();
  assert.equal(await result, true);
});

test('danger tone styles the confirm button as destructive and focuses Cancel', async (t) => {
  const { ui, result } = await openModal(t, {
    title: 'Revoke agent',
    message: 'Existing evidence remains available.',
    confirmLabel: 'Revoke',
    danger: true,
  });
  assert.equal(btn(ui, 'confirm').className, 'btn btn-danger');
  assert.equal(ui.document.activeElement, btn(ui, 'cancel'));
  btn(ui, 'confirm').click();
  assert.equal(await result, true);
});

test('light confirmations use the primary tone and focus Confirm', async (t) => {
  const { ui, result } = await openModal(t, { title: 'Restore revision' });
  assert.equal(btn(ui, 'confirm').className, 'btn btn-primary');
  assert.equal(ui.document.activeElement, btn(ui, 'confirm'));
  btn(ui, 'confirm').click();
  assert.equal(await result, true);
});

test('typed confirmation stays disabled until the verb is typed', async (t) => {
  const { ui, result } = await openModal(t, {
    title: 'Rotate credential',
    message: 'The current credential stops working.',
    confirmLabel: 'Rotate',
    typed: 'Rotate',
  });
  const confirm = btn(ui, 'confirm');
  const input = btn(ui, 'typed-input');
  const label = btn(ui, 'typed-label');
  assert.equal(confirm.disabled, true, 'starts disabled');
  assert.equal(label.textContent, 'Type “Rotate” to confirm');
  assert.equal(ui.document.activeElement, input);
  for (const [attempt, locked] of [
    ['wrong', true],
    [' rotate ', false],
    ['ROTATE', false],
  ]) {
    input.value = attempt;
    input.dispatchEvent(new ui.window.Event('input'));
    assert.equal(confirm.disabled, locked, attempt);
  }
  input.value = 'rotate';
  input.dispatchEvent(new ui.window.Event('input'));
  confirm.click();
  assert.equal(await result, true);
});

test('typed confirmation still blocks the confirm button after a wrong word', async (t) => {
  const { ui, result } = await openModal(t, {
    title: 'Replace recovery codes',
    confirmLabel: 'Replace',
    typed: 'Replace',
  });
  const confirm = btn(ui, 'confirm');
  const input = btn(ui, 'typed-input');
  input.value = 'replace the codes';
  input.dispatchEvent(new ui.window.Event('input'));
  assert.equal(confirm.disabled, true);
  btn(ui, 'cancel').click();
  assert.equal(await result, false);
});

test('the default labels and title render when options are omitted', async (t) => {
  const { ui, result } = await openModal(t, {});
  assert.equal(btn(ui, 'title').textContent, 'Are you sure?');
  assert.equal(btn(ui, 'confirm').textContent, 'Confirm');
  assert.equal(btn(ui, 'cancel').textContent, 'Cancel');
  btn(ui, 'confirm').click();
  assert.equal(await result, true);
});

test('requests are serialised: the second dialog opens after the first settles', async (t) => {
  const ui = await loadUi('pages/confirm-modal.js', { setup: dialogSetup });
  t.after(ui.close);
  const first = ui.api.confirmAction({ title: 'First' });
  const second = ui.api.confirmAction({ title: 'Second' });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(btn(ui, 'title').textContent, 'First');
  btn(ui, 'confirm').click();
  assert.equal(await first, true);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(dlg(ui).open, true);
  assert.equal(btn(ui, 'title').textContent, 'Second');
  btn(ui, 'cancel').click();
  assert.equal(await second, false);
});

test('the same dialog element is reused across requests', async (t) => {
  const ui = await loadUi('pages/confirm-modal.js', { setup: dialogSetup });
  t.after(ui.close);
  const first = ui.api.confirmAction({ title: 'One', confirmLabel: 'Delete', danger: true });
  await new Promise((r) => setTimeout(r, 0));
  btn(ui, 'confirm').click();
  await first;
  const second = ui.api.confirmAction({ title: 'Two' });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(ui.document.querySelectorAll('dialog.keen-confirm').length, 1);
  assert.equal(btn(ui, 'confirm').textContent, 'Confirm');
  assert.equal(btn(ui, 'confirm').className, 'btn btn-primary');
  assert.equal(btn(ui, 'confirm').disabled, false);
  btn(ui, 'confirm').click();
  assert.equal(await second, true);
});
