import {initNavbar, apiGet, apiPost, apiPatch, apiPut, apiDelete, apiPostForm, esc, toast, debounce, fmtTs, getCurrentFramework, withFramework, safeExternalHref, entityChangelogHtml, userPillHtml} from '/app.js';

const me = await initNavbar();
const currentFramework = getCurrentFramework();

const autoApplyFilters = !!(me?.preferences?.auto_apply_filters);

const frameworkInput = document.getElementById('framework');
if (frameworkInput && !frameworkInput.value) frameworkInput.value = currentFramework;

const isAdmin = !!me?.is_admin;
const canAudit = !!me?.can_audit_trail;


const status = document.getElementById('status');
const out = document.getElementById('out');

function selectedFrameworkFallback() {
  const f = (document.getElementById('framework')?.value || '').trim();
  return f || currentFramework || 'ISO27001:2022';
}

// Questions (admin queue)
const questionsStatus = document.getElementById('questionsStatus');
const questionsRows = document.getElementById('questionsRows');
const questionsRefresh = document.getElementById('questionsRefresh');
const questionsStatusFilter = document.getElementById('questionsStatusFilter');

// ------------------------------------------------------------------
// RBAC caches (users / groups / permissions)
// ------------------------------------------------------------------

let cachedUsers = [];
let cachedGroups = [];
let cachedPerms = [];

function selValues(selectEl) {
  if (!selectEl) return [];
  return Array.from(selectEl.selectedOptions || []).map((o) => String(o.value));
}

function setOptions(selectEl, items, getValue, getLabel, selected = []) {
  if (!selectEl) return;
  const sel = new Set((selected || []).map((v) => String(v)));
  selectEl.innerHTML = (items || []).map((it) => {
    const v = String(getValue(it));
    const label = String(getLabel(it));
    const s = sel.has(v) ? 'selected' : '';
    return `<option value="${esc(v)}" ${s} title="${esc(label)}">${esc(label)}</option>`;
  }).join('');
}


function enhanceWrappedMultiSelect(selectEl) {
  // Turns a <select multiple> into a scrollable checkbox list with wrapped labels.
  // Keeps the original <select> in sync so existing code can continue using selValues(selectEl).
  if (!selectEl) return;
  if (!selectEl.classList?.contains('keen-wrap-multiselect')) return;

  let wrap = selectEl.nextElementSibling;
  if (!wrap || !wrap.classList.contains('keen-multiselect-wrap')) {
    wrap = document.createElement('div');
    wrap.className = 'keen-multiselect-wrap';
    wrap.setAttribute('data-for', selectEl.id || '');
    selectEl.insertAdjacentElement('afterend', wrap);
  }

  // Hide the native control (but keep it in the DOM for reads/writes).
  selectEl.classList.add('keen-multiselect-native-hidden');

  wrap.innerHTML = '';
  const opts = Array.from(selectEl.options || []);
  for (const opt of opts) {
    const label = document.createElement('label');
    label.className = 'keen-ms-option';

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = String(opt.value ?? '');
    cb.checked = !!opt.selected;

    const span = document.createElement('span');
    span.className = 'keen-ms-label';
    span.textContent = opt.textContent || '';

    // Hover tooltip for very long entries
    label.title = span.textContent;

    cb.addEventListener('change', () => {
      opt.selected = cb.checked;
    });

    label.appendChild(cb);
    label.appendChild(span);
    wrap.appendChild(label);
  }
}


// Hide ingest runners that are disabled at runtime (e.g. KEEN_TAIGA_ENABLED=false).
async function hideDisabledIngestButtons() {
  try {
    const data = await apiGet('/api/v1/admin/ingest/status');
    const enabled = data?.enabled || {};
    document.querySelectorAll('[data-run]').forEach((btn) => {
      const path = (btn.getAttribute('data-run') || '').trim();
      const m = path.match(/\/admin\/ingest\/([^/]+)\/run/i);
      const name = (m?.[1] || '').toLowerCase();
      if (!name) return;
      if (enabled[name] === false) {
        btn.style.display = 'none';
      }
    });
    const resetLokiBtn = document.getElementById('resetLokiCursors');
    if (resetLokiBtn && enabled.loki === false) {
      resetLokiBtn.style.display = 'none';
    }
  } catch {
    // If we can't fetch status, keep buttons visible.
  }
}

if (isAdmin) {
  await hideDisabledIngestButtons();
}

// ------------------------------------------------------------------
// Tabs
// ------------------------------------------------------------------

const TAB_HASH_TO_ID = {
  '#users': 'tab-users',
  '#groups': 'tab-groups',
  '#ingest': 'tab-ingest',
  '#import': 'tab-ingest',
  '#diary': 'tab-diary',
  '#remap': 'tab-remap',
  '#audit': 'tab-audit',
};

function activateTabFromHash() {
  const tabId = TAB_HASH_TO_ID[(location.hash || '').toLowerCase()];
  if (!tabId) return;
  const el = document.getElementById(tabId);
  if (!el || !window.bootstrap?.Tab) return;
  try {
    window.bootstrap.Tab.getOrCreateInstance(el).show();
  } catch {
    // ignore
  }
}

// Update URL hash when the user switches tabs.
document.querySelectorAll('#adminTabs button[data-bs-toggle="tab"]').forEach((btn) => {
  btn.addEventListener('shown.bs.tab', (ev) => {
    const id = ev.target?.id;
    const entry = Object.entries(TAB_HASH_TO_ID).find(([, v]) => v === id);
    if (!entry) return;
    const [hash] = entry;
    if (location.hash !== hash) history.replaceState(null, '', hash);
  });
});

// On first load / back-forward navigation.
activateTabFromHash();
window.addEventListener('hashchange', activateTabFromHash);

function setOut(msg) {
  if (out) out.textContent = msg;
  // If output is collapsed, pop it open whenever we write something non-trivial.
  const collapseEl = document.getElementById('outCollapse');
  if (!collapseEl) return;
  const shouldOpen = (msg || '').trim() && (msg || '').trim() !== 'Ready.';
  if (!shouldOpen || !window.bootstrap?.Collapse) return;
  try {
    window.bootstrap.Collapse.getOrCreateInstance(collapseEl, {toggle: false}).show();
  } catch {
    // ignore
  }
}

