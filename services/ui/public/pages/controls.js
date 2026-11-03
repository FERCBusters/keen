import {
  errorText,
  initNavbar,
  apiGet,
  apiPost,
  esc,
  fmtTs,
  toast,
  qs,
  qbool,
  setQuery,
  enableTableSorting,
  attachSavedSearchAutocomplete,
  resolveSavedSearchUrl,
  getCurrentFramework,
  withFramework,
  localPath,
  showTableLoading,
  progressRing,
} from '/app.js';
import { measuresMet } from '/pages/control-requirements.js';
import { featureEnabled } from '/pages/feature-availability.js';

const me = await initNavbar();
const framework = getCurrentFramework();

enableTableSorting();

// Keep top action links on the selected framework.
for (const a of Array.from(document.querySelectorAll('a[href]'))) {
  const href = String(a.getAttribute('href') || '');
  if (href.startsWith('/events.html?unmapped=true')) {
    a.setAttribute('href', withFramework(href, framework));
  }
}

// Saved-search autocomplete for the front-page global event search
attachSavedSearchAutocomplete(document.getElementById('frontQ'));

const status = document.getElementById('status');
const tbody = document.getElementById('rows');

const inpFilter = document.getElementById('filterText');
const chkGaps = document.getElementById('onlyGaps');
const chkInScope = document.getElementById('onlyInScope');
const chkUnmet = document.getElementById('onlyUnmet');

// Init filters from query string so they can be bookmarked/saved
if (inpFilter) inpFilter.value = qs('q', '');
if (chkGaps) chkGaps.checked = qbool('gaps', false);
if (chkUnmet) chkUnmet.checked = qbool('unmet', false);
if (chkInScope) {
  // Default is true (in-scope only). Only when in_scope=0 do we show all.
  const v = qs('in_scope', null);
  chkInScope.checked = v === null ? true : qbool('in_scope', true);
}

let all = [];
// Each control's policies and measures, for people who can read ISMS records.
let isms = null;
const canIsms = !!(me?.is_admin || me?.can_view_isms) && featureEnabled(me, 'isms');

const plural = (count, one) => `${count} ${one}${count === 1 ? '' : 's'}`;
// The parts of a control's Requirements tab this reader can see, met or not:
// evidence mapped, policies and processes linked, and linked measures meeting
// their targets.
function requirementParts(c) {
  const parts = [];
  if (c.evidence_count !== null && featureEnabled(me, 'evidence'))
    parts.push(['Evidence', c.evidence_count > 0, plural(c.evidence_count || 0, 'event')]);
  const found = isms?.items?.[c.id];
  if (isms?.policies) {
    const count = found?.policies || 0;
    parts.push(['Policies and processes', count > 0, `${count} linked`]);
  }
  if (isms?.measures) {
    const m = {
      count: found?.measures || 0,
      met: found?.met || 0,
      judged: found?.judged || 0,
      unrecorded: found?.unrecorded || 0,
    };
    const detail = m.count
      ? `${m.met} of ${m.judged} meeting target${m.unrecorded ? `, ${m.unrecorded} without a result` : ''}`
      : 'none linked';
    parts.push(['Measures and tests', measuresMet(m), detail]);
  }
  return parts;
}
const allMet = (parts) => parts.length > 0 && parts.every(([, ok]) => ok);

function requirementsCell(c) {
  const parts = requirementParts(c);
  if (!parts.length) return '<td class="small-muted" data-sort="-1">-</td>';
  const met = parts.filter(([, ok]) => ok).length;
  const percent = Math.round((met / parts.length) * 100);
  const summary = parts
    .map(([name, ok, detail]) => `${name}: ${ok ? 'met' : 'not met'} (${detail})`)
    .join('; ');
  const ring = progressRing(
    percent,
    `${met} of ${parts.length} requirements met. ${summary}.`,
    `control-ring${met === parts.length ? ' is-met' : ''}`,
  );
  const href = withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework);
  return `<td data-sort="${percent}"><a class="d-inline-flex align-items-center gap-2 text-decoration-none" href="${esc(href)}" title="${esc(summary)}">${ring}<span class="small">${met === parts.length ? 'Met' : `${met} of ${parts.length}`}</span></a></td>`;
}

function render() {
  const qRaw = (inpFilter?.value || '').trim();
  const q = qRaw.toLowerCase();
  const onlyGaps = !!chkGaps?.checked;
  const onlyInScope = !!chkInScope?.checked;
  const onlyUnmet = !!chkUnmet?.checked;

  // Keep URL in sync so searches can be bookmarked/saved
  setQuery({
    q: qRaw || null,
    gaps: onlyGaps ? '1' : null,
    unmet: onlyUnmet ? '1' : null,
    in_scope: onlyInScope ? null : '0',
  });

  tbody.innerHTML = '';
  const view = all.filter((c) => {
    if (onlyInScope && !c.in_scope) return false;
    if (onlyGaps && (c.evidence_count === null || (c.evidence_count || 0) > 0)) return false;
    if (onlyUnmet && allMet(requirementParts(c))) return false;
    if (!q) return true;
    return `${c.ref} ${c.title || ''} ${c.justification || ''}`.toLowerCase().includes(q);
  });

  for (const c of view) {
    const scope = c.in_scope
      ? `<span class="badge badge-soft">in</span>`
      : `<span class="badge text-bg-light">out</span>`;

    // Evidence counts are withheld (null) for accounts without events.read.
    const withheld = c.evidence_count === null;
    const ev = c.evidence_count || 0;
    const evHtml = withheld
      ? '<span class="small-muted">-</span>'
      : ev > 0
        ? `<a href="${withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=evidence`, framework)}" class="badge badge-soft">${esc(ev)}</a>`
        : `<span class="badge text-bg-light">0</span>`;

    const lastIso = c.last_evidence || '';
    const lastTxt = c.last_evidence ? fmtTs(c.last_evidence) : '-';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td data-sort="${esc(c.ref)}"><a class="fw-semibold" href="${withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework)}">${esc(c.ref)}</a></td>
      <td class="wrap">${esc(c.title || '')}</td>
      <td class="small-muted" data-sort="${esc(c.justification || '')}">${esc(c.justification || '-')}</td>
      ${requirementsCell(c)}
      <td data-sort="${ev}">${evHtml}</td>
      <td class="small-muted" data-sort="${esc(lastIso)}">${esc(lastTxt)}</td>
      <td data-sort="${c.in_scope ? 1 : 0}">${scope}</td>`;
    tbody.appendChild(tr);
  }

  if (view.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="7" class="small-muted p-4">No controls match your filters.</td>`;
    tbody.appendChild(tr);
  }
}

