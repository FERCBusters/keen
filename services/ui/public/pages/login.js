import { safeNext } from './navigation-security.js';
import { qs, toast } from '/vendor/mosp-design-system/app.js';

const status = document.getElementById('status');
const form = document.getElementById('loginForm');
const usernameEl = document.getElementById('username');
const passwordEl = document.getElementById('password');
const nextEl = document.getElementById('next');
const btn = document.getElementById('loginBtn');
const ssoSection = document.getElementById('ssoSection');
const ssoButtons = document.getElementById('ssoButtons');

// Clean the current history entry before using its query as a return destination.
// This cannot remove requests already recorded by the browser or reverse proxy.
if (new URL(location.href).searchParams.has('password')) {
  history.replaceState(null, '', safeNext(location.pathname + location.search + location.hash));
}

const explicitNext = qs('next', null);
const hadExplicitNext = !!explicitNext;

function computeNext() {
  // If login is served as nginx error_page, the browser URL will be the protected page.
  // Prefer explicit ?next=, otherwise use the current path+query.
  if (explicitNext) return safeNext(explicitNext);

  const cur = location.pathname + (location.search || '');
  return safeNext(cur);
}

async function fetchLandingPage() {
  try {
    const r = await fetch('/api/v1/me', { credentials: 'same-origin' });
    if (!r.ok) return '/';
    const me = await r.json();
    const lp = me?.preferences?.landing_page || '/';
    return safeNext(lp);
  } catch {
    return '/';
  }
}

async function maybeAlreadyLoggedIn() {
  try {
    const r = await fetch('/api/v1/me', { credentials: 'same-origin' });
    if (!r.ok) return false;
    // Logged in already; go where the user intended.
    let n = computeNext();
    if (!hadExplicitNext && (n === '/' || n === '/index.html')) {
      n = await fetchLandingPage();
    }
    if (n) location.replace(n);
    return true;
  } catch {
    // not logged in
    return false;
  }
}

function startOidc(startUrl) {
  const n = computeNext();
  const u = new URL(startUrl, location.origin);
  if (n) u.searchParams.set('next', n);
  location.assign(u.pathname + u.search);
}

function iconForProvider(key) {
  if (key === 'github') return 'bi-github';
  if (key === 'google') return 'bi-google';
  return 'bi-shield-lock';
}

function renderSsoButtons(providers) {
  if (!ssoSection || !ssoButtons || !providers.length) return;
  ssoButtons.replaceChildren();
  for (const provider of providers) {
    const key = String(provider?.key || '')
      .trim()
      .toLowerCase();
    const label = String(provider?.label || 'single sign-on').trim() || 'single sign-on';
    const button = document.createElement('button');
    button.className = 'btn btn-outline-primary w-100';
    button.type = 'button';
    const icon = document.createElement('i');
    icon.className = `bi ${iconForProvider(key)} me-1`;
    const text = document.createElement('span');
    text.textContent = `Continue with ${label}`;
    button.append(icon, text);
    button.addEventListener('click', () => startOidc(provider.start_url), { once: true });
    ssoButtons.appendChild(button);
  }
  ssoSection.classList.remove('d-none');
}

async function loadAuthMethods() {
  let methods = null;
  try {
    const r = await fetch('/api/v1/auth/methods', { credentials: 'same-origin' });
    if (r.ok) methods = await r.json();
  } catch {
    methods = null;
  }

  const providers = Array.isArray(methods?.sso_providers) ? methods.sso_providers : [];
  renderSsoButtons(providers);

  if (methods?.ldap_enabled && form) {
    const label = document.createElement('label');
    label.className = 'form-label';
    label.htmlFor = 'login-method';
    label.textContent = 'Sign-in method';
    const select = document.createElement('select');
    select.id = 'login-method';
    select.className = 'form-select mb-3';
    select.add(new Option('LDAP directory', 'ldap'));
    if (methods.local_enabled) select.add(new Option('Local Keen account', 'local'));
    form.prepend(label, select);
  }
  if (methods && methods.local_enabled === false && !methods.ldap_enabled) {
    if (form) form.classList.add('d-none');
    if (!providers.length) toast(status, 'No interactive login method is enabled.', 'danger');
  }
}

if (nextEl) nextEl.value = computeNext();

(async () => {
  if (await maybeAlreadyLoggedIn()) return;
  await loadAuthMethods();
})();

if (form) {
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (status) status.style.display = 'none';

    const username = (usernameEl?.value || '').trim();
    const password = passwordEl?.value || '';

    if (!username || !password) {
      toast(status, 'Username and password are required', 'warning');
      return;
    }

    if (btn) btn.disabled = true;
    try {
      const r = await fetch('/api/v1/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username,
          password,
          method: document.getElementById('login-method')?.value || 'local',
        }),
      });
      if (!r.ok) {
        let msg = `HTTP ${r.status}`;
        try {
          const j = await r.json();
          msg = j?.detail || msg;
        } catch {}
        throw new Error(msg);
      }
      const result = await r.json();
      if (result.mfa_required) {
        passwordEl.value = '';
        location.assign('/mfa.html?next=' + encodeURIComponent(computeNext()));
        return;
      }
      let n = computeNext();
      if (!hadExplicitNext && (n === '/' || n === '/index.html')) {
        n = await fetchLandingPage();
      }
      location.replace(n || '/');
    } catch (e) {
      toast(status, `Login failed: ${String(e)}`, 'danger');
    } finally {
      if (btn) btn.disabled = false;
    }
  });
}

// Enable credentials only after the handler that prevents native submission exists.
if (form) {
  document.getElementById('loginFields').disabled = false;
  document.getElementById('loginLoading').hidden = true;
  // An autofocus attribute is lost while the fields start disabled, so focus
  // the username here and typing can start straight away.
  usernameEl?.focus();
}
