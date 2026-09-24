// Re-export the MOSP design-system helpers.
export * from '/vendor/mosp-design-system/app.js';

// Keen's UI pages historically imported a couple of helpers from /app.js.
// Keep them here so older pages (e.g. account.js) continue to work.
import { apiGet, apiPost, esc, fmtTs, getUiConfig, safeExternalHref, toast, sourceBadgeHtml as _mospSourceBadgeHtml, initNavbar as _mospInitNavbar, initCollapsibleFilterSections as _mospInitCollapsibleFilterSections } from '/vendor/mosp-design-system/app.js';


function _frameworkForHref() {
  try {
    const p = new URLSearchParams(window.location.search || '');
    return p.get('framework') || '';
  } catch {
    return '';
  }
}

function _withCurrentFramework(path) {
  const fw = _frameworkForHref();
  if (!fw) return path;
  try {
    const url = new URL(path, window.location.origin);
    url.searchParams.set('framework', fw);
    return url.pathname + url.search;
  } catch {
    return path;
  }
}


export function sourceBadgeHtml(source, metaMap, href = null) {
  const s = String(source || '').trim();
  const target = href || (s ? `/source.html?source=${encodeURIComponent(s)}` : '');
  return _mospSourceBadgeHtml(source, metaMap, target);
}

function _patchRiskNavDropdown() {
  if (typeof document === 'undefined') return;
  const navRoot = document.getElementById('navbar');
  if (!navRoot || navRoot.dataset.pestleRiskDropdownPatched === '1') return;

  const links = Array.from(navRoot.querySelectorAll('a[href]'));
  const riskLink = links.find((a) => {
    const href = String(a.getAttribute('href') || '');
    const txt = String(a.textContent || '').trim().toLowerCase();
    return href.includes('/risks.html') || txt === 'risks';
  });
  if (!riskLink) return;

  const li = riskLink.closest('li') || riskLink.parentElement;
  if (!li) return;
  li.classList.add('nav-item', 'dropdown');
  li.innerHTML = `
    <a class="nav-link dropdown-toggle" href="#" id="riskAssessmentsDropdown" role="button" data-bs-toggle="dropdown" aria-expanded="false">
      Risks
    </a>
    <ul class="dropdown-menu dropdown-menu-end" aria-labelledby="riskAssessmentsDropdown">
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/risks.html'))}">CIA Triad Risk Assessment</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/risk-register.html'))}">Risk Register &amp; Heatmap</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/pestle.html'))}">PESTLE(E) Impact Assessment</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/interested_parties.html'))}">Interested Parties</a></li>
      <li><a class="dropdown-item d-inline-flex align-items-center gap-2" href="${esc(_withCurrentFramework('/mitigator.html'))}"><img src="/keen-mitigator.svg" alt="" class="keen-mitigator-icon-sm"> Keen Mitigator</a></li>
    </ul>`;
  navRoot.dataset.pestleRiskDropdownPatched = '1';
}

function _patchIsmsNavDropdown() {
  if (typeof document === 'undefined') return;
  const navRoot = document.getElementById('navbar');
  if (!navRoot || navRoot.dataset.ismsDropdownPatched === '1') return;

  const existing = Array.from(navRoot.querySelectorAll('a[href]')).find((a) => {
    const href = String(a.getAttribute('href') || '');
    const txt = String(a.textContent || '').trim().toLowerCase();
    return href.includes('/isms.html') || txt === 'isms';
  });
  if (existing) {
    navRoot.dataset.ismsDropdownPatched = '1';
    return;
  }

  const list = navRoot.querySelector('ul.navbar-nav') || navRoot.querySelector('ul') || navRoot;
  const links = Array.from(navRoot.querySelectorAll('a[href]'));
  const riskLink = links.find((a) => {
    const href = String(a.getAttribute('href') || '');
    const txt = String(a.textContent || '').trim().toLowerCase();
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
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html'))}">ISMS Overview</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=objectives'))}">Objectives</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=effectiveness'))}">Effectiveness Measures</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=documents'))}">Policies &amp; Processes</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/documents.html'))}">Edit policy documents</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/bookstack-sections.html'))}">BookStack policy sections</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=org'))}">Organisation Chart</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=assets'))}">Asset Matrix</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/assurance.html'))}">People &amp; vendors</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=access'))}">Access Control Matrix</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=appconfig'))}">Application Configuration</a></li>
      <li><a class="dropdown-item" href="${esc(_withCurrentFramework('/isms.html?tab=meetings'))}">Minutes of Meetings</a></li>
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
  if (typeof document === 'undefined') return;
  const navRoot = document.getElementById('navbar');
  if (!navRoot || navRoot.dataset.homeRemoved === '1') return;
  const links = Array.from(navRoot.querySelectorAll('a[href]'));
  for (const a of links) {
    const label = String(a.textContent || '').trim().toLowerCase();
    const href = String(a.getAttribute('href') || '').trim();
    if (label !== 'home') continue;
    let isHome = href === '/' || href === 'index.html' || href.endsWith('/index.html');
    try {
      const url = new URL(href || '/', window.location.origin);
      isHome = isHome || url.pathname === '/' || url.pathname.endsWith('/index.html');
    } catch {
      // Fall back to the simple string checks above.
    }
    if (!isHome) continue;
    const item = a.closest('li') || a;
    item.remove();
  }
  navRoot.dataset.homeRemoved = '1';
}