// If the user is not an admin but has audit-trail access, restrict this page
// to the Audit trail tab (and avoid calling admin-only APIs).
function restrictToAuditOnly() {
  const tabs = document.getElementById('adminTabs');
  if (!tabs) return;

  // Hide all tabs except Audit.
  tabs.querySelectorAll('button.nav-link').forEach((b) => {
    if ((b.id || '').toLowerCase() !== 'tab-audit') {
      b.style.display = 'none';
    }
  });

  // Hide all panes except Audit.
  document.querySelectorAll('.tab-pane').forEach((p) => {
    if ((p.id || '').toLowerCase() !== 'pane-audit') {
      p.style.display = 'none';
    }
  });

  // Force audit tab.
  if ((location.hash || '').toLowerCase() !== '#audit') {
    history.replaceState(null, '', '#audit');
  }
  try {
    activateTabFromHash();
  } catch {
    // ignore
  }
}

async function runJsonPost(path, body) {
  status.style.display = 'none';
  setOut('Running…');
  try {
    const res = await apiPost(path, body);
    setOut(typeof res === 'string' ? res : JSON.stringify(res, null, 2));
    toast(status, 'OK', 'success');
  } catch (e) {
    setOut(String(e));
    toast(status, `Failed: ${String(e)}`, 'danger');
  }
}

// ------------------------------------------------------------------
// User management (admin-only)
// ------------------------------------------------------------------

const usersStatus = document.getElementById('usersStatus');
const usersRows = document.getElementById('usersRows');
const usersRefresh = document.getElementById('usersRefresh');
const userCreateForm = document.getElementById('userCreateForm');
const newUsername = document.getElementById('newUsername');
const newEmail = document.getElementById('newEmail');
const newPassword = document.getElementById('newPassword');
const newRole = document.getElementById('newRole');

function roleLabel(role) {
  const r = String(role || '').toLowerCase();
  if (r === 'admin') return 'Admin';
  if (r === 'normal') return 'Normal';
  if (r === 'inherit') return 'Inherit';
  return r || 'Normal';
}

function groupRoleSuffix(g) {
  const r = String(g?.role || '').toLowerCase();
  if (!r) return '';
  return ` (${roleLabel(r)})`;
}

function renderUsers(users) {
  if (!usersRows) return;
  const rows = (users || []).map((u) => {
    const role = (u.role || 'normal').toLowerCase();
    const effRole = (u.effective_role || role || 'normal').toLowerCase();
    const showEff = effRole && effRole !== role;
    const activeChecked = u.is_active ? 'checked' : '';
    const created = u.created_at ? fmtTs(u.created_at) : '–';
    const updated = u.updated_at ? fmtTs(u.updated_at) : '–';

    return `
      <tr data-user-id="${esc(u.id)}">
        <td data-username="${esc(u.username || '')}">${userPillHtml(u)}</td>
        <td>
          <input class="form-control form-control-sm" type="email" data-email value="${esc(u.email || '')}" placeholder="user@example.com" />
        </td>
        <td>
          <select class="form-select form-select-sm" data-role>
            <option value="inherit" ${role === 'inherit' ? 'selected' : ''}>Inherit (from groups)</option>
            <option value="normal" ${role === 'normal' ? 'selected' : ''}>Normal</option>
            <option value="admin" ${role === 'admin' ? 'selected' : ''}>Admin</option>
          </select>
          ${showEff ? `<div class="small-muted mt-1">Effective: ${esc(roleLabel(effRole))}</div>` : ''}
        </td>
        <td>
          <div class="form-check">
            <input class="form-check-input" type="checkbox" data-active ${activeChecked} />
          </div>
        </td>
        <td class="small-muted">${esc(created)}</td>
        <td class="small-muted">${esc(updated)}</td>
        <td class="d-flex flex-wrap gap-2">
          <button class="btn btn-outline-primary btn-sm" type="button" data-save>Save</button>
          <button class="btn btn-outline-primary btn-sm" type="button" data-access>Access…</button>
          <button class="btn btn-outline-primary btn-sm" type="button" data-reset>Password…</button>
          <button class="btn btn-outline-danger btn-sm" type="button" data-delete>Delete</button>
        </td>
      </tr>
    `;
  }).join('');

  usersRows.innerHTML = rows || `<tr><td colspan="7" class="small-muted">No users found.</td></tr>`;

  usersRows.querySelectorAll('[data-save]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const tr = btn.closest('tr');
      if (!tr) return;
      const userId = tr.getAttribute('data-user-id');
      const roleEl = tr.querySelector('[data-role]');
      const activeEl = tr.querySelector('[data-active]');
      const emailEl = tr.querySelector('[data-email]');
      const role = roleEl ? roleEl.value : null;
      const isActive = activeEl ? !!activeEl.checked : null;
      const email = emailEl ? String(emailEl.value || '').trim() : '';

      usersStatus.style.display = 'none';
      try {
        await apiPatch(`/api/v1/users/${encodeURIComponent(userId)}`, {role, is_active: isActive, email});
        toast(usersStatus, 'User updated', 'success');
        await refreshUsers(false);
      } catch (e) {
        toast(usersStatus, `Failed: ${String(e)}`, 'danger');
      }
    });
  });

  usersRows.querySelectorAll('[data-access]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const tr = btn.closest('tr');
      if (!tr) return;
      const userId = tr.getAttribute('data-user-id');
      const username = (tr.querySelector('td')?.textContent || '').trim() || 'user';
      await openUserAccessModal(userId, username);
    });
  });

  usersRows.querySelectorAll('[data-reset]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const tr = btn.closest('tr');
      if (!tr) return;
      const userId = tr.getAttribute('data-user-id');
      const username = (tr.querySelector('td')?.textContent || '').trim() || 'user';

      const pw = prompt(`Set a new password for ${username}:`);
      if (!pw) return;

      usersStatus.style.display = 'none';
      try {
        await apiPost(`/api/v1/users/${encodeURIComponent(userId)}/password`, {new_password: pw});
        toast(usersStatus, `Password updated for ${username}`, 'success');
      } catch (e) {
        toast(usersStatus, `Failed: ${String(e)}`, 'danger');
      }
    });
  });

  usersRows.querySelectorAll('[data-delete]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const tr = btn.closest('tr');
      if (!tr) return;
      const userId = tr.getAttribute('data-user-id');
      const username = (tr.querySelector('td')?.textContent || '').trim() || 'user';
      if (!confirm(`Delete ${username} permanently? This removes the account row but keeps historical audit/question records with a blank/deleted author.`)) return;
      usersStatus.style.display = 'none';
      try {
        await apiDelete(`/api/v1/users/${encodeURIComponent(userId)}`);
        toast(usersStatus, `Deleted ${username}`, 'success');
        await refreshUsers(false);
      } catch (e) {
        toast(usersStatus, `Failed: ${String(e)}`, 'danger');
      }
    });
  });

}

