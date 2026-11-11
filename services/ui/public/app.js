import { applyFeatures, featureEnabled } from '/pages/feature-availability.js';
// Re-export the MOSP design-system helpers.
export * from '/vendor/mosp-design-system/app.js';
export { confirmAction } from './pages/confirm-modal.js';
export { unsavedGuard } from './pages/unsaved-guard.js';
import { initContextHelp } from './help-launcher.js';
import { localPath } from './pages/navigation-security.js';
export { localPath };

// Design-system helpers this module uses itself; pages reach every
// design-system export through the re-export above.
import {
  errorText,
  getCurrentFramework,
  getLoadedFrameworkCatalog,
  esc,
  fmtTs,
  getUiConfig,
  toast,
  sourceBadgeHtml as _mospSourceBadgeHtml,
  initNavbar as _mospInitNavbar,
  initCollapsibleFilterSections as _mospInitCollapsibleFilterSections,
} from '/vendor/mosp-design-system/app.js';
import * as mosp from '/vendor/mosp-design-system/app.js';

// Pages build API paths from record identifiers and query values, and the
// browser resolves a "." or ".." path segment (encoded or not) before the
// request leaves, so an identifier of ".." would send the page's session and
// CSRF token to another route. Every API helper refuses such a path.
export function apiPath(path) {
  const route = String(path).split(/[?#]/, 1)[0];
  const dotted = route.split('/').some((segment) => {
    let decoded = segment;
    try {
      decoded = decodeURIComponent(segment);
    } catch {}
    return decoded === '.' || decoded === '..';
  });
  if (dotted) throw new Error('Invalid API path');
  return path;
}

export async function apiGet(path, ...rest) {
  return mosp.apiGet(apiPath(path), ...rest);
}
export async function apiPost(path, ...rest) {
  return mosp.apiPost(apiPath(path), ...rest);
}
export async function apiPut(path, ...rest) {
  return mosp.apiPut(apiPath(path), ...rest);
}
export async function apiPatch(path, ...rest) {
  return mosp.apiPatch(apiPath(path), ...rest);
}
export async function apiDelete(path, ...rest) {
  return mosp.apiDelete(apiPath(path), ...rest);
}
export async function apiPostForm(path, ...rest) {
  return mosp.apiPostForm(apiPath(path), ...rest);
}
export async function apiRequest(path, ...rest) {
  return mosp.apiRequest(apiPath(path), ...rest);
}

function _frameworkForHref() {
  const p = new URLSearchParams(window.location.search || '');
  return p.get('framework') || '';
}

function _withCurrentFramework(path) {
  const fw = _frameworkForHref();
  if (!fw) return path;
  const url = new URL(path, window.location.origin);
  url.searchParams.set('framework', fw);
  return url.pathname + url.search;
}

export function sourceBadgeHtml(source, metaMap, href = null) {
  const s = String(source || '').trim();
  const target = href || (s ? `/source.html?source=${encodeURIComponent(s)}` : '');
  return _mospSourceBadgeHtml(source, metaMap, target);
}

function _patchRiskNavDropdown() {
  const navRoot = document.getElementById('navbar');
  if (!navRoot || navRoot.dataset.pestleRiskDropdownPatched === '1') return;

  const links = Array.from(navRoot.querySelectorAll('a[href]'));
  const riskLink = links.find((a) => {
    const href = String(a.getAttribute('href'));
    const txt = String(a.textContent).trim().toLowerCase();
    return href.includes('/risks.html') || txt === 'risks';
  });
  if (!riskLink) return;

  const li = riskLink.closest('li') || riskLink.parentElement;
  li.classList.add('nav-item', 'dropdown');
  li.innerHTML = `
    <a class="nav-link dropdown-toggle" href="#" id="riskAssessmentsDropdown" role="button" data-bs-toggle="dropdown" aria-expanded="false">
      Risks
    </a>
    <ul class="dropdown-menu dropdown-menu-end" aria-labelledby="riskAssessmentsDropdown">
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/risks.html'))}">Risks dashboard</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/risk-wizard.html'))}">Add a risk</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/risks.html?tab=pestle'))}">PESTLE(E) method</a></li>
      <li><a class="dropdown-item d-inline-flex align-items-center gap-2" href="${esc(_withCurrentFramework('/mitigator.html'))}"><img src="/keen-mitigator-64.png" alt="" class="keen-mitigator-icon-sm"> Keen Mitigator</a></li>
    </ul>`;
  navRoot.dataset.pestleRiskDropdownPatched = '1';
}

function _patchIsmsNavDropdown() {
  const navRoot = document.getElementById('navbar');
  if (!navRoot || navRoot.dataset.ismsDropdownPatched === '1') return;

  const existing = Array.from(navRoot.querySelectorAll('a[href]')).find((a) => {
    const href = String(a.getAttribute('href'));
    const txt = String(a.textContent).trim().toLowerCase();
    return href.includes('/isms.html') || txt === 'isms';
  });
  if (existing) {
    navRoot.dataset.ismsDropdownPatched = '1';
    return;
  }

  const list = navRoot.querySelector('ul.navbar-nav') || navRoot.querySelector('ul') || navRoot;
  const links = Array.from(navRoot.querySelectorAll('a[href]'));
  const riskLink = links.find((a) => {
    const href = String(a.getAttribute('href'));
    const txt = String(a.textContent).trim().toLowerCase();
    return href.includes('/risks.html') || txt === 'risks';
  });
  const riskLi = riskLink?.closest?.('li');

  const li = document.createElement('li');
  li.className = 'nav-item dropdown';
  li.innerHTML = `
    <a class="nav-link dropdown-toggle" href="#" id="ismsDropdown" role="button" data-bs-toggle="dropdown" aria-expanded="false">
      ISMS
    </a>
    <ul class="dropdown-menu dropdown-menu-end" aria-labelledby="ismsDropdown">
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html'))}">ISMS overview</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=objectives'))}">Objectives</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/swot.html'))}">SWOT analysis</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=effectiveness'))}">Effectiveness measures</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/quizzes.html'))}">Quizzes</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=documents'))}">Policies &amp; processes</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=org'))}">Organisation chart</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=assets'))}">Asset matrix</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/assurance.html'))}">People &amp; vendors / 3rd parties</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=access'))}">Access control matrix</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=meetings'))}">Minutes of meetings</a></li>
      <li><hr class="dropdown-divider"></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/statement-of-applicability.html'))}">Statement of Applicability</a></li>
    </ul>`;

  if (riskLi?.parentElement) {
    riskLi.insertAdjacentElement('afterend', li);
  } else if (list?.appendChild) {
    list.appendChild(li);
  }
  navRoot.dataset.ismsDropdownPatched = '1';
}

function _removeHomeNavItem() {
  const navRoot = document.getElementById('navbar');
  if (!navRoot || navRoot.dataset.homeRemoved === '1') return;
  const links = Array.from(navRoot.querySelectorAll('a[href]'));
  for (const a of links) {
    const label = String(a.textContent).trim().toLowerCase();
    const href = String(a.getAttribute('href')).trim();
    if (label !== 'home') continue;
    let isHome = href === '/' || href === 'index.html' || href.endsWith('/index.html');
    // initNavbar has parsed every navbar link before this runs.
    const url = new URL(href, window.location.origin);
    isHome = isHome || url.pathname === '/' || url.pathname.endsWith('/index.html');
    if (!isHome) continue;
    const item = a.closest('li') || a;
    item.remove();
  }
  navRoot.dataset.homeRemoved = '1';
}

function _collapseFilterSectionsByDefault() {
  _mospInitCollapsibleFilterSections();
}

// Reconcile organisation changes without reinitialising the navbar or navigating away.
// Accept the successful save response so an old cached catalogue cannot overwrite it.
export async function refreshFrameworkNavigation(savedCatalog = null, cfgOverrides = null) {
  const cfg = getUiConfig(cfgOverrides);
  const navRoot = document.getElementById('navbar');
  const clauseLinks = [...(navRoot?.querySelectorAll('a[href]') || [])].filter(
    (a) => new URL(a.href, location.origin).pathname === '/clauses.html',
  );
  for (const link of clauseLinks) (link.closest('li') || link).hidden = true;
  const data = savedCatalog || (await getFrameworkCatalog(cfgOverrides));
  const catalog = { ...data, items: data.items.filter((item) => item.enabled !== false) };
  const allowed = new Set(catalog.items.map((item) => item.slug));
  let selected = getCurrentFramework();
  window.mospFrameworkDefault = catalog.default || '';
  if (!allowed.has(selected) && catalog.default) {
    selected = catalog.default;
    // The URL parser resolves dot segments, so a path it writes out again can
    // begin with "//" and name another host. Full addresses stay on Keen, and
    // a link keeps its path form only while that is a local path.
    const url = new URL(location.href);
    url.searchParams.set('framework', selected);
    history.replaceState(history.state, '', url.href);
    window.mospFramework = selected;
    try {
      localStorage.setItem(cfg.storage.frameworkKey, selected);
    } catch {}
    for (const link of navRoot?.querySelectorAll('a[href]') || []) {
      const target = new URL(link.href, location.origin);
      if (
        target.origin !== location.origin ||
        (!link.hasAttribute('data-nav-fw') && !target.searchParams.has('framework'))
      )
        continue;
      target.searchParams.set('framework', selected);
      link.href = localPath(target.pathname + target.search + target.hash) || target.href;
    }
  }
  const picker = navRoot?.querySelector('#navFrameworkSelect');
  if (picker) {
    picker.replaceChildren(
      ...catalog.items.map((item) => {
        const option = new Option(item.name, item.slug);
        option.title = `${item.name} (${item.slug})`;
        return option;
      }),
    );
    picker.value = selected;
    picker.disabled = !catalog.items.length;
    const describe = () => {
      const item = catalog.items.find((item) => item.slug === picker.value);
      picker.title = item
        ? `${item.name} (${item.slug})`
        : 'No frameworks available; ask an administrator to check organisation settings';
      picker.setAttribute('aria-label', `Framework: ${picker.title}`);
    };
    describe();
  }
  const framework = catalog.items.find((item) => item.slug === selected);
  for (const a of clauseLinks) (a.closest('li') || a).hidden = framework?.has_clauses !== true;
  window.dispatchEvent(new CustomEvent('keen:framework-catalog-changed', { detail: catalog }));
  return catalog;
}

// A framework as the top navigation names it, followed by its slug, or the
// slug alone when the picker does not list it.
export function frameworkLabel(slug) {
  const picker = document.getElementById('navFrameworkSelect');
  const name = [...(picker?.options || [])]
    .find((option) => option.value === slug)
    ?.textContent.trim();
  return name && name !== slug ? `${name} (${slug})` : slug;
}

let navbarInitialisation;
// A document can load several page modules. Share its complete shell setup,
// including notification polling, without persisting account data across loads.
export function initNavbar(...args) {
  if (!navbarInitialisation) {
    navbarInitialisation = initialiseNavbar(...args).catch((error) => {
      navbarInitialisation = null;
      throw error;
    });
  }
  return navbarInitialisation;
}

// Stories sit after Audits in the navigation for accounts that can read them.
export function withStoriesLink(links = []) {
  const list = Array.isArray(links) ? [...links] : [];
  if (list.some((link) => link?.key === 'stories')) return list;
  const stories = {
    key: 'stories',
    label: 'Stories',
    href: '/stories.html',
    requireAny: ['is_admin', 'can_view_stories'],
  };
  const after = list.findIndex((link) => link?.key === 'audits');
  list.splice(after < 0 ? list.length : after + 1, 0, stories);
  return list;
}

// The bell's notification kinds: questions for everyone who has them, and
// suggested mapping rules for administrators. Several kinds make the bell a menu.
export function bellItems(me) {
  // Everyone has an inbox: audits, quizzes and meeting minutes that concern them.
  const items = [
    {
      label: 'Notifications',
      href: '/notifications.html',
      icon: 'bi-inbox',
      summaryEndpoint: '/api/v1/me/inbox/summary',
      countKey: 'unread_count',
    },
  ];
  if (featureEnabled(me, 'questions'))
    items.push({
      label: 'Questions',
      href: '/questions.html',
      icon: 'bi-chat-left-text',
      summaryEndpoint: me?.is_admin
        ? '/api/v1/admin/questions/summary'
        : '/api/v1/me/questions/summary',
      countKey: me?.is_admin ? 'open_count' : 'unread_count',
      ws: true,
    });
  if (me?.is_admin && featureEnabled(me, 'evidence'))
    items.push({
      label: 'Suggested rules',
      href: '/admin.html#rule-suggestions',
      icon: 'bi-lightbulb',
      summaryEndpoint: '/api/v1/admin/rule-suggestions/summary',
      countKey: 'open_count',
    });
  return items;
}

async function initialiseNavbar(...args) {
  initContextHelp();
  const cfg = getUiConfig(args[0]);
  // A design system with bell menus lists `items` among its bell defaults; an
  // older one keeps the questions bell that summaryEndpoint describes.
  const menus = Object.prototype.hasOwnProperty.call(cfg.bell || {}, 'items');
  const me = await _mospInitNavbar({
    ...cfg,
    links: withStoriesLink(cfg.links),
    auth: { ...cfg.auth, meCacheMs: 0 },
    bell: {
      ...cfg.bell,
      summaryEndpoint: (me) =>
        featureEnabled(me, 'questions')
          ? me?.is_admin
            ? '/api/v1/admin/questions/summary'
            : '/api/v1/me/questions/summary'
          : '',
      ...(menus ? { title: 'Notifications', items: bellItems } : {}),
    },
    brandIcon: '/keen-mitigator-64.png',
    nav: {
      ...cfg.nav,
      expandClass: 'navbar-expand-lg',
      extraHtml: (me) =>
        me?.can_manage_audits || me?.is_admin
          ? '<a class="btn btn-sm btn-light me-2" data-nav-fw="1" data-base-href="/audit-wizard.html" href="/audit-wizard.html"><i class="bi bi-clipboard2-plus me-1" aria-hidden="true"></i><span class="nav-label-optional">Start audit</span></a>'
          : '',
    },
    framework: { ...cfg.framework, selectMaxChars: 19, selectMinChars: 14 },
  });
  // Keen logout mutates authentication state and therefore uses a CSRF POST.
  const logoutButton = document.getElementById('navLogout');
  const logoutUrl = String(me?.logout_url || '').trim();
  if (logoutButton && (!logoutUrl || logoutUrl === '/api/v1/auth/oidc/logout')) {
    logoutButton.addEventListener(
      'click',
      async (event) => {
        event.preventDefault();
        event.stopImmediatePropagation();
        logoutButton.disabled = true;
        try {
          const result = await apiPost(logoutUrl || '/api/v1/auth/logout', {});
          location.href = result?.logout_url || '/login.html';
        } catch {
          logoutButton.textContent = 'Retry logout';
          logoutButton.title = 'Logout failed. Please try again.';
          logoutButton.disabled = false;
        }
      },
      { capture: true },
    );
  }
  const navRoot = document.getElementById('navbar');
  if (navRoot && !navRoot.querySelector('[data-keen-documentation]')) {
    const list = navRoot.querySelector('.navbar-nav');
    const link = document.createElement('a');
    link.href = '/help/';
    link.textContent = 'Help';
    link.dataset.helpTopic = 'page';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.setAttribute('aria-haspopup', 'dialog');
    link.className = 'nav-link';
    link.dataset.keenDocumentation = '1';
    if (list) {
      const item = document.createElement('li');
      item.className = 'nav-item';
      item.append(link);
      list.append(item);
    } else navRoot.append(link);
  }
  try {
    await refreshFrameworkNavigation(getLoadedFrameworkCatalog(), cfg);
  } catch {
    // Optional navigation stays hidden when the catalogue cannot be checked:
    // refreshFrameworkNavigation hides the Clauses links before it reads it.
  }
  _removeHomeNavItem();
  _patchRiskNavDropdown();
  _patchIsmsNavDropdown();
  _collapseFilterSectionsByDefault();
  setTimeout(_collapseFilterSectionsByDefault, 0);
  applyFeatures(me);
  return me;
}

const RICH_TEXT_ALLOWED_TAGS = new Set([
  'a',
  'blockquote',
  'br',
  'code',
  'div',
  'em',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'li',
  'ol',
  'p',
  'pre',
  's',
  'strike',
  'strong',
  'u',
  'ul',
]);
const RICH_TEXT_VOID_TAGS = new Set(['br', 'hr']);
const _richTextEditors = new WeakMap();
const _pendingRichText = new Map();

export function isRichTextHtmlLike(value) {
  return /<\/?(?:p|br|hr|h[1-6]|ul|ol|li|strong|em|u|s|strike|a|blockquote|pre|code|div)\b/i.test(
    String(value),
  );
}

function _safeRichTextHref(href) {
  const raw = String(href || '').trim();
  if (!raw || /[\\\u0000-\u001f\u007f]/.test(raw) || raw.startsWith('//')) return '';
  if (raw.startsWith('/') || raw.startsWith('#')) return raw;
  try {
    const parsed = new URL(raw, window.location.origin);
    if (['http:', 'https:', 'mailto:', 'tel:'].includes(parsed.protocol)) return raw;
  } catch {}
  return '';
}

export function sanitizeRichTextHtml(source) {
  if (typeof document === 'undefined') return '';
  const template = document.createElement('template');
  template.innerHTML = String(source || '');

  const cleanNode = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent);
    if (node.nodeType !== Node.ELEMENT_NODE) return document.createTextNode('');
    const tag = node.tagName.toLowerCase();
    const fragment = document.createDocumentFragment();
    if (!RICH_TEXT_ALLOWED_TAGS.has(tag)) {
      for (const child of Array.from(node.childNodes)) fragment.appendChild(cleanNode(child));
      return fragment;
    }
    const el = document.createElement(tag === 'strike' ? 's' : tag);
    if (tag === 'a') {
      const href = _safeRichTextHref(node.getAttribute('href'));
      if (href) {
        el.setAttribute('href', href);
        if (/^https?:/i.test(href)) {
          el.setAttribute('target', '_blank');
          el.setAttribute('rel', 'noopener noreferrer');
        }
      }
    }
    if (!RICH_TEXT_VOID_TAGS.has(tag)) {
      for (const child of Array.from(node.childNodes)) el.appendChild(cleanNode(child));
    }
    return el;
  };

  const out = document.createElement('div');
  for (const child of Array.from(template.content.childNodes)) out.appendChild(cleanNode(child));
  return out.innerHTML.trim();
}

