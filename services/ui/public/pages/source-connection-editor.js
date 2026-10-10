import {apiPost, apiPut} from '/app.js';

// The editor owns credentials; the hub owns navigation and input management.
export function editSourceConnection({types, connection = null, onSaved}) {
  const dialog = document.createElement('dialog');
  dialog.className = 'p-4 rounded border';
  dialog.style.width = 'min(640px,95vw)';
  const form = document.createElement('form');
  const heading = document.createElement('h2');
  heading.className = 'h4';
  heading.textContent = connection ? 'Edit source' : 'Define a source';
  const hint = document.createElement('p');
  hint.textContent = 'Name the endpoint or account and enter its credentials, then add the inputs to collect. Credentials are encrypted on the server.';
  form.append(heading, hint);

  function field(parent, title, type = 'text', value = '') {
    const label = document.createElement('label');
    label.className = 'form-label d-block';
    label.textContent = title;
    const input = document.createElement(type === 'select' ? 'select' : 'input');
    input.className = type === 'checkbox' ? 'form-check-input ms-2' : 'form-control mb-3';
    if (type !== 'select') input.type = type;
    input.value = value;
    label.append(input);
    parent.append(label);
    return input;
  }

  const source = field(form, 'Source type', 'select');
  for (const type of types.filter(t => t.enabled || t.source === connection?.source)) {
    source.add(new Option(type.source.replaceAll('_', ' '), type.source));
  }
  if (connection) source.value = connection.source;
  source.disabled = !!connection;
  const name = field(form, 'Source name', 'text', connection?.name || '');
  name.required = true;
  const enabled = field(form, 'Enable ingestion', 'checkbox');
  enabled.checked = connection?.enabled ?? true;
  const fields = document.createElement('div');
  form.append(fields);

  function paint() {
    fields.replaceChildren();
    const spec = types.find(t => t.source === source.value);
    for (const [keys, secret] of [[spec?.fields || [], false], [spec?.credential_fields || [], true]]) {
      for (const key of keys) {
        const input = field(fields, key.replaceAll('_', ' ') + (secret && connection ? ' (blank keeps saved value)' : ''),
          key === 'auth_mode' ? 'select' : secret ? 'password' : 'text', secret ? '' : connection?.configuration[key] || '');
        if (key === 'auth_mode') {
          for (const mode of ['token', 'bearer', 'basic', 'query', 'cookie', 'none']) input.add(new Option(mode, mode));
          input.value = connection?.configuration[key] || 'token';
        }
        input.autocomplete = 'off';
        input.dataset.key = key;
        input.dataset.secret = String(secret);
        if (secret && connection) {
          const clear = field(fields, 'Remove saved ' + key.replaceAll('_', ' '), 'checkbox');
          clear.dataset.clear = key;
        }
      }
    }
  }
  source.onchange = paint;
  paint();
  const error = document.createElement('p');
  error.setAttribute('role', 'alert');
  const save = document.createElement('button');
  save.type = 'submit';
  save.className = 'btn btn-primary';
  save.textContent = connection ? 'Save source' : 'Create source';
  save.disabled = !source.options.length;
  if (!source.options.length) error.textContent = 'Enable a source type in the deployment configuration to set up a connection.';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-outline-secondary ms-2';
  cancel.textContent = 'Cancel';
  cancel.onclick = () => dialog.close();
  form.append(error, save, cancel);
  form.onsubmit = async event => {
    event.preventDefault();
    save.disabled = true;
    try {
      const configuration = {}, credentials = {}, clear_credentials = [];
      for (const input of fields.querySelectorAll('[data-key]')) {
        (input.dataset.secret === 'true' ? credentials : configuration)[input.dataset.key] = input.value;
      }
      for (const input of fields.querySelectorAll('[data-clear]')) if (input.checked) clear_credentials.push(input.dataset.clear);
      const payload = {source: source.value, name: name.value, enabled: enabled.checked, configuration,
        credentials, clear_credentials, inputs: connection?.inputs || {}, version: connection?.version || 0};
      const result = connection ? await apiPut('/api/v1/admin/source-connections/' + connection.id, payload)
        : await apiPost('/api/v1/admin/source-connections', payload);
      dialog.close();
      await onSaved(result);
    } catch (failure) {
      error.textContent = failure.message || String(failure);
    } finally {
      save.disabled = false;
    }
  };
  dialog.append(form);
  document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove());
  dialog.showModal();
  return dialog;
}
