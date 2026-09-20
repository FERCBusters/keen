import {initNavbar, apiGet, apiPost, esc, fmtTs, toast, qs, qbool, setQuery, enableTableSorting, attachSavedSearchAutocomplete, resolveSavedSearchUrl, getCurrentFramework, withFramework, showTableLoading} from '/app.js';

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

// Init filters from query string so they can be bookmarked/saved
if (inpFilter) inpFilter.value = qs('q', '');
if (chkGaps) chkGaps.checked = qbool('gaps', false);
if (chkInScope) {
  // Default is true (in-scope only). Only when in_scope=0 do we show all.
  const v = qs('in_scope', null);
  chkInScope.checked = v === null ? true : qbool('in_scope', true);
}

let all = [];

function render() {
  const qRaw = (inpFilter?.value || '').trim();
  const q = qRaw.toLowerCase();
  const onlyGaps = !!chkGaps?.checked;
  const onlyInScope = !!chkInScope?.checked;

  // Keep URL in sync so searches can be bookmarked/saved
  setQuery({
    q: qRaw || null,
    gaps: onlyGaps ? '1' : null,
    in_scope: onlyInScope ? null : '0',
  });

  tbody.innerHTML = '';
  const view = all.filter((c) => {
    if (onlyInScope && !c.in_scope) return false;
    if (onlyGaps && (c.evidence_count || 0) > 0) return false;
    if (!q) return true;
    return `${c.ref} ${c.title || ''} ${c.justification || ''}`.toLowerCase().includes(q);
  });

  for (const c of view) {
    const scope = c.in_scope ?
      `<span class="badge badge-soft">in</span>` :
      `<span class="badge text-bg-light">out</span>`;

    const ev = c.evidence_count || 0;
    const evHtml =
      ev > 0 ?
        `<a href="${withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework)}" class="badge badge-soft">${ev}</a>` :
        `<span class="badge text-bg-light">0</span>`;

    const lastIso = c.last_evidence || '';
    const lastTxt = c.last_evidence ? fmtTs(c.last_evidence) : '—';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td data-sort="${esc(c.ref)}"><a class="fw-semibold" href="${withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework)}">${esc(c.ref)}</a></td>
      <td class="wrap">${esc(c.title || '')}</td>
      <td class="small-muted" data-sort="${esc(c.justification || '')}">${esc(c.justification || '—')}</td>
      <td data-sort="${ev}">${evHtml}</td>
      <td class="small-muted" data-sort="${esc(lastIso)}">${esc(lastTxt)}</td>
      <td data-sort="${c.in_scope ? 1 : 0}">${scope}</td>`;
    tbody.appendChild(tr);
  }

  if (view.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="6" class="small-muted p-4">No controls match your filters.</td>`;
    tbody.appendChild(tr);
  }
}

function setKpiText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function applySummary(summary) {
  setKpiText('kpiEventsMapped', summary?.events?.mapped ?? '–');
  setKpiText('kpiEventsUnmapped', summary?.events?.unmapped ?? '–');
  setKpiText('kpiControlsWith', summary?.controls?.with_evidence ?? '–');
  setKpiText('kpiControlsWithout', summary?.controls?.without_evidence ?? '–');
}

async function loadSummary() {
  try {
    const summary = await apiGet(`/api/v1/stats/summary?framework=${encodeURIComponent(framework)}`);
    applySummary(summary);
  } catch (e) {
    applySummary(null);
    toast(status, `Loaded controls, but failed to load counters: ${String(e)}`, 'warning');
  }
}

async function load() {
  status.style.display = 'none';
  showTableLoading(tbody, 6, 'Loading controls…');
  setKpiText('kpiEventsMapped', '…');
  setKpiText('kpiEventsUnmapped', '…');
  setKpiText('kpiControlsWith', '…');
  setKpiText('kpiControlsWithout', '…');
  try {
    const data = await apiGet(`/api/v1/stats/controls?limit=5000&framework=${encodeURIComponent(framework)}`);
    all = data.items || [];
    if (all.length === 0) {
      toast(status, 'No controls loaded yet. Go to Admin → Import controls.', 'warning');
      tbody.innerHTML = '<tr><td colspan="6" class="small-muted p-4">No controls loaded yet. Go to Admin → Import controls.</td></tr>';
      await loadSummary();
      return;
    }
    render();
    await loadSummary();
  } catch (e) {
    toast(status, `Failed to load controls: ${String(e)}`, 'danger');
    tbody.innerHTML = '<tr><td colspan="6" class="small-muted p-4">Failed to load controls.</td></tr>';
    applySummary(null);
  }
}

document.getElementById('reload').addEventListener('click', load);

document.getElementById('resetFilters')?.addEventListener('click', (ev) => {
  ev.preventDefault();
  if (inpFilter) inpFilter.value = '';
  if (chkGaps) chkGaps.checked = false;
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
  if (!onlyInScope) parts.push('all-scope');
  const defName = ('Controls' + (parts.length ? ` (${parts.join(', ')})` : ''))
      .slice(0, 128);

  const name = (prompt('Name this saved search', defName) || '').trim();
  if (!name) return;

  const url = location.pathname + (location.search || '');
  try {
    await apiPost('/api/v1/me/saved-searches', {name, url});
    toast(status, 'Saved', 'success');
  } catch (e) {
    toast(status, `Failed to save: ${String(e)}`, 'danger');
  }
});
inpFilter?.addEventListener('input', render);
chkGaps?.addEventListener('change', render);
chkInScope?.addEventListener('change', render);

document.getElementById('frontSearch').addEventListener('submit', (ev) => {
  ev.preventDefault();
  const q = document.getElementById('frontQ').value.trim();

  // If the input matches a saved search name, jump straight to that saved URL.
  const saved = resolveSavedSearchUrl(q);
  if (saved) {
    location.href = withFramework(saved, framework);
    return;
  }

  const url = new URL('/events.html', location.origin);
  if (q) url.searchParams.set('q', q);
  url.searchParams.set('framework', framework);
  location.href = url.pathname + url.search;
});


load();
