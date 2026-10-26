import { featureEnabled } from '/pages/feature-availability.js';
import { deleteQuestionThread, myQuestionRowHtml } from '/pages/question-threads.js';
import { mountMyQuizzes } from '/pages/my-quizzes.js';
import {
  errorText,
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
  confirmAction,
  applyTheme,
  THEMES,
  toast,
  esc,
  fmtTs,
  getCurrentFramework,
  withFramework,
  safeExternalHref,
  idealTextColor,
  shorten,
  getFrameworkCatalog,
  userPillHtml,
  userPillsHtml,
} from '/app.js';

const status = document.getElementById('status');

const pwForm = document.getElementById('pwForm');
const curPw = document.getElementById('curPw');
const newPw = document.getElementById('newPw');
const pwCard = document.getElementById('pwCard');
const prefDateFormat = document.getElementById('prefDateFormat');
const prefDefaultDateFormatHelp = document.getElementById('prefDefaultDateFormatHelp');

const logoutBtn = document.getElementById('logoutBtn');

const savedSearchesList = document.getElementById('savedSearchesList');
const savedSearchesEmpty = document.getElementById('savedSearchesEmpty');

const prefsForm = document.getElementById('prefsForm');
const prefUseLocalTz = document.getElementById('prefUseLocalTz');
const prefTimezone = document.getElementById('prefTimezone');
const prefUseBrowserTz = document.getElementById('prefUseBrowserTz');
const prefBrowserTz = document.getElementById('prefBrowserTz');
const prefDefaultTzHelp = document.getElementById('prefDefaultTzHelp');
const prefTheme = document.getElementById('prefTheme');
const prefAutoApplyFilters = document.getElementById('prefAutoApplyFilters');
const prefLandingPage = document.getElementById('prefLandingPage');
const prefDefaultFramework = document.getElementById('prefDefaultFramework');

// Inline feedback next to the "Save preferences" button (useful when the page is scrolled).
const prefsSaveBtn = document.getElementById('prefsSaveBtn');
const prefsSaveToast = document.getElementById('prefsSaveToast');
let prefsSaveToastTimer = null;

function showPrefsSaveToast(message, kind = 'success', ttlMs = 2400) {
  if (!prefsSaveToast) return;
  if (prefsSaveToastTimer) {
    clearTimeout(prefsSaveToastTimer);
    prefsSaveToastTimer = null;
  }

  const cls =
    {
      success: 'badge text-bg-success',
      danger: 'badge text-bg-danger',
      warning: 'badge text-bg-warning',
      info: 'badge text-bg-secondary',
    }[kind] || 'badge text-bg-secondary';

  prefsSaveToast.className = cls;
  prefsSaveToast.textContent = message;
  prefsSaveToast.style.display = '';

  const ms = Number(ttlMs || 0);
  if (ms > 0) {
    prefsSaveToastTimer = setTimeout(() => {
      prefsSaveToast.style.display = 'none';
    }, ms);
  }
}

const sourceColorsLoading = document.getElementById('sourceColorsLoading');
const sourceColorsWrap = document.getElementById('sourceColorsWrap');
const sourceColorsBody = document.getElementById('sourceColorsBody');
const sourceColorsResetAll = document.getElementById('sourceColorsResetAll');

const me = await initNavbar();
const canDeleteQuestions = Boolean(me?.is_admin || me?.can_delete_questions);

// Hide local password management when using trusted proxy auth.
if (pwCard && me?.password_change_enabled === false) {
  pwCard.style.display = 'none';
}

// Hide logout when using trusted proxy auth (logout is upstream in that setup).
if (logoutBtn && me?.logout_enabled === false) {
  logoutBtn.style.display = 'none';
}

// ---------------------------------------------------------------------------
// Preferences
// ---------------------------------------------------------------------------

const browserTz = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return '';
  }
})();

if (prefBrowserTz) prefBrowserTz.textContent = browserTz || '(unknown)';

function setTzEnabled() {
  const on = !!prefUseLocalTz?.checked;
  if (prefTimezone) prefTimezone.disabled = !on;
  if (prefUseBrowserTz) prefUseBrowserTz.disabled = !on;
}

function renderThemeOptions(meObj) {
  const avail =
    meObj?.available_themes && Array.isArray(meObj.available_themes)
      ? meObj.available_themes
      : THEMES;

  if (!prefTheme) return;
  prefTheme.innerHTML = avail
    .map((t) => `<option value="${esc(String(t.id))}">${esc(String(t.name))}</option>`)
    .join('');
}

let currentLandingPagePref = (me?.preferences?.landing_page || '/').trim() || '/';
let currentDefaultFrameworkPref = (me?.preferences?.default_framework || '').trim();

