import {apiGet, apiPost} from '/app.js';
const status = document.getElementById('ssoEmailStatus');
const form = document.getElementById('ssoEmailForm');
const list = document.getElementById('ssoEmailList');
async function load() {
  const data = await apiGet('/api/v1/me/sso-emails');
  form.hidden = !data.smtp_configured;
  status.textContent = data.smtp_configured ? 'Verify each address you want to use for SSO. Adding or removing an address requires a sign-in within the last five minutes.' : 'Your administrator must configure SMTP before you can verify an SSO email address.';
  list.replaceChildren();
  for (const address of data.addresses) {
    const item = document.createElement('li'); item.className = 'd-flex align-items-center justify-content-between gap-3 py-2';
    const label = document.createElement('span'); label.textContent = address.email + ' — verified';
    const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-sm btn-outline-danger'; button.textContent = 'Remove';
    button.addEventListener('click', async () => {
      if (!confirm('Remove this address from future SSO linking? Existing linked sign-in identities will remain active.')) return;
      button.disabled = true;
      try { await apiPost('/api/v1/me/sso-emails/remove', {email:address.email}); await load(); }
      catch (error) { status.textContent = error.message; button.disabled = false; }
    });
    item.append(label, button); list.append(item);
  }
  if (!data.addresses.length) { const item=document.createElement('li'); item.textContent='No verified SSO email addresses.'; list.append(item); }
}
form.addEventListener('submit', async event => {
  event.preventDefault(); const button=form.querySelector('button'); button.disabled=true;
  try {
    const result=await apiPost('/api/v1/me/sso-emails/request', {email:document.getElementById('ssoEmail').value});
    status.textContent=result.already_verified ? 'This address is already verified.' : 'Verification email sent. Stay signed into this account and open the link within 30 minutes. Requesting another link replaces this one.';
  } catch (error) { status.textContent=error.message; } finally { button.disabled=false; }
});
load().catch(error => { status.textContent=error.message; });
