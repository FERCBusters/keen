const $ = id => document.getElementById(id);
let state, codesPending = false, method = null, busy = false;
function message(text, error = false) { $('message').textContent = text; $('message').className = 'alert ' + (error ? 'alert-danger' : 'alert-success'); }
async function post(path, body = {}) {
  const response = await fetch('/api/v1/auth/mfa/' + path, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Verification failed. Please start again.');
  return data;
}
async function run(action) {
  if (busy) return;
  busy = true;
  const buttons = [...document.querySelectorAll('button')]; buttons.forEach(b => b.disabled = true);
  try { await action(); } catch (error) { message(error.message, true); }
  finally { busy = false; buttons.forEach(b => b.disabled = false); $('finish').disabled = codesPending && !$('savedCodes').checked; }
}
function decode(value) { const raw = atob(value.replace(/-/g,'+').replace(/_/g,'/')); return Uint8Array.from(raw, c => c.charCodeAt(0)); }
function encode(value) { return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,''); }
function serialize(credential) {
  const response = {};
  for (const key of ['clientDataJSON','attestationObject','authenticatorData','signature','userHandle']) {
    if (credential.response[key]) response[key] = encode(credential.response[key]);
  }
  if (credential.response.getTransports) response.transports = credential.response.getTransports();
  return {id:credential.id,rawId:encode(credential.rawId),type:credential.type,response,clientExtensionResults:credential.getClientExtensionResults()};
}
function options(data) {
  data.challenge = decode(data.challenge);
  if (data.user) data.user.id = decode(data.user.id);
  for (const key of ['allowCredentials','excludeCredentials']) for (const item of data[key] || []) item.id = decode(item.id);
  return data;
}
function showCodes(data) {
  if (!data.recovery_codes) return;
  codesPending = true; $('codesPanel').hidden = false; $('savedCodes').checked = false;
  $('recoveryCodes').textContent = data.recovery_codes.join('\n');
}
async function refresh() {
  state = await post('state');
  const managing = ['manage','enrol'].includes(state.stage);
  $('intro').textContent = state.stage === 'verify' ? 'Verify your second factor to continue.' : managing ? 'Choose an authenticator for your local KEEN account.' : 'Verification complete.';
  $('verify').hidden = state.stage !== 'verify'; $('manage').hidden = !managing;
  $('chooseKey').hidden = !state.credentials.length;
  $('chooseTotp').hidden = !state.totp;
  $('chooseRecovery').hidden = !state.recovery_codes_remaining;
  $('addTotp').hidden = state.totp;
  $('regenerate').hidden = !state.totp && !state.credentials.length;
  $('remaining').textContent = `${state.recovery_codes_remaining} recovery codes remaining.`;
  $('factors').replaceChildren();
  const factors = [...(state.totp ? [{id:'totp', name:'Authenticator app'}] : []), ...state.credentials];
  for (const factor of factors) {
    const row = document.createElement('div'); row.className = 'd-flex align-items-center justify-content-between gap-3 border-bottom py-2';
    const label = document.createElement('span'); label.textContent = factor.name;
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn btn-sm btn-outline-danger'; remove.textContent = 'Remove';
    remove.addEventListener('click', () => run(async () => { if (!confirm(`Remove ${factor.name}?`)) return; await post('remove',{id:factor.id}); await refresh(); }));
    row.append(label,remove); $('factors').append(row);
  }
  $('finish').hidden = !['ready','manage'].includes(state.stage) || (state.required && !factors.length);
  $('finish').disabled = busy || (codesPending && !$('savedCodes').checked);
}
function preferredMethod() {
  let saved;
  try { saved = localStorage.getItem('keen.mfa.method:' + state.username); } catch {}
  if (saved === 'totp' && state.totp) return 'totp';
  if (state.credentials.length) return 'webauthn';
  return state.totp ? 'totp' : 'recovery';
}
function selectMethod(selected) {
  method = selected;
  $('message').className = 'alert d-none';
  $('keyPrompt').hidden = selected !== 'webauthn';
  $('codeForm').hidden = selected === 'webauthn';
  $('alternatives').hidden = true;
  $('intro').textContent = selected === 'webauthn' ? 'Verify with your security key or passkey.' : selected === 'totp' ? 'Enter the code from your authenticator app.' : 'Enter one of your unused recovery codes.';
  $('codeLabel').textContent = selected === 'recovery' ? 'Recovery code' : 'Six-digit authenticator code';
  $('code').value = '';
  $('code').inputMode = selected === 'totp' ? 'numeric' : 'text';
  if (selected !== 'webauthn') $('code').focus();
}
function alternatives(error) {
  $('alternatives').hidden = false;
  message(error?.name === 'NotAllowedError' ? 'Security key verification was cancelled or timed out. Try again or choose another method.' : error.message, true);
}
async function finishLogin() {
  if (codesPending && !$('savedCodes').checked) return;
  await post('finish');
  const raw = new URLSearchParams(location.search).get('next') || (state.purpose === 'manage' ? '/account.html#security' : '/');
  let next;
  try { next = new URL(raw, location.origin); } catch { next = new URL('/', location.origin); }
  location.replace(next.origin === location.origin && !['/login.html','/mfa.html'].includes(next.pathname) ? next.pathname + next.search + next.hash : '/');
}
async function afterVerification() {
  // Remember the successful normal factor on this browser; recovery is never a preference.
  if (method !== 'recovery') {
    try { localStorage.setItem('keen.mfa.method:' + state.username, method); } catch {}
  }
  await refresh();
  if (state.purpose === 'login' && state.stage === 'ready') await finishLogin();
}
async function verifyKey() {
  try {
    if (!window.PublicKeyCredential || !navigator.credentials?.get) throw new Error('This browser cannot use security keys/passkeys. Choose another method.');
    const credential = await navigator.credentials.get({publicKey:options(await post('webauthn/options'))});
    if (!credential) throw new Error('Verification was cancelled. Try again or choose another method.');
    await post('webauthn/verify',{credential:serialize(credential)});
  } catch (error) { alternatives(error); return; }
  await afterVerification();
}
$('codeForm').addEventListener('submit', event => { event.preventDefault(); run(async () => {
  try { await post('verify-code',{kind:method,code:$('code').value}); }
  catch (error) { alternatives(error); return; }
  $('code').value = ''; await afterVerification();
}); });
$('useKey').addEventListener('click', () => run(async () => { selectMethod('webauthn'); await verifyKey(); }));
$('chooseKey').addEventListener('click', () => run(async () => { selectMethod('webauthn'); await verifyKey(); }));
$('chooseTotp').addEventListener('click', () => selectMethod('totp'));
$('chooseRecovery').addEventListener('click', () => selectMethod('recovery'));
$('showAlternatives').addEventListener('click', () => { $('alternatives').hidden = false; });
$('addKey').addEventListener('click', () => run(async () => {
  if (!window.PublicKeyCredential) throw new Error('Use a browser supporting WebAuthn over HTTPS.');
  const name = prompt('Name this authenticator (for example, Work security key):','Security key / passkey'); if (!name) return;
  const credential = await navigator.credentials.create({publicKey:options(await post('webauthn/register-options'))});
  if (!credential) throw new Error('Registration was cancelled.');
  showCodes(await post('webauthn/register',{name,credential:serialize(credential)})); await refresh();
}));
$('addTotp').addEventListener('click', () => run(async () => { const result = await post('totp/start'); $('qr').src = result.qr; $('secret').textContent = result.secret; $('totpSetup').hidden = false; }));
$('confirmForm').addEventListener('submit', event => { event.preventDefault(); run(async () => { showCodes(await post('totp/confirm',{code:$('confirmCode').value})); $('confirmCode').value = ''; $('secret').textContent = ''; $('qr').removeAttribute('src'); $('totpSetup').hidden = true; await refresh(); }); });
$('regenerate').addEventListener('click', () => run(async () => { if (!confirm('Replace all unused recovery codes? Save the replacement codes before leaving.')) return; showCodes(await post('recovery/regenerate')); await refresh(); }));
$('copyCodes').addEventListener('click', () => run(async () => { await navigator.clipboard.writeText($('recoveryCodes').textContent); message('Recovery codes copied. Store them securely.'); }));
$('savedCodes').addEventListener('change', () => { $('finish').disabled = codesPending && !$('savedCodes').checked; });
$('finish').addEventListener('click', () => run(finishLogin));
$('cancel').addEventListener('click', event => { event.preventDefault(); run(async () => { await post('cancel'); location.assign('/login.html'); }); });
run(async () => {
  await refresh();
  if (state.stage === 'verify') {
    selectMethod(preferredMethod());
    if (method === 'webauthn') await verifyKey();
  }
});