function renderLandingPageOptions(meObj, savedSearches = []) {
  if (!prefLandingPage) return;

  const fixed = [
    // The API stores Home as '/', which is also the default preference.
    { v: '/', label: 'Home' },
    { v: '/controls.html', label: 'Controls register' },
    { v: '/events.html', label: 'Events' },
    { v: '/sources.html', label: 'Sources' },
    { v: '/risks.html', label: 'Risks' },
    { v: '/isms.html', label: 'ISMS' },
    { v: '/statement-of-applicability.html', label: 'Statement of Applicability' },
    { v: '/account.html', label: 'Account' },
  ];
  if (meObj?.is_admin) fixed.push({ v: '/admin.html', label: 'Admin' });

  const cur = (currentLandingPagePref || '/').trim() || '/';

  const hasFixed = new Set(fixed.map((o) => o.v));

  // Normalize saved searches into {name, url} entries.
  const rawArr = Array.isArray(savedSearches) ? savedSearches : [];
  const saved = [];
  for (const s of rawArr) {
    const name = String(s?.name || '').trim();
    const url = String(s?.url || '').trim();
    if (!name || !url) continue;
    saved.push({ name, url });
  }

  // Disambiguate duplicate names by suffixing with a shortened URL.
  const nameCounts = new Map();
  for (const s of saved) {
    const k = s.name.toLowerCase();
    nameCounts.set(k, (nameCounts.get(k) || 0) + 1);
  }
  const savedLabels = saved.map((s) => {
    const dup = (nameCounts.get(s.name.toLowerCase()) || 0) > 1;
    // Plain text; escaped once when the <option> is rendered.
    const label = dup ? `${s.name} - ${shorten(s.url, 60)}` : s.name;
    return { v: s.url, label };
  });

  const savedUrls = new Set(savedLabels.map((o) => o.v));

  let html = fixed.map((o) => `<option value="${esc(o.v)}">${esc(o.label)}</option>`).join('');

  if (savedLabels.length) {
    html += `\n<optgroup label="Saved searches">`;
    // Show most recently updated first as returned by API; keep that order.
    html += savedLabels.map((o) => `<option value="${esc(o.v)}">${esc(o.label)}</option>`).join('');
    html += `</optgroup>`;
  }

  // If the current preference is neither a fixed page nor one of the saved searches,
  // keep it selectable so users can see/edit it (e.g. after deleting a saved search).
  if (cur && !hasFixed.has(cur) && !savedUrls.has(cur)) {
    html += `\n<optgroup label="Current (custom)">`;
    html += `<option value="${esc(cur)}">${esc(shorten(cur, 80))}</option>`;
    html += `</optgroup>`;
  }

  prefLandingPage.innerHTML = html;

  // Re-select current value if present.
  try {
    prefLandingPage.value = cur;
  } catch {
    // ignore
  }
}

function loadPrefs(meObj) {
  const prefs = meObj?.preferences || {};
  if (prefDefaultTzHelp) {
    prefDefaultTzHelp.textContent = `Default timezone: ${meObj?.default_timezone || 'Etc/UTC'}.`;
  }
  if (prefDefaultDateFormatHelp) {
    const fmt =
      String(meObj?.default_date_format || 'ymd').toLowerCase() === 'dmy'
        ? 'DD/MM/YYYY'
        : 'YYYY-MM-DD';
    prefDefaultDateFormatHelp.textContent = `App default: ${fmt}. API values remain YYYY-MM-DD.`;
  }

  renderThemeOptions(meObj);
  currentLandingPagePref = (prefs.landing_page || '/').trim() || '/';
  currentDefaultFrameworkPref = (prefs.default_framework || '').trim();
  renderLandingPageOptions(meObj, []);

  if (prefUseLocalTz) prefUseLocalTz.checked = !!prefs.use_local_timezone;
  if (prefTimezone) prefTimezone.value = (prefs.timezone || browserTz || '').trim();
  if (prefTheme) {
    const t = (prefs.theme || 'sand').trim() || 'sand';
    prefTheme.value = t;
    applyTheme(t);
  }
  if (prefDateFormat) prefDateFormat.value = (prefs.date_format || 'default').trim() || 'default';
  if (prefLandingPage) {
    prefLandingPage.value = currentLandingPagePref;
  }
  if (prefAutoApplyFilters) prefAutoApplyFilters.checked = !!prefs.auto_apply_filters;
  setTzEnabled();
}

function _frameworkOptionLabel(it) {
  const name = String(it?.name || it?.slug || '').trim() || String(it?.slug || '').trim();
  const count = Number(it?.control_count || 0);
  const suffix = Number.isFinite(count) ? ` (${count})` : '';
  return `${name}${suffix}`;
}

