// The design system's related-item cards, as Keen's page tests see them.
//
// Page tests run against a stand-in for the design system, so this follows
// the contract of relatedCard, relatedCards and updateRelatedCard in
// mosp-design-system (src/js/190-related-cards.js): the same elements and
// classes, text written as text, markup taken only as a DOM node, and an
// unsafe address left without a link. The real builder has its own tests
// there; these functions let a page test read what the page passed to it.
export function relatedCardStandIn(window) {
  const { document, Node } = window;
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const given = (value, what) => {
    if (!(value instanceof Node)) throw new TypeError(`${what} must be a DOM node`);
    return value;
  };
  // One of this site's paths or an http(s) URL; anything else links nowhere.
  const safe = (href) => {
    const raw = String(href ?? '').trim();
    if (!raw || /[\\\u0000-\u001f\u007f]/.test(raw) || raw.startsWith('//')) return '';
    if (raw.startsWith('/')) return raw;
    return /^https?:\/\//i.test(raw) ? raw : '';
  };
  const link = (text, href, className) => {
    const address = safe(href);
    if (!address) return el('span', className, text);
    const a = el('a', className, text);
    a.setAttribute('href', address);
    return a;
  };
  const piece = (value) => {
    if (value === null || value === undefined || value === '') return [];
    if (value instanceof Node) return [value];
    if (Array.isArray(value)) return value.flatMap(piece);
    if (typeof value === 'object') {
      const a = link(value.text ?? '', value.href, 'mosp-related-link');
      if (value.newTab && a.tagName === 'A') {
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener noreferrer');
      }
      return [a, ...(value.detail ? [el('div', 'mosp-related-detail', value.detail)] : [])];
    }
    return [document.createTextNode(String(value))];
  };
  const table = ({ columns, rows }) => {
    const out = el('table', 'table table-sm align-middle mb-0 mosp-related-table');
    const head = el('tr');
    for (const column of columns) {
      const spec = typeof column === 'string' ? { label: column } : column;
      const th = el('th', '', spec.label);
      th.setAttribute('scope', 'col');
      if (/^\d{1,4}(?:px|rem|em|%)$/.test(String(spec.width || ''))) th.style.width = spec.width;
      if (spec.align === 'end') th.classList.add('text-end');
      head.append(th);
    }
    const body = el('tbody');
    for (const row of rows) {
      const tr = el('tr');
      row.forEach((cell, index) => {
        const td = el('td');
        if (columns[index]?.align === 'end') td.classList.add('text-end');
        td.append(...piece(cell));
        tr.append(td);
      });
      body.append(tr);
    }
    const thead = el('thead');
    thead.append(head);
    out.append(thead, body);
    return out;
  };
  const list = (links) => {
    const out = el('ul', 'list-unstyled mb-0 mosp-related-list');
    for (const item of links) {
      const li = el('li', 'mosp-related-list-item');
      const main = el('div', 'mosp-related-list-main');
      main.append(...piece({ ...item, action: undefined }));
      li.append(main);
      if (item.action) li.append(given(item.action, 'A list item action'));
      out.append(li);
    }
    return out;
  };
  const content = ({ table: t, links, body, empty }) => {
    if (body !== undefined && body !== null) return [given(body, 'A card body')];
    if (t && t.rows.length) return [table(t)];
    if (links && links.length) return [list(links)];
    return [el('p', 'mosp-related-empty mb-0', empty || 'Nothing is linked yet.')];
  };
  function updateRelatedCard(card, change = {}) {
    if ('count' in change) {
      const badge = card.querySelector('.mosp-related-card-count');
      const shown = change.count !== null && change.count !== undefined;
      badge.hidden = !shown;
      badge.textContent = shown ? String(change.count) : '';
    }
    if (['table', 'links', 'body', 'empty'].some((key) => key in change)) {
      card.querySelector('.mosp-related-card-body').replaceChildren(...content(change));
      if (card.dataset.relatedWidth === 'auto')
        card.classList.toggle(
          'mosp-related-card-wide',
          Boolean(change.table && change.table.rows.length),
        );
    }
    return card;
  }
  function relatedCard(options = {}) {
    const card = el('section', 'card mosp-related-card');
    if (/^[A-Za-z][\w-]{0,63}$/.test(String(options.id || ''))) card.id = options.id;
    card.dataset.relatedWidth = typeof options.wide === 'boolean' ? 'fixed' : 'auto';
    if (options.wide === true) card.classList.add('mosp-related-card-wide');
    const header = el('div', 'card-header mosp-related-card-header');
    const titles = el('div', 'mosp-related-card-titles');
    titles.append(el('h3', 'mosp-related-card-title', options.title ?? ''));
    if (options.description) titles.append(el('div', 'small-muted', options.description));
    const tools = el('div', 'mosp-related-card-tools');
    const badge = el('span', 'badge text-bg-light mosp-related-card-count');
    badge.hidden = true;
    tools.append(badge);
    if (options.action instanceof Node) tools.append(options.action);
    else if (options.action) {
      const a = link(
        options.action.label ?? '',
        options.action.href,
        'btn btn-sm btn-outline-primary',
      );
      if (a.tagName === 'A') tools.append(a);
    }
    header.append(titles, tools);
    card.append(header, el('div', 'card-body mosp-related-card-body'));
    return updateRelatedCard(card, {
      count: options.count,
      table: options.table,
      links: options.links,
      body: options.body,
      empty: options.empty,
    });
  }
  function relatedCards(cards = [], host = null) {
    const group = host ? given(host, 'The group') : el('div');
    group.classList.add('mosp-related-cards');
    group.replaceChildren(...cards.map((card) => given(card, 'A card')));
    return group;
  }
  return { relatedCard, relatedCards, updateRelatedCard };
}
