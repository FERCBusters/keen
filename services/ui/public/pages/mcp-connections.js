import { apiDelete, apiGet, confirmAction, errorText } from '/app.js';
const card = document.getElementById('mcpCard');
const status = document.getElementById('mcpStatus');
const list = document.getElementById('mcpGrants');
const url = document.getElementById('mcpUrl');
// Timestamps are UTC; the day is enough to recognise a connection.
const day = (value) => (value ? value.slice(0, 10) : 'never');
function row(grant) {
  const item = document.createElement('li');
  item.className = 'd-flex align-items-start justify-content-between gap-3 py-2 border-top';
  const text = document.createElement('div');
  const name = document.createElement('div');
  name.className = 'fw-semibold';
  name.textContent = grant.client_name;
  const detail = document.createElement('div');
  detail.className = 'small text-body-secondary';
  detail.textContent =
    `Returns to ${grant.redirect_host}. Reads: ${grant.scopes.join(', ') || 'frameworks and controls only'}. ` +
    `Approved ${day(grant.created_at)}, last used ${day(grant.last_used_at)}, ends ${day(grant.expires_at)}.`;
  text.append(name, detail);
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-sm btn-outline-danger';
  button.textContent = 'Revoke';
  button.addEventListener('click', async () => {
    if (
      !(await confirmAction({
        title: 'Revoke AI connection',
        message: `Revoke ${grant.client_name}? It stops reading Keen at once and must ask you again to reconnect.`,
        confirmLabel: 'Revoke',
      }))
    )
      return;
    button.disabled = true;
    try {
      await apiDelete('/api/v1/me/mcp/grants/' + encodeURIComponent(grant.id));
      await load();
    } catch (error) {
      status.textContent = errorText(error);
      button.disabled = false;
    }
  });
  item.append(text, button);
  return item;
}
async function load() {
  const data = await apiGet('/api/v1/me/mcp');
  card.hidden = !data.enabled;
  if (!data.enabled) return;
  url.value = data.mcp_url;
  list.replaceChildren(...data.grants.map(row));
  status.textContent = data.grants.length
    ? 'Connected AI apps:'
    : 'No AI apps are connected to your account.';
}
document.getElementById('mcpCopy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(url.value);
    status.textContent = 'Server address copied.';
  } catch {
    url.select();
    status.textContent = 'Clipboard unavailable. Copy the selected address.';
  }
});
load().catch((error) => {
  card.hidden = false;
  status.textContent = errorText(error);
});