async function loadDefaultFrameworkOptions(meObj) {
  if (!prefDefaultFramework) return;

  try {
    const catalog = await getFrameworkCatalog();
    const items = Array.isArray(catalog?.items) ? catalog.items.slice() : [];
    const defaultSlug = String(catalog?.default || '').trim() || 'KEEN-AF:1.0';

    // Keep options unique by slug and stable for rendering.
    const seen = new Set();
    const uniq = [];
    for (const it of items) {
      const slug = String(it?.slug || '').trim();
      if (!slug || seen.has(slug)) continue;
      seen.add(slug);
      uniq.push({ ...it, slug });
    }

    const saved = String(currentDefaultFrameworkPref || '').trim();
    if (saved && !seen.has(saved)) {
      uniq.unshift({ slug: saved, name: saved, control_count: 0 });
      seen.add(saved);
    }

    const defaultObj = uniq.find((x) => x.slug === defaultSlug);
    const defaultLabel = defaultObj ? _frameworkOptionLabel(defaultObj) : defaultSlug;

    const opts = [
      `<option value="">Use system default (${esc(defaultLabel)})</option>`,
      ...uniq.map(
        (it) => `<option value="${esc(it.slug)}">${esc(_frameworkOptionLabel(it))}</option>`,
      ),
    ];

    prefDefaultFramework.innerHTML = opts.join('');
    prefDefaultFramework.value = saved || '';
    prefDefaultFramework.disabled = false;
  } catch {
    const saved = String(currentDefaultFrameworkPref || '').trim();
    prefDefaultFramework.innerHTML = [
      '<option value="">Use system default</option>',
      ...(saved ? [`<option value="${esc(saved)}">${esc(saved)}</option>`] : []),
    ].join('');
    prefDefaultFramework.value = saved || '';
    prefDefaultFramework.disabled = false;
  }
}

loadPrefs(me);
await loadDefaultFrameworkOptions(me);

// ---------------------------------------------------------------------------
// Source color overrides (user preference)
// ---------------------------------------------------------------------------

let sourceColorsLoaded = false;
let sourceColorsDirty = false;
let sourceColorOverrides =
  me?.preferences?.source_colors && typeof me.preferences.source_colors === 'object'
    ? { ...me.preferences.source_colors }
    : {};

function normalizeHex(hex) {
  const h = String(hex || '').trim();
  if (!h) return '';
  let s = h.startsWith('#') ? h.slice(1) : h;
  if (/^[0-9a-fA-F]{3}$/.test(s))
    s = s
      .split('')
      .map((c) => c + c)
      .join('');
  if (!/^[0-9a-fA-F]{6}$/.test(s)) return '';
  return ('#' + s).toUpperCase();
}

function colorChipHtml(hex) {
  const c = normalizeHex(hex);
  if (!c) return '<span class="small-muted">-</span>';
  const fg = idealTextColor(c);
  const border = fg === '#FFFFFF' ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.14)';
  return `<span class="badge" style="background:${c}; border:1px solid ${border}; color:${fg}; font-weight:650;">${c}</span>`;
}

