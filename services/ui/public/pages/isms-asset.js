import {
  initNavbar,
  apiGet,
  esc,
  fmtTs,
  getCurrentFramework,
  toast,
  withFramework,
  canSampleIntoAudit,
  openAuditSampleModal,
  loadEntityChangelog,
} from '/app.js';
import {activateTabFromHashOrQuery, clauseBadges, controlBadges} from '/pages/isms-detail-common.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search || '');
const assetId = params.get('id') || params.get('asset') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);
const canManageIsms = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me);
let asset = null;

function assetHref(id = assetId) {
  return withFramework(`/isms-asset.html?id=${encodeURIComponent(id || '')}`, framework);
}

function renderAsset(row) {
  asset = row || {};
  const title = asset.asset || asset.name || 'Asset';
  document.title = `Keen – ${title}`;
  $('assetTitle').textContent = title;
  $('assetMeta').textContent = [asset.category?.name || asset.category_name, asset.subcategory?.name || asset.subcategory_name, asset.license_name || asset.license, `updated ${fmtTs(asset.updated_at) || '—'}`].filter(Boolean).join(' • ');
  $('assetDescription').textContent = asset.description || 'No description recorded.';
  $('assetCategory').textContent = asset.category?.name || asset.category_name || '—';
  $('assetSubcategory').textContent = asset.subcategory?.name || asset.subcategory_name || '—';
  $('assetLicense').textContent = asset.license_name || asset.license || '—';
  $('assetOwner').textContent = asset.owner_org_node?.name || '—';
  $('assetRegister').textContent = asset.register_held_by_org_node?.name || '—';
  $('assetRiskCount').textContent = Number(asset.risk_count || 0);
  $('assetUpdated').textContent = fmtTs(asset.updated_at) || '—';
  $('assetControls').innerHTML = controlBadges(asset.controls, framework);
  $('assetClauses').innerHTML = clauseBadges(asset.clauses, framework);

  const edit = $('editAssetLink');
  if (edit) {
    edit.href = withFramework(`/isms.html?tab=assets&edit=asset:${encodeURIComponent(asset.id || '')}`, framework);
    edit.style.display = canManageIsms ? '' : 'none';
  }
  const sample = $('sampleAssetAudit');
  if (sample) sample.style.display = canSampleAudits ? '' : 'none';
}

async function loadChangelog() {
  await loadEntityChangelog(
    $('assetChangelog'),
    `/api/v1/isms/asset/${encodeURIComponent(assetId)}/changelog?limit=100`,
    {empty: 'No asset changes have been recorded yet.'}
  );
}

async function load() {
  if (!assetId) {
    toast(status, 'Missing asset id.', 'danger');
    return;
  }
  try {
    const row = await apiGet(`/api/v1/isms/assets/${encodeURIComponent(assetId)}?framework=${encodeURIComponent(framework)}`);
    renderAsset(row);
    await loadChangelog();
    activateTabFromHashOrQuery(params, {changelog: $('assetChangelogTab')});
  } catch (e) {
    toast(status, `Failed to load asset: ${String(e)}`, 'danger');
  }
}

$('sampleAssetAudit')?.addEventListener('click', () => {
  if (!asset) return;
  openAuditSampleModal({
    me,
    entityType: 'isms_asset',
    entityId: asset.id,
    title: asset.asset || asset.name || 'ISMS asset',
    evidenceUrl: assetHref(asset.id),
    framework,
    statusEl: status,
  });
});

window.addEventListener('hashchange', () => activateTabFromHashOrQuery(params, {changelog: $('assetChangelogTab')}));
load();