async function refreshUsers(showLoading = true) {
  if (!usersRows) return;
  if (showLoading) {
    usersRows.innerHTML = `<tr><td colspan="7" class="small-muted">Loading…</td></tr>`;
  }

  try {
    const users = await apiGet('/api/v1/users');
    cachedUsers = Array.isArray(users) ? users : [];
    renderUsers(cachedUsers);
  } catch (e) {
    renderUsers([]);
    toast(usersStatus, `Failed to load users: ${String(e)}`, 'danger');
  }
}

if (usersRefresh) usersRefresh.addEventListener('click', () => refreshUsers(true));

if (userCreateForm) {
  userCreateForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    usersStatus.style.display = 'none';
    try {
      const username = (newUsername?.value || '').trim();
      const email = (newEmail?.value || '').trim();
      const password = (newPassword?.value || '');
      const role = (newRole?.value || 'inherit').trim();

      if (!username) throw new Error('Username is required');
      if (!password) throw new Error('Password is required');

      await apiPost('/api/v1/users', {username, email, password, role});
      toast(usersStatus, `Created ${username} (${roleLabel(role)})`, 'success');
      if (newUsername) newUsername.value = '';
      if (newEmail) newEmail.value = '';
      if (newPassword) newPassword.value = '';
      if (newRole) newRole.value = 'inherit';
      await refreshUsers(false);
    } catch (e) {
      toast(usersStatus, `Failed: ${String(e)}`, 'danger');
    }
  });
}

// ------------------------------------------------------------------
// User access modal (groups + explicit permissions)
// ------------------------------------------------------------------

const userAccessModalEl = document.getElementById('userAccessModal');
const userAccessUserId = document.getElementById('userAccessUserId');
const userAccessUsername = document.getElementById('userAccessUsername');
const userAccessGroups = document.getElementById('userAccessGroups');
const userAccessPerms = document.getElementById('userAccessPerms');
const userAccessEffective = document.getElementById('userAccessEffective');
const userAccessStatus = document.getElementById('userAccessStatus');
const userAccessSave = document.getElementById('userAccessSave');

async function ensurePermissionsLoaded() {
  if (cachedPerms && cachedPerms.length) return;
  try {
    const perms = await apiGet('/api/v1/admin/permissions');
    cachedPerms = Array.isArray(perms) ? perms : [];
  } catch {
    cachedPerms = [];
  }
}

async function ensureGroupsLoaded() {
  if (cachedGroups && cachedGroups.length) return;
  try {
    const groups = await apiGet('/api/v1/admin/groups');
    cachedGroups = Array.isArray(groups) ? groups : [];
  } catch {
    cachedGroups = [];
  }
}

function showUserAccessStatus(msg, kind = 'info') {
  if (!userAccessStatus) return;
  userAccessStatus.className = `alert alert-${kind} mt-3`;
  userAccessStatus.textContent = msg;
  userAccessStatus.style.display = msg ? 'block' : 'none';
}

async function openUserAccessModal(userId, username) {
  if (!userAccessModalEl || !window.bootstrap?.Modal) return;
  if (userAccessStatus) userAccessStatus.style.display = 'none';
  if (userAccessUserId) userAccessUserId.value = String(userId || '');
  if (userAccessUsername) userAccessUsername.textContent = `Editing access for: ${username}`;

  await ensureGroupsLoaded();
  await ensurePermissionsLoaded();

  // Populate option lists
  setOptions(
      userAccessGroups,
      (cachedGroups || []).slice().sort((a, b) => String(a.name).localeCompare(String(b.name))),
      (g) => g.id,
      (g) => `${g.name}${groupRoleSuffix(g)}`,
      []
  );
  setOptions(
      userAccessPerms,
      (cachedPerms || []).slice().sort((a, b) => String(a.code).localeCompare(String(b.code))),
      (p) => p.code,
      (p) => p.description ? `${p.code} — ${p.description}` : p.code,
      []
  );

  try {
    showUserAccessStatus('Loading…', 'info');
    const access = await apiGet(`/api/v1/users/${encodeURIComponent(String(userId))}/access`);
    showUserAccessStatus('', 'info');

    setOptions(
        userAccessGroups,
        (cachedGroups || []).slice().sort((a, b) => String(a.name).localeCompare(String(b.name))),
        (g) => g.id,
        (g) => `${g.name}${groupRoleSuffix(g)}`,
        access.group_ids || []
    );
    setOptions(
        userAccessPerms,
        (cachedPerms || []).slice().sort((a, b) => String(a.code).localeCompare(String(b.code))),
        (p) => p.code,
        (p) => p.description ? `${p.code} — ${p.description}` : p.code,
        access.explicit_permission_codes || []
    );

    if (userAccessEffective) {
      const eff = access.effective_permission_codes || [];
      userAccessEffective.textContent = Array.isArray(eff) ? eff.join(', ') : String(eff || '');
    }
  } catch (e) {
    showUserAccessStatus(`Failed to load: ${String(e)}`, 'danger');
    if (userAccessEffective) userAccessEffective.textContent = '';
  }

  window.bootstrap.Modal.getOrCreateInstance(userAccessModalEl).show();
}

if (userAccessSave) {
  userAccessSave.addEventListener('click', async () => {
    const userId = userAccessUserId?.value;
    if (!userId) return;

    try {
      showUserAccessStatus('Saving…', 'info');
      const group_ids = selValues(userAccessGroups);
      const explicit_permission_codes = selValues(userAccessPerms);
      const res = await apiPut(`/api/v1/users/${encodeURIComponent(String(userId))}/access`, {
        group_ids,
        explicit_permission_codes,
      });

      if (userAccessEffective) {
        const eff = res.effective_permission_codes || [];
        userAccessEffective.textContent = Array.isArray(eff) ? eff.join(', ') : String(eff || '');
      }

      showUserAccessStatus('Saved', 'success');
    } catch (e) {
      showUserAccessStatus(`Failed: ${String(e)}`, 'danger');
    }
  });
}


