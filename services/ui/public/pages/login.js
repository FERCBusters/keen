import {qs, toast} from '/app.js';

const status = document.getElementById('status');
const form = document.getElementById('loginForm');
const usernameEl = document.getElementById('username');
const passwordEl = document.getElementById('password');
const nextEl = document.getElementById('next');
const btn = document.getElementById('loginBtn');
const ssoSection = document.getElementById('ssoSection');
const ssoButtons = document.getElementById('ssoButtons');
const localLoginHint = document.getElementById('localLoginHint');

const explicitNext = qs('next', null);
const hadExplicitNext = !!explicitNext;

function safeNext(raw) {
  const s = String(raw || '').trim();
  if (!s) return '/';
  // Only allow same-origin absolute paths.
  if (!s.startsWith('/')) return '/';
  if (s.startsWith('//')) return '/';
  if (s.includes('://')) return '/';
  // Avoid looping back to login
  if (s === '/login.html' || s.startsWith('/login.html?')) return '/';
  return s;
}

function computeNext() {
  // If login is served as nginx error_page, the browser URL will be the protected page.
  // Prefer explicit ?next=, otherwise use the current path+query.
  if (explicitNext) return safeNext(explicitNext);

  const cur = location.pathname + (location.search || '');
  return safeNext(cur);
}

async function fetchLandingPage() {
  try {
    const r = await fetch('/api/v1/me', {credentials: 'same-origin'});
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
    const r = await fetch('/api/v1/me', {credentials: 'same-origin'});
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
  const u = new URL(startUrl || '/api/v1/auth/oidc/start', location.origin);
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
    const key = String(provider?.key || '').trim().toLowerCase();
    const label = String(provider?.label || 'single sign-on').trim() || 'single sign-on';
    const button = document.createElement('button');
    button.className = 'btn btn-outline-primary w-100';
    button.type = 'button';
    const icon = document.createElement('i');
    icon.className = `bi ${iconForProvider(key)} me-1`;
    const text = document.createElement('span');
    text.textContent = `Continue with ${label}`;
    button.append(icon, text);
    button.addEventListener('click', () => startOidc(provider.start_url), {once: true});
    ssoButtons.appendChild(button);
  }
  ssoSection.classList.remove('d-none');
}

async function loadAuthMethods() {
  let methods = null;
  try {
    const r = await fetch('/api/v1/auth/methods', {credentials: 'same-origin'});
    if (r.ok) methods = await r.json();
  } catch {
    methods = null;
  }

  let providers = Array.isArray(methods?.sso_providers) ? methods.sso_providers : [];
  if (!providers.length && methods?.oidc_enabled && methods?.oidc) providers = [methods.oidc];
  renderSsoButtons(providers);

  if (methods && methods.local_enabled === false) {
    if (form) form.classList.add('d-none');
    if (localLoginHint) localLoginHint.classList.add('d-none');
    if (!providers.length) toast(status, 'No interactive login method is enabled.', 'danger');
  } else if (!methods && ssoSection) {
    // Keep the local form as a safe fallback if the methods endpoint is unavailable.
    ssoSection.classList.add('d-none');
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
    const password = (passwordEl?.value || '');

    if (!username || !password) {
      toast(status, 'Username and password are required', 'warning');
      return;
    }

    if (btn) btn.disabled = true;
    try {
      const r = await fetch('/api/v1/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password}),
      });
      if (!r.ok) {
        let msg = `HTTP ${r.status}`;
        try {
          const j = await r.json();
          msg = j?.detail || msg;
        } catch {}
        throw new Error(msg);
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
