// Shared confirmation dialog for destructive and consequential actions: a
// promise-based replacement for window.confirm. confirmAction(options)
// resolves true when the person confirms the action and false when they
// cancel it (Cancel button, Escape or a click on the backdrop). Typed
// confirmation is reserved for actions that irreversibly replace
// user-controlled credentials.
//
// All dynamic text is set through textContent, so hostile strings stay inert.
// Requests are serialised: each dialog appears on its own, like window.confirm.

let dialog = null;
let pending = null; // The request the open dialog is waiting on.
let queue = Promise.resolve();

const defaults = {
  title: 'Are you sure?',
  message: '',
  confirmLabel: 'Confirm',
  cancelLabel: 'Cancel',
  danger: false,
  typed: null,
};

function finish(result) {
  if (!pending) return;
  const { resolve } = pending;
  pending = null;
  dialog.close();
  resolve(result);
}

function paint({ title, message, confirmLabel, cancelLabel, danger, typed }) {
  dialog.querySelector('[data-title]').textContent = title;
  dialog.querySelector('[data-message]').textContent = message;
  const confirm = dialog.querySelector('[data-confirm]');
  confirm.textContent = confirmLabel;
  confirm.className = danger ? 'btn btn-danger' : 'btn btn-primary';
  dialog.querySelector('[data-cancel]').textContent = cancelLabel;
  const typedWrap = dialog.querySelector('[data-typed]');
  const typedInput = dialog.querySelector('[data-typed-input]');
  if (typed) {
    typedWrap.hidden = false;
    dialog.dataset.typedWord = typed;
    dialog.querySelector('[data-typed-label]').textContent = `Type “${typed}” to confirm`;
    typedInput.value = '';
    confirm.disabled = true;
  } else {
    typedWrap.hidden = true;
    dialog.dataset.typedWord = '';
    confirm.disabled = false;
  }
}

function ensureDialog() {
  if (dialog) return;
  dialog = document.createElement('dialog');
  dialog.className = 'keen-confirm';
  dialog.setAttribute('aria-labelledby', 'keen-confirm-title');
  dialog.innerHTML =
    '<h2 id="keen-confirm-title" class="h5 mb-3" data-title></h2>' +
    '<p data-message class="mb-3"></p>' +
    '<div data-typed hidden><label data-typed-label for="keen-confirm-typed" class="form-label"></label>' +
    '<input id="keen-confirm-typed" data-typed-input class="form-control" autocomplete="off" spellcheck="false"></div>' +
    '<div class="d-flex gap-2 justify-content-end mt-3">' +
    '<button type="button" class="btn btn-outline-secondary" data-cancel></button>' +
    '<button type="button" data-confirm></button></div>';
  document.body.append(dialog);
  dialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    finish(false);
  });
  // A click on the dialog surface outside the content is a backdrop click.
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) finish(false);
  });
  dialog.querySelector('[data-cancel]').addEventListener('click', () => finish(false));
  dialog.querySelector('[data-confirm]').addEventListener('click', () => finish(true));
  const typedInput = dialog.querySelector('[data-typed-input]');
  typedInput.addEventListener('input', () => {
    const expected = String(dialog.dataset.typedWord || '')
      .trim()
      .toLowerCase();
    dialog.querySelector('[data-confirm]').disabled =
      typedInput.value.trim().toLowerCase() !== expected;
  });
}

function show(options) {
  const settings = { ...defaults, ...options };
  return new Promise((resolve) => {
    ensureDialog();
    paint(settings);
    pending = { resolve };
    dialog.showModal();
    const typedInput = dialog.querySelector('[data-typed-input]');
    const focusTarget = settings.typed
      ? typedInput
      : settings.danger
        ? dialog.querySelector('[data-cancel]')
        : dialog.querySelector('[data-confirm]');
    focusTarget.focus();
  });
}

export function confirmAction(options = {}) {
  const result = queue.then(() => show(options));
  queue = result.then(
    () => {},
    () => {},
  );
  return result;
}
