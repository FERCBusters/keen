import {
  initNavbar,
  apiGet,
  esc,
  fmtTs,
  getCurrentFramework,
  toast,
  withFramework,
  userPillHtml,
  canSampleIntoAudit,
  openAuditSampleModal,
  loadEntityChangelog,
} from '/app.js';
import {activateTabFromHashOrQuery, badgeList} from '/pages/isms-detail-common.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search || '');
const accessId = params.get('id') || params.get('access') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);
const canManageIsms = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me);
let access = null;

function accessHref(id = accessId) {
  return withFramework(`/isms-access.html?id=${encodeURIComponent(id || '')}`, framework);
}

function assetHref(id) {
  return withFramework(`/isms-asset.html?id=${encodeURIComponent(id || '')}`, framework);
}

function serviceHtml(row) {
  const service = row?.service || null;
  const label = service?.asset || service?.name || service?.display || '—';
  if (!service?.id) return esc(label);
  return `<a href="${esc(assetHref(service.id))}">${esc(label)}</a>`;
}

function accountLabel(acct) {
  return [acct?.name, acct?.account_id].filter(Boolean).join(' · ') || 'hosting account';
}

function accountBadges(items) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">—</span>';
  return arr.map((acct) => `<span class="badge badge-soft me-1 mb-1" title="${esc(accountLabel(acct))}">${esc(accountLabel(acct))}</span>`).join('');
}

function renderAccess(row) {
  access = row || {};
  const title = access.task_action || 'Access control row';
  document.title = `Keen – ${title}`;
  $('accessTitle').textContent = title;
  $('accessMeta').textContent = [access.service?.asset || access.service?.name, access.status || 'Pending Approval', `updated ${fmtTs(access.updated_at) || '—'}`].filter(Boolean).join(' • ');
  $('accessTask').textContent = access.task_action || '—';
  $('accessNotes').textContent = access.notes || 'No notes recorded.';
  $('accessService').innerHTML = serviceHtml(access);
  $('accessAccounts').innerHTML = accountBadges(access.aws_accounts);
  $('accessStatusValue').textContent = access.status || 'Pending Approval';
  $('accessApprovedBy').innerHTML = userPillHtml(access.approved_by);
  $('accessRoles').innerHTML = badgeList(access.roles, 'name');
  $('accessUpdated').textContent = fmtTs(access.updated_at) || '—';

  const edit = $('editAccessLink');
  if (edit) {
    edit.href = withFramework(`/isms.html?tab=access&edit=access:${encodeURIComponent(access.id || '')}`, framework);
    edit.style.display = canManageIsms ? '' : 'none';
  }
  const sample = $('sampleAccessAudit');
  if (sample) sample.style.display = canSampleAudits ? '' : 'none';
}

async function loadChangelog() {
  await loadEntityChangelog(
    $('accessChangelog'),
    `/api/v1/isms/access_control_matrix/${encodeURIComponent(accessId)}/changelog?limit=100`,
    {empty: 'No access control matrix changes have been recorded yet.'}
  );
}

async function load() {
  if (!accessId) {
    toast(status, 'Missing access control row id.', 'danger');
    return;
  }
  try {
    const row = await apiGet(`/api/v1/isms/access-control-matrix/${encodeURIComponent(accessId)}?framework=${encodeURIComponent(framework)}`);
    renderAccess(row);
    await loadChangelog();
    activateTabFromHashOrQuery(params, {changelog: $('accessChangelogTab')});
  } catch (e) {
    toast(status, `Failed to load access control row: ${String(e)}`, 'danger');
  }
}

$('sampleAccessAudit')?.addEventListener('click', () => {
  if (!access) return;
  openAuditSampleModal({
    me,
    entityType: 'isms_access_control_matrix',
    entityId: access.id,
    title: access.task_action || 'Access control matrix row',
    evidenceUrl: accessHref(access.id),
    framework,
    statusEl: status,
  });
});

window.addEventListener('hashchange', () => activateTabFromHashOrQuery(params, {changelog: $('accessChangelogTab')}));
load();