// ------------------------------------------------------------------
// Groups & permissions tab
// ------------------------------------------------------------------

const groupsRows = document.getElementById('groupsRows');
const groupsRefresh = document.getElementById('groupsRefresh');
const groupsStatus = document.getElementById('groupsStatus');

const groupCreateForm = document.getElementById('groupCreateForm');
const newGroupName = document.getElementById('newGroupName');
const newGroupDesc = document.getElementById('newGroupDesc');
const newGroupRole = document.getElementById('newGroupRole');

const groupDetailEmpty = document.getElementById('groupDetailEmpty');
const groupDetail = document.getElementById('groupDetail');
const groupDetailMeta = document.getElementById('groupDetailMeta');
const groupName = document.getElementById('groupName');
const groupDesc = document.getElementById('groupDesc');
const groupRole = document.getElementById('groupRole');
const groupSave = document.getElementById('groupSave');
const groupDelete = document.getElementById('groupDelete');
const groupMembers = document.getElementById('groupMembers');
const groupPermissions = document.getElementById('groupPermissions');
const groupApplyMembers = document.getElementById('groupApplyMembers');
const groupApplyPermissions = document.getElementById('groupApplyPermissions');

const permissionCreateForm = document.getElementById('permissionCreateForm');
const newPermCode = document.getElementById('newPermCode');
const newPermDesc = document.getElementById('newPermDesc');

let selectedGroupId = null;

function showGroupsStatus(msg, kind = 'info') {
  if (!groupsStatus) return;
  groupsStatus.className = `alert alert-${kind}`;
  groupsStatus.textContent = msg;
  groupsStatus.style.display = msg ? 'block' : 'none';
}

function renderGroups(groups) {
  if (!groupsRows) return;
  const items = Array.isArray(groups) ? groups : [];
  const rows = items.map((g) => {
    const isSel = selectedGroupId && String(g.id) === String(selectedGroupId);
    const roleText = g.role ? roleLabel(g.role) : '—';
    return `
      <tr data-group-id="${esc(g.id)}" style="cursor:pointer;" class="${isSel ? 'table-active' : ''}">
        <td class="mono">${esc(g.name || '')}</td>
        <td class="mono">${esc(roleText)}</td>
        <td class="mono">${esc(String(g.member_count ?? 0))}</td>
        <td class="mono">${esc(String(g.permission_count ?? 0))}</td>
      </tr>
    `;
  }).join('');

  groupsRows.innerHTML = rows || `<tr><td colspan="4" class="small-muted">No groups.</td></tr>`;
  groupsRows.querySelectorAll('tr[data-group-id]').forEach((tr) => {
    tr.addEventListener('click', () => {
      const gid = tr.getAttribute('data-group-id');
      if (!gid) return;
      openGroupDetail(gid);
    });
  });
}

async function refreshGroups(showLoading = true) {
  if (!groupsRows) return;
  if (showLoading) {
    groupsRows.innerHTML = `<tr><td colspan="4" class="small-muted">Loading…</td></tr>`;
  }
  try {
    const groups = await apiGet('/api/v1/admin/groups');
    cachedGroups = Array.isArray(groups) ? groups : [];
    renderGroups(cachedGroups);
  } catch (e) {
    cachedGroups = [];
    renderGroups([]);
    showGroupsStatus(`Failed to load groups: ${String(e)}`, 'danger');
  }
}

async function refreshPermissions() {
  try {
    const perms = await apiGet('/api/v1/admin/permissions');
    cachedPerms = Array.isArray(perms) ? perms : [];
  } catch {
    cachedPerms = [];
  }
}

async function openGroupDetail(gid) {
  selectedGroupId = String(gid);
  showGroupsStatus('', 'info');

  if (groupDetailEmpty) groupDetailEmpty.style.display = 'none';
  if (groupDetail) groupDetail.style.display = 'block';

  try {
    await refreshPermissions();
    const detail = await apiGet(`/api/v1/admin/groups/${encodeURIComponent(String(gid))}`);

    if (groupName) groupName.value = detail.name || '';
    if (groupDesc) groupDesc.value = detail.description || '';
    if (groupRole) groupRole.value = (detail.role || '').trim();
    if (groupDetailMeta) groupDetailMeta.textContent = detail.created_at ? `Created ${fmtTs(detail.created_at)}` : '';

    // Options
    const usersSorted = (cachedUsers || []).slice().sort((a, b) => String(a.username).localeCompare(String(b.username)));
    setOptions(groupMembers, usersSorted, (u) => u.id, (u) => u.username, detail.user_ids || []);

    const permsSorted = (cachedPerms || []).slice().sort((a, b) => String(a.code).localeCompare(String(b.code)));
    setOptions(groupPermissions, permsSorted, (p) => p.code, (p) => p.description ? `${p.code} — ${p.description}` : p.code, detail.permission_codes || []);
    enhanceWrappedMultiSelect(groupPermissions);

    // Highlight selection in list
    renderGroups(cachedGroups);
  } catch (e) {
    showGroupsStatus(`Failed to load group: ${String(e)}`, 'danger');
  }
}

if (groupsRefresh) groupsRefresh.addEventListener('click', () => refreshGroups(true));

if (groupCreateForm) {
  groupCreateForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    showGroupsStatus('', 'info');
    try {
      const name = (newGroupName?.value || '').trim();
      const description = (newGroupDesc?.value || '').trim() || null;
      const roleRaw = (newGroupRole?.value || '').trim();
      const role = roleRaw ? roleRaw : null;
      if (!name) throw new Error('Group name is required');
      const g = await apiPost('/api/v1/admin/groups', {name, description, role});
      toast(groupsStatus, `Created group ${name}`, 'success');
      if (newGroupName) newGroupName.value = '';
      if (newGroupDesc) newGroupDesc.value = '';
      if (newGroupRole) newGroupRole.value = '';
      await refreshGroups(false);
      if (g?.id) await openGroupDetail(g.id);
    } catch (e) {
      toast(groupsStatus, `Failed: ${String(e)}`, 'danger');
    }
  });
}

