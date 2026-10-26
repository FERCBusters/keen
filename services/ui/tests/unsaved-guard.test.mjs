// The shared unsaved-changes guard: beforeunload prompting, the visible hint
// and input tracking.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

async function guardUi(t, { html = '<form id="form"><input id="field"></form>' } = {}) {
  const ui = await loadUi('pages/unsaved-guard.js', { html });
  t.after(ui.close);
  return ui;
}

const fireUnload = (window) => {
  const event = new window.Event('beforeunload', { cancelable: true });
  // jsdom's Event.returnValue is a boolean accessor and cannot round-trip a
  // string through the test's VM context; shadow it so the assignment the
  // guard makes for real browsers is observable here.
  Object.defineProperty(event, 'returnValue', {
    value: '',
    writable: true,
    enumerable: true,
    configurable: true,
  });
  window.dispatchEvent(event);
  return event;
};

test('beforeunload is not blocked while the form is clean', async (t) => {
  const ui = await guardUi(t);
  ui.api.unsavedGuard({ root: ui.document.getElementById('form') });
  const event = fireUnload(ui.window);
  assert.equal(event.defaultPrevented, false);
});

test('user input marks the form dirty, blocks unload and shows the hint', async (t) => {
  const ui = await guardUi(t);
  const hint = ui.document.createElement('span');
  hint.hidden = true;
  ui.document.body.append(hint);
  const guard = ui.api.unsavedGuard({ root: ui.document.getElementById('form'), hint });
  assert.equal(guard.isDirty(), false);
  ui.document
    .getElementById('field')
    .dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.equal(guard.isDirty(), true);
  assert.equal(hint.hidden, false);
  const event = fireUnload(ui.window);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.returnValue, 'You have unsaved changes.');
});

test('change events also mark the form dirty', async (t) => {
  const ui = await guardUi(t);
  const guard = ui.api.unsavedGuard({ root: ui.document.getElementById('form') });
  ui.document.getElementById('field').dispatchEvent(new ui.window.Event('change'));
  assert.equal(guard.isDirty(), true);
});

test('markClean clears the dirty state and hides the hint', async (t) => {
  const ui = await guardUi(t);
  const hint = ui.document.createElement('span');
  ui.document.body.append(hint);
  const guard = ui.api.unsavedGuard({ hint });
  guard.markDirty();
  assert.equal(hint.hidden, false);
  guard.markClean();
  assert.equal(guard.isDirty(), false);
  assert.equal(hint.hidden, true);
  const event = fireUnload(ui.window);
  assert.equal(event.defaultPrevented, false);
});

test('an external dirty predicate drives the beforeunload prompt', async (t) => {
  const ui = await guardUi(t);
  let dirty = false;
  const guard = ui.api.unsavedGuard({ isDirty: () => dirty });
  ui.document
    .getElementById('field')
    .dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.equal(guard.isDirty(), false, 'external predicate ignores tracked input');
  dirty = true;
  assert.equal(fireUnload(ui.window).defaultPrevented, true);
  dirty = false;
  assert.equal(fireUnload(ui.window).defaultPrevented, false);
  guard.markDirty();
  assert.equal(guard.isDirty(), false, 'external predicate still wins');
});

test('stop removes the beforeunload listener', async (t) => {
  const ui = await guardUi(t);
  const guard = ui.api.unsavedGuard({});
  guard.markDirty();
  guard.stop();
  assert.equal(fireUnload(ui.window).defaultPrevented, false);
});