function _collapseFilterSectionsByDefault() {
  _mospInitCollapsibleFilterSections();
}

export async function initNavbar(...args) {
  const me = await _mospInitNavbar(...args);
  _removeHomeNavItem();
  _patchRiskNavDropdown();
  _patchIsmsNavDropdown();
  _collapseFilterSectionsByDefault();
  setTimeout(_collapseFilterSectionsByDefault, 0);
  return me;
}


const RICH_TEXT_ALLOWED_TAGS = new Set(['a', 'blockquote', 'br', 'code', 'div', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'li', 'ol', 'p', 'pre', 's', 'strike', 'strong', 'u', 'ul']);
const RICH_TEXT_VOID_TAGS = new Set(['br', 'hr']);
const _richTextEditors = new WeakMap();

export function isRichTextHtmlLike(value) {
  return /<\/?(?:p|br|hr|h[1-6]|ul|ol|li|strong|em|u|s|strike|a|blockquote|pre|code|div)\b/i.test(String(value || ''));
}

function _safeRichTextHref(href) {
  const raw = String(href || '').trim();
  if (!raw) return '';
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
    if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent || '');
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

function _inlineRichTextMarkdown(text) {
  let out = esc(text || '');
  // Small escaped markdown subset for legacy textarea values.
  out = out.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  return out;
}

export function legacyRichTextMarkdownToHtml(text, emptyHtml = '') {
  const source = String(text || '').replace(/\r\n?/g, '\n').trim();
  if (!source) return emptyHtml;

  const lines = source.split('\n');
  const html = [];
  let paragraph = [];
  let list = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${_inlineRichTextMarkdown(paragraph.join(' '))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list.length) return;
    html.push(`<ul>${list.map((item) => `<li>${_inlineRichTextMarkdown(item)}</li>`).join('')}</ul>`);
    list = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      html.push(`<h${level}>${_inlineRichTextMarkdown(heading[2].trim())}</h${level}>`);
      continue;
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      list.push(bullet[1].trim());
      continue;
    }
    flushList();
    paragraph.push(trimmed);
  }

  flushParagraph();
  flushList();
  return html.join('\n') || emptyHtml;
}

export function richTextEditorValue(raw) {
  const value = String(raw || '').trim();
  if (!value) return '';
  return isRichTextHtmlLike(value) ? value : legacyRichTextMarkdownToHtml(value);
}

export function renderRichTextContent(raw, emptyHtml = '<span class="small-muted">—</span>') {
  const value = String(raw || '').trim();
  if (!value) return emptyHtml;
  const html = isRichTextHtmlLike(value) ? sanitizeRichTextHtml(value) : legacyRichTextMarkdownToHtml(value, emptyHtml);
  return html || emptyHtml;
}

