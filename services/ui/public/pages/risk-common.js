// Shared vocabulary and helpers for the risk pages: the dashboard, the risk
// wizard, the risk detail page and the Mitigator. Other pages that list risks
// use its score bands. Each page passes its own framework so links keep the
// selected framework.
import { esc, node, userDisplayName, withFramework } from '/app.js';

export const RISK_TYPES = ['Confidentiality', 'Integrity', 'Availability'];

// The three ways a risk can be described. The register holds "general" and
// "cia" risks as one record type; a PESTLE(E) item is an impact-assessment
// item scored against controls and clauses.
export const RISK_METHODS = {
  general: {
    label: 'Simple risk',
    short: 'Risk',
    description:
      'Something that could go wrong for an asset, rated by how likely it is and how much it would hurt.',
    icon: 'bi-exclamation-triangle',
  },
  cia: {
    label: 'Information security (CIA)',
    short: 'CIA',
    description:
      'A risk to the confidentiality, integrity or availability of information held in an asset.',
    icon: 'bi-shield-lock',
  },
  pestle: {
    label: 'PESTLE(E) item',
    short: 'PESTLE(E)',
    description:
      'An outside or inside influence - political, economic, social, technological, legal, environmental or ethical - scored for how relevant it is to the controls and clauses of your framework.',
    icon: 'bi-globe2',
  },
};

export const TREATMENT_STRATEGIES = [
  ['', 'Not decided yet'],
  ['mitigate', 'Mitigate - reduce it with controls'],
  ['accept', 'Accept - live with it, and say why'],
  ['avoid', 'Avoid - stop doing the risky thing'],
  ['transfer', 'Transfer - insure or contract it out'],
];
export const TREATMENT_STATUSES = [
  ['open', 'Open'],
  ['in_progress', 'In progress'],
  ['awaiting_review', 'Awaiting review'],
  ['closed', 'Closed'],
];

// Plain-language anchors for the 1-5 scales, so a non-specialist can rate a
// risk without a scoring manual.
export const LIKELIHOOD_SCALE = [
  [1, 'Rare', 'Hard to imagine it happening here.'],
  [2, 'Unlikely', 'Could happen, but not expected in the next few years.'],
  [3, 'Possible', 'Might happen within a year or two.'],
  [4, 'Likely', 'Expected to happen within the year.'],
  [5, 'Almost certain', 'Happens regularly or is happening now.'],
];
export const IMPACT_SCALE = [
  [1, 'Negligible', 'A nuisance; no lasting effect.'],
  [2, 'Minor', 'Some rework or cost, contained within a team.'],
  [3, 'Moderate', 'Disruption felt across the organisation or by some customers.'],
  [4, 'Major', 'Serious harm: regulatory, financial or to many people.'],
  [5, 'Severe', 'Threatens the organisation or causes lasting harm to people.'],
];

export function methodOf(risk) {
  if (risk.method) return risk.method;
  return (risk.risk_types || []).length ? 'cia' : 'general';
}

export function methodBadgeHtml(method) {
  const meta = RISK_METHODS[method];
  const cls =
    method === 'cia'
      ? 'text-bg-primary'
      : method === 'pestle'
        ? 'text-bg-info'
        : 'text-bg-secondary';
  return `<span class="badge ${cls}" title="${esc(meta.label)}"><i class="bi ${esc(meta.icon)} me-1" aria-hidden="true"></i>${esc(meta.short)}</span>`;
}

export function riskTypeBadgesHtml(types) {
  return types.map((x) => `<span class="badge badge-soft me-1">${esc(x)}</span>`).join('');
}

export function scoreClass(score) {
  const n = Number(score || 0);
  if (n < 5) return 'text-bg-success';
  if (n <= 12) return 'text-bg-warning';
  return 'text-bg-danger';
}

export function scoreBadge(score) {
  const n = Number(score || 0);
  return `<span class="badge ${scoreClass(n)}">${esc(String(n))}</span>`;
}

export function scorePreviewClass(score) {
  const n = Number(score);
  if (n < 5) return 'score-pill score-low';
  if (n <= 12) return 'score-pill score-medium';
  return 'score-pill score-high';
}

export function clampScore(value) {
  return Math.max(1, Math.min(5, Math.floor(Number(value))));
}

// Register thresholds turn a 1-25 score into a severity word and colour.
export function severityFor(score, thresholds) {
  if (score == null) return { text: 'Needs rating', color: '#eef0f4' };
  if (score <= thresholds.low_max) return { text: 'Low', color: '#d9efde' };
  if (score <= thresholds.moderate_max) return { text: 'Moderate', color: '#fff2bf' };
  if (score <= thresholds.high_max) return { text: 'High', color: '#ffd5a7' };
  return { text: 'Critical', color: '#f9b9ba' };
}

export function severityBadge(score, thresholds) {
  const s = severityFor(score, thresholds);
  return `<span class="badge text-dark" style="background-color:${esc(s.color)}">${score == null ? esc(s.text) : `${esc(String(score))} · ${esc(s.text)}`}</span>`;
}

export function treatmentLabel(strategy) {
  const [, label] = TREATMENT_STRATEGIES.find(([value]) => value === String(strategy || ''));
  return label.split(' - ')[0];
}

export function treatmentStatusLabel(status) {
  return TREATMENT_STATUSES.find(([value]) => value === String(status || 'open'))[1];
}