if (groupSave) {
  groupSave.addEventListener('click', async () => {
    if (!selectedGroupId) return;
    showGroupsStatus('', 'info');
    try {
      const payload = {
        name: (groupName?.value || '').trim(),
        description: (groupDesc?.value || '').trim(),
        role: (groupRole?.value || '').trim(),
      };
      const res = await apiPatch(`/api/v1/admin/groups/${encodeURIComponent(String(selectedGroupId))}`, payload);
      toast(groupsStatus, 'Group updated', 'success');
      await refreshGroups(false);
      await openGroupDetail(res.id || selectedGroupId);
    } catch (e) {
      toast(groupsStatus, `Failed: ${String(e)}`, 'danger');
    }
  });
}

if (groupDelete) {
  groupDelete.addEventListener('click', async () => {
    if (!selectedGroupId) return;
    if (!confirm('Delete this group?')) return;
    showGroupsStatus('', 'info');
    try {
      await apiDelete(`/api/v1/admin/groups/${encodeURIComponent(String(selectedGroupId))}`);
      toast(groupsStatus, 'Group deleted', 'success');
      selectedGroupId = null;
      if (groupDetail) groupDetail.style.display = 'none';
      if (groupDetailEmpty) groupDetailEmpty.style.display = 'block';
      await refreshGroups(false);
    } catch (e) {
      toast(groupsStatus, `Failed: ${String(e)}`, 'danger');
    }
  });
}

if (groupApplyMembers) {
  groupApplyMembers.addEventListener('click', async () => {
    if (!selectedGroupId) return;
    try {
      showGroupsStatus('Saving members…', 'info');
      const user_ids = selValues(groupMembers);
      await apiPut(`/api/v1/admin/groups/${encodeURIComponent(String(selectedGroupId))}/members`, {user_ids});
      showGroupsStatus('Members saved', 'success');
      await refreshGroups(false);
      await openGroupDetail(selectedGroupId);
    } catch (e) {
      showGroupsStatus(`Failed: ${String(e)}`, 'danger');
    }
  });
}

if (groupApplyPermissions) {
  groupApplyPermissions.addEventListener('click', async () => {
    if (!selectedGroupId) return;
    try {
      showGroupsStatus('Saving permissions…', 'info');
      const permission_codes = selValues(groupPermissions);
      await apiPut(`/api/v1/admin/groups/${encodeURIComponent(String(selectedGroupId))}/permissions`, {permission_codes});
      showGroupsStatus('Permissions saved', 'success');
      await refreshGroups(false);
      await openGroupDetail(selectedGroupId);
    } catch (e) {
      showGroupsStatus(`Failed: ${String(e)}`, 'danger');
    }
  });
}

if (permissionCreateForm) {
  permissionCreateForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    try {
      const code = (newPermCode?.value || '').trim();
      const description = (newPermDesc?.value || '').trim() || null;
      if (!code) throw new Error('Permission code is required');
      await apiPost('/api/v1/admin/permissions', {code, description});
      toast(groupsStatus, `Created permission ${code}`, 'success');
      if (newPermCode) newPermCode.value = '';
      if (newPermDesc) newPermDesc.value = '';
      await refreshPermissions();
      if (selectedGroupId) await openGroupDetail(selectedGroupId);
    } catch (e) {
      toast(groupsStatus, `Failed: ${String(e)}`, 'danger');
    }
  });
}


// ------------------------------------------------------------------
// Controls import / ingest runners / remap
// ------------------------------------------------------------------

document.getElementById('file')?.addEventListener('change', async (ev) => {
  const f = ev.target.files?.[0];
  if (!f) return;
  const txt = await f.text();
  document.getElementById('payload').value = txt;
});

document.getElementById('loadSample')?.addEventListener('click', () => {
  document.getElementById('payload').value = JSON.stringify({
    framework: selectedFrameworkFallback(),
    items: [
      {type: 'annex_control', ref: 'A.5.7', title: 'Threat intelligence', in_scope: true, tags: {}, metadata: {}},
      {type: 'annex_control', ref: 'A.8.12', title: 'Data leakage prevention', in_scope: true, tags: {}, metadata: {}},
    ],
  }, null, 2);
});

document.getElementById('importBtn')?.addEventListener('click', async () => {
  try {
    const payload = document.getElementById('payload').value.trim();
    if (!payload) throw new Error('Missing JSON payload');
    const obj = JSON.parse(payload);
    obj.framework = obj.framework || selectedFrameworkFallback();
    await runJsonPost('/api/v1/admin/controls/import', obj);
    await loadControls();
  } catch (e) {
    setOut(String(e));
    toast(status, String(e), 'danger');
  }
});

document.querySelectorAll('[data-run]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    await runJsonPost(btn.getAttribute('data-run'), {});
  });
});

document.getElementById('resetLokiCursors')?.addEventListener('click', async () => {
  const ok = window.confirm(
    'Reset all Loki ingestion cursors to now? Existing events are not deleted, but old Loki backlog will be skipped.'
  );
  if (!ok) return;
  await runJsonPost('/api/v1/admin/ingest/loki/reset', {});
});

document.getElementById('remapBtn')?.addEventListener('click', async () => {
  const limit = parseInt(document.getElementById('remapLimit')?.value || '500', 10);
  const includeMapped = !!document.getElementById('remapIncludeMapped')?.checked;
  const fromDate = (document.getElementById('remapFrom')?.value || '').trim();
  const toDate = (document.getElementById('remapTo')?.value || '').trim();
  const source = (document.getElementById('remapSource')?.value || '').trim();
  const system = (document.getElementById('remapSystem')?.value || '').trim();
  const action = (document.getElementById('remapAction')?.value || '').trim();
  const outcome = (document.getElementById('remapOutcome')?.value || '').trim();

  const params = new URLSearchParams();
  if (!Number.isNaN(limit)) params.set('limit', String(limit));
  if (includeMapped) params.set('include_mapped', 'true');
  if (fromDate) params.set('from_date', fromDate);
  if (toDate) params.set('to_date', toDate);
  if (source) params.set('source', source);
  if (system) params.set('system', system);
  if (action) params.set('action', action);
  if (outcome) params.set('outcome', outcome);

  const qs = params.toString();
  await runJsonPost(`/api/v1/admin/remap/run${qs ? `?${qs}` : ''}`, {});
});

