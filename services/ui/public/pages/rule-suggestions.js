// Suggested mapping rules: rules Keen proposes from the latest unmapped events.
// Creating one saves an ordinary rule and maps past events in the background;
// nothing is mapped before then. The rule editor opens a suggestion to edit it
// first, through the keen-suggested-rule event.
import { apiGet, apiPost, errorText, fmtTs } from '/app.js';

const apiRoot = '/api/v1/admin';
const $ = (suffix) => document.getElementById(`ec-suggestions${suffix}`);
// After Check now, the list is read again this often until the run finishes.
const CHECK_POLL_MS = 5000;
const CHECK_POLLS = 24;
let data = null,
  request = 0,
  checking = false;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(label, className, handler) {
  const node = element('button', `btn btn-sm ${className}`, label);
  node.type = 'button';
  node.addEventListener('click', handler);
  return node;
}
function notice(message, tone = 'success') {
  const box = $('-notice');
  box.textContent = message;
  box.hidden = !message;
  box.className = message ? `alert alert-${tone} small py-2 mb-2` : '';
  box.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
}
function plural(count, noun) {
  return `${Number(count).toLocaleString()} ${noun}${count === 1 ? '' : 's'}`;
}
// The events a suggestion was found in, on the Events page.
export function eventsHref(item, framework) {
  const params = new URLSearchParams();
  params.set('source', item.sources.map((s) => s.source).join(','));
  params.set('unmapped', 'true');
  if (item.action) params.append('f', `action:equals:${item.action}`);
  if (framework) params.set('framework', framework);
  return `/events.html?${params}`;
}
function describeRun() {
  if (!data.enabled)
    return 'Suggestions are off while evidence browsing is turned off under Features.';
  const run = data.last_run;
  if (!run) return 'Not checked yet. Keen checks every 6 hours; choose Check now to check sooner.';
  if (run.status === 'failed') return `The check at ${fmtTs(run.finished_at)} failed: ${run.error}`;
  return (
    `Checked ${fmtTs(run.finished_at)}: the latest ${plural(data.sample_size, 'event')} of each source from the last ${data.window_days} days, ` +
    `${Number(run.unmapped).toLocaleString()} of ${Number(run.examined).toLocaleString()} not mapped to ${data.framework_name}. ` +
    `An activity needs ${data.min_events} unmapped events to be suggested. Nothing is mapped until you create a rule.`
  );
}
function chosenRefs(card) {
  return [...card.querySelectorAll('input[data-ref]:checked')].map((box) => box.dataset.ref);
}
function controlList(item, open) {
  const fieldset = element('fieldset', 'small mb-2');
  fieldset.append(element('legend', 'small fw-semibold mb-1', 'Map to'));
  for (const control of item.controls) {
    const row = element('div', 'form-check');
    const box = element('input', 'form-check-input');
    box.type = 'checkbox';
    box.id = `ec-suggestion-${item.id}-${control.ref}`;
    box.dataset.ref = control.ref;
    box.checked = !!control.selected;
    box.disabled = !open;
    const label = element('label', 'form-check-label', `${control.ref} ${control.title || ''}`);
    label.htmlFor = box.id;
    row.append(box, label);
    if (control.reasons?.length)
      row.append(element('div', 'small-muted', control.reasons.join('. ') + '.'));
    fieldset.append(row);
  }
  return fieldset;
}
function sampleList(item) {
  const details = element('details', 'small mb-2');
  details.append(element('summary', '', 'Recent events'));
  const list = element('ul', 'list-unstyled mb-0 mt-1');
  for (const sample of item.samples) {
    const entry = element('li', 'mb-1');
    const link = element('a', '', fmtTs(sample.timestamp));
    link.href = `/event.html?id=${encodeURIComponent(sample.id)}`;
    link.target = '_blank';
    link.rel = 'noopener';
    entry.append(link, ` ${sample.summary}`);
    list.append(entry);
  }
  if (!item.samples.length)
    list.append(element('li', 'small-muted', 'These events are no longer held.'));
  details.append(list);
  return details;
}
async function act(card, work) {
  for (const control of card.querySelectorAll('button')) control.disabled = true;
  try {
    await work();
  } catch (error) {
    notice(errorText(error), 'danger');
    for (const control of card.querySelectorAll('button')) control.disabled = false;
  }
}
function changed() {
  window.dispatchEvent(new CustomEvent('keen-rules-changed'));
  window.keenRefreshBell?.();
}
async function create(item, card) {
  const refs = chosenRefs(card);
  if (!refs.length) {
    notice('Choose at least one control to map to.', 'danger');
    return;
  }
  await act(card, async () => {
    const { version } = await apiGet(`${apiRoot}/mapping-rules`);
    const result = await apiPost(
      `${apiRoot}/rule-suggestions/${encodeURIComponent(item.id)}/apply`,
      { version, refs },
    );
    const past =
      result.backfill?.status === 'queued'
        ? ' Past events are being mapped in the background.'
        : result.backfill?.status === 'no_previous_evidence'
          ? ' There were no past events to map.'
          : '';
    notice(`Rule “${result.rule.description}” created.${past}`);
    changed();
    await load();
  });
}
async function decide(item, card, status, message) {
  await act(card, async () => {
    await apiPost(`${apiRoot}/rule-suggestions/${encodeURIComponent(item.id)}/status`, { status });
    notice(message);
    window.keenRefreshBell?.();
    await load();
  });
}
// The editor opens the rule, with the controls ticked here, as a new rule.
function editFirst(item, card) {
  const refs = new Set(chosenRefs(card));
  const rule = {
    ...structuredClone(item.rule),
    map_to: item.controls
      .filter((control) => refs.has(control.ref))
      .map((control) => ({ framework: data.framework, ref: control.ref, kind: control.kind })),
  };
  window.dispatchEvent(
    new CustomEvent('keen-suggested-rule', {
      detail: { id: item.id, rule, eventCount: item.event_count },
    }),
  );
}
function card(item) {
  const open = item.status === 'open';
  const node = element('div', 'border rounded p-2 mb-2');
  node.dataset.suggestion = item.id;
  const heading = element('div', 'd-flex flex-wrap align-items-center gap-2');
  heading.append(
    element('strong', '', item.rule.description),
    element('span', 'badge text-bg-secondary', plural(item.event_count, 'unmapped event')),
  );
  const seen = element(
    'p',
    'small mb-1',
    `From ${item.sources.map((s) => s.label).join(', ')}, between ${fmtTs(item.first_seen)} and ${fmtTs(item.last_seen)}. `,
  );
  const events = element('a', '', 'View these events');
  events.href = eventsHref(item, data.framework);
  events.target = '_blank';
  events.rel = 'noopener';
  seen.append(events);
  node.append(heading, seen);
  if (!open)
    node.append(
      element(
        'p',
        'small-muted mb-1',
        `Dismissed${item.decided_by ? ` by ${item.decided_by}` : ''} ${fmtTs(item.decided_at)}.`,
      ),
    );
  node.append(controlList(item, open), sampleList(item));
  const actions = element('div', 'd-flex flex-wrap gap-2');
  const name = item.rule.description;
  if (open) {
    const createButton = button('Create rule', 'btn-primary', () => create(item, node));
    createButton.setAttribute('aria-label', `Create rule ${name} and map past events`);
    const edit = button('Edit first', 'btn-outline-primary', () => editFirst(item, node));
    edit.setAttribute('aria-label', `Edit ${name} before creating it`);
    const dismiss = button('Dismiss', 'btn-outline-secondary', () =>
      decide(item, node, 'dismissed', `Dismissed “${name}”. It will not be suggested again.`),
    );
    dismiss.setAttribute('aria-label', `Dismiss ${name}`);
    actions.append(createButton, edit, dismiss);
  } else {
    const restore = button('Restore', 'btn-outline-primary', () =>
      decide(item, node, 'open', `Restored “${name}”.`),
    );
    restore.setAttribute('aria-label', `Restore ${name}`);
    actions.append(restore);
  }
  node.append(actions);
  return node;
}
function render() {
  const dismissed = $('-dismissed').checked;
  const items = data?.items || [];
  $('-count').textContent = data ? ` · ${data.open_count} to review` : '';
  if (!checking) $('-meta').textContent = data ? describeRun() : '';
  $('-refresh').disabled = checking || !data?.enabled;
  const list = $('-list');
  list.replaceChildren(...items.map(card));
  if (data && !items.length)
    list.append(
      element(
        'p',
        'small-muted',
        dismissed ? 'No dismissed suggestions.' : 'No suggestions to review.',
      ),
    );
}
export async function load() {
  const current = ++request;
  const status = $('-dismissed').checked ? 'dismissed' : 'open';
  try {
    const result = await apiGet(`${apiRoot}/rule-suggestions?status=${status}`);
    if (current !== request) return;
    data = result;
  } catch (error) {
    if (current !== request) return;
    data = null;
    render();
    $('-meta').textContent = `Suggested rules are unavailable: ${errorText(error)}`;
    return;
  }
  render();
}
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function checkNow() {
  checking = true;
  const before = data?.last_run?.finished_at || null;
  render();
  try {
    const result = await apiPost(`${apiRoot}/rule-suggestions/refresh`, {});
    if (result.status === 'recent') {
      notice('Checked less than a minute ago; the suggestions below are current.');
      return;
    }
    $('-meta').textContent = 'Checking for suggestions. They appear here when the check finishes.';
    for (let poll = 0; poll < CHECK_POLLS; poll++) {
      await pause(CHECK_POLL_MS);
      await load();
      if ((data?.last_run?.finished_at || null) !== before) {
        window.keenRefreshBell?.();
        return;
      }
    }
    notice('The check is taking a while; the list updates when you return.', 'warning');
  } catch (error) {
    notice(errorText(error), 'danger');
  } finally {
    checking = false;
    if (data) render();
  }
}
// The bell and the home page link here, past the Sources view of this tab. The
// panel opens at once and is scrolled to once the list has loaded.
function reveal({ scroll = true } = {}) {
  if ((location.hash || '').toLowerCase() !== '#rule-suggestions') return;
  window.dispatchEvent(new Event('keen-show-evidence-mapping'));
  const panel = $('');
  panel.open = true;
  if (scroll) scrollTo(panel);
}
// The admin page shows this tab once its navigation has loaded; a panel not
// laid out yet is scrolled to when a tab is shown.
function scrollTo(panel) {
  if (!panel.getClientRects().length) {
    document.addEventListener('shown.bs.tab', () => scrollTo(panel), { once: true });
    return;
  }
  panel.scrollIntoView({ block: 'start' });
  panel.querySelector('summary').focus({ preventScroll: true });
}
if ($('')) {
  $('-dismissed').addEventListener('change', () => {
    notice('');
    load();
  });
  $('-refresh').addEventListener('click', checkNow);
  window.addEventListener('keen-rule-suggestions-changed', () => load());
  window.addEventListener('hashchange', () => reveal());
  reveal({ scroll: false });
  load().then(() => reveal());
}
