import {
  errorText,
  initNavbar,
  apiGet,
  fmtTs,
  getCurrentFramework,
  toast,
  withFramework,
  canSampleIntoAudit,
  openAuditSampleModal,
  loadEntityChangelog,
  isoDateToDisplay,
  userDisplayName,
  node,
  assetHref,
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
  scoreClass,
  treatmentLabel,
  treatmentStatusLabel,
} from '/pages/risk-common.js';
import { fetchedCard, mountRelatedItems } from '/pages/related-items-tab.js';

const me = await initNavbar();
const framework = getCurrentFramework();
const params = new URLSearchParams(window.location.search);
const assetId = params.get('id') || params.get('asset') || '';
const status = document.getElementById('status');
const $ = (id) => document.getElementById(id);
const canManageIsms = !!(me?.is_admin || me?.can_manage_isms || me?.can_manage_risks);
const canSampleAudits = canSampleIntoAudit(me, 'isms');
const canViewRisks =
  featureEnabled(me, 'risk_cia') && !!(me?.is_admin || me?.can_view_risks || me?.can_manage_risks);
let asset = null;

function renderAsset(row) {
  asset = row;
  const title = asset.asset || asset.name;
  document.title = `Keen - ${title}`;
  $('assetTitle').textContent = title;
  $('assetMeta').textContent = [
    asset.category?.name || asset.category_name,
    asset.subcategory_name,
    asset.license_name || asset.license,
    `updated ${fmtTs(asset.updated_at)}`,
  ]
    .filter(Boolean)
    .join(' · ');
  $('assetDescription').textContent = asset.description || 'No description recorded.';
  $('assetCategory').textContent = asset.category?.name || asset.category_name || '-';
  $('assetSubcategory').textContent = asset.subcategory_name || '-';
  $('assetLicense').textContent = asset.license_name || asset.license || '-';
  $('assetOwner').textContent = asset.owner_org_node?.name || '-';
  $('assetRiskCount').textContent = Number(asset.risk_count || 0);
  $('assetUpdated').textContent = fmtTs(asset.updated_at);
  $('assetControls').innerHTML = controlBadges(asset.controls, framework);
  $('assetClauses').innerHTML = clauseBadges(asset.clauses, framework);

  const edit = $('editAssetLink');
  if (edit) {
    edit.href = withFramework(
      `/isms.html?tab=assets&edit=asset:${encodeURIComponent(asset.id)}`,
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

// The risks recorded against the asset, as a card of the Related items tab.
function risksTable(items) {
  const score = (value) =>
    node('span', { class: `badge ${scoreClass(value)}` }, String(Number(value || 0)));
  return {
    columns: [
      { label: 'Risk' },
      { label: 'Treatment', width: '220px' },
      { label: 'Risk', width: '90px', align: 'end' },
      { label: 'Residual', width: '100px', align: 'end' },
      { label: 'Owner', width: '140px' },
    ],
    rows: items.map((r) => {
      const due = isoDateToDisplay(r.treatment_due_at);
      const owner = userDisplayName(r.risk_owner);
      return [
        [
          node('a', { class: 'fw-semibold', href: riskHref(r.id, framework) }, riskDisplayTitle(r)),
          ...(r.risk_types.length
            ? [
                node(
                  'div',
                  {},
                  ...r.risk_types.map((type) =>
                    node('span', { class: 'badge badge-soft me-1' }, type),
                  ),
                ),
              ]
            : []),
        ],
        [
          treatmentLabel(r.treatment_strategy),
          node(
            'div',
            { class: 'small-muted' },
            `${treatmentStatusLabel(r.treatment_status)}${due ? ` · due ${due}` : ''}`,
          ),
        ],
        score(r.risk_score),
        score(r.residual_risk_score),
        owner
          ? node('span', { class: 'badge badge-soft user-pill' }, owner)
          : node('span', { class: 'small-muted' }, '-'),
      ];
    }),
  };
}

const related = mountRelatedItems({
  me,
  kind: 'asset',
  id: assetId,
  framework,
  first: canViewRisks
    ? [
        fetchedCard({
          id: 'assetRisksCard',
          title: 'Risks',
          description: 'Risks recorded against this asset, highest first.',
          what: 'risks',
          empty: 'No risks are recorded against this asset.',
          read: async () => {
            const data = await apiGet(
              `/api/v1/risks?asset_id=${encodeURIComponent(assetId)}&framework=${encodeURIComponent(framework)}&limit=500`,
            );
            return { count: data.items.length, table: risksTable(data.items) };
          },
        }),
      ]
    : [],
});
// An address that names the Risks tab, as earlier pages did, opens the tab
// the risks are on now.
const tabs = () => ({
  changelog: $('assetChangelogTab'),
  ...(related ? { related: related.tab } : {}),
  ...(related && canViewRisks ? { risks: related.tab } : {}),
});

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
    activateTabFromHashOrQuery(params, tabs());
  } catch (e) {
    toast(status, `Failed to load asset: ${errorText(e)}`, 'danger');
  }
}

$('sampleAssetAudit')?.addEventListener('click', () => {
  openAuditSampleModal({
    me,
    entityType: 'isms_asset',
    entityId: asset.id,
    title: asset.asset,
    evidenceUrl: assetHref(asset.id, framework),
    framework,
    statusEl: status,
  });
});

window.addEventListener('hashchange', () => activateTabFromHashOrQuery(params, tabs()));
load();