// ------------------------------------------------------------------
// Diary evidence creator (admin-only)
// ------------------------------------------------------------------

let allControls = [];
let selectedRefs = [];

// Diary link rows (optional)
const diaryLinksWrap = document.getElementById('diaryLinks');

function diaryAddLinkRow(url = '', text = '') {
  if (!diaryLinksWrap) return;
  const row = document.createElement('div');
  row.className = 'row g-2 align-items-end';
  row.setAttribute('data-link-row', '1');
  row.innerHTML = `
    <div class="col-12 col-lg-6">
      <label class="form-label small-muted mb-1">URL</label>
      <input class="form-control" data-link-url value="${esc(url)}" placeholder="https://…" />
    </div>
    <div class="col-10 col-lg-5">
      <label class="form-label small-muted mb-1">Link text</label>
      <input class="form-control" data-link-text value="${esc(text)}" placeholder="e.g. Change request #123" />
    </div>
    <div class="col-2 col-lg-1 d-grid">
      <button type="button" class="btn btn-outline-danger" data-link-remove aria-label="Remove">×</button>
    </div>
  `;
  diaryLinksWrap.appendChild(row);

  row.querySelector('[data-link-remove]')?.addEventListener('click', () => {
    row.remove();
  });
}

function diaryReadLinks() {
  if (!diaryLinksWrap) return [];
  const links = [];
  diaryLinksWrap.querySelectorAll('[data-link-row]').forEach((row) => {
    const url = (row.querySelector('[data-link-url]')?.value || '').trim();
    const text = (row.querySelector('[data-link-text]')?.value || '').trim();
    if (url) links.push({url, text});
  });
  return links;
}

document.getElementById('diaryAddLink')?.addEventListener('click', () => diaryAddLinkRow());
// Start with one empty row for convenience
if (diaryLinksWrap && diaryLinksWrap.children.length === 0) diaryAddLinkRow();


function renderSelected() {
  const wrap = document.getElementById('controlSelected');
  if (!wrap) return;

  wrap.innerHTML = selectedRefs.map((ref) => {
    return `
      <span class="badge badge-soft">
        ${esc(ref)}
        <button type="button" class="btn btn-sm btn-link link-danger p-0 ms-1" data-remove="${esc(ref)}" aria-label="Remove">×</button>
      </span>
    `;
  }).join('');

  wrap.querySelectorAll('[data-remove]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ref = btn.getAttribute('data-remove');
      selectedRefs = selectedRefs.filter((r) => r !== ref);
      renderSelected();
    });
  });
}

function renderResults(items) {
  const resEl = document.getElementById('controlResults');
  if (!resEl) return;

  if (!items.length) {
    resEl.innerHTML = '';
    return;
  }

  resEl.innerHTML = items.map((it) => {
    const title = it.title ? ` <span class="small-muted">${esc(it.title)}</span>` : '';
    const disabled = selectedRefs.includes(it.ref) ? 'disabled' : '';
    return `<button type="button" class="list-group-item list-group-item-action ${disabled}" data-ref="${esc(it.ref)}">` +
      `<span class="badge badge-chip me-2">${esc(it.ref)}</span>${title}` +
      `</button>`;
  }).join('');

  resEl.querySelectorAll('[data-ref]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ref = btn.getAttribute('data-ref');
      if (!ref || selectedRefs.includes(ref)) return;
      selectedRefs.push(ref);
      selectedRefs.sort();
      renderSelected();
      document.getElementById('controlSearch').value = '';
      renderResults([]);
    });
  });
}

async function loadControls() {
  try {
    const res = await apiGet(`/api/v1/controls?framework=${encodeURIComponent(selectedFrameworkFallback())}&limit=5000`);
    allControls = (res.items || []).filter((it) => it.ref);
  } catch {
    allControls = [];
  }
}

document.getElementById('controlSearch')?.addEventListener('input', (ev) => {
  const q = (ev.target.value || '').trim().toLowerCase();
  if (!q || allControls.length === 0) {
    renderResults([]);
    return;
  }
  const hits = [];
  for (const c of allControls) {
    const hay = `${c.ref} ${c.title || ''}`.toLowerCase();
    if (hay.includes(q)) {
      hits.push(c);
      if (hits.length >= 25) break;
    }
  }
  renderResults(hits);
});

document.getElementById('diaryForm')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  status.style.display = 'none';
  setOut('Submitting diary evidence…');

  try {
    const summary = document.getElementById('diarySummary').value.trim();
    if (!summary) throw new Error('Missing summary');

    const fd = new FormData();
    fd.append('summary', summary);

    const details = document.getElementById('diaryDetails').value.trim();
    if (details) fd.append('details', details);

    const links = diaryReadLinks();
    if (links && links.length) fd.append('links', JSON.stringify(links));

    const tsVal = document.getElementById('diaryTs').value;
    if (tsVal) {
      // datetime-local is local time; convert to ISO (UTC) for the API.
      const dt = new Date(tsVal);
      if (!Number.isNaN(dt.getTime())) fd.append('timestamp', dt.toISOString());
    }

    for (const ref of selectedRefs) fd.append('controls', ref);


    const files = document.getElementById('diaryImages').files;
    if (files && files.length) {
      Array.from(files).slice(0, 10).forEach((f) => fd.append('attachments', f, f.name));
    }

    const res = await apiPostForm('/api/v1/admin/diary', fd);
    setOut(JSON.stringify(res, null, 2));
    toast(status, 'Diary evidence created', 'success');

    if (res && res.event_id) {
      const openUrl = withFramework(`/event.html?id=${encodeURIComponent(res.event_id)}`, selectedFrameworkFallback());
      setOut(`${out.textContent}\n\nOpen: ${location.origin}${openUrl}`);
    }

    // Reset inputs (keep controls selection)
    document.getElementById('diarySummary').value = '';
    document.getElementById('diaryDetails').value = '';
    document.getElementById('diaryTs').value = '';
    document.getElementById('diaryImages').value = '';

    if (diaryLinksWrap) {
      diaryLinksWrap.innerHTML = ''; diaryAddLinkRow();
    }
  } catch (e) {
    setOut(String(e));
    toast(status, `Failed: ${String(e)}`, 'danger');
  }
});

// ------------------------------------------------------------------
// Entity changelog viewer (semantic before/after diffs)
// ------------------------------------------------------------------

