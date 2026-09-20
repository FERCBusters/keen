import {esc, withFramework} from '/app.js';

export function badgeList(items, labelKey = 'ref', urlFn = null) {
  const arr = Array.isArray(items) ? items : [];
  if (!arr.length) return '<span class="small-muted">—</span>';
  return arr.map((it) => {
    const label = String(it?.[labelKey] || it?.name || it?.title || 'Item');
    const title = String(it?.title || it?.name || label);
    const href = urlFn ? urlFn(it) : '';
    if (href) return `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(href)}" title="${esc(title)}">${esc(label)}</a>`;
    return `<span class="badge badge-soft me-1 mb-1" title="${esc(title)}">${esc(label)}</span>`;
  }).join('');
}

export function controlBadges(items, framework) {
  return badgeList(items, 'ref', (c) => withFramework(`/control.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework));
}

export function clauseBadges(items, framework) {
  return badgeList(items, 'ref', (c) => withFramework(`/clause.html?id=${encodeURIComponent(c.id)}&tab=isms`, c.framework || framework));
}

export function activateTabFromHashOrQuery(params, map, defaultKey = '') {
  const key = String(params.get('tab') || window.location.hash.replace(/^#/, '') || defaultKey || '').toLowerCase();
  const target = map[key];
  if (target && window.bootstrap?.Tab) window.bootstrap.Tab.getOrCreateInstance(target).show();
}
