// Warns when a long form holds unsaved edits: a beforeunload prompt when the
// tab closes or navigates, plus a visible hint near the form. Pages that
// already track their own dirty flag pass `isDirty`; otherwise the guard
// watches user input inside `root`. Programmatic fills do not fire input
// events, so loading a record does not mark it dirty.

export function unsavedGuard({ isDirty = null, root = null, hint = null } = {}) {
  let touched = false;
  const markDirty = () => {
    touched = true;
    if (hint) hint.hidden = false;
  };
  const markClean = () => {
    touched = false;
    if (hint) hint.hidden = true;
  };
  const onBeforeUnload = (event) => {
    if (!(isDirty ? isDirty() : touched)) return;
    event.preventDefault();
    event.returnValue = 'You have unsaved changes.';
  };
  window.addEventListener('beforeunload', onBeforeUnload);
  if (root) {
    root.addEventListener('input', markDirty, true);
    root.addEventListener('change', markDirty, true);
  }
  return {
    markDirty,
    markClean,
    isDirty: () => (isDirty ? isDirty() : touched),
    stop: () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      if (root) {
        root.removeEventListener('input', markDirty, true);
        root.removeEventListener('change', markDirty, true);
      }
    },
  };
}