const entityChangeLimit = 25;
let entityChangeOffset = 0;

const entityChangeRows = document.getElementById('entityChangeRows');
const entityChangeMeta = document.getElementById('entityChangeMeta');
const entityChangeType = document.getElementById('entityChangeType');
const entityChangeUser = document.getElementById('entityChangeUser');
const entityChangeQuery = document.getElementById('entityChangeQuery');
const entityChangePrev = document.getElementById('entityChangePrev');
const entityChangeNext = document.getElementById('entityChangeNext');

let entityChangeDebouncedApply = null;

function renderEntityChanges(items, total) {
  if (entityChangeRows) {
    entityChangeRows.innerHTML = entityChangelogHtml(items || [], {
      empty: 'No Control, Clause or Risk changes match these filters.',
    });
  }
  if (entityChangeMeta) {
    const from = total ? entityChangeOffset + 1 : 0;
    const to = Math.min(entityChangeOffset + entityChangeLimit, total || 0);
    entityChangeMeta.textContent = `Showing ${from}–${to} of ${total || 0}`;
  }
  if (entityChangePrev) entityChangePrev.disabled = entityChangeOffset <= 0;
  if (entityChangeNext) entityChangeNext.disabled = (entityChangeOffset + entityChangeLimit) >= (total || 0);
}

async function refreshEntityChanges() {
  if (entityChangeRows) entityChangeRows.innerHTML = '<div class="p-3 small-muted">Loading changelog…</div>';
  const url = new URL('/api/v1/admin/entity-changelog', location.origin);
  url.searchParams.set('limit', String(entityChangeLimit));
  url.searchParams.set('offset', String(entityChangeOffset));
  const t = (entityChangeType?.value || '').trim();
  const u = (entityChangeUser?.value || '').trim();
  const q = (entityChangeQuery?.value || '').trim();
  if (t) url.searchParams.set('entity_type', t);
  if (u) url.searchParams.set('username', u);
  if (q) url.searchParams.set('q', q);

  try {
    const res = await apiGet(url.pathname + url.search);
    renderEntityChanges(res.items || [], res.total || 0);
  } catch (e) {
    renderEntityChanges([], 0);
    toast(status, `Failed to load entity changelog: ${String(e)}`, 'danger');
  }
}

document.getElementById('entityChangeRefresh')?.addEventListener('click', () => {
  entityChangeDebouncedApply?.cancel?.();
  entityChangeOffset = 0;
  refreshEntityChanges();
});

entityChangePrev?.addEventListener('click', () => {
  entityChangeDebouncedApply?.cancel?.();
  entityChangeOffset = Math.max(0, entityChangeOffset - entityChangeLimit);
  refreshEntityChanges();
});

entityChangeNext?.addEventListener('click', () => {
  entityChangeDebouncedApply?.cancel?.();
  entityChangeOffset = entityChangeOffset + entityChangeLimit;
  refreshEntityChanges();
});

function wireEntityChangeAutoApply() {
  const apply = () => { entityChangeOffset = 0; refreshEntityChanges(); };
  const applyOnEnter = (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      entityChangeDebouncedApply?.cancel?.();
      apply();
    }
  };
  if (me?.pref_auto_apply_filters) {
    entityChangeDebouncedApply = debounce(apply, 500);
    entityChangeUser?.addEventListener('input', entityChangeDebouncedApply);
    entityChangeQuery?.addEventListener('input', entityChangeDebouncedApply);
  }
  entityChangeType?.addEventListener('change', apply);
  entityChangeUser?.addEventListener('change', apply);
  entityChangeQuery?.addEventListener('change', apply);
  entityChangeUser?.addEventListener('keydown', applyOnEnter);
  entityChangeQuery?.addEventListener('keydown', applyOnEnter);
}

wireEntityChangeAutoApply();

// ------------------------------------------------------------------
// Audit trail viewer (paginated)
// ------------------------------------------------------------------

const auditLimit = 50;
let auditOffset = 0;

const auditRows = document.getElementById('auditRows');
const auditMeta = document.getElementById('auditMeta');
const auditUser = document.getElementById('auditUser');
const auditPath = document.getElementById('auditPath');
const auditMethod = document.getElementById('auditMethod');
const auditPrev = document.getElementById('auditPrev');
const auditNext = document.getElementById('auditNext');

let auditDebouncedApply = null;

function renderAudit(items, total) {
  if (!auditRows) return;

  auditRows.innerHTML = (items || []).map((a) => {
    const qs = a.query_string ? `?${esc(a.query_string)}` : '';
    return `
      <tr>
        <td class="small-muted">${esc(fmtTs(a.ts))}</td>
        <td>${userPillHtml(a.username, {empty: '-'})}</td>
        <td class="mono">${esc(a.method)}</td>
        <td class="mono">${esc(a.path)}${qs}</td>
        <td class="mono">${esc(String(a.status_code))}</td>
        <td class="mono">${esc(String(a.duration_ms))}</td>
        <td class="mono">${esc(a.client_ip || '-')}</td>
        <td class="small-muted">${esc(a.user_agent || '-')}</td>
      </tr>
    `;
  }).join('');

  if (auditMeta) {
    const from = total ? auditOffset + 1 : 0;
    const to = Math.min(auditOffset + auditLimit, total || 0);
    auditMeta.textContent = `Showing ${from}–${to} of ${total || 0}`;
  }

  if (auditPrev) auditPrev.disabled = auditOffset <= 0;
  if (auditNext) auditNext.disabled = (auditOffset + auditLimit) >= (total || 0);
}

async function refreshAudit() {
  if (auditRows) auditRows.innerHTML = `<tr><td colspan="8" class="small-muted">Loading…</td></tr>`;

  const url = new URL('/api/v1/admin/audit', location.origin);
  url.searchParams.set('limit', String(auditLimit));
  url.searchParams.set('offset', String(auditOffset));

  const u = (auditUser?.value || '').trim();
  const p = (auditPath?.value || '').trim();
  const m = (auditMethod?.value || '').trim();

  if (u) url.searchParams.set('username', u);
  if (p) url.searchParams.set('path', p);
  if (m) url.searchParams.set('method', m);

  try {
    const res = await apiGet(url.pathname + url.search);
    renderAudit(res.items || [], res.total || 0);
  } catch (e) {
    renderAudit([], 0);
    setOut(String(e));
    toast(status, `Failed to load audit: ${String(e)}`, 'danger');
  }
}

