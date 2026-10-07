import {apiPost} from '/app.js';
const token=new URLSearchParams(location.hash.slice(1)).get('token');
history.replaceState(null, '', location.pathname);
const button=document.getElementById('confirmEmail'), status=document.getElementById('emailProofStatus');
if (!token) { button.hidden=true; status.textContent='Open the verification link from your email while signed into the account that requested it.'; }
button.addEventListener('click', async () => {
  button.disabled=true;
  try { const result=await apiPost('/api/v1/me/sso-emails/confirm', {token}); status.textContent=result.email + ' is verified and available for SSO linking.'; button.hidden=true; }
  catch (error) { status.textContent=error.message + ' Sign into the requesting account and reopen the email link, or request a fresh link from Account → Security.'; button.disabled=false; }
});
