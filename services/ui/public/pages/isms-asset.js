import {
  errorText,
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
  isoDateToDisplay,
  userPillHtml,
} from '/app.js';
import {
  activateTabFromHashOrQuery,
  clauseBadges,
  controlBadges,
} from '/pages/isms-detail-common.js';
import { featureEnabled } from '/pages/feature-availability.js';
import {
  riskDisplayTitle,
  riskHref,
  scoreBadge,
  treatmentLabel,
  treatmentStatusLabel,
} from '/pages/risk-common.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search || '');
const assetId = params.get('id') || params.get('asset') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);
const canManageIsms = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me, 'isms');
const canViewRisks =
  featureEnabled(me, 'risk_cia') && !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
let asset = null;
let risksLoaded = false;

function assetHref(id = assetId) {
  return withFramework(`/isms-asset.html?id=${encodeURIComponent(id || '')}`, framework);
}

function renderAsset(row) {
  asset = row || {};
  const title = asset.asset || asset.name || 'Asset';
  document.title = `Keen - ${title}`;
  $('assetTitle').textContent = title;
  $('assetMeta').textContent = [
    asset.category?.name || asset.category_name,
    asset.subcategory?.name || asset.subcategory_name,
    asset.license_name || asset.license,
    `updated ${fmtTs(asset.updated_at) || '-'}`,
  ]
    .filter(Boolean)
    .join(' · ');
  $('assetDescription').textContent = asset.description || 'No description recorded.';
  $('assetCategory').textContent = asset.category?.name || asset.category_name || '-';
  $('assetSubcategory').textContent = asset.subcategory?.name || asset.subcategory_name || '-';
  $('assetLicense').textContent = asset.license_name || asset.license || '-';
  $('assetOwner').textContent = asset.owner_org_node?.name || '-';
  $('assetRegister').textContent = asset.register_held_by_org_node?.name || '-';
  $('assetRiskCount').textContent = Number(asset.risk_count || 0);
  $('assetUpdated').textContent = fmtTs(asset.updated_at) || '-';
  $('assetControls').innerHTML = controlBadges(asset.controls, framework);
  $('assetClauses').innerHTML = clauseBadges(asset.clauses, framework);

  const edit = $('editAssetLink');
  if (edit) {
    edit.href = withFramework(
      `/isms.html?tab=assets&edit=asset:${encodeURIComponent(asset.id || '')}`,
      framework,
    );
    edit.style.display = canManageIsms ? '' : 'none';
  }
  const sample = $('sampleAssetAudit');
  if (sample) sample.style.display = canSampleAudits ? '' : 'none';
}

async function loadChangelog() {
  await loadEntityChangelog(
    $('assetChangelog'),
    `/api/v1/isms/asset/${encodeURIComponent(assetId)}/changelog?limit=100`,
    { empty: 'No asset changes have been recorded yet.' },
  );
}

function risksHtml(items) {
  if (!items.length)
    return '<p class="small-muted mb-0">No risks are recorded against this asset.</p>';
  const rows = items
    .map((r) => {
      const types = (r.risk_types || [])
        .map((x) => `<span class="badge badge-soft me-1">${esc(x)}</span>`)
        .join('');
      const due = r.treatment_due_at ? isoDateToDisplay(r.treatment_due_at) : '';
      return `<tr>
        <td><a class="fw-semibold" href="${esc(riskHref(r.id, framework))}">${esc(riskDisplayTitle(r))}</a>${types ? `<div>${types}</div>` : ''}</td>
        <td>${esc(treatmentLabel(r.treatment_strategy))}<div class="small-muted">${esc(treatmentStatusLabel(r.treatment_status))}${due ? ` · due ${esc(due)}` : ''}</div></td>
        <td class="text-end">${scoreBadge(r.risk_score)}</td>
        <td class="text-end">${scoreBadge(r.residual_risk_score)}</td>
        <td>${userPillHtml(r.risk_owner)}</td>
      </tr>`;
    })
    .join('');
  return `<div class="table-responsive"><table class="table table-sm align-middle mb-0">
    <thead><tr><th>Risk</th><th>Treatment</th><th class="text-end">Risk</th><th class="text-end">Residual</th><th>Owner</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

async function loadRisks() {
  if (!canViewRisks || risksLoaded) return;
  const host = $('assetRisks');
  try {
    const data = await apiGet(
      `/api/v1/risks?asset_id=${encodeURIComponent(assetId)}&framework=${encodeURIComponent(framework)}&limit=500`,
    );
    risksLoaded = true;
    host.innerHTML = risksHtml(data?.items || []);
  } catch (e) {
    host.textContent = `Risks could not be loaded: ${errorText(e)}`;
  }
}

if (canViewRisks) {
  $('assetRisksTabItem').hidden = false;
  $('assetRisksTab').addEventListener('shown.bs.tab', loadRisks);
}

async function load() {
  if (!assetId) {
    toast(status, 'Missing asset id.', 'danger');
    return;
  }
  try {
    const row = await apiGet(
      `/api/v1/isms/assets/${encodeURIComponent(assetId)}?framework=${encodeURIComponent(framework)}`,
    );
    renderAsset(row);
    await loadChangelog();
    activateTabFromHashOrQuery(params, {
      changelog: $('assetChangelogTab'),
      ...(canViewRisks ? { risks: $('assetRisksTab') } : {}),
    });
  } catch (e) {
    toast(status, `Failed to load asset: ${errorText(e)}`, 'danger');
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

window.addEventListener('hashchange', () =>
  activateTabFromHashOrQuery(params, {
    changelog: $('assetChangelogTab'),
    ...(canViewRisks ? { risks: $('assetRisksTab') } : {}),
  }),
);
load();

import { mountSwotRelated } from '/pages/swot-related.js';
import { mountStoryRelated } from '/pages/story-related.js';
mountSwotRelated('asset', new URLSearchParams(location.search).get('id'));
mountStoryRelated({ me, kind: 'asset', id: new URLSearchParams(location.search).get('id') });