// Rich-text fields hold sanitised HTML. A value without any markup is plain
// text: it is escaped and shown as paragraphs, with its line breaks kept.
export function plainTextToRichTextHtml(text, emptyHtml = '') {
  const source = String(text || '')
    .replace(/\r\n?/g, '\n')
    .trim();
  if (!source) return emptyHtml;
  return source
    .split(/\n{2,}/)
    .map((paragraph) => `<p>${esc(paragraph.trim()).replaceAll('\n', '<br>')}</p>`)
    .join('\n');
}

export function richTextEditorValue(raw) {
  const value = String(raw || '').trim();
  if (!value) return '';
  // HTML-like values reach editable.innerHTML in setRichTextEditorValue, so
  // they pass through the shared allowlist sanitiser first; plain text is
  // escaped while it is wrapped and needs no extra pass. Top-level text then
  // goes into paragraphs, which Wysi leaves as they are (see below).
  if (!isRichTextHtmlLike(value)) return plainTextToRichTextHtml(value);
  const template = document.createElement('template');
  template.innerHTML = sanitizeRichTextHtml(value);
  _wrapTopLevelText(template.content);
  return template.innerHTML.trim();
}

export function renderRichTextContent(raw, emptyHtml = '<span class="small-muted">-</span>') {
  const value = String(raw || '').trim();
  if (!value) return emptyHtml;
  const html = isRichTextHtmlLike(value)
    ? sanitizeRichTextHtml(value)
    : plainTextToRichTextHtml(value, emptyHtml);
  return html || emptyHtml;
}