function setKpiText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function applySummary(summary) {
  setKpiText('kpiEventsMapped', summary?.events?.mapped ?? '-');
  setKpiText('kpiEventsUnmapped', summary?.events?.unmapped ?? '-');
  setKpiText(
    'kpiControlsWith',
    summary?.events ? all.filter((c) => c.in_scope && (c.evidence_count || 0) > 0).length : '-',
  );
  setKpiText(
    'kpiControlsWithout',
    summary?.events ? all.filter((c) => c.in_scope && !(c.evidence_count || 0)).length : '-',
  );
  const scoped = all.filter((c) => c.in_scope);
  setKpiText(
    'kpiRequirementsMet',
    scoped.some((c) => requirementParts(c).length)
      ? `${scoped.filter((c) => allMet(requirementParts(c))).length} of ${scoped.length}`
      : '-',
  );
}

async function loadSummary() {
  try {
    const summary = await apiGet(
      `/api/v1/stats/summary?framework=${encodeURIComponent(framework)}`,
    );
    applySummary(summary);
  } catch (e) {
    applySummary(null);
    toast(status, `Loaded controls, but failed to load counters: ${errorText(e)}`, 'warning');
  }
}

async function load() {
  status.style.display = 'none';
  showTableLoading(tbody, 7, 'Loading controls');
  setKpiText('kpiEventsMapped', '-');
  setKpiText('kpiEventsUnmapped', '-');
  setKpiText('kpiControlsWith', '-');
  setKpiText('kpiControlsWithout', '-');
  setKpiText('kpiRequirementsMet', '-');
  // Policies and measures come from ISMS; without them, requirements count evidence.
  const requirements = canIsms
    ? apiGet(`/api/v1/isms/control-requirements?framework=${encodeURIComponent(framework)}`).catch(
        () => null,
      )
    : Promise.resolve(null);
  try {
    const data = await apiGet(
      `/api/v1/stats/controls?limit=5000&framework=${encodeURIComponent(framework)}`,
    );
    isms = await requirements;
    all = (data.items || []).filter((item) => item.type !== 'clause');
    if (all.length === 0) {
      toast(status, 'No controls loaded yet. Go to Admin → Frameworks.', 'warning');
      tbody.innerHTML =
        '<tr><td colspan="7" class="small-muted p-4">No controls loaded yet. Go to Admin → Frameworks.</td></tr>';
      await loadSummary();
      return;
    }
    render();
    await loadSummary();
  } catch (e) {
    toast(status, `Failed to load controls: ${errorText(e)}`, 'danger');
    tbody.innerHTML =
      '<tr><td colspan="7" class="small-muted p-4">Failed to load controls.</td></tr>';
    applySummary(null);
  }
}

document.getElementById('reload').addEventListener('click', load);

document.getElementById('resetFilters')?.addEventListener('click', (ev) => {
  ev.preventDefault();
  if (inpFilter) inpFilter.value = '';
  if (chkGaps) chkGaps.checked = false;
  if (chkUnmet) chkUnmet.checked = false;
  if (chkInScope) chkInScope.checked = true;
  render();
});

document.getElementById('saveSearch')?.addEventListener('click', async () => {
  // Ensure URL reflects current filters
  render();
  const qRaw = (inpFilter?.value || '').trim();
  const onlyGaps = !!chkGaps?.checked;
  const onlyInScope = !!chkInScope?.checked;

  const parts = [];
  if (qRaw) parts.push(`q=${qRaw}`);
  if (onlyGaps) parts.push('gaps');
  if (chkUnmet?.checked) parts.push('unmet requirements');
  if (!onlyInScope) parts.push('all-scope');
  const defName = ('Controls' + (parts.length ? ` (${parts.join(', ')})` : '')).slice(0, 128);

  const name = (prompt('Name this saved search', defName) || '').trim();
  if (!name) return;

  const url = location.pathname + (location.search || '');
  try {
    await apiPost('/api/v1/me/saved-searches', { name, url });
    toast(status, 'Saved', 'success');
  } catch (e) {
    toast(status, `Failed to save: ${errorText(e)}`, 'danger');
  }
});
inpFilter?.addEventListener('input', render);
chkGaps?.addEventListener('change', render);
chkUnmet?.addEventListener('change', render);
chkInScope?.addEventListener('change', render);

document.getElementById('frontSearch').addEventListener('submit', (ev) => {
  ev.preventDefault();
  const q = document.getElementById('frontQ').value.trim();

  // If the input matches a saved search name, jump straight to that saved URL.
  const saved = resolveSavedSearchUrl(q);
  const target = saved && localPath(withFramework(saved, framework));
  if (target) {
    location.href = target;
    return;
  }

  const url = new URL('/events.html', location.origin);
  if (q) url.searchParams.set('q', q);
  url.searchParams.set('framework', framework);
  location.href = url.pathname + url.search;
});

load();