function renderSourceColors(items) {
  if (!sourceColorsBody || !sourceColorsWrap || !sourceColorsLoading) return;
  sourceColorsBody.innerHTML = '';
  const arr = Array.isArray(items) ? items : [];

  for (const it of arr) {
    const src = String(it.source || '').trim();
    if (!src) continue;
    const label = String(it.label || src).trim() || src;
    const defaultColor = normalizeHex(it.default_color || it.color || '');
    const overrideColor = normalizeHex(sourceColorOverrides[src] || '');
    const effective = overrideColor || defaultColor || '#000000';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="mono">${esc(src)}</td>
      <td>${esc(label)}</td>
      <td>${colorChipHtml(defaultColor)}</td>
      <td>
        <div class="d-flex flex-wrap gap-2 align-items-center">
          <input type="color" class="form-control form-control-color" value="${esc(effective)}" data-src-color="${esc(src)}" data-default="${esc(defaultColor)}" title="Pick a colour" />
          <input type="text" class="form-control form-control-sm mono" style="max-width:120px" value="${esc(overrideColor || defaultColor)}" data-src-hex="${esc(src)}" placeholder="#RRGGBB" />
        </div>
      </td>
      <td class="text-end">
        <button type="button" class="btn btn-sm btn-outline-secondary" data-src-reset="${esc(src)}">Reset</button>
      </td>
    `;
    sourceColorsBody.appendChild(tr);
  }
}

function updateSourceColorRow(src) {
  if (!sourceColorsBody || !src) return;
  const picker = sourceColorsBody.querySelector(`[data-src-color="${CSS.escape(src)}"]`);
  const hexInp = sourceColorsBody.querySelector(`[data-src-hex="${CSS.escape(src)}"]`);
  const def = picker?.getAttribute('data-default') || '';
  const defaultColor = normalizeHex(def);
  const overrideColor = normalizeHex(sourceColorOverrides[src] || '');
  const effective = overrideColor || defaultColor || '#000000';

  if (picker) picker.value = effective;
  if (hexInp) hexInp.value = overrideColor || defaultColor || '';
}

let sourceColorsEventsBound = false;
function bindSourceColorsEvents() {
  if (!sourceColorsBody || sourceColorsEventsBound) return;
  sourceColorsEventsBound = true;

  // Color picker: prefer change over input. Some browsers close the native picker
  // if we mutate related DOM elements during the picker interaction.
  sourceColorsBody.addEventListener('change', (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLElement)) return;

    if (t.matches('[data-src-color]')) {
      const src = t.getAttribute('data-src-color');
      if (!src) return;
      const c = normalizeHex(t.value);
      const def = normalizeHex(t.getAttribute('data-default') || '');
      if (!c) return;
      // If user picks the default color, treat it as "no override".
      if (def && c === def) delete sourceColorOverrides[src];
      else sourceColorOverrides[src] = c;
      sourceColorsDirty = true;
      updateSourceColorRow(src);
    }
  });

  // Hex text field: apply on change (avoid fighting while user types)
  sourceColorsBody.addEventListener('change', (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLElement)) return;

    if (t.matches('[data-src-hex]')) {
      const src = t.getAttribute('data-src-hex');
      if (!src) return;
      const c = normalizeHex(t.value);
      const picker = sourceColorsBody.querySelector(`[data-src-color="${CSS.escape(src)}"]`);
      const def = normalizeHex(picker?.getAttribute('data-default') || '');
      if (!c) {
        toast(status, 'Invalid hex colour (use #RRGGBB)', 'warning');
        updateSourceColorRow(src);
        return;
      }
      if (def && c === def) delete sourceColorOverrides[src];
      else sourceColorOverrides[src] = c;
      sourceColorsDirty = true;
      updateSourceColorRow(src);
    }
  });

  sourceColorsBody.addEventListener('click', (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLElement)) return;
    const btn = t.closest('[data-src-reset]');
    if (!btn) return;
    const src = btn.getAttribute('data-src-reset');
    if (!src) return;
    delete sourceColorOverrides[src];
    sourceColorsDirty = true;
    updateSourceColorRow(src);
  });
}

async function loadSourceColors() {
  if (!sourceColorsLoading || !sourceColorsWrap || !sourceColorsBody) return;
  sourceColorsLoading.style.display = '';
  sourceColorsWrap.style.display = 'none';

  try {
    const data = await apiGet('/api/v1/sources/meta');
    const items = data?.items || [];
    sourceColorsLoaded = true;
    sourceColorsLoading.style.display = 'none';
    sourceColorsWrap.style.display = '';
    renderSourceColors(items);
    bindSourceColorsEvents();
  } catch (e) {
    // Non-fatal: hide section if the server doesn't support it.
    sourceColorsLoaded = false;
    sourceColorsLoading.textContent = 'Source colours are unavailable on this server.';
  }
}

if (sourceColorsResetAll) {
  sourceColorsResetAll.addEventListener('click', async () => {
    if (
      !(await confirmAction({
        title: 'Reset source colours',
        message: 'Reset all your source colour overrides to the defaults?',
        confirmLabel: 'Reset',
      }))
    )
      return;
    sourceColorOverrides = {};
    sourceColorsDirty = true;
    loadSourceColors();
  });
}

loadSourceColors();

// ---------------------------------------------------------------------------
// Saved searches
// ---------------------------------------------------------------------------

function renderSavedSearches(items) {
  if (!savedSearchesList) return;
  savedSearchesList.innerHTML = '';
  const arr = Array.isArray(items) ? items : [];
  if (savedSearchesEmpty) savedSearchesEmpty.style.display = arr.length ? 'none' : '';

  for (const s of arr) {
    const row = document.createElement('div');
    row.className =
      'list-group-item d-flex flex-wrap gap-2 align-items-center justify-content-between';

    const left = document.createElement('div');
    left.className = 'me-2';

    const a = document.createElement('a');
    a.className = 'fw-semibold';
    // Saved-search URLs are validated server-side (relative only), but
    // scheme-check here too so a javascript: value can never become clickable.
    a.href = safeExternalHref(s.url) || '#';
    a.textContent = s.name || '(untitled)';

    const meta = document.createElement('div');
    meta.className = 'small-muted';
    meta.innerHTML = esc(s.url || '');

    left.appendChild(a);
    left.appendChild(meta);

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'btn btn-sm btn-outline-danger';
    del.textContent = 'Delete';
    del.addEventListener('click', async () => {
      if (
        !(await confirmAction({
          title: 'Delete saved search',
          message: `Delete the saved search ${s.name || '(without a name)'}?`,
          confirmLabel: 'Delete',
        }))
      )
        return;
      try {
        await apiDelete(`/api/v1/me/saved-searches/${encodeURIComponent(s.id)}`);
        await loadSavedSearches();
      } catch (e) {
        toast(status, `Failed to delete: ${errorText(e)}`, 'danger');
      }
    });

    row.appendChild(left);
    row.appendChild(del);
    savedSearchesList.appendChild(row);
  }
}

async function loadSavedSearches() {
  if (!savedSearchesList) return;
  try {
    const items = await apiGet('/api/v1/me/saved-searches');
    renderSavedSearches(items);

    // Also surface saved searches as landing-page options.
    // (Non-fatal if the server supports saved searches but we can't render.)
    try {
      renderLandingPageOptions(me, items);
      if (prefLandingPage) prefLandingPage.value = (currentLandingPagePref || '/').trim() || '/';
    } catch {
      // ignore
    }
  } catch (e) {
    if (savedSearchesEmpty) {
      savedSearchesEmpty.className = 'alert alert-danger mb-0';
      savedSearchesEmpty.style.display = '';
      savedSearchesEmpty.textContent = `Failed to load saved searches: ${errorText(e)}`;
    }
  }
}

loadSavedSearches();

// ---------------------------------------------------------------------------
// Account tabs
// ---------------------------------------------------------------------------

// A hash names the tab whose data-tab-hash matches it, such as #my-questions.
function accountTabForHash(hash) {
  const wanted = String(hash || '')
    .trim()
    .toLowerCase();
  return wanted
    ? [...document.querySelectorAll('#accountTabs [data-tab-hash]')].find(
        (button) => button.dataset.tabHash === wanted,
      )
    : null;
}

function maybeLoadActiveAccountTab(target) {
  if (Object.hasOwn(accountLists, target)) accountLists[target].loadOnce();
}

const accountNavigationToggle = document.getElementById('account-navigation-toggle');
accountNavigationToggle?.addEventListener('click', () => {
  const open = document.getElementById('accountTabs').classList.toggle('admin-nav-open');
  accountNavigationToggle.setAttribute('aria-expanded', String(open));
});
document.getElementById('accountTabs')?.addEventListener('shown.bs.tab', () => {
  document.getElementById('accountTabs').classList.remove('admin-nav-open');
  accountNavigationToggle?.setAttribute('aria-expanded', 'false');
});

function bindAccountTabs() {
  const tabButtons = Array.from(document.querySelectorAll('#accountTabs [data-bs-toggle="tab"]'));
  for (const btn of tabButtons) {
    btn.addEventListener('shown.bs.tab', () => {
      const target = btn.getAttribute('data-bs-target') || '';
      const hash = btn.getAttribute('data-tab-hash') || '';
      if (hash && window.location.hash !== hash) {
        try {
          window.history.replaceState(
            null,
            '',
            `${window.location.pathname}${window.location.search}${hash}`,
          );
        } catch {
          // ignore
        }
      }
      maybeLoadActiveAccountTab(target);
    });
  }

  const trigger = accountTabForHash(window.location.hash);
  if (trigger && window.bootstrap?.Tab) {
    window.bootstrap.Tab.getOrCreateInstance(trigger).show();
    return;
  }
  maybeLoadActiveAccountTab('#preferences-pane');
}

// ---------------------------------------------------------------------------
// My questions
// ---------------------------------------------------------------------------

// Each "My ..." tab lists records from one /api/v1/me endpoint. Its elements
// share the tab's id prefix: <prefix>Status, <prefix>Tbody, <prefix>Empty,
// <prefix>TableWrap and <prefix>Refresh. A list loads the first time its tab
// opens; Refresh loads it again.
function accountList({
  prefix,
  feature,
  endpoint,
  framework = false,
  noun,
  columns,
  row,
  onLoaded,
}) {
  const el = (part) => document.getElementById(prefix + part);
  const [listStatus, tbody, empty, wrap] = ['Status', 'Tbody', 'Empty', 'TableWrap'].map(el);
  let loaded = false;
  async function load() {
    if (!featureEnabled(me, feature)) return;
    listStatus.style.display = 'none';
    tbody.innerHTML = `<tr><td colspan="${columns}" class="p-4 small-muted">Loading</td></tr>`;
    wrap.style.display = '';
    empty.style.display = 'none';
    try {
      const url = new URL(endpoint, location.origin);
      const fw = framework ? getCurrentFramework() : '';
      if (fw) url.searchParams.set('framework', fw);
      const res = await apiGet(url.pathname + url.search);
      const items = Array.isArray(res?.items) ? res.items : [];
      empty.style.display = items.length ? 'none' : '';
      wrap.style.display = items.length ? '' : 'none';
      tbody.innerHTML = items.map(row).join('');
      loaded = true;
      onLoaded?.();
    } catch (err) {
      wrap.style.display = 'none';
      listStatus.className = 'alert alert-danger';
      listStatus.style.display = '';
      listStatus.textContent = `Failed to load ${noun}: ${errorText(err)}`;
    }
  }
  el('Refresh')?.addEventListener('click', load);
  return { load, loadOnce: () => (loaded ? undefined : load()) };
}

document.getElementById('myQuestionsTbody')?.addEventListener('click', async (ev) => {
  const button = ev.target.closest?.('[data-delete-question]');
  if (!button || !canDeleteQuestions) return;
  button.disabled = true;
  try {
    if (await deleteQuestionThread(button.dataset.deleteQuestion)) {
      toast(status, 'Question deleted', 'success');
      await accountLists['#my-questions-pane'].load();
    }
  } catch (e) {
    toast(status, errorText(e), 'danger');
  }
  button.disabled = false;
});

// ---------------------------------------------------------------------------
// My audits
// ---------------------------------------------------------------------------

function auditStatusLabel(s) {
  const v = String(s || 'open');
  if (v === 'in_progress') return 'In progress';
  return v ? v.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase()) : 'Open';
}

function myAuditRow(a) {
  const url = withFramework(
    `/audit.html?id=${encodeURIComponent(a.id)}`,
    a.framework_slug || getCurrentFramework(),
  );
  const dates = [a.start_date, a.end_date].filter(Boolean).join(' → ') || '-';
  const attendeeRole = String(a?.my_attendee?.role || '').trim() || 'Attendee';
  const updated = a.updated_at ? fmtTs(a.updated_at) : '-';
  return `<tr>
    <td>
      <a class="fw-semibold" href="${esc(url)}">${esc(a.title || 'Audit')}</a>
      <div class="small-muted">Updated ${esc(updated)}</div>
    </td>
    <td><span class="badge badge-soft">${esc(a.framework_slug || '')}</span></td>
    <td class="small-muted">${esc(dates)}</td>
    <td class="small-muted">${esc(attendeeRole)}</td>
    <td><span class="badge text-bg-light">${esc(auditStatusLabel(a.status))}</span></td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(url)}">Open</a></td>
  </tr>`;
}

// ---------------------------------------------------------------------------
// My Risks
// ---------------------------------------------------------------------------

function riskScoreClass(score) {
  const n = Number(score || 0);
  if (n < 5) return 'text-bg-success';
  if (n <= 12) return 'text-bg-warning';
  return 'text-bg-danger';
}

function riskScoreBadge(score) {
  const n = Number(score || 0);
  return `<span class="badge ${riskScoreClass(n)}">${esc(String(n))}</span>`;
}

function myRiskRow(r) {
  const url = withFramework(
    `/risk.html?id=${encodeURIComponent(r.id || '')}`,
    r.framework || getCurrentFramework(),
  );
  const asset = String(r.asset || r.asset_entity?.name || 'Risk').trim() || 'Risk';
  const threat = String(r.threat_summary || '').trim();
  const categoryBits = [r.category?.name, r.subcategory?.name].filter(Boolean);
  const category = categoryBits.join(' / ') || '-';
  const types = Array.isArray(r.risk_types) ? r.risk_types : [];
  const typeHtml = types.length
    ? types.map((t) => `<span class="badge badge-soft me-1">${esc(t)}</span>`).join('')
    : '<span class="small-muted">-</span>';
  const controlsText =
    Number(r.control_count || 0) === 1 ? '1 control' : `${Number(r.control_count || 0)} controls`;
  const updated = r.updated_at ? fmtTs(r.updated_at) : '-';

  return `<tr>
    <td class="wrap">
      <a class="fw-semibold" href="${esc(url)}">${esc(asset)}</a>
      <div class="small-muted text-truncate" style="max-width:420px;">${esc(threat || 'No threat summary')}</div>
    </td>
    <td class="small-muted">${esc(category)}</td>
    <td>${typeHtml}</td>
    <td class="text-end">${riskScoreBadge(r.risk_score)}</td>
    <td class="text-end">${riskScoreBadge(r.residual_risk_score)}</td>
    <td><span class="badge text-bg-light">${esc(controlsText)}</span></td>
    <td class="small-muted">${esc(updated)}</td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(url)}">Open</a></td>
  </tr>`;
}

// ---------------------------------------------------------------------------
// My ISMS account cross-references
// ---------------------------------------------------------------------------

function ismsUrl(tab = 'overview') {
  return withFramework(`/isms.html?tab=${encodeURIComponent(tab)}`, getCurrentFramework());
}

function peopleBadges(items = []) {
  return userPillsHtml(items);
}

function orgNodeLabel(node) {
  if (!node) return '';
  if (typeof node === 'string') return node.trim();
  if (Array.isArray(node)) return orgPathLabel(node);
  if (typeof node === 'object') {
    return String(node.name || node.title || node.username || node.email || node.id || '').trim();
  }
  return String(node || '').trim();
}

function orgPathLabel(path) {
  const rows = Array.isArray(path) ? path : [];
  return rows
    .map((entry) => orgNodeLabel(entry))
    .filter(Boolean)
    .join(' → ');
}

function meetingTimeLabel(item) {
  const start = item?.start_time || '';
  const end = item?.end_time || '';
  if (start && end) return `${start}-${end}`;
  return start || end || '-';
}

function myMeetingRow(item) {
  const href = withFramework(
    `/isms-meeting.html?id=${encodeURIComponent(item?.id || '')}`,
    getCurrentFramework(),
  );
  return `<tr>
    <td class="wrap"><span class="fw-semibold">${esc(item?.title || 'Meeting')}</span><div class="small-muted">${esc(item?.agenda_minutes_notes || '')}</div></td>
    <td class="small-muted">${esc(item?.date || '-')}</td>
    <td class="small-muted">${esc(meetingTimeLabel(item))}</td>
    <td><span class="badge text-bg-light">${esc(item?.my_attendance_label || '-')}</span></td>
    <td>${userPillsHtml(item?.attendees || [])}</td>
    <td>${userPillsHtml(item?.apologies || [])}</td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(href)}">Open</a></td>
  </tr>`;
}

function myIsmsObjectiveRow(item) {
  const owner = userPillHtml(item?.owner_user);
  const target = item?.completion_target_date || '-';
  return `<tr>
    <td class="wrap"><span class="fw-semibold">${esc(item?.requirement || 'Objective')}</span><div class="small-muted">${esc(item?.metric || '')}</div></td>
    <td class="wrap">${esc(item?.goal || '-')}</td>
    <td>${owner}</td>
    <td>${peopleBadges(item?.resource_users)}</td>
    <td class="small-muted">${esc(target)}</td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(ismsUrl('objectives'))}">Open</a></td>
  </tr>`;
}

function myIsmsRoleRow(item) {
  const node = item?.org_node || item?.node || item || {};
  const path = orgPathLabel(item?.path) || orgPathLabel(node?.path) || orgNodeLabel(node) || '-';
  return `<tr>
    <td class="wrap"><span class="fw-semibold">${esc(orgNodeLabel(node) || 'Role')}</span><div class="small-muted">${esc(node.description || '')}</div></td>
    <td><span class="badge text-bg-light">${esc(node.node_type || 'role')}</span></td>
    <td>${esc(item?.relationship_type || 'member')}</td>
    <td class="small-muted wrap">${esc(path)}</td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(ismsUrl('org'))}">Open</a></td>
  </tr>`;
}

function assetRoleLabel(item) {
  const roles = [];
  if (item?.is_owner) roles.push('Owner');
  if (item?.is_register_holder) roles.push('Register holder');
  return roles.join(', ') || item?.relationship || '-';
}

function myIsmsAssetRow(item) {
  const asset = item?.asset && typeof item.asset === 'object' ? item.asset : item || {};
  const taxonomy = [
    asset.category?.name || asset.category_name,
    asset.subcategory?.name || asset.subcategory_name,
  ]
    .filter(Boolean)
    .join(' / ');
  return `<tr>
    <td class="wrap"><span class="fw-semibold">${esc(asset.asset || asset.name || 'Asset')}</span><div class="small-muted">${esc(taxonomy || asset.description || '')}</div></td>
    <td>${esc(asset.license || '-')}</td>
    <td><span class="badge text-bg-light">${esc(assetRoleLabel(item))}</span></td>
    <td class="small-muted">${esc(orgNodeLabel(asset.owner_org_node) || '-')}</td>
    <td class="small-muted">${esc(orgNodeLabel(asset.register_held_by_org_node) || '-')}</td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(ismsUrl('assets'))}">Open</a></td>
  </tr>`;
}

const accountLists = {
  '#my-questions-pane': accountList({
    prefix: 'myQuestions',
    feature: 'questions',
    endpoint: '/api/v1/me/questions',
    noun: 'questions',
    columns: 4,
    row: (item) =>
      myQuestionRowHtml(item, { framework: getCurrentFramework(), canDelete: canDeleteQuestions }),
    // Keep the navbar's question count in step with the list.
    onLoaded: () => window.keenRefreshBell?.(),
  }),
  '#my-audits-pane': accountList({
    prefix: 'myAudits',
    feature: 'audits',
    endpoint: '/api/v1/me/audits',
    noun: 'audits',
    columns: 6,
    row: myAuditRow,
  }),
  // The My quizzes tab is richer than a list; its tab is hidden while
  // quizzes are switched off.
  ...(featureEnabled(me, 'quizzes')
    ? { '#my-quizzes-pane': mountMyQuizzes(document.getElementById('my-quizzes-pane')) }
    : {}),
  '#my-meetings-pane': accountList({
    prefix: 'myMeetings',
    feature: 'meetings',
    endpoint: '/api/v1/me/isms-meetings',
    framework: true,
    noun: 'ISMS meetings',
    columns: 7,
    row: myMeetingRow,
  }),
  '#my-risks-pane': accountList({
    prefix: 'myRisks',
    feature: 'risk_cia',
    endpoint: '/api/v1/me/risks',
    framework: true,
    noun: 'risks',
    columns: 8,
    row: myRiskRow,
  }),
  '#my-isms-objectives-pane': accountList({
    prefix: 'myIsmsObjectives',
    feature: 'objectives',
    endpoint: '/api/v1/me/isms-objectives',
    framework: true,
    noun: 'ISMS objectives',
    columns: 6,
    row: myIsmsObjectiveRow,
  }),
  '#my-isms-role-pane': accountList({
    prefix: 'myIsmsRole',
    feature: 'organisation',
    endpoint: '/api/v1/me/isms-role',
    noun: 'ISMS roles',
    columns: 5,
    row: myIsmsRoleRow,
  }),
  '#my-isms-assets-pane': accountList({
    prefix: 'myIsmsAssets',
    feature: 'assets',
    endpoint: '/api/v1/me/isms-assets',
    framework: true,
    noun: 'ISMS assets',
    columns: 6,
    row: myIsmsAssetRow,
  }),
};

bindAccountTabs();

if (prefUseLocalTz) {
  prefUseLocalTz.addEventListener('change', () => {
    setTzEnabled();
    if (prefUseLocalTz.checked && prefTimezone && !prefTimezone.value.trim()) {
      prefTimezone.value = browserTz;
    }
  });
}

if (prefUseBrowserTz) {
  prefUseBrowserTz.addEventListener('click', () => {
    if (prefTimezone) prefTimezone.value = browserTz;
  });
}

if (prefTheme) {
  prefTheme.addEventListener('change', () => {
    applyTheme(prefTheme.value);
  });
}

if (prefsForm) {
  prefsForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (status) status.style.display = 'none';
    // Clear any prior inline feedback.
    if (prefsSaveToast) prefsSaveToast.style.display = 'none';

    const payload = {
      use_local_timezone: !!prefUseLocalTz?.checked,
      theme: (prefTheme?.value || 'sand').trim() || 'sand',
      date_format: (prefDateFormat?.value || 'default').trim() || 'default',
      auto_apply_filters: !!prefAutoApplyFilters?.checked,
      landing_page: (prefLandingPage?.value || '/').trim() || '/',
      default_framework: (prefDefaultFramework?.value || '').trim() || null,
    };

    if (sourceColorsLoaded) {
      payload.source_colors = sourceColorOverrides || {};
    }

    if (payload.use_local_timezone) {
      const tz = (prefTimezone?.value || '').trim() || browserTz;
      payload.timezone = tz;
    }

    try {
      const updated = await apiPatch('/api/v1/me/preferences', payload);
      // Keep globals up to date for other pages in this tab.
      window.keenPreferences = updated || {};
      window.mospPreferences = updated || {};
      if (updated?.landing_page) {
        currentLandingPagePref = String(updated.landing_page).trim() || '/';
      }
      currentDefaultFrameworkPref = String(updated?.default_framework || '').trim();
      window.keenPreferredFramework = currentDefaultFrameworkPref;
      try {
        if (currentDefaultFrameworkPref)
          localStorage.setItem('keen_framework', currentDefaultFrameworkPref);
        else localStorage.removeItem('keen_framework');
      } catch {
        // ignore
      }
      if (updated?.theme) applyTheme(updated.theme);
      // Show a small toast beside the button (so it's visible even when scrolled),
      // and also update the standard page-level status.
      showPrefsSaveToast('Saved', 'success', 2000);
      toast(status, 'Preferences saved', 'success', 2500);
    } catch (e) {
      showPrefsSaveToast('Failed', 'danger', 5200);
      toast(status, `Failed: ${errorText(e)}`, 'danger');
    }
  });
}

if (logoutBtn && me?.logout_enabled !== false) {
  const logoutUrl = (me && me.logout_url ? String(me.logout_url) : '').trim();
  logoutBtn.addEventListener('click', async () => {
    if (logoutUrl && logoutUrl !== '/api/v1/auth/oidc/logout') {
      location.href = logoutUrl;
      return;
    }

    try {
      const result = await apiPost(logoutUrl || '/api/v1/auth/logout', {});
      if (logoutUrl && result?.logout_url) {
        location.href = result.logout_url;
        return;
      }
    } catch (_e) {
      if (status) {
        status.textContent = 'Logout failed. Please try again.';
        status.style.display = '';
      }
      return;
    }

    location.href = '/login.html';
  });
}

if (pwForm && me?.password_change_enabled !== false) {
  pwForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (status) status.style.display = 'none';

    const current_password = curPw?.value || '';
    const new_password = newPw?.value || '';

    if (!current_password || !new_password) {
      toast(status, 'Both current and new password are required', 'warning');
      return;
    }

    try {
      await apiPost('/api/v1/me/password', { current_password, new_password });
      toast(status, 'Password updated. Sign in again to continue.', 'success');
      location.assign('/login.html');
      if (curPw) curPw.value = '';
      if (newPw) newPw.value = '';
    } catch (e) {
      toast(status, `Failed: ${errorText(e)}`, 'danger');
    }
  });
}