export function plainTextFromRichText(value) {
  const text = String(value || '')
    .replace(/<\/?(?:p|div|h[1-6]|li|ul|ol|blockquote|br|hr)\b[^>]*>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
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
  const editable = _richTextEditable(textarea);
  if (editable) textarea.value = editable.innerHTML || '';
  return textarea.value || '';
}

export function syncRichTextEditors(root = document) {
  root?.querySelectorAll?.('textarea[data-rich-text]').forEach((textarea) => syncRichTextEditor(textarea));
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

export function initRichTextEditors(root = document, opts = {}) {
  if (typeof window === 'undefined' || typeof window.Wysi !== 'function') return [];
  const textareas = Array.from(root?.querySelectorAll?.('textarea[data-rich-text]') || []);
  const initialized = [];
  for (const textarea of textareas) {
    if (_richTextEditors.has(textarea)) continue;
    textarea.setAttribute('data-wysi-fallback', '1');
    try {
      const instance = window.Wysi({
        el: textarea,
        height: opts.height || Number(textarea.dataset.richTextHeight || 260),
        autoGrow: opts.autoGrow ?? true,
        autoHide: opts.autoHide ?? false,
        tools: opts.tools || [
          'format', '|',
          'bold', 'italic', 'underline', 'strike', '|',
          'ul', 'ol', '|',
          'link', 'hr', 'quote', '|',
          'removeFormat',
        ],
        onChange: (content) => {
          textarea.value = String(content || '');
          opts.onChange?.(textarea, textarea.value);
        },
      }) || null;
      _richTextEditors.set(textarea, instance || true);
      textarea.closest('.rich-text-editor')?.setAttribute('data-rich-text-ready', '1');
      setRichTextEditorValue(textarea, textarea.value || '');
      initialized.push(textarea);
    } catch (e) {
      console.warn('Failed to initialise rich text editor', e);
    }
  }
  refreshRichTextEditorStates(root);
  return initialized;
}

export function setRichTextEditorValue(textareaOrId, value) {
  const textarea = typeof textareaOrId === 'string' ? document.getElementById(textareaOrId) : textareaOrId;
  if (!textarea) return;
  textarea.value = richTextEditorValue(value);
  const instance = _richTextEditors.get(textarea);
  if (instance && typeof instance?.setContent === 'function') {
    try { instance.setContent(textarea.value); } catch {}
  }
  const editable = _richTextEditable(textarea);
  if (editable) editable.innerHTML = textarea.value || '';
}

function _hexToRgb(hex) {
  const raw = String(hex || '').trim().replace(/^#/, '');
  if (!raw) return null;
  const h = raw.length === 3
    ? raw.split('').map((c) => c + c).join('')
    : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  const n = parseInt(h, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function _srgbToLinear(v) {
  const x = v / 255;
  return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
}

function _relativeLuminance({ r, g, b }) {
  const R = _srgbToLinear(r);
  const G = _srgbToLinear(g);
  const B = _srgbToLinear(b);
  return 0.2126 * R + 0.7152 * G + 0.0722 * B;
}

// Returns a readable text colour for the given background.
export function idealTextColor(bgHex) {
  const rgb = _hexToRgb(bgHex);
  if (!rgb) return '#111827'; // slate-900
  const lum = _relativeLuminance(rgb);
  // Threshold chosen to keep badges readable across our theme palette.
  return lum > 0.55 ? '#111827' : '#f8fafc'; // slate-900 / slate-50
}

export function userDisplayName(userOrName, fallback = '') {
  if (userOrName === null || userOrName === undefined) return String(fallback || '').trim();
  if (typeof userOrName === 'string' || typeof userOrName === 'number') return String(userOrName || fallback || '').trim();
  const user = userOrName.user && typeof userOrName.user === 'object' ? userOrName.user : userOrName;
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

export function userPillHtml(userOrName, {empty = '—', className = '', title = '', prefix = ''} = {}) {
  const label = userDisplayName(userOrName);
  if (!label) return `<span class="small-muted">${esc(empty || '—')}</span>`;
  const user = userOrName && typeof userOrName === 'object' ? (userOrName.user || userOrName) : null;
  const tip = String(title || user?.email || label || '').trim();
  const cls = ['badge', 'badge-soft', 'user-pill', className].filter(Boolean).join(' ');
  return `<span class="${esc(cls)}"${tip ? ` title="${esc(tip)}"` : ''}>${esc(prefix || '')}${esc(label)}</span>`;
}

export function userPillsHtml(items = [], opts = {}) {
  const rows = Array.isArray(items) ? items : (items ? [items] : []);
  const html = rows
    .map((item) => userPillHtml(item, {...opts, empty: ''}))
    .filter((value) => value && !value.includes('small-muted'))
    .join('');
  if (!html) return `<span class="small-muted">${esc(opts.empty || '—')}</span>`;
  return `<span class="user-pill-list">${html}</span>`;
}


export const ACTIVE_AUDIT_STATUSES = new Set(['open', 'in_progress']);
export const LOCKED_AUDIT_STATUSES = new Set(['completed', 'archived']);

export function auditStatusValue(auditOrStatus = null) {
  const raw = (auditOrStatus && typeof auditOrStatus === 'object')
    ? auditOrStatus.status
    : auditOrStatus;
  return String(raw || 'open').trim().toLowerCase();
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

export function canSampleIntoAudit(me = null) {
  return Boolean(me?.can_manage_audits || me?.is_admin);
}

function _sampleCurrentRelativeUrl() {
  return `${window.location.pathname || '/'}${window.location.search || ''}`;
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
            <div id="entityAuditSampleStatus" class="alert alert-info" style="display:none;"></div>
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
            <a id="entityAuditSampleCreate" class="btn btn-outline-primary me-auto" href="/audits.html?new=1">Start an Audit</a>
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
  try {
    return new URLSearchParams(window.location.search || '').get('framework') || '';
  } catch {
    return '';
  }
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
  const url = new URL('/audits.html', window.location.origin);
  url.searchParams.set('new', '1');
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
  if (!canSampleIntoAudit(me)) {
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
  if (els.title) els.title.textContent = title ? `Sample “${title}” into audit` : 'Sample item into audit';
  if (els.status) els.status.style.display = 'none';
  if (els.notes) {
    els.notes.value = '';
    els.notes.disabled = false;
    els.notes.placeholder = notesPlaceholder || 'Why this item was sampled, what was verified, screenshots taken, etc.';
  }
  if (els.useUrl) {
    els.useUrl.checked = true;
    els.useUrl.disabled = false;
  }
  if (els.create) els.create.href = _sampleCreateAuditUrl(framework);
  if (els.submit) els.submit.disabled = false;
  if (els.select) {
    els.select.innerHTML = '<option value="">Loading audits…</option>';
    try {
      const data = await apiGet(_sampleAuditsUrl(framework));
      const items = (data?.items || []).filter((a) => isAuditActive(a));
      els.select.innerHTML = items.map((a) => {
        const dates = [a.start_date, a.end_date].filter(Boolean).join(' → ');
        const suffix = dates ? ` · ${dates}` : '';
        const statusSuffix = auditStatusValue(a) === 'in_progress' ? ' · In progress' : ' · Open';
        return `<option value="${esc(a.id)}">${esc(a.title || 'Audit')}${esc(suffix)}${esc(statusSuffix)}</option>`;
      }).join('') || '<option value="">No open or in-progress audits — start one first</option>';
      const disabled = !items.length;
      if (els.submit) els.submit.disabled = disabled;
      if (els.notes) els.notes.disabled = disabled;
      if (els.useUrl) els.useUrl.disabled = disabled;
      if (disabled) toast(els.status || statusEl, 'No open or in-progress audits are available. Start an Audit first, then sample this item into it.', 'warning');
    } catch (e) {
      els.select.innerHTML = '<option value="">Failed to load audits</option>';
      if (els.submit) els.submit.disabled = true;
      if (els.notes) els.notes.disabled = true;
      if (els.useUrl) els.useUrl.disabled = true;
      toast(els.status || statusEl, `Failed to load audits: ${String(e)}`, 'danger');
    }
  }

  const submitHandler = async () => {
    const auditId = (els.select?.value || '').trim();
    if (!auditId) {
      toast(els.status || statusEl, 'Start an Audit first, then sample this item into it.', 'warning');
      return;
    }
    const btn = document.getElementById('entityAuditSampleSubmit');
    const prev = btn?.textContent || '';
    if (btn) { btn.disabled = true; btn.textContent = 'Sampling…'; }
    try {
      const body = {
        entity_type: cleanEntityType,
        entity_id: cleanEntityId,
        evidence_url: els.useUrl?.checked ? (evidenceUrl || _sampleCurrentRelativeUrl()) : null,
        title: title || 'Keen item',
        notes: els.notes?.value || '',
      };
      await apiPost(`/api/v1/audits/${encodeURIComponent(auditId)}/evidence`, body);
      toast(statusEl, 'Item sampled into audit', 'success');
      if (window.bootstrap?.Modal && els.modal) {
        window.bootstrap.Modal.getOrCreateInstance(els.modal).hide();
      }
      if (typeof onSampled === 'function') await onSampled(auditId);
    } catch (e) {
      toast(els.status || statusEl, `Failed: ${String(e)}`, 'danger');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = prev || 'Sample into audit'; }
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

export function auditSampleActionButtonHtml({
  label = 'Sample into audit',
  title = 'Sample into audit',
  className = 'btn btn-sm btn-outline-success',
  attrs = '',
} = {}) {
  return `<button class="${esc(className)}" type="button" ${attrs} title="${esc(title)}"><i class="bi bi-clipboard2-plus" aria-hidden="true"></i><span class="d-none d-xl-inline ms-1">${esc(label)}</span></button>`;
}

export function loadingSpinnerHtml(message = 'Loading data…', {small = true} = {}) {
  const msg = esc(message || 'Loading data…');
  const spinnerClass = small ? 'spinner-border spinner-border-sm' : 'spinner-border';
  return `<div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted">
    <span class="${spinnerClass}" role="status" aria-hidden="true"></span>
    <span>${msg}</span>
  </div>`;
}

export function tableLoadingHtml(colspan = 1, message = 'Loading data…') {
  const n = Math.max(1, parseInt(colspan, 10) || 1);
  return `<tr class="table-loading-row"><td colspan="${n}" class="p-4">${loadingSpinnerHtml(message, {small: true})}</td></tr>`;
}

export function showTableLoading(tbody, colspan = 1, message = 'Loading data…') {
  if (!tbody) return;
  tbody.innerHTML = tableLoadingHtml(colspan, message);
}

export function showInlineLoading(el, message = 'Loading data…') {
  if (!el) return;
  el.innerHTML = loadingSpinnerHtml(message, {small: true});
}

export function arrowLinkHtml(url, {className = 'btn btn-sm btn-outline-secondary', title = 'Open link'} = {}) {
  const safe = safeExternalHref(url);
  if (!safe) return '';
  const label = title || 'Open link';
  return `<a class="${esc(className)}" href="${esc(safe)}" target="_blank" rel="noopener noreferrer" title="${esc(label)}" aria-label="${esc(label)}"><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i></a>`;
}

export function safeAbsoluteHttpHref(url) {
  const raw = String(url || '').trim();
  if (!raw || raw.length > 2048) return '';
  // Upstream evidence links must be actual absolute web URLs.  Do not turn
  // payload values such as /home/miguel into same-origin KEEN links.
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

export function linkArrowsHtml(urls, {className = 'btn btn-sm btn-outline-secondary', title = 'Open link', empty = ''} = {}) {
  const rows = Array.isArray(urls) ? urls : (urls ? [urls] : []);
  const links = rows
      .map((url, idx) => arrowLinkHtml(url, {className, title: rows.length > 1 ? `${title} ${idx + 1}` : title}))
      .filter(Boolean);
  if (!links.length) return empty || '';
  return `<div class="btn-group btn-group-sm" role="group">${links.join('')}</div>`;
}

export function titledLinksHtml(items, {className = 'link-primary', title = 'Open link', empty = '', itemClass = 'd-inline-flex align-items-center gap-2 me-2 mb-1'} = {}) {
  const rows = Array.isArray(items) ? items : (items ? [items] : []);
  const links = rows.map((item, idx) => {
    const url = typeof item === 'string' ? item : (item?.url || item?.href || item?.link || '');
    const safe = safeExternalHref(url);
    if (!safe) return '';
    const label = String((typeof item === 'object' && item ? (item.title || item.label || item.name) : '') || url || `Evidence ${idx + 1}`).trim();
    const linkTitle = rows.length > 1 ? `${title} ${idx + 1}` : title;
    return `<span class="${esc(itemClass)}"><a class="${esc(className)} text-break" href="${esc(safe)}" target="_blank" rel="noopener noreferrer" title="${esc(linkTitle)}">${esc(label)}</a></span>`;
  }).filter(Boolean);
  if (!links.length) return empty || '';
  return `<div class="d-flex flex-wrap align-items-center gap-1">${links.join('')}</div>`;
}

export function upstreamLinkHtml(url, {className = 'btn btn-sm btn-outline-secondary', title = 'Open upstream link'} = {}) {
  const safe = safeAbsoluteHttpHref(url);
  if (!safe) return '';
  return arrowLinkHtml(safe, {className, title});
}

// Fetch the framework catalog for the account page's "default framework" dropdown.
export async function getFrameworkCatalog(cfgOverrides = null) {
  const cfg = getUiConfig(cfgOverrides);
  const ep = cfg?.framework?.endpoint || '/api/v1/frameworks';
  return apiGet(ep, cfgOverrides);
}
export function uniqElements(items = []) {
  const out = [];
  const seen = new Set();
  for (const item of items || []) {
    if (!item || seen.has(item)) continue;
    seen.add(item);
    out.push(item);
  }
  return out;
}

function _selectAll(root, selector) {
  if (!root || !selector || !root.querySelectorAll) return [];
  return Array.from(root.querySelectorAll(selector));
}

function _byIds(root, ids = []) {
  const out = [];
  for (const id of ids || []) {
    if (!id) continue;
    let el = null;
    if (root && typeof root.getElementById === 'function') el = root.getElementById(id);
    if (!el && typeof document !== 'undefined' && typeof document.getElementById === 'function') {
      el = document.getElementById(id);
    }
    if (el) out.push(el);
  }
  return out;
}

export function collectOffsetPagerButtons({
  scope = (typeof document !== 'undefined' ? document : null),
  prevIds = ['prev', 'prevBottom'],
  nextIds = ['next', 'nextBottom'],
  prevSelector = '[data-pager="prev"], [data-offset-pager="prev"], .js-offset-page[data-direction="prev"]',
  nextSelector = '[data-pager="next"], [data-offset-pager="next"], .js-offset-page[data-direction="next"]',
} = {}) {
  const root = scope || (typeof document !== 'undefined' ? document : null);
  return {
    prev: uniqElements([..._byIds(root, prevIds), ..._selectAll(root, prevSelector)]),
    next: uniqElements([..._byIds(root, nextIds), ..._selectAll(root, nextSelector)]),
  };
}

export function offsetPagerState({total = 0, offset = 0, limit = 50} = {}) {
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

export function offsetPaginationHtml({
  kind = '',
  total = 0,
  offset = 0,
  limit = 50,
  wrapperClass = 'd-flex align-items-center gap-2',
  buttonClass = 'btn btn-outline-secondary',
  pageClass = 'js-offset-page',
  previousLabel = '<i class="bi bi-chevron-left" aria-hidden="true"></i> Previous',
  nextLabel = 'Next <i class="bi bi-chevron-right" aria-hidden="true"></i>',
  summaryPrefix = '',
  formatNumber = false,
} = {}) {
  const s = offsetPagerState({total, offset, limit});
  if (s.total <= s.limit) return '';
  const fmt = (n) => formatNumber ? Number(n || 0).toLocaleString() : String(n);
  const kindAttr = kind ? ` data-kind="${esc(kind)}"` : '';
  return `<div class="${esc(wrapperClass)}">
    <div class="small-muted">${esc(summaryPrefix)}${fmt(s.start)}–${fmt(s.end)} of ${fmt(s.total)}</div>
    <div class="btn-group btn-group-sm" role="group" aria-label="Pagination">
      <button class="${esc(buttonClass)} ${esc(pageClass)}" type="button" data-pager="prev" data-direction="prev" data-offset-pager="prev" data-offset="${esc(String(s.prevOffset))}"${kindAttr} ${s.prevDisabled ? 'disabled' : ''}>${previousLabel}</button>
      <button class="${esc(buttonClass)} ${esc(pageClass)}" type="button" data-pager="next" data-direction="next" data-offset-pager="next" data-offset="${esc(String(s.nextOffset))}"${kindAttr} ${s.nextDisabled ? 'disabled' : ''}>${nextLabel}</button>
    </div>
  </div>`;
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

export function effectivenessMetricThresholdState(measure, entry) {
  if (!measure || !entry) return null;
  const op = String(measure.threshold_operator || '').trim();
  if (!['lt', 'lte', 'eq', 'gte', 'gt'].includes(op)) return null;
  if (entry.metric_value === null || entry.metric_value === undefined || entry.metric_value === '') return null;
  if (measure.target_value === null || measure.target_value === undefined || measure.target_value === '') return null;
  const value = Number(entry.metric_value);
  const target = Number(measure.target_value);
  if (!Number.isFinite(value) || !Number.isFinite(target)) return null;
  let ok = false;
  if (op === 'lt') ok = value < target;
  if (op === 'lte') ok = value <= target;
  if (op === 'eq') ok = Math.abs(value - target) < 1e-9;
  if (op === 'gte') ok = value >= target;
  if (op === 'gt') ok = value > target;
  const symbol = {lt: '<', lte: '≤', eq: '=', gte: '≥', gt: '>'}[op] || op;
  const unit = String(measure.target_unit || entry.metric_unit || '').trim();
  const targetText = `${symbol} ${target}${unit ? ` ${unit}` : ''}`;
  return {
    ok,
    state: ok ? 'ok' : 'bad',
    label: ok ? 'Meets target' : 'Outside target',
    title: `Metric value ${value}${unit ? ` ${unit}` : ''} ${ok ? 'satisfies' : 'does not satisfy'} target ${targetText}`,
  };
}

export function effectivenessMetricValueHtml(entry, measure, {href = '', fallback = 'Metric entry', className = ''} = {}) {
  if (!entry) return `<span class="small-muted">${esc(fallback)}</span>`;
  const value = entry.value_display || [entry.metric_value ?? '', entry.metric_unit || '', entry.qualitative_value || ''].filter(Boolean).join(' ') || fallback;
  const state = effectivenessMetricThresholdState(measure, entry);
  const baseClass = className || 'fw-semibold';
  if (!state) {
    return href
      ? `<a class="${esc(baseClass)}" href="${esc(href)}">${esc(value)}</a>`
      : `<span class="${esc(baseClass)}">${esc(value)}</span>`;
  }
  const cls = `eff-metric-value eff-metric-value-${state.state}${baseClass ? ` ${baseClass}` : ''}`;
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
    return '<span class="small-muted">—</span>';
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') {
    let text = '';
    try { text = JSON.stringify(value, null, 2); } catch { text = String(value); }
    if (text.length > 1200) text = text.slice(0, 1200) + '…';
    return `<pre class="small bg-light border rounded p-2 mb-0 changelog-json">${esc(text)}</pre>`;
  }
  const text = String(value);
  return `<span class="text-break">${esc(text.length > 1000 ? text.slice(0, 1000) + '…' : text)}</span>`;
}

export function entityChangelogHtml(items = [], {empty = 'No changelog entries yet.'} = {}) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return `<div class="small-muted p-3">${esc(empty)}</div>`;
  return `<div class="list-group list-group-flush">
    ${rows.map((entry) => {
      const changes = Array.isArray(entry?.changes) ? entry.changes : [];
      const actor = entry?.changed_by_username || 'system';
      const action = String(entry?.action || '').replaceAll('_', ' ') || 'updated';
      const details = changes.length
        ? `<div class="table-responsive mt-2"><table class="table table-sm align-middle mb-0 changelog-diff-table"><thead><tr><th style="width:180px;">Field</th><th>Before</th><th>After</th></tr></thead><tbody>${changes.map((c) => `<tr><td class="fw-semibold">${esc(c?.label || c?.field || '')}</td><td>${_changelogValueHtml(c?.before)}</td><td>${_changelogValueHtml(c?.after)}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="small-muted mt-2">No field-level changes recorded.</div>';
      return `<div class="list-group-item">
        <div class="d-flex flex-wrap gap-2 justify-content-between align-items-start">
          <div>
            <div class="fw-semibold">${esc(entry?.summary || 'Entity changed')}</div>
            <div class="small-muted">${esc(fmtTs(entry?.changed_at) || entry?.changed_at || '')} • ${esc(actor)} • ${esc(action)}</div>
          </div>
          ${entry?.request_path ? `<span class="badge text-bg-light mono">${esc(entry.request_method || '')} ${esc(entry.request_path)}</span>` : ''}
        </div>
        ${details}
      </div>`;
    }).join('')}
  </div>`;
}

export async function loadEntityChangelog(container, url, {empty = 'No changelog entries yet.', loading = 'Loading changelog…'} = {}) {
  if (!container) return null;
  container.innerHTML = loadingSpinnerHtml(loading, {small: true});
  const res = await apiGet(url);
  container.innerHTML = entityChangelogHtml(res?.items || [], {empty});
  return res;
}
