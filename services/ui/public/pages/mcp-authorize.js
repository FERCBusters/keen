import { apiPost, errorText } from '/app.js';
// The request key is used once; keep it out of history and of later requests.
const request = new URLSearchParams(location.search).get('request');
history.replaceState(null, '', location.pathname);
const status = document.getElementById('consentStatus');
const form = document.getElementById('consentForm');
const scopes = document.getElementById('consentScopes');
const changes = document.getElementById('consentChanges');
// The API returns the app's registered redirect URI; never navigate to a scheme
// that would run or read something in this page's context.
const REFUSED = new Set(['javascript:', 'data:', 'vbscript:', 'file:', 'blob:', 'about:']);
function leave(target) {
  let url;
  try {
    url = new URL(target);
  } catch {
    url = null;
  }
  if (!url || REFUSED.has(url.protocol)) {
    status.textContent = 'Keen could not return you to the app.';
    return;
  }
  status.textContent = 'Returning you to the app.';
  location.assign(url.href);
}
async function answer(approve) {
  for (const button of form.querySelectorAll('button')) button.disabled = true;
  try {
    const chosen = [...form.querySelectorAll('input:checked')].map((box) => box.value);
    const result = await apiPost('/api/v1/me/mcp/consent', { request, approve, scopes: chosen });
    form.hidden = true;
    leave(result.redirect);
  } catch (error) {
    status.textContent = errorText(error);
    for (const button of form.querySelectorAll('button')) button.disabled = false;
  }
}
function checkbox(scope) {
  const wrap = document.createElement('div');
  wrap.className = 'form-check';
  const box = document.createElement('input');
  box.className = 'form-check-input';
  box.type = 'checkbox';
  box.id = 'scope-' + scope.scope.replace(/[^a-z_]/g, '-');
  box.value = scope.scope;
  // A change starts unticked, whatever the app asked for: the person turns it on.
  box.checked = scope.requested && !scope.changes;
  const label = document.createElement('label');
  label.className = 'form-check-label';
  label.htmlFor = box.id;
  label.textContent = scope.description;
  wrap.append(box, label);
  return { wrap, box };
}
function render(view) {
  document.getElementById('consentClient').textContent = view.client_name;
  document.getElementById('consentRedirect').textContent = view.redirect_host;
  const reads = new Map();
  for (const scope of view.scopes.filter((item) => !item.changes)) {
    const { wrap, box } = checkbox(scope);
    reads.set(scope.scope, box);
    scopes.append(wrap);
  }
  const offered = view.scopes.filter((item) => item.changes);
  for (const scope of offered) {
    const { wrap, box } = checkbox(scope);
    // A change scope brings its read scope, so that box stays ticked with it.
    const read = reads.get(scope.includes);
    box.addEventListener('change', () => {
      if (!read) return;
      if (box.checked) read.checked = true;
      read.disabled = [...changes.querySelectorAll('input:checked')].some(
        (other) => reads.get(other.dataset.includes) === read,
      );
    });
    box.dataset.includes = scope.includes;
    changes.append(wrap);
  }
  document.getElementById('consentChangesSet').hidden = offered.length === 0;
  if (offered.some((item) => item.requested)) {
    document.getElementById('consentWants').textContent = 'wants to read and change Keen as you.';
  }
  document.getElementById('consentNoScopes').hidden = view.scopes.length > 0;
  status.textContent = 'Review this request.';
  form.hidden = false;
}
form.addEventListener('submit', (event) => {
  event.preventDefault();
  answer(true);
});
document.getElementById('consentRefuse').addEventListener('click', () => answer(false));
if (request) {
  apiPost('/api/v1/me/mcp/consent/view', { request })
    .then(render)
    .catch((error) => {
      status.textContent = errorText(error);
    });
} else {
  status.textContent = 'Start the connection from your AI app; it opens this page for you.';
}
