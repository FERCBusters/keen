// Availability supplements permissions. The API enforces every operation.
const pages = {
  '/': 'explorer',
  '/index.html': 'explorer',
  '/controls.html': 'controls',
  '/control.html': 'controls',
  '/clauses.html': 'clauses',
  '/clause.html': 'clauses',
  '/events.html': 'evidence',
  '/event.html': 'evidence',
  '/sources.html': 'sources',
  '/source.html': 'sources',
  '/visualisation.html': 'visualisations',
  '/visualisations.html': 'visualisations',
  '/home.html': 'explorer',
  '/questions.html': 'questions',
  '/my-questions.html': 'questions',
  '/isms-access.html': 'access_control',
  '/isms-effectiveness-metric.html': 'effectiveness',
  '/documents.html': 'documents',
  '/bookstack-sections.html': 'documents',
  '/statement-of-applicability.html': 'soa',
  '/pestle_item.html': 'pestle',
  '/interested_parties.html': 'interested_parties',
  '/interested_party.html': 'interested_parties',
  '/assurance.html': 'isms',

  '/risks.html': 'risk_cia',
  '/risk.html': 'risk_cia',
  '/risk-register.html': 'risk_register',
  '/mitigator.html': 'mitigator',
  '/pestle.html': 'pestle',
  '/pestle-item.html': 'pestle',
  '/interested-parties.html': 'interested_parties',
  '/interested-party.html': 'interested_parties',
  '/isms.html': 'isms',
  '/swot.html': 'swot',
  '/isms-swot.html': 'swot',
  '/people.html': 'people',
  '/person.html': 'people',
  '/vendors.html': 'vendors',
  '/vendor.html': 'vendors',
  '/audits.html': 'audits',
  '/audit.html': 'audits',
  '/scheduled-audits.html': 'audit_scheduling',
  '/soa.html': 'soa',
  '/bookstack-section.html': 'documents',
  '/isms-objective.html': 'objectives',
  '/isms-document.html': 'documents',
  '/isms-asset.html': 'assets',
  '/isms-org-node.html': 'organisation',
  '/isms-effectiveness-measure.html': 'effectiveness',
  '/isms-meeting.html': 'meetings',
  '/isms-access-control-matrix.html': 'access_control',
};
export const sectionFeatures = {
  objectives: 'objectives',
  documents: 'documents',
  org: 'organisation',
  assets: 'assets',
  access: 'access_control',
  effectiveness: 'effectiveness',
  meetings: 'meetings',
  swot: 'swot',
  soa: 'soa',
};
export function featureEnabled(me, key) {
  return me?.features?.enabled?.[key] !== false;
}
export function hrefFeature(href) {
  let url;
  try {
    url = new URL(href, window.location.href);
  } catch {
    return null;
  }
  if (url.origin !== window.location.origin) return null;
  if (url.pathname === '/isms.html') return sectionFeatures[url.searchParams.get('tab')] || 'isms';
  if (url.pathname === '/isms-swot.html' && url.searchParams.get('mode') === 'objectives')
    return 'objectives';
  return pages[url.pathname];
}
let observer;
let tabGuardInstalled = false;
export function applyFeatures(me) {
  observer?.disconnect();
  if (!tabGuardInstalled) {
    document.addEventListener('show.bs.tab', (event) => {
      if (event.target.closest('[data-feature-hidden]')) event.preventDefault();
    });
    tabGuardInstalled = true;
  }
  const apply = (root) => {
    const nodes = [...root.querySelectorAll('[data-feature], a[href]')];
    if (root.matches?.('[data-feature], a[href]')) nodes.unshift(root);
    for (const node of nodes) {
      const key = node.dataset.feature || hrefFeature(node.getAttribute('href'));
      if (!key) continue;
      const disabled = !featureEnabled(me, key);
      const target = node.parentElement?.tagName === 'LI' ? node.parentElement : node;
      if (disabled) target.setAttribute('data-feature-hidden', '');
      else target.removeAttribute('data-feature-hidden');
      if (node.dataset.bsTarget) {
        const pane = document.getElementById(node.dataset.bsTarget.slice(1));
        if (pane) {
          if (disabled) pane.setAttribute('data-feature-hidden', '');
          else pane.removeAttribute('data-feature-hidden');
        }
      }
    }
  };
  for (const [id, key] of [
    ['riskAssessmentsDropdown', 'risks'],
    ['ismsDropdown', 'isms'],
  ]) {
    const toggle = document.getElementById(id);
    if (toggle) toggle.dataset.feature = key;
  }
  const bell = document.getElementById('navBell');
  if (bell) bell.dataset.feature = 'questions';
  apply(document);
  if (!featureEnabled(me, 'visualisations')) {
    const url = new URL(window.location.href);
    if (['visualisations', 'visualizations'].includes(url.searchParams.get('tab'))) {
      url.searchParams.delete('tab');
      window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    }
    for (const tab of document.querySelectorAll('[data-feature="visualisations"].active')) {
      const fallback = [
        ...(tab
          .closest('[role="tablist"], .nav-tabs')
          ?.querySelectorAll('[data-bs-toggle="tab"]') || []),
      ].find((candidate) => !candidate.closest('[data-feature-hidden]'));
      if (fallback) window.bootstrap?.Tab.getOrCreateInstance(fallback).show();
    }
  }
  observer = new window.MutationObserver((records) => {
    for (const record of records)
      for (const node of record.addedNodes) if (node.nodeType === 1) apply(node);
  });
  observer.observe(document.body, { childList: true, subtree: true });
  const key = hrefFeature(window.location.href);
  // ISMS tabs have an available Overview fallback; standalone pages stop here.
  const pageKey = window.location.pathname === '/isms.html' ? 'isms' : key;
  if (pageKey && !featureEnabled(me, pageKey)) {
    const main = document.querySelector('main');
    if (main) {
      const notice = document.createElement('div');
      notice.className = 'alert alert-info';
      notice.textContent =
        'This feature is disabled by an administrator. Your existing records are retained.';
      main.replaceChildren(notice);
    }
    throw new Error('Feature disabled');
  }
}
