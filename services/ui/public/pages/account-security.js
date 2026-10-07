const summary = document.getElementById('securitySummary');
const form = document.getElementById('securityForm');
(async () => {
  try {
    const response = await fetch('/api/v1/me/mfa',{credentials:'same-origin'});
    if (!response.ok) throw new Error('Could not load security settings.');
    const state = await response.json();
    if (!state.local_enabled) { summary.textContent = 'Local sign-in is disabled. Manage multi-factor authentication with your sign-in provider.'; form.hidden = true; return; }
    summary.textContent = state.enabled ? `Two-factor authentication is enabled. ${state.totp ? 'Authenticator app; ' : ''}${state.credentials.length} security keys/passkeys; ${state.recovery_codes_remaining} recovery codes remaining.` : 'Two-factor authentication is not configured for local sign-in.';
    if (state.required) summary.textContent += ' Your administrator requires a second factor.';
  } catch (error) { summary.textContent = error.message; form.hidden = true; }
})();
form.addEventListener('submit', async event => {
  event.preventDefault(); const button = form.querySelector('button'); button.disabled = true;
  const input = document.getElementById('securityPassword');
  try {
    const response = await fetch('/api/v1/auth/mfa/manage/start',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:input.value})});
    const data = await response.json(); input.value = '';
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Verification failed.');
    location.assign('/mfa.html?next=' + encodeURIComponent('/account.html#security'));
  } catch (error) { summary.textContent = error.message; } finally { button.disabled = false; }
});
