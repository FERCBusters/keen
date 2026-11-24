import { apiGet, apiPost, confirmAction, errorText } from '/app.js';
const status = document.getElementById('ssoEmailStatus');
const form = document.getElementById('ssoEmailForm');
const list = document.getElementById('ssoEmailList');
const identityStatus = document.getElementById('ssoIdentityStatus');
const identityList = document.getElementById('ssoIdentityList');
async function load() {
  const data = await apiGet('/api/v1/me/sso-emails');
  form.hidden = !data.smtp_configured;
  status.textContent = data.smtp_configured
    ? 'Verify each address you want to use for SSO. Adding or removing an address requires a sign-in within the last five minutes.'
    : 'Your administrator must configure SMTP before you can verify an SSO email address.';
  list.replaceChildren();
  for (const address of data.addresses) {
    const item = document.createElement('li');
    item.className = 'd-flex align-items-center justify-content-between gap-3 py-2';
    const label = document.createElement('span');
    label.textContent = address.email + ' - verified';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm btn-outline-danger';
    button.textContent = 'Remove';
    button.addEventListener('click', async () => {
      if (
        !(await confirmAction({
          title: 'Remove SSO email address',
          message:
            'Remove this address from future SSO linking? An identity already linked through it keeps signing in until you remove it under Linked sign-in identities.',
          confirmLabel: 'Remove',
        }))
      )
        return;
      button.disabled = true;
      try {
        await apiPost('/api/v1/me/sso-emails/remove', { email: address.email });
        await load();
      } catch (error) {
        status.textContent = errorText(error);
        button.disabled = false;
      }
    });
    item.append(label, button);
    list.append(item);
  }
  if (!data.addresses.length) {
    const item = document.createElement('li');
    item.textContent = 'No verified SSO email addresses.';
    list.append(item);
  }
}
async function loadIdentities() {
  const data = await apiGet('/api/v1/me/sso-identities');
  identityList.replaceChildren();
  identityStatus.textContent = data.identities.length
    ? 'Removing an identity needs a sign-in within the last five minutes.'
    : 'No sign-in identities are linked.';
  for (const identity of data.identities) {
    const item = document.createElement('li');
    item.className = 'd-flex align-items-center justify-content-between gap-3 py-2';
    const label = document.createElement('span');
    const who = identity.email || identity.name || 'unnamed identity';
    label.textContent = `${identity.provider}: ${who} - linked ${identity.linked_at.slice(0, 10)}`;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm btn-outline-danger';
    button.textContent = 'Remove';
    button.addEventListener('click', async () => {
      if (
        !(await confirmAction({
          title: 'Remove sign-in identity',
          message:
            'Remove this identity? It stops signing in to your account, the address it was linked through is removed, and every session ends, this one included.',
          confirmLabel: 'Remove',
        }))
      )
        return;
      button.disabled = true;
      try {
        await apiPost('/api/v1/me/sso-identities/remove', { id: identity.id });
        location.assign('/login.html');
      } catch (error) {
        identityStatus.textContent = errorText(error);
        button.disabled = false;
      }
    });
    item.append(label, button);
    identityList.append(item);
  }
}
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    const result = await apiPost('/api/v1/me/sso-emails/request', {
      email: document.getElementById('ssoEmail').value,
    });
    status.textContent = result.already_verified
      ? 'This address is already verified.'
      : 'Verification email sent. Stay signed into this account and open the link within 30 minutes. Requesting another link replaces this one.';
  } catch (error) {
    status.textContent = errorText(error);
  } finally {
    button.disabled = false;
  }
});
load().catch((error) => {
  status.textContent = errorText(error);
});
loadIdentities().catch((error) => {
  identityStatus.textContent = errorText(error);
});