document.getElementById('auditRefresh')?.addEventListener('click', () => {
  auditDebouncedApply?.cancel?.();
  auditOffset = 0;
  refreshAudit();
});

auditPrev?.addEventListener('click', () => {
  auditDebouncedApply?.cancel?.();
  auditOffset = Math.max(0, auditOffset - auditLimit);
  refreshAudit();
});

auditNext?.addEventListener('click', () => {
  auditDebouncedApply?.cancel?.();
  auditOffset = auditOffset + auditLimit;
  refreshAudit();
});

// Optional UX: auto-apply audit filters as fields change (user preference).
if (autoApplyFilters) {
  const applyAuditNow = () => {
    auditOffset = 0;
    refreshAudit();
  };

  // Keep this fairly short; there is no datalist here, and the results table
  // is small enough that instant feedback feels good.
  auditDebouncedApply = debounce(applyAuditNow, 500);

  const applyOnEnter = (ev) => {
    if (ev?.key === 'Enter') {
      ev.preventDefault();
      auditDebouncedApply?.cancel?.();
      applyAuditNow();
    }
  };

  auditUser?.addEventListener('input', auditDebouncedApply);
  auditPath?.addEventListener('input', auditDebouncedApply);
  auditUser?.addEventListener('change', () => {
 auditDebouncedApply?.cancel?.(); applyAuditNow();
  });
  auditPath?.addEventListener('change', () => {
 auditDebouncedApply?.cancel?.(); applyAuditNow();
  });
  auditMethod?.addEventListener('change', () => {
 auditDebouncedApply?.cancel?.(); applyAuditNow();
  });
  auditUser?.addEventListener('keydown', applyOnEnter);
  auditPath?.addEventListener('keydown', applyOnEnter);
}


// ------------------------------------------------------------------
// Questions queue (admin-only)
// ------------------------------------------------------------------

function questionStatusBadge(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'answered') return `<span class="badge text-bg-success">answered</span>`;
  if (s === 'reviewing') return `<span class="badge text-bg-info">reviewing</span>`;
  return `<span class="badge text-bg-warning">unanswered</span>`;
}

function renderQuestionsQueue(items) {
  if (!questionsRows) return;
  const rows = (items || []).map((it) => {
    // Render the status badge (typo here previously caused: "ReferenceError: question is not defined")
    const st = questionStatusBadge(it.status);
    const upd = it.updated_at ? fmtTs(it.updated_at) : '–';
    const askedBy = userPillHtml(it.created_by_username, {empty: '–'});
    const evSum = esc(it.target_label || it.event_summary || '(no summary)');
    const evMeta = esc([it.target_type && it.target_type !== 'event' ? String(it.target_type).replaceAll('_', ' ') : (it.event_source || ''), it.event_timestamp ? fmtTs(it.event_timestamp) : (it.target_ref || '—')].filter(Boolean).join(' • '));
    const rawHref = withFramework(it?.target_url || `/event.html?id=${encodeURIComponent(it?.event_id || '')}&thread=${encodeURIComponent(it?.thread_id || '')}#questions`, currentFramework);
    const href = safeExternalHref(rawHref) || '#';
    return `
      <tr>
        <td>${st}</td>
        <td class="small-muted">${esc(upd)}</td>
        <td>
          <div class="fw-bold">${evSum}</div>
          <div class="small-muted">${evMeta}</div>
          <div class="small-muted mono">Thread: ${esc(it.thread_id)}</div>
        </td>
        <td>${askedBy}</td>
        <td class="text-end">
          <div class="btn-group btn-group-sm" role="group" aria-label="Question actions">
            <a class="btn btn-outline-primary" href="${esc(href)}">Open</a>
            <button class="btn btn-outline-danger" type="button" data-delete-question="${esc(it.thread_id || '')}">Delete</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
  questionsRows.innerHTML = rows || `<tr><td colspan="5" class="small-muted">No matching questions.</td></tr>`;
}

async function refreshQuestionsQueue() {
  if (!isAdmin) return;
  if (!questionsRows) return;

  if (questionsStatus) {
    questionsStatus.style.display = '';
    questionsStatus.textContent = 'Loading questions…';
  }

  const st = (questionsStatusFilter?.value || '').trim();
  const url = new URL('/api/v1/admin/questions', location.origin);
  if (st) url.searchParams.set('status', st);

  try {
    const res = await apiGet(url.pathname + url.search);
    renderQuestionsQueue(res.items || []);
    if (questionsStatus) {
      questionsStatus.style.display = 'none';
    }
  } catch (e) {
    renderQuestionsQueue([]);
    if (questionsStatus) {
      questionsStatus.style.display = '';
      questionsStatus.textContent = `Failed: ${String(e)}`;
    }
  }
}

questionsRefresh?.addEventListener('click', refreshQuestionsQueue);
questionsStatusFilter?.addEventListener('change', refreshQuestionsQueue);
questionsRows?.addEventListener('click', async (ev) => {
  const btn = ev.target?.closest?.('[data-delete-question]');
  if (!btn || !isAdmin) return;
  const tid = btn.getAttribute('data-delete-question') || '';
  if (!tid) return;
  if (!confirm('Delete this question thread and all replies?')) return;
  btn.disabled = true;
  try {
    await apiDelete(`/api/v1/questions/${encodeURIComponent(tid)}`);
    toast(questionsStatus || status, 'Question deleted', 'success');
    await refreshQuestionsQueue();
  } catch (e) {
    toast(questionsStatus || status, String(e), 'danger');
    btn.disabled = false;
  }
});


// ------------------------------------------------------------------
// Init
// ------------------------------------------------------------------

if (!isAdmin && !canAudit) {
  // The navbar usually hides the link, but don't assume.
  setOut('You do not have access to this page.');
} else if (!isAdmin && canAudit) {
  // Audit-only mode for e.g. an "auditors" group.
  restrictToAuditOnly();
  await refreshEntityChanges();
  await refreshAudit();
} else {
  // Full admin
  await loadControls();
  renderSelected();
  await refreshUsers(true);
  await refreshGroups(true);
  await refreshPermissions();
  await refreshEntityChanges();
  await refreshAudit();
}
