import { esc, lookupTab, withFramework } from '/app.js';

export function badgeList(items, labelKey = 'ref', urlFn = null) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">-</span>';
  return arr
    .map((it) => {
      const label = String(it?.[labelKey] || it?.name || it?.title || 'Item');
      const title = String(it?.title || it?.name || label);
      const href = urlFn ? urlFn(it) : '';
      if (href)
        return `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(href)}" title="${esc(title)}">${esc(label)}</a>`;
      return `<span class="badge badge-soft me-1 mb-1" title="${esc(title)}">${esc(label)}</span>`;
    })
    .join('');
}

// Control links open the tab that lists the record: Requirements (the
// control's default tab, `tab: ''`) for policies, processes and measures, and
// the Governance tab for other records.
export function controlBadges(items, framework, { tab = 'governance' } = {}) {
  return badgeList(items, 'ref', (c) =>
    withFramework(
      `/control.html?id=${encodeURIComponent(c.id)}${tab ? `&tab=${tab}` : ''}`,
      c.framework || framework,
    ),
  );
}

// Controls linked to an effectiveness measure, opening each control's default tab.
export function effectivenessControlBadges(items, framework) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">No controls linked.</span>';
  return arr
    .map(
      (c) =>
        `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, c.framework || framework))}" title="${esc(c.title || c.ref || 'Control')}">${esc(c.ref || 'Control')}</a>`,
    )
    .join('');
}

export function clauseBadges(items, framework) {
  return badgeList(items, 'ref', (c) =>
    withFramework(`/clause.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework),
  );
}

export function activateTabFromHashOrQuery(params, map, defaultKey = '') {
  const key = String(
    params.get('tab') || window.location.hash.replace(/^#/, '') || defaultKey || '',
  ).toLowerCase();
  const target = lookupTab(map, key);
  if (target && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(target).show();
}