export function plainTextFromRichText(value) {
  const text = String(value || '')
    .replace(/<\/?(?:p|div|h[1-6]|li|ul|ol|blockquote|br|hr)\b[^>]*>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    // The ampersand entity is decoded last so an escaped entity such as
    // &amp;lt; stays the literal text &lt; instead of becoming markup.
    .replace(/&amp;/gi, '&')
    .replace(/\s+/g, ' ')
    .trim();
  return text;
}

function _richTextEditable(textarea) {
  const holder = textarea?.closest?.('.rich-text-editor');
  return holder?.querySelector?.('[contenteditable]') || null;
}

export function syncRichTextEditor(textarea) {
  if (!textarea) return '';
  if (_pendingRichText.has(textarea)) return _pendingRichText.get(textarea);
  const editable = _richTextEditable(textarea);
  if (editable) textarea.value = editable.innerHTML;
  return textarea.value || '';
}

export function syncRichTextEditors(root = document) {
  root
    ?.querySelectorAll?.('textarea[data-rich-text]')
    .forEach((textarea) => syncRichTextEditor(textarea));
}

export function refreshRichTextEditorStates(root = document) {
  root?.querySelectorAll?.('textarea[data-rich-text]').forEach((textarea) => {
    const holder = textarea.closest('.rich-text-editor');
    const editable = _richTextEditable(textarea);
    const readOnly = Boolean(textarea.disabled || textarea.readOnly);
    holder?.setAttribute('data-rich-text-readonly', readOnly ? '1' : '0');
    if (editable) {
      editable.setAttribute('contenteditable', readOnly ? 'false' : 'true');
      editable.setAttribute('aria-readonly', readOnly ? 'true' : 'false');
    }
    holder?.querySelectorAll?.('button, select, input').forEach((el) => {
      if (el.closest('[contenteditable]')) return;
      el.disabled = readOnly;
      el.tabIndex = readOnly ? -1 : 0;
    });
  });
}

// Wysi parses the content it is given unsafely: it wraps top-level text in
// paragraphs by assigning the text as HTML, so escaped markup in a saved value
// (or in pasted HTML) becomes live elements. It does this to the content it is
// given at the start, to pasted HTML, and to the editor's own content after
// every edit. Every editor therefore starts from an empty textarea, receives
// its content only through setRichTextEditorValue and the shared sanitiser,
// has pasted HTML sanitised here before Wysi's own paste handler can see it,
// and keeps all top-level text inside paragraphs: when content is loaded, and
// before Wysi reads each edit. This is the only place that starts Wysi.
//
// Wysi builds its editor once the document has loaded, so a value set before
// then waits in _pendingRichText, and the textarea stays empty until the
// editor exists.

function _applyPendingRichText() {
  for (const [textarea, value] of _pendingRichText) {
    if (!_richTextEditable(textarea)) continue;
    _pendingRichText.delete(textarea);
    setRichTextEditorValue(textarea, value);
    const holder = textarea.closest('.rich-text-editor');
    if (holder?.parentElement) refreshRichTextEditorStates(holder.parentElement);
  }
}

function _pasteIntoRichText(event) {
  const editable = event.target?.closest?.('.rich-text-editor [contenteditable]');
  const data = event.clipboardData;
  if (!editable || !data?.types?.includes?.('text/html')) return;
  event.preventDefault();
  event.stopPropagation();
  const html = sanitizeRichTextHtml(data.getData('text/html'));
  if (html) document.execCommand('insertHTML', false, html);
  else document.execCommand('insertText', false, data.getData('text/plain'));
}

// Top-level elements Wysi leaves in place, with the other containers a
// paragraph cannot hold. Text and phrasing elements between them form runs
// that Wysi would re-parse as HTML, so each run is moved into a paragraph.
const _RICH_TEXT_CONTAINERS = new Set([
  'BLOCKQUOTE',
  'DIV',
  'H1',
  'H2',
  'H3',
  'H4',
  'H5',
  'H6',
  'HR',
  'LI',
  'OL',
  'P',
  'PRE',
  'TABLE',
  'UL',
]);

function _inTopLevelRun(node) {
  if (node.nodeType === Node.TEXT_NODE) return true;
  return node.nodeType === Node.ELEMENT_NODE && !_RICH_TEXT_CONTAINERS.has(node.tagName);
}

// Moves each top-level run holding text or a phrasing element into a <p>,
// as nodes: nothing is parsed, so escaped markup stays text. Runs of only
// white space between blocks are left alone. Returns whether anything moved.
function _wrapTopLevelText(root) {
  const runs = [];
  let run = [];
  for (const node of Array.from(root.childNodes)) {
    if (_inTopLevelRun(node)) {
      run.push(node);
      continue;
    }
    runs.push(run);
    run = [];
  }
  runs.push(run);
  let moved = false;
  for (const nodes of runs) {
    const visible = nodes.some(
      (node) => node.nodeType === Node.ELEMENT_NODE || node.textContent.trim(),
    );
    if (!visible) continue;
    const paragraph = root.ownerDocument.createElement('p');
    nodes[0].before(paragraph);
    paragraph.append(...nodes);
    moved = true;
  }
  return moved;
}

// A selection boundary as a node and offset that survive _wrapTopLevelText:
// a boundary between the editable's children is held as the child after it.
function _selectionPoint(editable, node, offset) {
  if (node !== editable) return { node, offset };
  return { after: editable.childNodes[offset] || null };
}

function _restoreSelectionPoint(editable, point) {
  if (!('after' in point)) return [point.node, point.offset];
  const after = point.after;
  if (!after) return [editable, editable.childNodes.length];
  const parent = after.parentNode;
  return [parent, Array.prototype.indexOf.call(parent.childNodes, after)];
}

// Runs in the capturing phase at the document, before Wysi's bubbling input
// listener reads the editor's content and re-parses its top-level text.
function _keepRichTextInParagraphs(event) {
  const editable = event.target?.closest?.('.rich-text-editor [contenteditable]');
  if (!editable || event.isComposing) return;
  const selection = editable.ownerDocument.getSelection?.();
  const inside =
    selection?.rangeCount && editable.contains(selection.anchorNode)
      ? [
          _selectionPoint(editable, selection.anchorNode, selection.anchorOffset),
          _selectionPoint(editable, selection.focusNode, selection.focusOffset),
        ]
      : null;
  if (!_wrapTopLevelText(editable) || !inside) return;
  const [anchor, focus] = inside.map((point) => _restoreSelectionPoint(editable, point));
  selection.setBaseAndExtent(...anchor, ...focus);
}

let _richTextPasteGuarded = false;

export function initRichTextEditors(root = document, opts = {}) {
  if (typeof window === 'undefined' || typeof window.Wysi !== 'function') return [];
  if (!_richTextPasteGuarded) {
    // Capturing at the document runs before Wysi's bubbling paste handler,
    // which stopping propagation here then skips.
    document.addEventListener('paste', _pasteIntoRichText, true);
    // Likewise before Wysi's bubbling input handler re-parses each edit.
    document.addEventListener('input', _keepRichTextInParagraphs, true);
    _richTextPasteGuarded = true;
  }
  const textareas = Array.from(root?.querySelectorAll?.('textarea[data-rich-text]') || []);
  const initialized = [];
  for (const textarea of textareas) {
    if (_richTextEditors.has(textarea)) continue;
    textarea.setAttribute('data-wysi-fallback', '1');
    const saved = textarea.value || '';
    textarea.value = '';
    try {
      window.Wysi({
        el: textarea,
        height: opts.height || Number(textarea.dataset.richTextHeight || 260),
        autoGrow: opts.autoGrow ?? true,
        autoHide: opts.autoHide ?? false,
        tools: opts.tools || [
          'format',
          '|',
          'bold',
          'italic',
          'underline',
          'strike',
          '|',
          'ul',
          'ol',
          '|',
          'link',
          'hr',
          'quote',
          '|',
          'removeFormat',
        ],
        // Wysi reports its own re-parsed copy of the content; the form
        // keeps what the editor shows, through the shared sanitiser.
        onChange: () => {
          textarea.value = sanitizeRichTextHtml(_richTextEditable(textarea)?.innerHTML || '');
          opts.onChange?.(textarea, textarea.value);
        },
      });
      _richTextEditors.set(textarea, true);
      textarea.closest('.rich-text-editor')?.setAttribute('data-rich-text-ready', '1');
      setRichTextEditorValue(textarea, saved);
      if (document.readyState === 'loading') {
        // A listener of its own, registered after the one Wysi has just added,
        // so it runs once this editor exists.
        document.addEventListener('DOMContentLoaded', () => _applyPendingRichText(), {
          once: true,
        });
      }
      initialized.push(textarea);
    } catch (e) {
      _pendingRichText.delete(textarea);
      textarea.value = saved;
      console.warn('Failed to initialise rich text editor', e);
    }
  }
  refreshRichTextEditorStates(root);
  return initialized;
}

export function setRichTextEditorValue(textareaOrId, value) {
  const textarea =
    typeof textareaOrId === 'string' ? document.getElementById(textareaOrId) : textareaOrId;
  if (!textarea) return;
  const html = richTextEditorValue(value);
  const editable = _richTextEditable(textarea);
  if (_richTextEditors.has(textarea) && !editable) {
    _pendingRichText.set(textarea, html);
    textarea.value = '';
    return;
  }
  _pendingRichText.delete(textarea);
  textarea.value = html;
  if (editable) editable.innerHTML = html;
}

function _hexToRgb(hex) {
  const raw = String(hex).trim().replace(/^#/, '');
  const h =
    raw.length === 3
      ? raw
          .split('')
          .map((c) => c + c)
          .join('')
      : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  const n = parseInt(h, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function _srgbToLinear(v) {
  const x = v / 255;
  return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
}

// W3C relative luminance (0..1) of an { r, g, b } colour with 0-255 channels.
export function relativeLuminance({ r, g, b }) {
  const R = _srgbToLinear(r);
  const G = _srgbToLinear(g);
  const B = _srgbToLinear(b);
  return 0.2126 * R + 0.7152 * G + 0.0722 * B;
}

// Returns a readable text colour for the given background.
export function idealTextColor(bgHex) {
  const rgb = _hexToRgb(bgHex);
  if (!rgb) return '#111827'; // slate-900
  const lum = relativeLuminance(rgb);
  // Threshold chosen to keep badges readable across our theme palette.
  return lum > 0.55 ? '#111827' : '#f8fafc'; // slate-900 / slate-50
}

export function userDisplayName(userOrName, fallback = '') {
  if (userOrName === null || userOrName === undefined) return String(fallback || '').trim();
  if (typeof userOrName === 'string' || typeof userOrName === 'number')
    return String(userOrName || fallback || '').trim();
  const user =
    userOrName.user && typeof userOrName.user === 'object' ? userOrName.user : userOrName;
  const fields = [
    user.username,
    user.name,
    user.display_name,
    user.full_name,
    user.email,
    user.label,
    user.display,
    user.changed_by_username,
    user.created_by_username,
    user.author_username,
    user.added_by_username,
  ];
  for (const field of fields) {
    const value = String(field || '').trim();
    if (value) return value;
  }
  return String(fallback || '').trim();
}

export function userPillHtml(
  userOrName,
  { empty = '-', className = '', title = '', prefix = '' } = {},
) {
  const label = userDisplayName(userOrName);
  if (!label) return `<span class="small-muted">${esc(empty || '-')}</span>`;
  const user = userOrName && typeof userOrName === 'object' ? userOrName.user || userOrName : null;
  const tip = String(title || user?.email || label).trim();
  const cls = ['badge', 'badge-soft', 'user-pill', className].filter(Boolean).join(' ');
  return `<span class="${esc(cls)}"${tip ? ` title="${esc(tip)}"` : ''}>${esc(prefix || '')}${esc(label)}</span>`;
}

export function userPillsHtml(items = [], opts = {}) {
  const rows = Array.isArray(items) ? items : items ? [items] : [];
  const html = rows
    .filter((item) => userDisplayName(item))
    .map((item) => userPillHtml(item, opts))
    .join('');
  if (!html) return `<span class="small-muted">${esc(opts.empty || '-')}</span>`;
  return `<span class="user-pill-list">${html}</span>`;
}

export const ACTIVE_AUDIT_STATUSES = new Set(['open', 'in_progress']);
export const LOCKED_AUDIT_STATUSES = new Set(['completed', 'archived']);

export function auditStatusValue(auditOrStatus = null) {
  const raw =
    auditOrStatus && typeof auditOrStatus === 'object' ? auditOrStatus.status : auditOrStatus;
  return String(raw || 'open')
    .trim()
    .toLowerCase();
}

export function isAuditActive(auditOrStatus = null) {
  return ACTIVE_AUDIT_STATUSES.has(auditStatusValue(auditOrStatus));
}

export function isAuditLocked(auditOrStatus = null) {
  return LOCKED_AUDIT_STATUSES.has(auditStatusValue(auditOrStatus));
}

export function auditStatusLabel(auditOrStatus = null) {
  const v = auditStatusValue(auditOrStatus);
  if (v === 'in_progress') return 'In progress';
  return v.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase());
}

export function canSampleIntoAudit(me = null, entityType = '') {
  if (!featureEnabled(me, 'sampling')) return false;
  if ((!entityType || entityType === 'event') && !featureEnabled(me, 'event_sampling'))
    return false;
  if (!(me?.can_manage_audits || me?.can_admin_audits || me?.is_admin)) return false;
  if (me?.is_admin) return true;
  const type = String(entityType || '')
    .trim()
    .toLowerCase();
  if (type === 'isms_swot') return Boolean(me?.can_view_isms);
  if (type === 'story') return Boolean(me?.can_view_stories);
  if (type === 'event') return Boolean(me?.can_view_events);
  if (type === 'bookstack_section' || type.startsWith('isms')) return Boolean(me?.can_sample_isms);
  return true;
}

// The audit an auditor is gathering evidence for, remembered in this browser
// tab so each sampling dialog starts with it. A convenience only: the dialog
// still lists every audit the account can sample into.
const SAMPLING_AUDIT_KEY = 'keen.samplingAudit';

export function rememberSamplingAudit(auditId) {
  try {
    sessionStorage.setItem(SAMPLING_AUDIT_KEY, String(auditId || ''));
  } catch {
    // Without storage each dialog starts with the first audit.
  }
}

// Selects the remembered audit in a list of audit choices when it is there.
export function preselectSamplingAudit(select) {
  let remembered = '';
  try {
    remembered = sessionStorage.getItem(SAMPLING_AUDIT_KEY);
  } catch {
    return;
  }
  if (remembered && [...select.options].some((option) => option.value === remembered))
    select.value = remembered;
}

export function canEditAuditForUser(me = null, audit = null) {
  if (!audit) return false;
  if (me?.is_admin || me?.can_admin_audits) return true;
  return Boolean(
    me?.can_manage_audits && me?.user_id && String(me.user_id) === String(audit.created_by_user_id),
  );
}

function _sampleCurrentRelativeUrl() {
  return `${window.location.pathname}${window.location.search}`;
}

function _ensureEntityAuditSampleModal() {
  let el = document.getElementById('entityAuditSampleModal');
  if (el) return el;
  const wrap = document.createElement('div');
  wrap.innerHTML = `
    <div class="modal fade" id="entityAuditSampleModal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-lg modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title" id="entityAuditSampleTitle">Sample item into audit</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <div id="entityAuditSampleStatus" class="alert alert-info" style="display:none;" role="status"></div>
            <div class="mb-3">
              <label class="form-label small-muted mb-1" for="entityAuditSampleSelect">Audit</label>
              <select id="entityAuditSampleSelect" class="form-select"></select>
              <div class="form-text">Only Open and In progress audits can receive sampled items. Start an Audit if none are available.</div>
            </div>
            <div class="mb-3">
              <label class="form-label small-muted mb-1" for="entityAuditSampleNotes">Audit notes for this sampled item</label>
              <textarea id="entityAuditSampleNotes" class="form-control" rows="4" placeholder="Why this item was sampled, what was verified, screenshots taken, etc."></textarea>
            </div>
            <div class="form-check">
              <input id="entityAuditSampleUseCurrentUrl" class="form-check-input" type="checkbox" checked>
              <label class="form-check-label" for="entityAuditSampleUseCurrentUrl">Store the current page URL as the evidence reference</label>
            </div>
          </div>
          <div class="modal-footer">
            <a id="entityAuditSampleCreate" class="btn btn-outline-primary me-auto" href="/audit-wizard.html">Start an audit</a>
            <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
            <button id="entityAuditSampleSubmit" type="button" class="btn btn-primary">Sample into audit</button>
          </div>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrap.firstElementChild);
  return document.getElementById('entityAuditSampleModal');
}

function _entityAuditSampleEls() {
  return {
    modal: _ensureEntityAuditSampleModal(),
    title: document.getElementById('entityAuditSampleTitle'),
    status: document.getElementById('entityAuditSampleStatus'),
    select: document.getElementById('entityAuditSampleSelect'),
    notes: document.getElementById('entityAuditSampleNotes'),
    useUrl: document.getElementById('entityAuditSampleUseCurrentUrl'),
    create: document.getElementById('entityAuditSampleCreate'),
    submit: document.getElementById('entityAuditSampleSubmit'),
  };
}

function _sampleFramework(framework = '') {
  if (framework) return framework;
  return new URLSearchParams(window.location.search).get('framework');
}

function _sampleAuditsUrl(framework = '') {
  const fw = _sampleFramework(framework);
  const url = new URL('/api/v1/audits', window.location.origin);
  if (fw) url.searchParams.set('framework', fw);
  url.searchParams.set('limit', '500');
  return url.pathname + url.search;
}

function _sampleCreateAuditUrl(framework = '') {
  const fw = _sampleFramework(framework);
  const url = new URL('/audit-wizard.html', window.location.origin);
  if (fw) url.searchParams.set('framework', fw);
  return url.pathname + url.search;
}

export async function openAuditSampleModal({
  me = null,
  entityType = '',
  entityId = '',
  title = '',
  evidenceUrl = null,
  framework = '',
  statusEl = null,
  notesPlaceholder = '',
  onSampled = null,
} = {}) {
  if (!canSampleIntoAudit(me, entityType)) {
    toast(statusEl, 'audits.manage permission required.', 'danger');
    return;
  }
  const cleanEntityType = String(entityType || '').trim();
  const cleanEntityId = String(entityId || '').trim();
  if (!cleanEntityType || !cleanEntityId) {
    toast(statusEl, 'Save this item before sampling it into an audit.', 'warning');
    return;
  }

  const els = _entityAuditSampleEls();
  if (els.title)
    els.title.textContent = title ? `Sample “${title}” into audit` : 'Sample item into audit';
  if (els.status) els.status.style.display = 'none';
  if (els.notes) {
    els.notes.value = '';
    els.notes.disabled = false;
    els.notes.placeholder =
      notesPlaceholder || 'Why this item was sampled, what was verified, screenshots taken, etc.';
  }
  if (els.useUrl) {
    els.useUrl.checked = true;
    els.useUrl.disabled = false;
  }
  if (els.create) els.create.href = _sampleCreateAuditUrl(framework);
  if (els.submit) els.submit.disabled = false;
  if (els.select) {
    els.select.innerHTML = '<option value="">Loading audits</option>';
    try {
      const data = await apiGet(_sampleAuditsUrl(framework));
      const items = data.items.filter((a) => isAuditActive(a) && canEditAuditForUser(me, a));
      els.select.innerHTML =
        items
          .map((a) => {
            const dates = [a.start_date, a.end_date].filter(Boolean).join(' → ');
            const suffix = dates ? ` · ${dates}` : '';
            const statusSuffix =
              auditStatusValue(a) === 'in_progress' ? ' · In progress' : ' · Open';
            return `<option value="${esc(a.id)}">${esc(a.title || 'Audit')}${esc(suffix)}${esc(statusSuffix)}</option>`;
          })
          .join('') || '<option value="">No open or in-progress audits - start one first</option>';
      preselectSamplingAudit(els.select);
      const disabled = !items.length;
      if (els.submit) els.submit.disabled = disabled;
      if (els.notes) els.notes.disabled = disabled;
      if (els.useUrl) els.useUrl.disabled = disabled;
      if (disabled)
        toast(
          els.status,
          'No open or in-progress audits are available. Start an Audit first, then sample this item into it.',
          'warning',
        );
    } catch (e) {
      els.select.innerHTML = '<option value="">Failed to load audits</option>';
      if (els.submit) els.submit.disabled = true;
      if (els.notes) els.notes.disabled = true;
      if (els.useUrl) els.useUrl.disabled = true;
      toast(els.status, `Failed to load audits: ${errorText(e)}`, 'danger');
    }
  }

  const submitHandler = async () => {
    const auditId = (els.select?.value || '').trim();
    if (!auditId) {
      toast(els.status, 'Start an Audit first, then sample this item into it.', 'warning');
      return;
    }
    const btn = document.getElementById('entityAuditSampleSubmit');
    const prev = btn.textContent;
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Sampling';
    }
    try {
      const body = {
        entity_type: cleanEntityType,
        entity_id: cleanEntityId,
        evidence_url: els.useUrl?.checked ? evidenceUrl || _sampleCurrentRelativeUrl() : null,
        title: title || 'Keen item',
        notes: els.notes?.value || '',
      };
      await apiPost(`/api/v1/audits/${encodeURIComponent(auditId)}/evidence`, body);
      rememberSamplingAudit(auditId);
      toast(statusEl, 'Item sampled into audit', 'success');
      if (window.bootstrap?.Modal && els.modal) {
        window.bootstrap.Modal.getOrCreateInstance(els.modal).hide();
      }
      if (typeof onSampled === 'function') await onSampled(auditId);
    } catch (e) {
      toast(els.status, `Failed: ${errorText(e)}`, 'danger');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = prev;
      }
    }
  };

  if (els.submit) {
    els.submit.replaceWith(els.submit.cloneNode(true));
    const fresh = document.getElementById('entityAuditSampleSubmit');
    fresh?.addEventListener('click', submitHandler);
  }
  if (window.bootstrap?.Modal && els.modal) {
    window.bootstrap.Modal.getOrCreateInstance(els.modal).show();
  }
}

export function loadingSpinnerHtml(message = 'Loading data', { small = true } = {}) {
  const msg = esc(message || 'Loading data');
  const spinnerClass = small ? 'spinner-border spinner-border-sm' : 'spinner-border';
  return `<div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted">
    <span class="${spinnerClass}" role="status" aria-hidden="true"></span>
    <span>${msg}</span>
  </div>`;
}

export function tableLoadingHtml(colspan = 1, message = 'Loading data') {
  const n = Math.max(1, parseInt(colspan, 10) || 1);
  return `<tr class="table-loading-row"><td colspan="${n}" class="p-4">${loadingSpinnerHtml(message, { small: true })}</td></tr>`;
}

export function showTableLoading(tbody, colspan = 1, message = 'Loading data') {
  if (!tbody) return;
  tbody.innerHTML = tableLoadingHtml(colspan, message);
}

export function showInlineLoading(el, message = 'Loading data') {
  if (!el) return;
  el.innerHTML = loadingSpinnerHtml(message, { small: true });
}

export function arrowLinkHtml(
  url,
  { className = 'btn btn-sm btn-outline-secondary', title = 'Open link' } = {},
) {
  const safe = safeExternalHref(url);
  if (!safe) return '';
  const label = title || 'Open link';
  return `<a class="${esc(className)}" href="${esc(safe)}" target="_blank" rel="noopener noreferrer" title="${esc(label)}" aria-label="${esc(label)}"><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i></a>`;
}

/**
 * Validate a link before placing it into an href.
 *
 * Same-origin paths are allowed, but browsers read a backslash as "/" and drop
 * tabs and newlines while parsing, so "/" followed by either can leave Keen.
 * HTML escaping does not make a URL safe; use this before esc().
 */
export function safeExternalHref(url) {
  const raw = String(url ?? '').trim();
  if (!raw || raw.length > 2048) return '';
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return '';
  if (raw.startsWith('/')) return localPath(raw);
  try {
    const parsed = new URL(raw);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.href;
  } catch {
    // Fall through to blank.
  }
  return '';
}

export function safeAbsoluteHttpHref(url) {
  const raw = String(url || '').trim();
  if (!raw || raw.length > 2048) return '';
  // Upstream evidence links must be actual absolute web URLs.  Do not turn
  // payload values such as /home/miguel into same-origin Keen links.
  if (!/^https?:\/\//i.test(raw)) return '';
  try {
    const parsed = new URL(raw);
    const scheme = parsed.protocol.toLowerCase();
    if ((scheme === 'http:' || scheme === 'https:') && parsed.hostname) return raw;
  } catch {
    // Fall through to blank.
  }
  return '';
}

export function titledLinksHtml(
  items,
  {
    className = 'link-primary',
    title = 'Open link',
    empty = '',
    itemClass = 'd-inline-flex align-items-center gap-2 me-2 mb-1',
  } = {},
) {
  const rows = Array.isArray(items) ? items : items ? [items] : [];
  const links = rows
    .map((item, idx) => {
      const url = typeof item === 'string' ? item : item?.url || item?.href || item?.link;
      const safe = safeExternalHref(url);
      if (!safe) return '';
      const label = String(item.title || item.label || item.name || url).trim();
      const linkTitle = rows.length > 1 ? `${title} ${idx + 1}` : title;
      return `<span class="${esc(itemClass)}"><a class="${esc(className)} text-break" href="${esc(safe)}" target="_blank" rel="noopener noreferrer" title="${esc(linkTitle)}">${esc(label)}</a></span>`;
    })
    .filter(Boolean);
  if (!links.length) return empty;
  return `<div class="d-flex flex-wrap align-items-center gap-1">${links.join('')}</div>`;
}

export function upstreamLinkHtml(
  url,
  { className = 'btn btn-sm btn-outline-secondary', title = 'Open upstream link' } = {},
) {
  const safe = safeAbsoluteHttpHref(url);
  if (!safe) return '';
  return arrowLinkHtml(safe, { className, title });
}

// A page's tab for a name taken from the address. Only the map's own keys
// count, so a link such as ?tab=constructor gets the fallback rather than an
// inherited property.
export function lookupTab(tabs, name, fallback = null) {
  return (Object.hasOwn(tabs, name) && tabs[name]) || fallback;
}

// Fetch the framework catalog for the navigation's framework picker, the
// account page's default framework and the risk wizard.
export async function getFrameworkCatalog(cfgOverrides = null) {
  const cfg = getUiConfig(cfgOverrides);
  const ep = cfg?.framework?.endpoint || '/api/v1/frameworks';
  return apiGet(ep, cfgOverrides);
}

// The pager buttons above and below a paged list, marked with data-pager.
export function collectOffsetPagerButtons() {
  return {
    prev: [...document.querySelectorAll('[data-pager="prev"]')],
    next: [...document.querySelectorAll('[data-pager="next"]')],
  };
}

export function offsetPagerState({ total = 0, offset = 0, limit = 50 } = {}) {
  const safeTotal = Math.max(0, Number(total || 0));
  const safeLimit = Math.max(1, Number(limit || 50));
  const safeOffset = Math.max(0, Number(offset || 0));
  const start = safeTotal ? Math.min(safeOffset + 1, safeTotal) : 0;
  const end = Math.min(safeTotal, safeOffset + safeLimit);
  const prevOffset = Math.max(0, safeOffset - safeLimit);
  const nextOffset = safeOffset + safeLimit;
  return {
    total: safeTotal,
    limit: safeLimit,
    offset: safeOffset,
    start,
    end,
    prevOffset,
    nextOffset,
    prevDisabled: safeOffset <= 0,
    nextDisabled: nextOffset >= safeTotal,
  };
}

export function setOffsetPagerDisabled(pager, state = {}) {
  const s = offsetPagerState(state);
  const prev = Array.isArray(pager?.prev) ? pager.prev : [];
  const next = Array.isArray(pager?.next) ? pager.next : [];
  for (const btn of prev) btn.disabled = s.prevDisabled;
  for (const btn of next) btn.disabled = s.nextDisabled;
  return s;
}

export function wireOffsetPagerButtons(pager, onPage) {
  const prev = Array.isArray(pager?.prev) ? pager.prev : [];
  const next = Array.isArray(pager?.next) ? pager.next : [];
  for (const btn of prev) {
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      if (btn.disabled) return;
      onPage?.('prev', btn, ev);
    });
  }
  for (const btn of next) {
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      if (btn.disabled) return;
      onPage?.('next', btn, ev);
    });
  }
}

// A progress ring in the theme accent, described for screen readers. The
// caller's class sizes it.
export function progressRing(percent, label, className = '') {
  const clamped = Math.min(Math.max(Number(percent) || 0, 0), 100);
  const radius = 18;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped / 100);
  return (
    `<svg class="keen-ring ${esc(className)}" viewBox="0 0 44 44" role="img" aria-label="${esc(label)}">` +
    `<circle class="keen-ring-track" cx="22" cy="22" r="${radius}"/>` +
    `<circle class="keen-ring-value" cx="22" cy="22" r="${radius}" stroke-linecap="round" stroke-dasharray="${circumference.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}" transform="rotate(-90 22 22)"/></svg>`
  );
}

export function effectivenessMetricThresholdState(measure, entry) {
  if (!measure || !entry) return null;
  const op = String(measure.threshold_operator || '').trim();
  if (!['lt', 'lte', 'eq', 'gte', 'gt'].includes(op)) return null;
  if (entry.metric_value === null || entry.metric_value === undefined || entry.metric_value === '')
    return null;
  if (
    measure.target_value === null ||
    measure.target_value === undefined ||
    measure.target_value === ''
  )
    return null;
  const value = Number(entry.metric_value);
  const target = Number(measure.target_value);
  if (!Number.isFinite(value) || !Number.isFinite(target)) return null;
  let ok = false;
  if (op === 'lt') ok = value < target;
  if (op === 'lte') ok = value <= target;
  if (op === 'eq') ok = Math.abs(value - target) < 1e-9;
  if (op === 'gte') ok = value >= target;
  if (op === 'gt') ok = value > target;
  const symbol = { lt: '<', lte: '≤', eq: '=', gte: '≥', gt: '>' }[op];
  const unit = String(measure.target_unit || entry.metric_unit || '').trim();
  const targetText = `${symbol} ${target}${unit ? ` ${unit}` : ''}`;
  return {
    ok,
    state: ok ? 'ok' : 'bad',
    label: ok ? 'Meets target' : 'Outside target',
    title: `Metric value ${value}${unit ? ` ${unit}` : ''} ${ok ? 'satisfies' : 'does not satisfy'} target ${targetText}`,
  };
}

export function effectivenessMetricValueHtml(
  entry,
  measure,
  { href = '', fallback = 'Metric entry', className = '' } = {},
) {
  if (!entry) return `<span class="small-muted">${esc(fallback)}</span>`;
  const value =
    entry.value_display ||
    [entry.metric_value ?? '', entry.metric_unit || '', entry.qualitative_value || '']
      .filter(Boolean)
      .join(' ') ||
    fallback;
  const state = effectivenessMetricThresholdState(measure, entry);
  const baseClass = className || 'fw-semibold';
  if (!state) {
    return href
      ? `<a class="${esc(baseClass)}" href="${esc(href)}">${esc(value)}</a>`
      : `<span class="${esc(baseClass)}">${esc(value)}</span>`;
  }
  const cls = `eff-metric-value eff-metric-value-${state.state} ${baseClass}`;
  return href
    ? `<a class="${esc(cls)}" href="${esc(href)}" title="${esc(state.title)}">${esc(value)}</a>`
    : `<span class="${esc(cls)}" title="${esc(state.title)}">${esc(value)}</span>`;
}

export function effectivenessMetricThresholdBadgeHtml(measure, entry) {
  const state = effectivenessMetricThresholdState(measure, entry);
  if (!state) return '';
  return `<span class="eff-metric-threshold-label eff-metric-threshold-label-${esc(state.state)}" title="${esc(state.title)}">${esc(state.label)}</span>`;
}

function _changelogValueHtml(value) {
  if (value === null || value === undefined || value === '') {
    return '<span class="small-muted">-</span>';
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') {
    let text = '';
    try {
      text = JSON.stringify(value, null, 2);
    } catch {
      text = String(value);
    }
    if (text.length > 1200) text = text.slice(0, 1200) + '…';
    return `<pre class="small bg-light border rounded p-2 mb-0 changelog-json">${esc(text)}</pre>`;
  }
  const text = String(value);
  return `<span class="text-break">${esc(text.length > 1000 ? text.slice(0, 1000) + '…' : text)}</span>`;
}

export function entityChangelogHtml(items = [], { empty = 'No changelog entries yet.' } = {}) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return `<div class="small-muted p-3">${esc(empty)}</div>`;
  return `<div class="list-group list-group-flush">
    ${rows
      .map((entry) => {
        const changes = Array.isArray(entry?.changes) ? entry.changes : [];
        const actor = entry?.changed_by_username || 'system';
        const action = String(entry?.action || '').replaceAll('_', ' ') || 'updated';
        const details = changes.length
          ? `<div class="table-responsive mt-2"><table class="table table-sm align-middle mb-0 changelog-diff-table"><thead><tr><th style="width:180px;">Field</th><th>Before</th><th>After</th></tr></thead><tbody>${changes.map((c) => `<tr><td class="fw-semibold">${esc(c?.label || c?.field)}</td><td>${_changelogValueHtml(c?.before)}</td><td>${_changelogValueHtml(c?.after)}</td></tr>`).join('')}</tbody></table></div>`
          : '<div class="small-muted mt-2">No field-level changes recorded.</div>';
        return `<div class="list-group-item">
        <div class="d-flex flex-wrap gap-2 justify-content-between align-items-start">
          <div>
            <div class="fw-semibold">${esc(entry?.summary || 'Entity changed')}</div>
            <div class="small-muted">${esc(fmtTs(entry?.changed_at))} · ${esc(actor)} · ${esc(action)}</div>
          </div>
          ${entry?.request_path ? `<span class="badge text-bg-light mono">${esc(entry.request_method || '')} ${esc(entry.request_path)}</span>` : ''}
        </div>
        ${details}
      </div>`;
      })
      .join('')}
  </div>`;
}

// Shows a record's changelog in its tab; a failure is reported in the tab.
export async function loadEntityChangelog(
  container,
  url,
  { empty = 'No changelog entries yet.', loading = 'Loading changelog' } = {},
) {
  if (!container) return null;
  container.innerHTML = loadingSpinnerHtml(loading, { small: true });
  try {
    const res = await apiGet(url);
    container.innerHTML = entityChangelogHtml(res?.items, { empty });
    return res;
  } catch {
    container.innerHTML = '<div class="text-danger p-3">Failed to load changelog.</div>';
    return null;
  }
}
