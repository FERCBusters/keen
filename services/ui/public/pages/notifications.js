// Notifications: the records that concern the signed-in person, changed by
// someone else. Opening one marks it as read and goes to its record; Mark all
// as read clears the rest without opening them.
import {
  apiGet,
  apiPost,
  errorText,
  fmtTs,
  initNavbar,
  isoDateToDisplay,
  localPath,
} from '/app.js';

const apiRoot = '/api/v1/me/inbox';
const ICONS = {
  audit_started: 'bi-clipboard-check',
  quiz_round: 'bi-patch-question',
  meeting_minutes: 'bi-journal-text',
  measure_outside: 'bi-graph-down-arrow',
};
const $ = (suffix) => document.getElementById(`inbox-${suffix}`);
let items = [],
  unreadCount = 0,
  request = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function notice(message, tone = 'danger') {
  const box = $('status');
  box.textContent = message;
  box.hidden = !message;
  box.className = message ? `alert alert-${tone} small py-2` : '';
  box.setAttribute('role', message && tone === 'danger' ? 'alert' : 'status');
}
export function describe(item) {
  const parts = [];
  if (item.date) parts.push(`${item.date_label} ${isoDateToDisplay(item.date)}`);
  parts.push(item.actor ? `by ${item.actor}` : 'by Keen');
  return parts.join(' · ');
}
function row(item) {
  const unread = !item.read_at;
  const link = element(
    'a',
    `list-group-item list-group-item-action d-flex gap-3 align-items-start${unread ? ' inbox-unread' : ''}`,
  );
  // Links stay on this site: a path, never another origin or a script.
  link.href = localPath(item.href) || '#';
  link.dataset.item = item.id;
  const icon = element('i', `bi ${ICONS[item.kind] || 'bi-bell'} fs-5 mt-1`);
  icon.setAttribute('aria-hidden', 'true');
  const body = element('div', 'flex-grow-1 text-break');
  const top = element('div', 'd-flex flex-wrap justify-content-between gap-2');
  const heading = element('span', unread ? 'fw-semibold' : '');
  if (unread) heading.append(element('span', 'badge text-bg-primary me-2', 'New'));
  heading.append(`${item.summary}: `, element('span', '', item.title));
  top.append(heading, element('span', 'small-muted text-nowrap', fmtTs(item.created_at)));
  body.append(top, element('div', 'small-muted', describe(item)));
  link.append(icon, body);
  return link;
}
function render() {
  const list = $('list');
  list.replaceChildren(...items.map(row));
  const unreadOnly = $('unread').checked;
  if (!items.length)
    list.append(
      element(
        'p',
        'small-muted mb-0',
        unreadOnly ? 'No unread notifications.' : 'No notifications yet.',
      ),
    );
  $('meta').textContent = unreadCount
    ? `${unreadCount} unread notification${unreadCount === 1 ? '' : 's'}`
    : 'Everything is read.';
  $('read-all').disabled = !unreadCount;
}
export async function load() {
  const current = ++request;
  try {
    const data = await apiGet(`${apiRoot}${$('unread').checked ? '?unread=true' : ''}`);
    if (current !== request) return;
    items = data.items;
    unreadCount = data.unread_count;
    notice('');
    render();
  } catch (error) {
    if (current !== request) return;
    items = [];
    unreadCount = 0;
    render();
    $('meta').textContent = 'Notifications are unavailable.';
    notice(`Notifications could not be loaded: ${errorText(error)}`);
  }
}
async function markRead(id) {
  const result = await apiPost(`${apiRoot}/${encodeURIComponent(id)}/read`, {});
  unreadCount = result.unread_count;
  window.keenRefreshBell?.();
}
// Opening an unread notification marks it first; a new tab keeps this page.
async function open(event) {
  const link = event.target.closest('a[data-item]');
  if (!link || !link.classList.contains('inbox-unread')) return;
  if (event.type === 'auxclick' && event.button !== 1) return;
  const newTab =
    event.type === 'auxclick' || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey;
  if (!newTab) event.preventDefault();
  const item = items.find((entry) => entry.id === link.dataset.item);
  try {
    await markRead(link.dataset.item);
    if (item) item.read_at = new Date().toISOString();
  } catch {
    /* Opening the record matters more than the mark; it stays unread. */
  }
  if (newTab) render();
  else location.assign(link.getAttribute('href'));
}
async function readAll() {
  const button = $('read-all');
  button.disabled = true;
  try {
    await apiPost(`${apiRoot}/read-all`, {});
    window.keenRefreshBell?.();
    await load();
  } catch (error) {
    notice(`Notifications could not be marked as read: ${errorText(error)}`);
    button.disabled = false;
  }
}

await initNavbar();
$('list').addEventListener('click', open);
$('list').addEventListener('auxclick', open);
$('unread').addEventListener('change', load);
$('read-all').addEventListener('click', readAll);
await load();