export function riskDisplayTitle(risk) {
  if (risk.display) return risk.display;
  const title = String(risk.title || '').trim();
  if (title) return title;
  const asset = String(risk.asset || risk.asset_entity?.name || '').trim();
  const threat = String(risk.threat_summary || '').trim();
  if (asset && threat) return `${asset} - ${threat.slice(0, 120)}`;
  return asset || threat.slice(0, 120) || 'Risk';
}

export function riskHref(id, framework) {
  return withFramework(`/risk.html?id=${encodeURIComponent(id)}`, framework);
}

export function controlHref(id, framework) {
  return withFramework(`/control.html?id=${encodeURIComponent(id)}`, framework);
}

export function pestleHref(id, framework, tab = 'details') {
  return withFramework(
    `/pestle_item.html?id=${encodeURIComponent(id)}&tab=${encodeURIComponent(tab)}`,
    framework,
  );
}

export function riskDashboardHref(framework, tab = '') {
  return withFramework(`/risks.html${tab ? `?tab=${encodeURIComponent(tab)}` : ''}`, framework);
}

// The wizard opens blank, for one method, for an existing risk, or from a
// library template. Anything longer (a Mitigator analysis) travels through the
// draft hand-off below rather than the address.
export function riskWizardHref(framework, { id = '', method = '', template = '' } = {}) {
  const u = new URL('/risk-wizard.html', 'https://keen.invalid');
  if (id) u.searchParams.set('id', id);
  if (method) u.searchParams.set('method', method);
  if (template) u.searchParams.set('template', template);
  return withFramework(u.pathname + u.search, framework);
}

const DRAFT_KEY = 'keen.riskWizardDraft';

// A draft is a per-browser convenience for the hop from the Mitigator into
// the wizard; storage can be unavailable, so both directions fail quietly.
export function storeWizardDraft(draft) {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function takeWizardDraft() {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    sessionStorage.removeItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch {
    return null;
  }
}

// How heavily a visualisation draws an item's link for each relevance it can
// be given, from 0 to 1. A level it does not know is drawn as the lowest.
const RELEVANCE_STRENGTH = { high: 1, medium: 0.6, low: 0.3 };

export function relevanceStrength(rel) {
  return RELEVANCE_STRENGTH[rel.code] ?? RELEVANCE_STRENGTH.low;
}

// How relevant a PESTLE(E) item is: overall, or to one clause or control.
export function relevanceBadge(rel) {
  const cls =
    { high: 'text-bg-danger', medium: 'text-bg-warning', low: 'text-bg-success' }[rel?.code] ||
    'text-bg-light border';
  return node('span', { class: `badge ${cls}` }, rel?.label || 'N/A');
}

export function relevanceBadgeHtml(rel) {
  return relevanceBadge(rel).outerHTML;
}

function cell(attrs, ...children) {
  return node('td', attrs, ...children);
}

// The risks linked to one control or clause, as that record's page lists them.
export function linkedRisksTable(items, framework) {
  const heading = (label, attrs = {}) => node('th', attrs, label);
  const score = (value) =>
    cell(
      { class: 'text-end' },
      node('span', { class: `badge ${scoreClass(value)}` }, String(Number(value || 0))),
    );
  const rows = items.map((r) => {
    const owner = userDisplayName(r.risk_owner);
    const types = (r.risk_types || []).map((type) =>
      node('span', { class: 'badge badge-soft me-1' }, type),
    );
    return node(
      'tr',
      {},
      cell(
        {},
        node('a', { class: 'fw-semibold', href: riskHref(r.id, framework) }, r.asset),
        node(
          'div',
          { class: 'small-muted text-truncate', style: 'max-width:360px;' },
          r.threat_summary || '',
        ),
      ),
      cell({}, r.category?.name || ''),
      cell({}, r.subcategory?.name || ''),
      cell({}, ...(types.length ? types : [node('span', { class: 'small-muted' }, '-')])),
      score(r.risk_score),
      score(r.residual_risk_score),
      cell(
        {},
        owner
          ? node('span', { class: 'badge badge-soft user-pill' }, owner)
          : node('span', { class: 'small-muted' }, '-'),
      ),
    );
  });
  return node(
    'div',
    { class: 'table-responsive' },
    node(
      'table',
      { class: 'table table-sm align-middle mb-0' },
      node(
        'thead',
        {},
        node(
          'tr',
          {},
          heading('Asset'),
          heading('Category', { style: 'width:130px;' }),
          heading('Subcategory', { style: 'width:150px;' }),
          heading('Type', { style: 'width:170px;' }),
          heading('Risk', { class: 'text-end', style: 'width:90px;' }),
          heading('Residual', { class: 'text-end', style: 'width:100px;' }),
          heading('Owner', { style: 'width:120px;' }),
        ),
      ),
      node('tbody', {}, ...rows),
    ),
  );
}

// Table rows for the PESTLE(E) items rated against one clause or control:
// type, lens, item, relevance to that record, overall relevance, rationale.
export function pestleItemRows(items, framework) {
  return items.map((row) => {
    const it = row.pestle_item;
    return node(
      'tr',
      {},
      cell({}, it.type || ''),
      cell({}, it.lens || ''),
      cell({}, node('a', { class: 'fw-semibold', href: pestleHref(it.id, framework) }, it.item)),
      cell({}, relevanceBadge(row.relevance)),
      cell({}, relevanceBadge(it.overall_relevance)),
      cell({ class: 'wrap small-muted' }, it.rationale || '-'),
    );
  });
}
