import {
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
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
const meUser = document.getElementById('meUser');
const meRole = document.getElementById('meRole');
const meAdmin = document.getElementById('meAdmin');

const pwForm = document.getElementById('pwForm');
const curPw = document.getElementById('curPw');
const newPw = document.getElementById('newPw');
const pwCard = document.getElementById('pwCard');
const prefDateFormat = document.getElementById('prefDateFormat');
const prefDefaultDateFormatHelp = document.getElementById('prefDefaultDateFormatHelp');

const logoutBtn = document.getElementById('logoutBtn');

const savedSearchesList = document.getElementById('savedSearchesList');
const savedSearchesEmpty = document.getElementById('savedSearchesEmpty');

const myQuestionsStatus = document.getElementById('myQuestionsStatus');
const myQuestionsTbody = document.getElementById('myQuestionsTbody');
const myQuestionsEmpty = document.getElementById('myQuestionsEmpty');
const myQuestionsTableWrap = document.getElementById('myQuestionsTableWrap');
const myQuestionsRefresh = document.getElementById('myQuestionsRefresh');

const myAuditsStatus = document.getElementById('myAuditsStatus');
const myAuditsTbody = document.getElementById('myAuditsTbody');
const myAuditsEmpty = document.getElementById('myAuditsEmpty');
const myAuditsTableWrap = document.getElementById('myAuditsTableWrap');
const myAuditsRefresh = document.getElementById('myAuditsRefresh');

const myMeetingsStatus = document.getElementById('myMeetingsStatus');
const myMeetingsTbody = document.getElementById('myMeetingsTbody');
const myMeetingsEmpty = document.getElementById('myMeetingsEmpty');
const myMeetingsTableWrap = document.getElementById('myMeetingsTableWrap');
const myMeetingsRefresh = document.getElementById('myMeetingsRefresh');

const myRisksStatus = document.getElementById('myRisksStatus');
const myRisksTbody = document.getElementById('myRisksTbody');
const myRisksEmpty = document.getElementById('myRisksEmpty');
const myRisksTableWrap = document.getElementById('myRisksTableWrap');
const myRisksRefresh = document.getElementById('myRisksRefresh');

const myIsmsObjectivesStatus = document.getElementById('myIsmsObjectivesStatus');
const myIsmsObjectivesTbody = document.getElementById('myIsmsObjectivesTbody');
const myIsmsObjectivesEmpty = document.getElementById('myIsmsObjectivesEmpty');
const myIsmsObjectivesTableWrap = document.getElementById('myIsmsObjectivesTableWrap');
const myIsmsObjectivesRefresh = document.getElementById('myIsmsObjectivesRefresh');

const myIsmsRoleStatus = document.getElementById('myIsmsRoleStatus');
const myIsmsRoleTbody = document.getElementById('myIsmsRoleTbody');
const myIsmsRoleEmpty = document.getElementById('myIsmsRoleEmpty');
const myIsmsRoleTableWrap = document.getElementById('myIsmsRoleTableWrap');
const myIsmsRoleRefresh = document.getElementById('myIsmsRoleRefresh');

const myIsmsAssetsStatus = document.getElementById('myIsmsAssetsStatus');
const myIsmsAssetsTbody = document.getElementById('myIsmsAssetsTbody');
const myIsmsAssetsEmpty = document.getElementById('myIsmsAssetsEmpty');
const myIsmsAssetsTableWrap = document.getElementById('myIsmsAssetsTableWrap');
const myIsmsAssetsRefresh = document.getElementById('myIsmsAssetsRefresh');

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

  const cls = {
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

if (meUser) meUser.textContent = me?.user || '–';
if (meRole) meRole.textContent = me?.effective_role || me?.role || '–';
if (meAdmin) meAdmin.textContent = me?.is_admin ? 'Yes' : 'No';

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
  const avail = (meObj?.available_themes && Array.isArray(meObj.available_themes)) ?
    meObj.available_themes :
    THEMES;

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
    {v: '/index.html', label: 'Home'},
    {v: '/controls.html', label: 'Controls register'},
    {v: '/events.html', label: 'Events'},
    {v: '/sources.html', label: 'Sources'},
    {v: '/risks.html', label: 'Risks'},
    {v: '/isms.html', label: 'ISMS'},
    {v: '/statement-of-applicability.html', label: 'Statement of Applicability'},
    {v: '/account.html', label: 'Account'},
  ];
  if (meObj?.is_admin) fixed.push({v: '/admin.html', label: 'Admin'});

  const cur = (currentLandingPagePref || '/').trim() || '/';

  const hasFixed = new Set(fixed.map((o) => o.v));

  // Normalize saved searches into {name, url} entries.
  const rawArr = Array.isArray(savedSearches) ? savedSearches : [];
  const saved = [];
  for (const s of rawArr) {
    const name = String(s?.name || '').trim();
    const url = String(s?.url || '').trim();
    if (!name || !url) continue;
    saved.push({name, url});
  }

  // Disambiguate duplicate names by suffixing with a shortened URL.
  const nameCounts = new Map();
  for (const s of saved) {
    const k = s.name.toLowerCase();
    nameCounts.set(k, (nameCounts.get(k) || 0) + 1);
  }
  const savedLabels = saved.map((s) => {
    const dup = (nameCounts.get(s.name.toLowerCase()) || 0) > 1;
    const label = dup ? `${esc(s.name)} — ${esc(shorten(s.url, 60))}` : esc(s.name);
    return {v: s.url, label};
  });

  const savedUrls = new Set(savedLabels.map((o) => o.v));

  let html = fixed
      .map((o) => `<option value="${esc(o.v)}">${esc(o.label)}</option>`)
      .join('');

  if (savedLabels.length) {
    html += `\n<optgroup label="Saved searches">`;
    // Show most recently updated first as returned by API; keep that order.
    html += savedLabels
        .map((o) => `<option value="${esc(o.v)}">${esc(o.label)}</option>`)
        .join('');
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
    const fmt = String(meObj?.default_date_format || 'ymd').toLowerCase() === 'dmy' ? 'DD/MM/YYYY' : 'YYYY-MM-DD';
    prefDefaultDateFormatHelp.textContent = `App default: ${fmt}. API values remain YYYY-MM-DD.`;
  }

  renderThemeOptions(meObj);
  currentLandingPagePref = (prefs.landing_page || '/').trim() || '/';
  currentDefaultFrameworkPref = (prefs.default_framework || '').trim();
  renderLandingPageOptions(meObj, []);

  if (prefUseLocalTz) prefUseLocalTz.checked = !!prefs.use_local_timezone;
  if (prefTimezone) prefTimezone.value = (prefs.timezone || browserTz || '').trim();
  if (prefTheme) {
    const t = (prefs.theme || 'purple').trim() || 'purple';
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
    const defaultSlug = String(catalog?.default || '').trim() || 'ISO27001:2022';

    // Keep options unique by slug and stable for rendering.
    const seen = new Set();
    const uniq = [];
    for (const it of items) {
      const slug = String(it?.slug || '').trim();
      if (!slug || seen.has(slug)) continue;
      seen.add(slug);
      uniq.push({...it, slug});
    }

    const saved = String(currentDefaultFrameworkPref || '').trim();
    if (saved && !seen.has(saved)) {
      uniq.unshift({slug: saved, name: saved, control_count: 0});
      seen.add(saved);
    }

    const defaultObj = uniq.find((x) => x.slug === defaultSlug);
    const defaultLabel = defaultObj ? _frameworkOptionLabel(defaultObj) : defaultSlug;

    const opts = [
      `<option value="">Use system default (${esc(defaultLabel)})</option>`,
      ...uniq.map((it) => `<option value="${esc(it.slug)}">${esc(_frameworkOptionLabel(it))}</option>`),
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
let sourceColorOverrides = (me?.preferences?.source_colors && typeof me.preferences.source_colors === 'object') ?
  {...me.preferences.source_colors} :
  {};

function normalizeHex(hex) {
  const h = String(hex || '').trim();
  if (!h) return '';
  let s = h.startsWith('#') ? h.slice(1) : h;
  if (/^[0-9a-fA-F]{3}$/.test(s)) s = s.split('').map((c) => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(s)) return '';
  return ('#' + s).toUpperCase();
}

function colorChipHtml(hex) {
  const c = normalizeHex(hex);
  if (!c) return '<span class="small-muted">–</span>';
  const fg = idealTextColor(c);
  const border = (fg === '#FFFFFF') ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.14)';
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
  const badge = sourceColorsBody.querySelector(`[data-src-override-badge="${CSS.escape(src)}"]`);
  const preview = sourceColorsBody.querySelector(`[data-src-preview="${CSS.escape(src)}"]`);
  const def = picker?.getAttribute('data-default') || '';
  const defaultColor = normalizeHex(def);
  const overrideColor = normalizeHex(sourceColorOverrides[src] || '');
  const effective = overrideColor || defaultColor || '#000000';
  const fg = idealTextColor(effective);
  const border = (fg === '#FFFFFF') ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.14)';

  if (picker) picker.value = effective;
  if (hexInp) hexInp.value = overrideColor || defaultColor || '';
  if (preview) {
    preview.textContent = effective;
    preview.style.background = effective;
    preview.style.color = fg;
    preview.style.borderColor = border;
  }
  if (badge) {
    const show = !!(overrideColor && defaultColor && overrideColor !== defaultColor);
    badge.style.display = show ? '' : 'none';
  }
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
  sourceColorsResetAll.addEventListener('click', () => {
    if (!confirm('Reset all your source colour overrides?')) return;
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
    row.className = 'list-group-item d-flex flex-wrap gap-2 align-items-center justify-content-between';

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
      if (!confirm(`Delete saved search "${s.name || ''}"?`)) return;
      try {
        await apiDelete(`/api/v1/me/saved-searches/${encodeURIComponent(s.id)}`);
        await loadSavedSearches();
      } catch (e) {
        toast(status, `Failed to delete: ${String(e)}`, 'danger');
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
    // Non-fatal (e.g., older server); hide the card if unsupported.
    const card = document.getElementById('savedSearchesCard');
    if (card) card.style.display = 'none';
  }
}

loadSavedSearches();


// ---------------------------------------------------------------------------
// Account tabs
// ---------------------------------------------------------------------------

function accountTabTargetFromHash(hash) {
  const h = String(hash || '').trim().toLowerCase();
  if (h === '#profile') return '#profile-pane';
  if (h === '#preferences') return '#preferences-pane';
  if (h === '#saved-searches' || h === '#savedsearches') return '#saved-searches-pane';
  if (h === '#my-questions' || h === '#questions') return '#my-questions-pane';
  if (h === '#my-audits' || h === '#audits') return '#my-audits-pane';
  if (h === '#my-meetings' || h === '#meetings' || h === '#minutes') return '#my-meetings-pane';
  if (h === '#my-risks' || h === '#risks-i-own' || h === '#risks') return '#my-risks-pane';
  if (h === '#my-isms-objectives' || h === '#isms-objectives') return '#my-isms-objectives-pane';
  if (h === '#my-isms-role' || h === '#isms-role' || h === '#my-role') return '#my-isms-role-pane';
  if (h === '#my-isms-assets' || h === '#isms-assets' || h === '#assets-i-manage') return '#my-isms-assets-pane';
  if (h === '#security' || h === '#password') return '#security-pane';
  return '';
}

function maybeLoadActiveAccountTab(target) {
  if (target === '#my-questions-pane') loadMyQuestionsOnce();
  if (target === '#my-audits-pane') loadMyAuditsOnce();
  if (target === '#my-meetings-pane') loadMyMeetingsOnce();
  if (target === '#my-risks-pane') loadMyRisksOnce();
  if (target === '#my-isms-objectives-pane') loadMyIsmsObjectivesOnce();
  if (target === '#my-isms-role-pane') loadMyIsmsRoleOnce();
  if (target === '#my-isms-assets-pane') loadMyIsmsAssetsOnce();
}

function bindAccountTabs() {
  const tabButtons = Array.from(document.querySelectorAll('#accountTabs [data-bs-toggle="tab"]'));
  for (const btn of tabButtons) {
    btn.addEventListener('shown.bs.tab', () => {
      const target = btn.getAttribute('data-bs-target') || '';
      const hash = btn.getAttribute('data-tab-hash') || '';
      if (hash && window.location.hash !== hash) {
        try {
          window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${hash}`);
        } catch {
          // ignore
        }
      }
      maybeLoadActiveAccountTab(target);
    });
  }

  const target = accountTabTargetFromHash(window.location.hash);
  if (target) {
    const trigger = document.querySelector(`#accountTabs [data-bs-target="${target}"]`);
    if (trigger && window.bootstrap?.Tab) {
      window.bootstrap.Tab.getOrCreateInstance(trigger).show();
      return;
    }
  }
  maybeLoadActiveAccountTab('#profile-pane');
}


// ---------------------------------------------------------------------------
// My Questions
// ---------------------------------------------------------------------------

let myQuestionsLoaded = false;

function myQuestionStatusBadge(st, unread) {
  const s = String(st || 'unanswered').toLowerCase();
  if (unread) return `<span class="badge text-bg-warning">new reply</span>`;
  if (s === 'answered') return `<span class="badge text-bg-success">answered</span>`;
  if (s === 'reviewing') return `<span class="badge text-bg-info">reviewing</span>`;
  return `<span class="badge text-bg-secondary">unanswered</span>`;
}

function myQuestionRow(it) {
  const framework = getCurrentFramework();
  const rawHref = withFramework(it?.target_url || `/event.html?id=${encodeURIComponent(it?.event_id || '')}&thread=${encodeURIComponent(it?.thread_id || '')}#questions`, framework);
  const href = safeExternalHref(rawHref) || '#';
  const title = it.target_label || it.event_summary || '(no summary)';
  const src = it.target_type && it.target_type !== 'event' ? String(it.target_type).replaceAll('_', ' ') : (it.event_source || '');
  const ts = it.event_timestamp ? fmtTs(it.event_timestamp) : (it.target_ref || '—');
  const updated = it.updated_at ? fmtTs(it.updated_at) : '—';
  const badge = myQuestionStatusBadge(it.status, !!it.unread);

  return `
    <tr class="${it.unread ? 'table-warning' : ''}">
      <td class="wrap">
        <div class="fw-semibold">${esc(title)}</div>
        <div class="small-muted">${esc(src)} • ${esc(ts)}</div>
      </td>
      <td>${badge}</td>
      <td class="small-muted">${esc(updated)}</td>
      <td class="text-end">
        <div class="btn-group btn-group-sm" role="group" aria-label="Question actions">
          <a class="btn btn-outline-primary" href="${esc(href)}">Open</a>
          ${canDeleteQuestions ? `<button class="btn btn-outline-danger" type="button" data-delete-question="${esc(it.thread_id || '')}">Delete</button>` : ''}
        </div>
      </td>
    </tr>
  `;
}

async function loadMyQuestions() {
  if (!myQuestionsTbody) return;
  try {
    if (myQuestionsStatus) myQuestionsStatus.style.display = 'none';
    myQuestionsTbody.innerHTML = '<tr><td colspan="4" class="p-4 small-muted">Loading…</td></tr>';
    if (myQuestionsTableWrap) myQuestionsTableWrap.style.display = '';
    if (myQuestionsEmpty) myQuestionsEmpty.style.display = 'none';

    const res = await apiGet('/api/v1/me/questions');
    const items = Array.isArray(res?.items) ? res.items : [];

    if (items.length === 0) {
      if (myQuestionsEmpty) myQuestionsEmpty.style.display = '';
      if (myQuestionsTableWrap) myQuestionsTableWrap.style.display = 'none';
      myQuestionsTbody.innerHTML = '';
    } else {
      if (myQuestionsEmpty) myQuestionsEmpty.style.display = 'none';
      if (myQuestionsTableWrap) myQuestionsTableWrap.style.display = '';
      myQuestionsTbody.innerHTML = items.map(myQuestionRow).join('');
    }

    myQuestionsLoaded = true;
    try {
      window.keenRefreshQuestionBell?.();
    } catch {
      // ignore
    }
  } catch (err) {
    if (myQuestionsTableWrap) myQuestionsTableWrap.style.display = 'none';
    if (myQuestionsStatus) {
      myQuestionsStatus.className = 'alert alert-danger';
      myQuestionsStatus.style.display = '';
      myQuestionsStatus.textContent = `Failed to load questions: ${String(err)}`;
    } else {
      toast(status, `Failed to load questions: ${String(err)}`, 'danger');
    }
  }
}

function loadMyQuestionsOnce() {
  if (!myQuestionsLoaded) loadMyQuestions();
}

myQuestionsRefresh?.addEventListener('click', () => {
  myQuestionsLoaded = false;
  loadMyQuestions();
});
myQuestionsTbody?.addEventListener('click', async (ev) => {
  const btn = ev.target?.closest?.('[data-delete-question]');
  if (!btn || !canDeleteQuestions) return;
  const tid = btn.getAttribute('data-delete-question') || '';
  if (!tid) return;
  if (!confirm('Delete this question thread and all replies?')) return;
  btn.disabled = true;
  try {
    await apiDelete(`/api/v1/questions/${encodeURIComponent(tid)}`);
    toast(status, 'Question deleted', 'success');
    await loadMyQuestions();
  } catch (e) {
    toast(status, String(e), 'danger');
    btn.disabled = false;
  }
});


// ---------------------------------------------------------------------------
// My Audits
// ---------------------------------------------------------------------------

let myAuditsLoaded = false;

function auditStatusLabel(s) {
  const v = String(s || 'open');
  if (v === 'in_progress') return 'In progress';
  return v ? v.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase()) : 'Open';
}

function myAuditRow(a) {
  const url = withFramework(`/audit.html?id=${encodeURIComponent(a.id)}`, a.framework_slug || getCurrentFramework());
  const dates = [a.start_date, a.end_date].filter(Boolean).join(' → ') || '—';
  const attendeeRole = String(a?.my_attendee?.role || '').trim() || 'Attendee';
  const updated = a.updated_at ? fmtTs(a.updated_at) : '—';
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

async function loadMyAudits() {
  if (!myAuditsTbody) return;
  try {
    if (myAuditsStatus) myAuditsStatus.style.display = 'none';
    myAuditsTbody.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">Loading…</td></tr>';
    if (myAuditsTableWrap) myAuditsTableWrap.style.display = '';
    if (myAuditsEmpty) myAuditsEmpty.style.display = 'none';

    const res = await apiGet('/api/v1/me/audits');
    const items = Array.isArray(res?.items) ? res.items : [];

    if (items.length === 0) {
      if (myAuditsEmpty) myAuditsEmpty.style.display = '';
      if (myAuditsTableWrap) myAuditsTableWrap.style.display = 'none';
      myAuditsTbody.innerHTML = '';
    } else {
      if (myAuditsEmpty) myAuditsEmpty.style.display = 'none';
      if (myAuditsTableWrap) myAuditsTableWrap.style.display = '';
      myAuditsTbody.innerHTML = items.map(myAuditRow).join('');
    }

    myAuditsLoaded = true;
  } catch (err) {
    if (myAuditsTableWrap) myAuditsTableWrap.style.display = 'none';
    if (myAuditsStatus) {
      myAuditsStatus.className = 'alert alert-danger';
      myAuditsStatus.style.display = '';
      myAuditsStatus.textContent = `Failed to load audits: ${String(err)}`;
    } else {
      toast(status, `Failed to load audits: ${String(err)}`, 'danger');
    }
  }
}

function loadMyAuditsOnce() {
  if (!myAuditsLoaded) loadMyAudits();
}

myAuditsRefresh?.addEventListener('click', () => {
  myAuditsLoaded = false;
  loadMyAudits();
});

// ---------------------------------------------------------------------------
// Risks I own
// ---------------------------------------------------------------------------

let myRisksLoaded = false;

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
  const url = withFramework(`/risk.html?id=${encodeURIComponent(r.id || '')}`, r.framework || getCurrentFramework());
  const asset = String(r.asset || r.asset_entity?.name || 'Risk').trim() || 'Risk';
  const threat = String(r.threat_summary || '').trim();
  const categoryBits = [r.category?.name, r.subcategory?.name].filter(Boolean);
  const category = categoryBits.join(' / ') || '—';
  const types = Array.isArray(r.risk_types) ? r.risk_types : [];
  const typeHtml = types.length
    ? types.map((t) => `<span class="badge badge-soft me-1">${esc(t)}</span>`).join('')
    : '<span class="small-muted">—</span>';
  const controlsText = Number(r.control_count || 0) === 1 ? '1 control' : `${Number(r.control_count || 0)} controls`;
  const updated = r.updated_at ? fmtTs(r.updated_at) : '—';

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

async function loadMyRisks() {
  if (!myRisksTbody) return;
  try {
    if (myRisksStatus) myRisksStatus.style.display = 'none';
    myRisksTbody.innerHTML = '<tr><td colspan="8" class="p-4 small-muted">Loading…</td></tr>';
    if (myRisksTableWrap) myRisksTableWrap.style.display = '';
    if (myRisksEmpty) myRisksEmpty.style.display = 'none';

    const u = new URL('/api/v1/me/risks', location.origin);
    const fw = getCurrentFramework();
    if (fw) u.searchParams.set('framework', fw);
    const res = await apiGet(u.pathname + u.search);
    const items = Array.isArray(res?.items) ? res.items : [];

    if (items.length === 0) {
      if (myRisksEmpty) myRisksEmpty.style.display = '';
      if (myRisksTableWrap) myRisksTableWrap.style.display = 'none';
      myRisksTbody.innerHTML = '';
    } else {
      if (myRisksEmpty) myRisksEmpty.style.display = 'none';
      if (myRisksTableWrap) myRisksTableWrap.style.display = '';
      myRisksTbody.innerHTML = items.map(myRiskRow).join('');
    }

    myRisksLoaded = true;
  } catch (err) {
    if (myRisksTableWrap) myRisksTableWrap.style.display = 'none';
    if (myRisksStatus) {
      myRisksStatus.className = 'alert alert-danger';
      myRisksStatus.style.display = '';
      myRisksStatus.textContent = `Failed to load risks: ${String(err)}`;
    } else {
      toast(status, `Failed to load risks: ${String(err)}`, 'danger');
    }
  }
}

function loadMyRisksOnce() {
  if (!myRisksLoaded) loadMyRisks();
}

myRisksRefresh?.addEventListener('click', () => {
  myRisksLoaded = false;
  loadMyRisks();
});



// ---------------------------------------------------------------------------
// My ISMS account cross-references
// ---------------------------------------------------------------------------

let myIsmsObjectivesLoaded = false;
let myIsmsMeetingsLoaded = false;
let myIsmsRoleLoaded = false;
let myIsmsAssetsLoaded = false;

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
  if (start && end) return `${start}–${end}`;
  return start || end || '—';
}

function myMeetingRow(item) {
  const href = withFramework(
    `/isms-meeting.html?id=${encodeURIComponent(item?.id || '')}`,
    getCurrentFramework(),
  );
  return `<tr>
    <td class="wrap"><span class="fw-semibold">${esc(item?.title || 'Meeting')}</span><div class="small-muted">${esc(item?.agenda_minutes_notes || '')}</div></td>
    <td class="small-muted">${esc(item?.date || '—')}</td>
    <td class="small-muted">${esc(meetingTimeLabel(item))}</td>
    <td><span class="badge text-bg-light">${esc(item?.my_attendance_label || '—')}</span></td>
    <td>${userPillsHtml(item?.attendees || [])}</td>
    <td>${userPillsHtml(item?.apologies || [])}</td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(href)}">Open</a></td>
  </tr>`;
}

async function loadMyMeetings() {
  if (!myMeetingsTbody) return;
  try {
    if (myMeetingsStatus) myMeetingsStatus.style.display = 'none';
    myMeetingsTbody.innerHTML = '<tr><td colspan="7" class="p-4 small-muted">Loading…</td></tr>';
    if (myMeetingsTableWrap) myMeetingsTableWrap.style.display = '';
    if (myMeetingsEmpty) myMeetingsEmpty.style.display = 'none';

    const u = new URL('/api/v1/me/isms-meetings', location.origin);
    const fw = getCurrentFramework();
    if (fw) u.searchParams.set('framework', fw);
    const res = await apiGet(u.pathname + u.search);
    const items = Array.isArray(res?.items) ? res.items : [];
    if (!items.length) {
      if (myMeetingsEmpty) myMeetingsEmpty.style.display = '';
      if (myMeetingsTableWrap) myMeetingsTableWrap.style.display = 'none';
      myMeetingsTbody.innerHTML = '';
    } else {
      if (myMeetingsEmpty) myMeetingsEmpty.style.display = 'none';
      if (myMeetingsTableWrap) myMeetingsTableWrap.style.display = '';
      myMeetingsTbody.innerHTML = items.map(myMeetingRow).join('');
    }
    myIsmsMeetingsLoaded = true;
  } catch (err) {
    if (myMeetingsTableWrap) myMeetingsTableWrap.style.display = 'none';
    if (myMeetingsStatus) {
      myMeetingsStatus.className = 'alert alert-danger';
      myMeetingsStatus.style.display = '';
      myMeetingsStatus.textContent = `Failed to load ISMS meetings: ${String(err)}`;
    } else {
      toast(status, `Failed to load ISMS meetings: ${String(err)}`, 'danger');
    }
  }
}

function loadMyMeetingsOnce() {
  if (!myIsmsMeetingsLoaded) loadMyMeetings();
}

myMeetingsRefresh?.addEventListener('click', () => {
  myIsmsMeetingsLoaded = false;
  loadMyMeetings();
});

function myIsmsObjectiveRow(item) {
  const owner = userPillHtml(item?.owner_user);
  const target = item?.completion_target_date || '—';
  return `<tr>
    <td class="wrap"><span class="fw-semibold">${esc(item?.requirement || 'Objective')}</span><div class="small-muted">${esc(item?.metric || '')}</div></td>
    <td class="wrap">${esc(item?.goal || '—')}</td>
    <td>${owner}</td>
    <td>${peopleBadges(item?.resource_users)}</td>
    <td class="small-muted">${esc(target)}</td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(ismsUrl('objectives'))}">Open</a></td>
  </tr>`;
}

async function loadMyIsmsObjectives() {
  if (!myIsmsObjectivesTbody) return;
  try {
    if (myIsmsObjectivesStatus) myIsmsObjectivesStatus.style.display = 'none';
    myIsmsObjectivesTbody.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">Loading…</td></tr>';
    if (myIsmsObjectivesTableWrap) myIsmsObjectivesTableWrap.style.display = '';
    if (myIsmsObjectivesEmpty) myIsmsObjectivesEmpty.style.display = 'none';

    const u = new URL('/api/v1/me/isms-objectives', location.origin);
    const fw = getCurrentFramework();
    if (fw) u.searchParams.set('framework', fw);
    const res = await apiGet(u.pathname + u.search);
    const items = Array.isArray(res?.items) ? res.items : [];
    if (!items.length) {
      if (myIsmsObjectivesEmpty) myIsmsObjectivesEmpty.style.display = '';
      if (myIsmsObjectivesTableWrap) myIsmsObjectivesTableWrap.style.display = 'none';
      myIsmsObjectivesTbody.innerHTML = '';
    } else {
      if (myIsmsObjectivesEmpty) myIsmsObjectivesEmpty.style.display = 'none';
      if (myIsmsObjectivesTableWrap) myIsmsObjectivesTableWrap.style.display = '';
      myIsmsObjectivesTbody.innerHTML = items.map(myIsmsObjectiveRow).join('');
    }
    myIsmsObjectivesLoaded = true;
  } catch (err) {
    if (myIsmsObjectivesTableWrap) myIsmsObjectivesTableWrap.style.display = 'none';
    if (myIsmsObjectivesStatus) {
      myIsmsObjectivesStatus.className = 'alert alert-danger';
      myIsmsObjectivesStatus.style.display = '';
      myIsmsObjectivesStatus.textContent = `Failed to load ISMS objectives: ${String(err)}`;
    } else {
      toast(status, `Failed to load ISMS objectives: ${String(err)}`, 'danger');
    }
  }
}

function loadMyIsmsObjectivesOnce() {
  if (!myIsmsObjectivesLoaded) loadMyIsmsObjectives();
}

myIsmsObjectivesRefresh?.addEventListener('click', () => {
  myIsmsObjectivesLoaded = false;
  loadMyIsmsObjectives();
});

function myIsmsRoleRow(item) {
  const node = item?.org_node || item?.node || item || {};
  const path = orgPathLabel(item?.path) || orgPathLabel(node?.path) || orgNodeLabel(node) || '—';
  return `<tr>
    <td class="wrap"><span class="fw-semibold">${esc(orgNodeLabel(node) || 'Role')}</span><div class="small-muted">${esc(node.description || '')}</div></td>
    <td><span class="badge text-bg-light">${esc(node.node_type || 'role')}</span></td>
    <td>${esc(item?.relationship_type || 'member')}</td>
    <td class="small-muted wrap">${esc(path)}</td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(ismsUrl('org'))}">Open</a></td>
  </tr>`;
}

async function loadMyIsmsRole() {
  if (!myIsmsRoleTbody) return;
  try {
    if (myIsmsRoleStatus) myIsmsRoleStatus.style.display = 'none';
    myIsmsRoleTbody.innerHTML = '<tr><td colspan="5" class="p-4 small-muted">Loading…</td></tr>';
    if (myIsmsRoleTableWrap) myIsmsRoleTableWrap.style.display = '';
    if (myIsmsRoleEmpty) myIsmsRoleEmpty.style.display = 'none';

    const res = await apiGet('/api/v1/me/isms-role');
    const items = Array.isArray(res?.items) ? res.items : [];
    if (!items.length) {
      if (myIsmsRoleEmpty) myIsmsRoleEmpty.style.display = '';
      if (myIsmsRoleTableWrap) myIsmsRoleTableWrap.style.display = 'none';
      myIsmsRoleTbody.innerHTML = '';
    } else {
      if (myIsmsRoleEmpty) myIsmsRoleEmpty.style.display = 'none';
      if (myIsmsRoleTableWrap) myIsmsRoleTableWrap.style.display = '';
      myIsmsRoleTbody.innerHTML = items.map(myIsmsRoleRow).join('');
    }
    myIsmsRoleLoaded = true;
  } catch (err) {
    if (myIsmsRoleTableWrap) myIsmsRoleTableWrap.style.display = 'none';
    if (myIsmsRoleStatus) {
      myIsmsRoleStatus.className = 'alert alert-danger';
      myIsmsRoleStatus.style.display = '';
      myIsmsRoleStatus.textContent = `Failed to load ISMS roles: ${String(err)}`;
    } else {
      toast(status, `Failed to load ISMS roles: ${String(err)}`, 'danger');
    }
  }
}

function loadMyIsmsRoleOnce() {
  if (!myIsmsRoleLoaded) loadMyIsmsRole();
}

myIsmsRoleRefresh?.addEventListener('click', () => {
  myIsmsRoleLoaded = false;
  loadMyIsmsRole();
});

function assetRoleLabel(item) {
  const roles = [];
  if (item?.is_owner) roles.push('Owner');
  if (item?.is_register_holder) roles.push('Register holder');
  return roles.join(', ') || item?.relationship || '—';
}

function myIsmsAssetRow(item) {
  const asset = (item?.asset && typeof item.asset === 'object') ? item.asset : (item || {});
  const taxonomy = [asset.category?.name || asset.category_name, asset.subcategory?.name || asset.subcategory_name].filter(Boolean).join(' / ');
  return `<tr>
    <td class="wrap"><span class="fw-semibold">${esc(asset.asset || asset.name || 'Asset')}</span><div class="small-muted">${esc(taxonomy || asset.description || '')}</div></td>
    <td>${esc(asset.license || '—')}</td>
    <td><span class="badge text-bg-light">${esc(assetRoleLabel(item))}</span></td>
    <td class="small-muted">${esc(orgNodeLabel(asset.owner_org_node) || '—')}</td>
    <td class="small-muted">${esc(orgNodeLabel(asset.register_held_by_org_node) || '—')}</td>
    <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="${esc(ismsUrl('assets'))}">Open</a></td>
  </tr>`;
}

async function loadMyIsmsAssets() {
  if (!myIsmsAssetsTbody) return;
  try {
    if (myIsmsAssetsStatus) myIsmsAssetsStatus.style.display = 'none';
    myIsmsAssetsTbody.innerHTML = '<tr><td colspan="6" class="p-4 small-muted">Loading…</td></tr>';
    if (myIsmsAssetsTableWrap) myIsmsAssetsTableWrap.style.display = '';
    if (myIsmsAssetsEmpty) myIsmsAssetsEmpty.style.display = 'none';

    const u = new URL('/api/v1/me/isms-assets', location.origin);
    const fw = getCurrentFramework();
    if (fw) u.searchParams.set('framework', fw);
    const res = await apiGet(u.pathname + u.search);
    const items = Array.isArray(res?.items) ? res.items : [];
    if (!items.length) {
      if (myIsmsAssetsEmpty) myIsmsAssetsEmpty.style.display = '';
      if (myIsmsAssetsTableWrap) myIsmsAssetsTableWrap.style.display = 'none';
      myIsmsAssetsTbody.innerHTML = '';
    } else {
      if (myIsmsAssetsEmpty) myIsmsAssetsEmpty.style.display = 'none';
      if (myIsmsAssetsTableWrap) myIsmsAssetsTableWrap.style.display = '';
      myIsmsAssetsTbody.innerHTML = items.map(myIsmsAssetRow).join('');
    }
    myIsmsAssetsLoaded = true;
  } catch (err) {
    if (myIsmsAssetsTableWrap) myIsmsAssetsTableWrap.style.display = 'none';
    if (myIsmsAssetsStatus) {
      myIsmsAssetsStatus.className = 'alert alert-danger';
      myIsmsAssetsStatus.style.display = '';
      myIsmsAssetsStatus.textContent = `Failed to load ISMS assets: ${String(err)}`;
    } else {
      toast(status, `Failed to load ISMS assets: ${String(err)}`, 'danger');
    }
  }
}

function loadMyIsmsAssetsOnce() {
  if (!myIsmsAssetsLoaded) loadMyIsmsAssets();
}

myIsmsAssetsRefresh?.addEventListener('click', () => {
  myIsmsAssetsLoaded = false;
  loadMyIsmsAssets();
});

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
      theme: (prefTheme?.value || 'purple').trim() || 'purple',
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
        if (currentDefaultFrameworkPref) localStorage.setItem('keen_framework', currentDefaultFrameworkPref);
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
      toast(status, `Failed: ${String(e)}`, 'danger');
    }
  });
}

if (logoutBtn && me?.logout_enabled !== false) {
  const logoutUrl = (me && me.logout_url ? String(me.logout_url) : '').trim();
  logoutBtn.addEventListener('click', async () => {
    // Native OIDC logout needs the stored ID token, so navigate directly to
    // KEEN's logout URL; it clears local cookies before redirecting upstream.
    if (logoutUrl) {
      location.href = logoutUrl;
      return;
    }

    try {
      await apiPost('/api/v1/auth/logout', {});
    } catch (_e) {
      // ignore
    }

    location.href = '/login.html';
  });
}

if (pwForm && me?.password_change_enabled !== false) {
  pwForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (status) status.style.display = 'none';

    const current_password = (curPw?.value || '');
    const new_password = (newPw?.value || '');

    if (!current_password || !new_password) {
      toast(status, 'Both current and new password are required', 'warning');
      return;
    }

    try {
      await apiPost('/api/v1/me/password', {current_password, new_password});
      toast(status, 'Password updated', 'success');
      if (curPw) curPw.value = '';
      if (newPw) newPw.value = '';
    } catch (e) {
      toast(status, `Failed: ${String(e)}`, 'danger');
    }
  });
}
