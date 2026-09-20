import {
  initNavbar,
  apiGet,
  esc,
  fmtTs,
  getCurrentFramework,
  getFrameworkCatalog,
  withFramework,
  toast,
  offsetPaginationHtml,
  wireOffsetPagerButtons,
  collapseToggleButtonHtml,
} from '/app.js';

await initNavbar();

const status = document.getElementById('status');
const entryCards = document.getElementById('entryCards');
const explorerTitle = document.getElementById('explorerTitle');
const explorerSubtitle = document.getElementById('explorerSubtitle');
const explorerCanvas = document.getElementById('explorerCanvas');
const crumbs = document.getElementById('crumbs');
const btnBack = document.getElementById('explorerBack');
const btnRoot = document.getElementById('explorerRoot');

let framework = getCurrentFramework();
let stack = [];
let current = null;
let clauseCache = null;
let transitionActive = false;
let latestEvidenceCursor = null;
let latestEvidenceTimer = null;
let latestEvidenceNextTimer = null;
let latestEvidenceAnimating = false;
const latestEvidenceQueue = [];
const latestEvidenceSeenIds = new Set();
const pageState = new Map();
let sectionDomCounter = 0;
const PAGE_SIZE = 100;
const LATEST_EVIDENCE_INITIAL_DELAY_MS = 5000;
const LATEST_EVIDENCE_POLL_MS = 30000;
const LATEST_EVIDENCE_LIMIT = 5;
const LATEST_EVIDENCE_BETWEEN_ITEMS_MS = 2200;
const LATEST_EVIDENCE_AFTER_SCROLL_PAUSE_MS = 900;

const ROOT_NODE = {
  kind: 'root',
  id: '',
  label: 'Home',
  title: 'Explore connections',
  subtitle: 'Choose a starting point above, then use “Zoom in” where available to replace the current layer with the next connected layer.',
};

const KINDS = {
  controls: {singular: 'control', label: 'Controls', icon: 'bi-shield-check', href: '/controls.html', tone: 'primary'},
  clauses: {singular: 'clause', label: 'Clauses', icon: 'bi-diagram-3', href: '/clauses.html', tone: 'info'},
  risks: {singular: 'risk', label: 'CIA Triad Risks', icon: 'bi-exclamation-triangle', href: '/risks.html', tone: 'warning'},
  pestle: {singular: 'pestle_item', label: 'PESTLE(E)', icon: 'bi-globe2', href: '/pestle.html', tone: 'info'},
  interested_parties: {singular: 'interested_party', label: 'Interested Parties', icon: 'bi-people', href: '/interested_parties.html', tone: 'primary'},
  audits: {singular: 'audit', label: 'Audits', icon: 'bi-clipboard2-check', href: '/audits.html', tone: 'success'},
  effectiveness_measures: {singular: 'effectiveness_measure', label: 'Effectiveness Measures', icon: 'bi-speedometer2', href: '/isms.html?tab=effectiveness', tone: 'primary'},
  evidence: {singular: 'evidence', label: 'Evidence', icon: 'bi-file-earmark-text', href: '/events.html', tone: 'secondary'},
  sources: {singular: 'source', label: 'Sources', icon: 'bi-hdd-network', href: '/sources.html', tone: 'dark'},
};

const LEAF_KIND_CONFIGS = {
  artifact: {singular: 'artifact', label: 'Artifact', icon: 'bi-paperclip', tone: 'secondary'},
  incident: {singular: 'incident', label: 'Incident', icon: 'bi-exclamation-octagon', tone: 'warning'},
  question: {singular: 'question', label: 'Question', icon: 'bi-chat-left-text', tone: 'info'},
  business_process: {singular: 'business_process', label: 'Business process', icon: 'bi-diagram-2', tone: 'secondary'},
  communication: {singular: 'communication', label: 'Communication', icon: 'bi-megaphone', tone: 'secondary'},
  metric_entry: {singular: 'metric_entry', label: 'Metric entry', icon: 'bi-activity', tone: 'secondary'},
};

const KIND_CONFIGS = {...KINDS, ...LEAF_KIND_CONFIGS};

const ENTITY_TO_COLLECTION = {
  control: 'controls',
  clause: 'clauses',
  risk: 'risks',
  pestle_item: 'pestle',
  interested_party: 'interested_parties',
  audit: 'audits',
  effectiveness_measure: 'effectiveness_measures',
  evidence: 'evidence',
  source: 'sources',
};

function pluralKind(kind) {
  return ENTITY_TO_COLLECTION[kind] || kind;
}

function collectionConfig(kind) {
  return KIND_CONFIGS[kind] || KIND_CONFIGS[pluralKind(kind)] || KINDS.evidence;
}

function shortText(value, limit = 120) {
  const s = String(value || '').replace(/\s+/g, ' ').trim();
  if (!s) return '';
  return s.length > limit ? `${s.slice(0, Math.max(0, limit - 1))}…` : s;
}

async function safeGet(url, fallback = null) {
  try { return await apiGet(url); }
  catch { return fallback; }
}

function latestEvidenceTickerElements() {
  return {
    wrap: document.getElementById('latestEvidenceTicker'),
    link: document.getElementById('latestEvidenceTickerLink'),
    text: document.getElementById('latestEvidenceTickerText'),
  };
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, Math.max(0, ms || 0)));
}

function latestEvidenceText(event) {
  const source = event?.source || 'unknown-source';
  const summary = shortText(
    event?.summary || event?.identifier || 'New evidence received',
    260
  );
  const bits = [source];
  if (event?.system) bits.push(event.system);
  if (event?.action) bits.push(event.action);
  bits.push(summary);
  return bits.filter(Boolean).join(' :: ');
}

function latestEvidenceIdentity(event) {
  return String(
    event?.id
      || event?.event_id
      || `${event?.created_at || ''}:${event?.timestamp || ''}:${event?.summary || ''}`
  );
}

function latestEvidenceTimestampMs(event) {
  const raw = event?.created_at || event?.timestamp || '';
  const parsed = raw ? Date.parse(raw) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

function latestEvidenceItemsFromResponse(data) {
  if (Array.isArray(data?.events)) return data.events.filter(Boolean);
  return data?.event ? [data.event] : [];
}

function setLatestEvidenceCursor(events) {
  const newest = events.reduce((best, event) => {
    if (!best) return event;
    return latestEvidenceTimestampMs(event) > latestEvidenceTimestampMs(best)
      ? event
      : best;
  }, null);
  latestEvidenceCursor = newest?.created_at
    || newest?.timestamp
    || latestEvidenceCursor;
}

function rememberLatestEvidence(id) {
  if (!id) return;
  latestEvidenceSeenIds.add(id);
  if (latestEvidenceSeenIds.size <= 100) return;
  const oldest = latestEvidenceSeenIds.values().next().value;
  latestEvidenceSeenIds.delete(oldest);
}

function enqueueLatestEvidence(events) {
  for (const event of events) {
    const id = latestEvidenceIdentity(event);
    if (!id || latestEvidenceSeenIds.has(id)) continue;
    rememberLatestEvidence(id);
    latestEvidenceQueue.push(event);
  }
  scheduleLatestEvidenceNext(0);
}

function animateLatestEvidenceHorizontalScroll(link) {
  if (prefersReducedMotion()) return sleep(1800);

  link.scrollLeft = 0;
  const overflow = Math.max(0, link.scrollWidth - link.clientWidth);
  if (overflow < 8) return sleep(2600);

  const duration = Math.min(8000, Math.max(3600, overflow * 26));
  return new Promise((resolve) => {
    const start = performance.now();
    const step = (now) => {
      const elapsed = now - start;
      const progress = Math.min(1, elapsed / duration);
      link.scrollLeft = Math.round(overflow * progress);
      if (progress < 1) {
        window.requestAnimationFrame(step);
      } else {
        resolve();
      }
    };
    window.requestAnimationFrame(step);
  });
}

async function renderLatestEvidenceTicker(event) {
  const {wrap, link, text} = latestEvidenceTickerElements();
  if (!wrap || !link || !text || !event) return;

  const hadContent = !wrap.classList.contains('d-none') && text.textContent;
  if (hadContent && !prefersReducedMotion()) {
    wrap.classList.remove('keen-latest-evidence-ticker-slide-in');
    wrap.classList.add('keen-latest-evidence-ticker-slide-out');
    await sleep(240);
  }

  const id = event.id || event.event_id || '';
  const href = event.url
    || (id ? `/event.html?id=${encodeURIComponent(id)}` : '/events.html');
  link.href = withFramework(href, framework);
  link.title = event.timestamp
    ? `Open latest evidence from ${fmtTs(event.timestamp)}`
    : 'Open latest evidence';
  link.scrollLeft = 0;
  text.textContent = latestEvidenceText(event);

  wrap.classList.remove(
    'd-none',
    'keen-latest-evidence-ticker-pulse',
    'keen-latest-evidence-ticker-slide-out',
    'keen-latest-evidence-ticker-slide-in'
  );
  // Restart the slide-in animation for each item in the latest-evidence batch.
  void wrap.offsetWidth;
  wrap.classList.add('keen-latest-evidence-ticker-slide-in');

  await sleep(220);
  await animateLatestEvidenceHorizontalScroll(link);
  await sleep(LATEST_EVIDENCE_AFTER_SCROLL_PAUSE_MS);
}

async function showNextLatestEvidence() {
  latestEvidenceNextTimer = null;
  if (latestEvidenceAnimating || !latestEvidenceQueue.length) return;

  latestEvidenceAnimating = true;
  const event = latestEvidenceQueue.shift();
  try {
    await renderLatestEvidenceTicker(event);
  } finally {
    latestEvidenceAnimating = false;
    if (latestEvidenceQueue.length) {
      scheduleLatestEvidenceNext(LATEST_EVIDENCE_BETWEEN_ITEMS_MS);
    }
  }
}

function scheduleLatestEvidenceNext(delayMs = LATEST_EVIDENCE_BETWEEN_ITEMS_MS) {
  if (latestEvidenceNextTimer || latestEvidenceAnimating || !latestEvidenceQueue.length) {
    return;
  }
  latestEvidenceNextTimer = window.setTimeout(showNextLatestEvidence, Math.max(0, delayMs));
}

async function refreshLatestEvidenceTicker() {
  if (document.visibilityState === 'hidden') return;

  const params = new URLSearchParams();
  params.set('limit', String(LATEST_EVIDENCE_LIMIT));
  if (latestEvidenceCursor) params.set('after', latestEvidenceCursor);
  const query = params.toString();
  const url = `/api/v1/evidence/latest${query ? `?${query}` : ''}`;

  try {
    const data = await apiGet(url);
    const events = latestEvidenceItemsFromResponse(data);
    if (!events.length) return;

    setLatestEvidenceCursor(events);
    enqueueLatestEvidence(events);
  } catch (err) {
    // Best-effort ambient UI; never interrupt the main homepage experience.
    console.debug('Latest evidence ticker refresh failed', err);
  }
}

function startLatestEvidenceTicker() {
  const {wrap, link, text} = latestEvidenceTickerElements();
  if (!wrap || !link || !text || latestEvidenceTimer) return;

  window.setTimeout(async () => {
    await refreshLatestEvidenceTicker();
    latestEvidenceTimer = window.setInterval(
      refreshLatestEvidenceTicker,
      LATEST_EVIDENCE_POLL_MS
    );
  }, LATEST_EVIDENCE_INITIAL_DELAY_MS);
}

async function resolveFramework() {
  framework = getCurrentFramework();
  if (framework) return framework;

  const catalog = await safeGet('/api/v1/frameworks', null);
  const items = Array.isArray(catalog?.items) ? catalog.items : [];
  const selected = String(
    catalog?.default
      || items.find((x) => x?.is_default)?.slug
      || items.find((x) => Number(x?.control_count || 0) > 0)?.slug
      || items[0]?.slug
      || ''
  ).trim();
  framework = selected || getCurrentFramework(selected) || '';

  // Prime the shared design-system cache for the navbar/framework picker when possible.
  try { await getFrameworkCatalog(); } catch {}
  return framework;
}

function apiUrl(path, params = {}, {includeFramework = true} = {}) {
  const u = new URL(String(path || '/'), window.location.origin);
  if (includeFramework && framework) u.searchParams.set('framework', framework);
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null || value === '') continue;
    u.searchParams.set(key, String(value));
  }
  return `${u.pathname}${u.search}`;
}

function prefersReducedMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true;
}


function activeLayerElements() {
  return [entryCards, explorerCanvas].filter((el) => el && !el.classList.contains('d-none'));
}

function markCardsEntering(scope = document) {
  if (prefersReducedMotion()) return;
  const root = scope instanceof Element ? scope : document;
  const cards = Array.from(root.querySelectorAll('.home-entry-card, .home-node-card'));
  cards.forEach((card, index) => {
    card.removeAttribute('data-entering');
    card.style.setProperty('--card-index', String(Math.min(index, 12)));
    // Restart the animation when moving back to a layer that already existed.
    void card.offsetWidth;
    card.setAttribute('data-entering', 'true');
  });
}

async function animateLayerSwap(callback, direction = 'forward') {
  if (prefersReducedMotion()) {
    await callback();
    markCardsEntering(document.querySelector('.keen-home') || document);
    return;
  }

  const outgoingLayers = activeLayerElements();
  outgoingLayers.forEach((el) => {
    el.classList.remove('home-layer-swap-back');
    if (direction === 'back') el.classList.add('home-layer-swap-back');
    el.classList.add('home-layer-outgoing');
  });

  await new Promise((resolve) => setTimeout(resolve, 140));
  // The layer loaders call setLoading() synchronously before awaiting API
  // responses. Clear the outgoing opacity before invoking the loader so that
  // the interim Bootstrap spinner is visible during slow relationship fetches.
  outgoingLayers.forEach((el) => el.classList.remove('home-layer-outgoing', 'home-layer-swap-back'));

  await callback();

  const incomingLayers = activeLayerElements();
  incomingLayers.forEach((el) => {
    el.classList.remove('home-layer-outgoing', 'home-layer-swap-back');
    if (direction === 'back') el.classList.add('home-layer-swap-back');
    el.classList.add('home-layer-incoming');
  });
  markCardsEntering(document.querySelector('.keen-home') || document);

  requestAnimationFrame(() => {
    incomingLayers.forEach((el) => el.classList.add('is-visible'));
  });

  await new Promise((resolve) => setTimeout(resolve, 220));
  incomingLayers.forEach((el) => el.classList.remove('home-layer-incoming', 'home-layer-swap-back', 'is-visible'));
}

async function zoomToWithAnimation(kind, id) {
  if (transitionActive) return;
  transitionActive = true;
  try {
    await animateLayerSwap(() => zoomTo(kind, id), 'forward');
  } finally {
    transitionActive = false;
  }
}

function setLoading(message = 'Loading…') {
  explorerCanvas.innerHTML = `<div class="loading-state d-flex gap-2 align-items-center justify-content-center text-center small-muted p-4"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span>${esc(message)}</span></div>`;
}

function setEmpty(message) {
  explorerCanvas.innerHTML = `<div class="empty-state p-4 text-center small-muted">${esc(message)}</div>`;
}

function setEntryCardsVisible(visible) {
  entryCards?.classList.toggle('d-none', !visible);
}

function entityHref(kind, item) {
  const id = item?.id || item?.event_id || '';
  const fw = framework;
  if (kind === 'control') return id ? withFramework(`/control.html?id=${encodeURIComponent(id)}`, fw) : withFramework('/controls.html', fw);
  if (kind === 'clause') return id ? withFramework(`/clause.html?id=${encodeURIComponent(id)}`, fw) : withFramework('/clauses.html', fw);
  if (kind === 'risk') return id ? withFramework(`/risk.html?id=${encodeURIComponent(id)}`, fw) : withFramework('/risks.html', fw);
  if (kind === 'pestle_item') return id ? withFramework(`/pestle_item.html?id=${encodeURIComponent(id)}`, fw) : withFramework('/pestle.html', fw);
  if (kind === 'interested_party') return id ? withFramework(`/interested_party.html?id=${encodeURIComponent(id)}`, fw) : withFramework('/interested_parties.html', fw);
  if (kind === 'audit') return id ? withFramework(`/audit.html?id=${encodeURIComponent(id)}`, fw) : withFramework('/audits.html', fw);
  if (kind === 'effectiveness_measure') return withFramework(id ? `/isms-effectiveness-measure.html?id=${encodeURIComponent(id)}` : '/isms.html?tab=effectiveness', fw);
  if (kind === 'metric_entry') return id ? withFramework(`/isms-effectiveness-metric.html?id=${encodeURIComponent(id)}`, fw) : withFramework('/isms.html?tab=effectiveness', fw);
  if (kind === 'evidence') return id ? withFramework(`/event.html?id=${encodeURIComponent(id)}`, fw) : withFramework('/events.html', fw);
  if (kind === 'source') {
    const src = item?.id || item?.source || item?.label || '';
    return withFramework(`/sources.html?source=${encodeURIComponent(src)}`, fw);
  }
  if (kind === 'artifact') {
    if (item?.download_url) return item.download_url;
    return id ? `/api/v1/artifacts/${encodeURIComponent(id)}/download` : withFramework('/events.html', fw);
  }
  if (kind === 'incident') {
    const eventId = item?.event_id || item?.eventId || '';
    return eventId ? withFramework(`/event.html?id=${encodeURIComponent(eventId)}#incidentsCard`, fw) : withFramework('/events.html', fw);
  }
  if (kind === 'question') {
    const eventId = item?.event_id || item?.eventId || '';
    const threadId = item?.id || item?.thread_id || '';
    const base = eventId ? `/event.html?id=${encodeURIComponent(eventId)}` : '/questions.html';
    const withThread = threadId && eventId ? `${base}&thread=${encodeURIComponent(threadId)}#questions` : `${base}#questions`;
    return withFramework(withThread, fw);
  }
  return withFramework('/', fw);
}

function primaryText(kind, item) {
  if (kind === 'control') return `${item.ref || ''} ${item.title || ''}`.trim() || 'Control';
  if (kind === 'clause') return `${item.ref || ''} ${item.title || ''}`.trim() || 'Clause';
  if (kind === 'risk') return item.title || [item.asset_name || item.asset, item.threat_summary].filter(Boolean).join(' – ') || 'Risk';
  if (kind === 'pestle_item') return [item.type, item.lens, item.item].filter(Boolean).join(' / ') || 'PESTLE(E) item';
  if (kind === 'business_process') return item.name || item.title || 'Business process';
  if (kind === 'interested_party') return item.title || [item.name?.name || item.name, item.nature?.name || item.nature].filter(Boolean).join(' – ') || 'Interested Party';
  if (kind === 'communication') return item.title || [item.event, item.with_whom].filter(Boolean).join(' / ') || 'Communication';
  if (kind === 'audit') return item.title || 'Audit';
  if (kind === 'effectiveness_measure') return item.summary || item.metric || item.effectiveness_measure || 'Effectiveness measure';
  if (kind === 'metric_entry') return item.value_display || item.source_title || 'Metric entry';
  if (kind === 'source') return item.label || item.source || item.id || 'Source';
  if (kind === 'artifact') return item.filename || item.kind || item.content_type || 'Artifact';
  if (kind === 'incident') return item.title || 'Incident';
  if (kind === 'question') return item.title || `Question thread${item.status ? ` · ${item.status}` : ''}`;
  return item.summary || item.title || item.identifier || 'Evidence';
}

function secondaryText(kind, item) {
  if (kind === 'evidence') return [item.timestamp ? fmtTs(item.timestamp) : '', item.source || ''].filter(Boolean).join(' · ');
  if (kind === 'audit') return item.status || '';
  if (kind === 'effectiveness_measure') return [item.metric_key ? `key: ${item.metric_key}` : '', item.target_display ? `target ${item.target_display}` : ''].filter(Boolean).join(' · ');
  if (kind === 'metric_entry') return [item.period || '', item.source_type || ''].filter(Boolean).join(' · ');
  if (kind === 'risk') return Array.isArray(item.risk_types) ? item.risk_types.join(', ') : '';
  if (kind === 'pestle_item') return item.overall_relevance?.label || item.overall_relevance_label || '';
  if (kind === 'business_process') return item.relevance?.label || item.relevance_label || '';
  if (kind === 'interested_party') return item.nature?.name || item.nature || '';
  if (kind === 'communication') return [item.when, (item.methods || []).join(', ')].filter(Boolean).join(' · ');
  if (kind === 'control') return item.in_scope === true ? 'In scope' : (item.in_scope === false ? 'Out of scope' : '');
  if (kind === 'clause') return item.parent_ref ? `Under ${item.parent_ref}` : '';
  if (kind === 'artifact') return [item.content_type || '', item.size_bytes ? `${Number(item.size_bytes).toLocaleString()} bytes` : '', item.captured_at ? fmtTs(item.captured_at) : ''].filter(Boolean).join(' · ');
  if (kind === 'incident') return [item.created_at ? fmtTs(item.created_at) : '', item.created_by ? `by ${item.created_by}` : ''].filter(Boolean).join(' · ');
  if (kind === 'question') return [item.status || '', item.created_at ? fmtTs(item.created_at) : '', item.created_by_username ? `by ${item.created_by_username}` : ''].filter(Boolean).join(' · ');
  return '';
}

function gridItem(html) {
  return `<div class="col-12 col-lg-6 col-xxl-4">${html}</div>`;
}

function pageSlug(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'section';
}

function homeSectionBodyId(sectionKey, title) {
  sectionDomCounter += 1;
  return [
    'home-section',
    pageSlug(currentPageScope()),
    pageSlug(sectionKey || title),
    String(sectionDomCounter),
  ].filter(Boolean).join('-');
}

function homeSectionCollapseButton(bodyId, title) {
  return collapseToggleButtonHtml({
    targetId: bodyId,
    expanded: true,
    className: 'btn btn-sm btn-outline-secondary home-section-collapse-toggle',
    controlsLabel: title,
  });
}

function currentPageScope() {
  const kind = current?.kind || 'root';
  const id = current?.id || '';
  return `${kind}:${id}`;
}

function paginationKey(sectionKey) {
  return `${currentPageScope()}:${sectionKey}`;
}

function clampPage(sectionKey, total, pageSize) {
  const maxPage = Math.max(0, Math.ceil(Number(total || 0) / pageSize) - 1);
  const key = paginationKey(sectionKey);
  const raw = Number(pageState.get(key) || 0);
  const page = Math.min(Math.max(0, Number.isFinite(raw) ? raw : 0), maxPage);
  if (page !== raw) pageState.set(key, page);
  return page;
}

function paginationHtml(sectionKey, total, page, pageSize) {
  if (Number(total || 0) <= pageSize) return '';
  const maxPage = Math.max(0, Math.ceil(total / pageSize) - 1);
  const start = page * pageSize + 1;
  const end = Math.min(total, (page + 1) * pageSize);
  return `<div class="home-pagination d-flex flex-wrap gap-2 align-items-center justify-content-between mt-3">
    <div class="small-muted">Showing ${start.toLocaleString()}–${end.toLocaleString()} of ${Number(total).toLocaleString()}</div>
    <div class="btn-group btn-group-sm" role="group" aria-label="Section pagination">
      <button class="btn btn-outline-secondary js-section-page" type="button" data-section-key="${esc(sectionKey)}" data-page="${esc(String(page - 1))}" ${page <= 0 ? 'disabled' : ''}><i class="bi bi-chevron-left" aria-hidden="true"></i> Previous</button>
      <button class="btn btn-outline-secondary js-section-page" type="button" data-section-key="${esc(sectionKey)}" data-page="${esc(String(page + 1))}" ${page >= maxPage ? 'disabled' : ''}>Next <i class="bi bi-chevron-right" aria-hidden="true"></i></button>
    </div>
  </div>`;
}

function paginatedSectionHtml(title, subtitle, items, renderItem, {empty = 'No connected items found.', sectionKey = '', pageSize = PAGE_SIZE} = {}) {
  const allItems = Array.isArray(items) ? items : [];
  const key = sectionKey || pageSlug(title);
  if (!allItems.length) return sectionHtml(title, subtitle, '', empty, {sectionKey: key});

  const safePageSize = Math.max(1, Number(pageSize || PAGE_SIZE));
  const page = clampPage(key, allItems.length, safePageSize);
  const visibleItems = allItems.slice(page * safePageSize, (page + 1) * safePageSize);
  const cardsHtml = visibleItems.map((item) => renderItem(item)).join('');
  const pager = paginationHtml(key, allItems.length, page, safePageSize);
  const bodyId = homeSectionBodyId(key, title);
  const collapseButton = homeSectionCollapseButton(bodyId, title);

  return `<section class="home-section mb-4" data-section-key="${esc(key)}">
    <div class="home-section-header d-flex flex-wrap gap-2 align-items-start justify-content-between mb-2">
      <div class="min-w-0">
        <h2 class="h5 mb-0">${esc(title)}</h2>
        ${subtitle ? `<div class="small-muted">${esc(subtitle)}</div>` : ''}
      </div>
      <div class="home-section-actions d-flex flex-wrap gap-2 align-items-center justify-content-end">
        ${pager ? `<div class="d-none d-md-block">${pager}</div>` : ''}
        ${collapseButton}
      </div>
    </div>
    <div id="${esc(bodyId)}" class="collapse show home-section-body">
      <div class="row g-3 home-layer-grid">${cardsHtml}</div>
      ${pager ? `<div class="d-md-none">${pager}</div>` : ''}
    </div>
  </section>`;
}

function collectionPaginationHtml(kind, total, offset, limit) {
  return offsetPaginationHtml({
    kind,
    total,
    offset,
    limit,
    wrapperClass: 'home-pagination d-flex flex-wrap gap-2 align-items-center justify-content-between mt-3',
    summaryPrefix: 'Showing ',
    formatNumber: true,
    buttonClass: 'btn btn-outline-secondary',
    pageClass: 'js-collection-page js-offset-page',
  });
}

function serverPaginatedSectionHtml(title, subtitle, cardsHtml, {kind, total = 0, offset = 0, limit = PAGE_SIZE} = {}) {
  const pager = collectionPaginationHtml(kind, total, offset, limit);
  const key = kind || pageSlug(title);
  const bodyId = homeSectionBodyId(key, title);
  const collapseButton = homeSectionCollapseButton(bodyId, title);
  return `<section class="home-section mb-4" data-section-key="${esc(key)}">
    <div class="home-section-header d-flex flex-wrap gap-2 align-items-start justify-content-between mb-2">
      <div class="min-w-0">
        <h2 class="h5 mb-0">${esc(title)}</h2>
        ${subtitle ? `<div class="small-muted">${esc(subtitle)}</div>` : ''}
      </div>
      <div class="home-section-actions d-flex flex-wrap gap-2 align-items-center justify-content-end">
        ${pager ? `<div class="d-none d-md-block">${pager}</div>` : ''}
        ${collapseButton}
      </div>
    </div>
    <div id="${esc(bodyId)}" class="collapse show home-section-body">
      ${cardsHtml ? `<div class="row g-3 home-layer-grid">${cardsHtml}</div>` : `<div class="small-muted border rounded p-3 bg-light">No connected items found.</div>`}
      ${pager ? `<div class="d-md-none">${pager}</div>` : ''}
    </div>
  </section>`;
}

function hasDeeperLayer(kind, item = {}) {
  // Evidence/event cards are terminal in this explorer for now: open the
  // event detail page instead, where source, controls, audits, artifacts,
  // incidents and questions can be viewed reliably. This must be checked
  // before collection kinds because `evidence` is both the collection name
  // and the entity card kind.
  if (kind === 'evidence') return false;

  if (KINDS[kind]) return true;

  const id = item?.id || item?.event_id || item?.source || item?.label || '';
  if (['control', 'clause', 'risk', 'pestle_item', 'interested_party', 'audit', 'source', 'effectiveness_measure'].includes(kind)) return Boolean(id);

  // These are leaf cards on the homepage: they are best opened on their
  // owning event/download page rather than replacing the layer again.
  if (['artifact', 'incident', 'question', 'business_process', 'communication', 'metric_entry'].includes(kind)) return false;

  return false;
}

function openAttrs() {
  return ' target="_blank" rel="noopener noreferrer"';
}

function cardHtml(kind, item, {zoom = null, open = true} = {}) {
  const cfg = collectionConfig(kind);
  const label = primaryText(kind, item);
  const meta = secondaryText(kind, item);
  const id = item.id || item.event_id || item.source || item.label || '';
  const canZoom = zoom === null ? hasDeeperLayer(kind, item) : Boolean(zoom);
  const body = kind === 'risk' ? item.threat_summary : (kind === 'pestle_item' ? item.rationale : (kind === 'interested_party' ? item.description : (kind === 'incident' ? item.text : (kind === 'metric_entry' ? item.notes : item.description))));
  return `<div class="home-node-card card h-100" data-kind="${esc(kind)}" data-id="${esc(id)}">
    <div class="card-body d-flex flex-column gap-2">
      <div class="d-flex align-items-start gap-2 min-w-0">
        <div class="home-node-icon text-bg-${esc(cfg.tone)}"><i class="bi ${esc(cfg.icon)}" aria-hidden="true"></i></div>
        <div class="min-w-0 flex-grow-1">
          <div class="home-node-title" title="${esc(label)}">${esc(label)}</div>
          ${meta ? `<div class="home-node-meta small-muted">${esc(meta)}</div>` : ''}
        </div>
      </div>
      ${body ? `<div class="home-node-description small text-secondary">${esc(shortText(body, 180))}</div>` : ''}
      <div class="d-flex flex-wrap gap-2 mt-auto pt-2">
        ${canZoom ? `<button class="btn btn-sm btn-outline-primary js-zoom" type="button"><i class="bi bi-zoom-in" aria-hidden="true"></i> Zoom in</button>` : ''}
        ${open ? `<a class="btn btn-sm btn-outline-secondary" href="${esc(entityHref(kind, item))}"${openAttrs()}><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Open this</a>` : ''}
      </div>
    </div>
  </div>`;
}

function sectionHtml(title, subtitle, cardsHtml, empty = 'No connected items found.', {sectionKey = ''} = {}) {
  const key = sectionKey || pageSlug(title);
  const bodyId = homeSectionBodyId(key, title);
  const collapseButton = homeSectionCollapseButton(bodyId, title);
  return `<section class="home-section mb-4" data-section-key="${esc(key)}">
    <div class="home-section-header d-flex flex-wrap gap-2 align-items-start justify-content-between mb-2">
      <div class="min-w-0">
        <h2 class="h5 mb-0">${esc(title)}</h2>
        ${subtitle ? `<div class="small-muted">${esc(subtitle)}</div>` : ''}
      </div>
      <div class="home-section-actions d-flex flex-wrap gap-2 align-items-center justify-content-end">
        ${collapseButton}
      </div>
    </div>
    <div id="${esc(bodyId)}" class="collapse show home-section-body">
      ${cardsHtml ? `<div class="row g-3 home-layer-grid">${cardsHtml}</div>` : `<div class="small-muted border rounded p-3 bg-light">${esc(empty)}</div>`}
    </div>
  </section>`;
}

function renderCrumbs() {
  const parts = [...stack, current]
    .filter((x) => x && x.kind !== 'root')
    .map((x) => esc(x.label || collectionConfig(x.kind).label));
  crumbs.innerHTML = parts.length ? parts.join(' <span class="mx-1">/</span> ') : '';
  btnBack.disabled = stack.length === 0;
}

function showRoot() {
  current = {...ROOT_NODE};
  setEntryCardsVisible(true);
  explorerTitle.textContent = ROOT_NODE.title;
  explorerSubtitle.textContent = ROOT_NODE.subtitle;
  renderCrumbs();
  setEmpty('Pick one of the widgets above to start exploring KEEN as a connected graph.');
  markCardsEntering(entryCards);
}

function wireHomeControls(scope = explorerCanvas) {
  for (const btn of Array.from(scope.querySelectorAll('.js-zoom'))) {
    btn.addEventListener('click', () => {
      const card = btn.closest('[data-kind]');
      if (!card) return;
      const kind = card.dataset.kind || '';
      const id = card.dataset.id || '';
      if (!kind) return;
      zoomToWithAnimation(kind, id);
    });
  }

  wireOffsetPagerButtons({
    prev: Array.from(scope.querySelectorAll('.js-offset-page[data-direction="prev"], .js-collection-page[data-pager="prev"]')),
    next: Array.from(scope.querySelectorAll('.js-offset-page[data-direction="next"], .js-collection-page[data-pager="next"]')),
  }, (_direction, btn) => {
    if (transitionActive || btn.disabled) return;
    const kind = btn.dataset.kind || current?.kind || '';
    const nextOffset = Math.max(0, Number(btn.dataset.offset || 0));
    const oldOffset = Math.max(0, Number(current?.offset || 0));
    if (!kind) return;
    transitionActive = true;
    animateLayerSwap(() => loadCollection(kind, false, nextOffset), nextOffset >= oldOffset ? 'forward' : 'back')
      .finally(() => { transitionActive = false; });
  });

  for (const btn of Array.from(scope.querySelectorAll('.js-section-page'))) {
    btn.addEventListener('click', () => {
      if (transitionActive || btn.disabled) return;
      const sectionKey = btn.dataset.sectionKey || '';
      const nextPage = Math.max(0, Number(btn.dataset.page || 0));
      if (!sectionKey || !current) return;
      const key = paginationKey(sectionKey);
      const oldPage = Math.max(0, Number(pageState.get(key) || 0));
      pageState.set(key, nextPage);
      transitionActive = true;
      animateLayerSwap(() => zoomTo(current.kind, current.id || '', false, {offset: current.offset || 0}), nextPage >= oldPage ? 'forward' : 'back')
        .finally(() => { transitionActive = false; });
    });
  }
}

function enter(node, push = true) {
  if (push && current) stack.push(current);
  current = node;
  setEntryCardsVisible(false);
  explorerTitle.textContent = node.title || node.label || 'Explore connections';
  explorerSubtitle.textContent = node.subtitle || 'Use “Zoom in” where available to replace the current layer with the next connected layer.';
  renderCrumbs();
}

async function loadEntryCards() {
  const defs = [
    {kind: 'controls', title: 'Controls', description: 'Start with an Annex A control and move to evidence, clauses and risks.'},
    {kind: 'clauses', title: 'Clauses', description: 'Start from ISO27001 clauses, then zoom into linked controls and evidence.'},
    {kind: 'risks', title: 'CIA Triad Risks', description: 'Start from Confidentiality, Integrity and Availability asset/threat scenarios and move to mapped controls.'},
    {kind: 'pestle', title: 'PESTLE(E)', description: 'Start from strategic/contextual impact items and move to relevant clauses, controls and business processes.'},
    {kind: 'interested_parties', title: 'Interested Parties', description: 'Start from interested parties and move to linked controls or communication audiences.'},
    {kind: 'audits', title: 'Audits', description: 'Start from an engagement and move through scope and sampled evidence.'},
    {kind: 'effectiveness_measures', title: 'Effectiveness Measures', description: 'Start from ISMS effectiveness measures and move to linked controls, sources and metric entries.'},
    {kind: 'evidence', title: 'Evidence', description: 'Start with events and move backwards to sources, controls and audits.'},
    {kind: 'sources', title: 'Sources', description: 'Start from a source and inspect the evidence it produced.'},
  ];

  entryCards.innerHTML = defs.map((d) => {
    const cfg = collectionConfig(d.kind);
    return `<div class="col-12 col-md-6 col-xl-4">
      <div class="card h-100 home-entry-card" data-kind="${esc(d.kind)}">
        <div class="card-body d-flex flex-column gap-3">
          <div class="d-flex align-items-start gap-3 min-w-0">
            <div class="home-entry-icon text-bg-${esc(cfg.tone)}"><i class="bi ${esc(cfg.icon)}" aria-hidden="true"></i></div>
            <div class="flex-grow-1 min-w-0">
              <h2 class="h5 mb-0">${esc(d.title)}</h2>
            </div>
          </div>
          <p class="mb-0 text-secondary">${esc(d.description)}</p>
          <div class="d-flex flex-wrap gap-2 mt-auto">
            <button class="btn btn-primary js-entry-zoom" type="button"><i class="bi bi-zoom-in" aria-hidden="true"></i> Zoom in</button>
            <a class="btn btn-outline-secondary" href="${esc(withFramework(cfg.href, framework))}"${openAttrs()}><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Open this</a>
          </div>
        </div>
      </div>
    </div>`;
  }).join('');

  markCardsEntering(entryCards);

  for (const btn of Array.from(entryCards.querySelectorAll('.js-entry-zoom'))) {
    btn.addEventListener('click', () => {
      const card = btn.closest('[data-kind]');
      const kind = card?.dataset?.kind || '';
      if (kind) zoomToWithAnimation(kind, '');
    });
  }
}

async function loadCollection(kind, push = true, offset = 0) {
  const cfg = collectionConfig(kind);
  const safeOffset = Math.max(0, Number(offset || 0));
  enter({
    kind,
    id: '',
    label: cfg.label,
    title: cfg.label,
    subtitle: `Pick a ${cfg.singular} to zoom into its connected layer.`,
    offset: safeOffset,
  }, push);
  setLoading(`Loading ${cfg.label.toLowerCase()}…`);

  let data = {items: [], total: 0, limit: PAGE_SIZE, offset: safeOffset};
  let renderKind = cfg.singular;

  if (kind === 'controls') {
    data = await safeGet(apiUrl('/api/v1/controls', {limit: PAGE_SIZE, offset: safeOffset}), data);
  } else if (kind === 'clauses') {
    data = await safeGet(apiUrl('/api/v1/clauses', {limit: PAGE_SIZE, offset: safeOffset}), data);
  } else if (kind === 'risks') {
    data = await safeGet(apiUrl('/api/v1/risks', {limit: PAGE_SIZE, offset: safeOffset}), data);
  } else if (kind === 'pestle') {
    data = await safeGet(apiUrl('/api/v1/pestle/items', {limit: PAGE_SIZE, offset: safeOffset}), data);
    renderKind = 'pestle_item';
  } else if (kind === 'interested_parties') {
    data = await safeGet(apiUrl('/api/v1/interested-parties', {limit: PAGE_SIZE, offset: safeOffset}), data);
    renderKind = 'interested_party';
  } else if (kind === 'audits') {
    data = await safeGet(apiUrl('/api/v1/audits', {limit: PAGE_SIZE, offset: safeOffset}), data);
  } else if (kind === 'effectiveness_measures') {
    data = await safeGet(apiUrl('/api/v1/isms/effectiveness-measures', {limit: PAGE_SIZE, offset: safeOffset}), data);
    renderKind = 'effectiveness_measure';
  } else if (kind === 'evidence') {
    data = await safeGet(apiUrl('/api/v1/events', {limit: PAGE_SIZE, offset: safeOffset}), data);
    renderKind = 'evidence';
  } else if (kind === 'sources') {
    data = await safeGet(apiUrl('/api/v1/sources', {limit: PAGE_SIZE, offset: safeOffset}, {includeFramework: false}), data);
    renderKind = 'source';
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const total = Number(data.total ?? items.length ?? 0);
  if (!items.length && safeOffset > 0) {
    const prevOffset = Math.max(0, safeOffset - PAGE_SIZE);
    return loadCollection(kind, false, prevOffset);
  }
  if (!items.length) {
    setEmpty(`No ${cfg.label.toLowerCase()} found, or you do not have permission to view them.`);
    return;
  }

  explorerCanvas.innerHTML = serverPaginatedSectionHtml(
    cfg.label,
    'Use these starting points to move into a connected layer. Use the pager to move through everything available at this level.',
    items.map((it) => gridItem(cardHtml(renderKind, normaliseItem(renderKind, it)))).join(''),
    {kind, total, offset: safeOffset, limit: PAGE_SIZE}
  );
  wireHomeControls();
}

function normaliseItem(kind, it) {
  const out = {...(it || {})};
  if (kind === 'source' && !out.id) out.id = out.source;
  if (kind === 'evidence' && !out.id && out.event_id) out.id = out.event_id;
  if (kind === 'artifact') {
    out.id = out.id || out.artifact_id;
    out.filename = out.filename || out.name || out.storage_uri || out.kind || 'Artifact';
    out.download_url = out.download_url || (out.id ? `/api/v1/artifacts/${encodeURIComponent(out.id)}/download` : '');
  }
  if (kind === 'question') {
    const posts = Array.isArray(out.posts) ? out.posts : [];
    const firstPost = posts[0] || {};
    out.title = out.title || shortText(firstPost.body || 'Question thread', 90);
  }
  if (kind === 'risk') {
    out.title = out.title || [out.asset_name || out.asset, shortText(out.threat_summary, 70)].filter(Boolean).join(' – ');
  }
  if (kind === 'pestle_item') {
    out.title = out.title || [out.type, out.lens, shortText(out.item, 90)].filter(Boolean).join(' / ');
    out.description = out.description || out.rationale || '';
  }
  if (kind === 'effectiveness_measure') {
    out.title = out.title || out.summary || out.metric || out.effectiveness_measure || 'Effectiveness measure';
    out.description = out.description || out.effectiveness_measure || out.metric || '';
  }
  if (kind === 'metric_entry') {
    out.title = out.title || out.value_display || out.source_title || 'Metric entry';
    out.description = out.description || out.qualitative_value || out.notes || '';
  }
  if (kind === 'interested_party') {
    out.title = out.title || [out.name?.name || out.name, out.nature?.name || out.nature].filter(Boolean).join(' – ');
    out.description = out.description || `${Number(out.control_count || (out.controls || []).length || 0)} linked controls • ${Number(out.communication_count || (out.communications || []).length || 0)} communications`;
  }
  return out;
}

async function getClauses() {
  if (!clauseCache) clauseCache = await safeGet(apiUrl('/api/v1/clauses', {limit: 5000, offset: 0}), {items: []});
  return clauseCache;
}

async function loadControl(id, push = true) {
  enter({kind: 'control', id, label: 'Control', title: 'Control connections', subtitle: 'Controls connect clauses, risks, Interested Parties and evidence.'}, push);
  setLoading('Loading control connections…');
  const [control, evidence, risks, interestedParties, effectiveness] = await Promise.all([
    safeGet(`/api/v1/controls/${encodeURIComponent(id)}`, null),
    safeGet(`/api/v1/controls/${encodeURIComponent(id)}/evidence?limit=${PAGE_SIZE}`, {items: []}),
    safeGet(apiUrl(`/api/v1/controls/${encodeURIComponent(id)}/risks`), {items: []}),
    safeGet(apiUrl(`/api/v1/controls/${encodeURIComponent(id)}/interested-parties`), {items: []}),
    safeGet(apiUrl(`/api/v1/controls/${encodeURIComponent(id)}/effectiveness-measures`), {items: []}),
  ]);
  if (!control) return setEmpty('Control not found.');
  current.label = `${control.ref || ''} ${control.title || ''}`.trim() || 'Control';
  renderCrumbs();

  const clauses = control.clauses || [];
  const evItems = (evidence.items || []).map((x) => ({...x, id: x.event_id, summary: x.summary, source: x.source, timestamp: x.timestamp}));
  const riskItems = (risks.items || []).map((x) => normaliseItem('risk', x));
  const interestedPartyItems = (interestedParties.items || []).map((x) => normaliseItem('interested_party', x));
  const effectivenessItems = (effectiveness.items || []).map((x) => normaliseItem('effectiveness_measure', x));

  explorerCanvas.innerHTML = `
    ${nodeHeader('control', control, control.in_scope ? 'In scope' : 'Out of scope')}
    ${paginatedSectionHtml('Linked clauses', 'Move from this control to the ISO27001 clause structure.', clauses, (c) => gridItem(cardHtml('clause', {id: c.clause_id, ref: c.ref, title: c.title})), {sectionKey: 'control-clauses'})}
    ${paginatedSectionHtml('Recent evidence', 'Events mapped to this control.', evItems, (e) => gridItem(cardHtml('evidence', e)), {sectionKey: 'control-evidence'})}
    ${paginatedSectionHtml('Related risks', 'Risk scenarios mapped to this control.', riskItems, (r) => gridItem(cardHtml('risk', r)), {sectionKey: 'control-risks'})}
    ${paginatedSectionHtml('Effectiveness Measures', 'Measures that use this control as part of the ISMS effectiveness ledger.', effectivenessItems, (m) => gridItem(cardHtml('effectiveness_measure', m)), {sectionKey: 'control-effectiveness-measures'})}
    ${paginatedSectionHtml('Interested Parties', 'Interested Parties mapped to this control.', interestedPartyItems, (p) => gridItem(cardHtml('interested_party', p)), {sectionKey: 'control-interested-parties'})}
  `;
  wireHomeControls();
}

async function loadClause(id, push = true) {
  enter({kind: 'clause', id, label: 'Clause', title: 'Clause connections', subtitle: 'Clauses connect framework structure to controls and evidence.'}, push);
  setLoading('Loading clause connections…');
  const [clause, controls, events, allClauses] = await Promise.all([
    safeGet(`/api/v1/clauses/${encodeURIComponent(id)}`, null),
    safeGet(`/api/v1/clauses/${encodeURIComponent(id)}/controls?limit=${PAGE_SIZE}`, {items: []}),
    safeGet(apiUrl('/api/v1/events', {clause: id, limit: PAGE_SIZE}), {items: []}),
    getClauses(),
  ]);
  if (!clause) return setEmpty('Clause not found.');
  current.label = `${clause.ref || ''} ${clause.title || ''}`.trim() || 'Clause';
  renderCrumbs();

  const children = (allClauses.items || []).filter((c) => String(c.parent_id || '') === String(clause.id));
  const parent = clause.parent_id ? (allClauses.items || []).find((c) => String(c.id) === String(clause.parent_id)) : null;
  const ctrlItems = controls.items || [];
  const evItems = events.items || [];

  explorerCanvas.innerHTML = `
    ${nodeHeader('clause', clause, 'Explore this clause through its framework hierarchy, linked controls and evidence.')}
    ${parent ? sectionHtml('Parent clause', 'Move back up the framework hierarchy.', gridItem(cardHtml('clause', parent))) : ''}
    ${paginatedSectionHtml('Child clauses', 'Move deeper into the clause hierarchy.', children, (c) => gridItem(cardHtml('clause', c)), {sectionKey: 'clause-children'})}
    ${paginatedSectionHtml('Applicable controls', 'Controls connected to this clause.', ctrlItems, (c) => gridItem(cardHtml('control', c)), {sectionKey: 'clause-controls'})}
    ${paginatedSectionHtml('Recent evidence', 'Events connected through linked controls.', evItems, (e) => gridItem(cardHtml('evidence', e)), {sectionKey: 'clause-evidence'})}
  `;
  wireHomeControls();
}

async function loadRisk(id, push = true) {
  enter({kind: 'risk', id, label: 'Risk', title: 'Risk connections', subtitle: 'Risks connect assets and threats back to mitigating controls.'}, push);
  setLoading('Loading risk connections…');
  const [risk, controls] = await Promise.all([
    safeGet(apiUrl(`/api/v1/risks/${encodeURIComponent(id)}`), null),
    safeGet(apiUrl(`/api/v1/risks/${encodeURIComponent(id)}/controls`), {items: []}),
  ]);
  if (!risk) return setEmpty('Risk not found, or you do not have permission to view it.');
  const r = normaliseItem('risk', risk);
  current.label = primaryText('risk', r);
  renderCrumbs();
  explorerCanvas.innerHTML = `
    ${nodeHeader('risk', r, (r.risk_types || []).join(', ') || 'Risk scenario')}
    ${paginatedSectionHtml('Mapped controls', 'Controls currently linked to this risk scenario.', controls.items || [], (c) => gridItem(cardHtml('control', c)), {sectionKey: 'risk-controls'})}
  `;
  wireHomeControls();
}

async function loadPestleItem(id, push = true) {
  enter({kind: 'pestle_item', id, label: 'PESTLE(E)', title: 'PESTLE(E) connections', subtitle: 'PESTLE(E) items connect context and impact assessment to relevant clauses, controls and business processes.'}, push);
  setLoading('Loading PESTLE(E) connections…');
  const item = await safeGet(apiUrl(`/api/v1/pestle/items/${encodeURIComponent(id)}`), null);
  if (!item) return setEmpty('PESTLE(E) item not found, or you do not have permission to view it.');
  const p = normaliseItem('pestle_item', item);
  current.label = primaryText('pestle_item', p);
  renderCrumbs();

  const processes = (item.business_processes || []).map((link) => ({
    ...(link.business_process || {}),
    relevance: link.relevance || null,
    relevance_label: link.relevance?.label || '—',
    description: `Relevance: ${link.relevance?.label || '—'}`,
  }));
  const clauses = (item.clauses || []).map((link) => ({
    ...(link.clause || {}),
    relevance: link.relevance || null,
    relevance_label: link.relevance?.label || '—',
    description: `PESTLE(E) relevance: ${link.relevance?.label || '—'}`,
  }));
  const controls = item.related_controls || [];

  explorerCanvas.innerHTML = `
    ${nodeHeader('pestle_item', p, [p.type, p.lens, p.overall_relevance?.label].filter(Boolean).join(' · '))}
    ${paginatedSectionHtml('Business process relevance', 'Business processes scored against this PESTLE(E) item.', processes, (bp) => gridItem(cardHtml('business_process', bp, {zoom: false, open: false})), {sectionKey: 'pestle-processes'})}
    ${paginatedSectionHtml('Relevant clauses', 'Framework clauses scored against this PESTLE(E) item.', clauses, (c) => gridItem(cardHtml('clause', c)), {sectionKey: 'pestle-clauses'})}
    ${paginatedSectionHtml('Related controls', 'Controls linked to those relevant clauses.', controls, (c) => gridItem(cardHtml('control', c)), {sectionKey: 'pestle-controls'})}
  `;
  wireHomeControls();
}


async function loadInterestedParty(id, push = true) {
  enter({kind: 'interested_party', id, label: 'Interested Party', title: 'Interested Party connections', subtitle: 'Interested Parties connect Nature of Interest, communications and controls.'}, push);
  setLoading('Loading Interested Party connections…');
  const item = await safeGet(apiUrl(`/api/v1/interested-parties/${encodeURIComponent(id)}`), null);
  if (!item) return setEmpty('Interested Party not found, or you do not have permission to view it.');
  const party = normaliseItem('interested_party', item);
  current.label = primaryText('interested_party', party);
  renderCrumbs();
  const controls = item.controls || [];
  const communications = (item.communications || []).map((c) => normaliseItem('communication', c));
  explorerCanvas.innerHTML = `
    ${nodeHeader('interested_party', party, item.nature?.name || '')}
    ${paginatedSectionHtml('Mapped controls', 'Controls currently linked to this interested party.', controls, (c) => gridItem(cardHtml('control', c)), {sectionKey: 'interested-party-controls'})}
    ${paginatedSectionHtml('Communications', 'Communication events, audiences, timing and methods for this interested party.', communications, (c) => gridItem(cardHtml('communication', c, {zoom: false, open: false})), {sectionKey: 'interested-party-communications'})}
  `;
  wireHomeControls();
}

async function loadAudit(id, push = true) {
  enter({kind: 'audit', id, label: 'Audit', title: 'Audit connections', subtitle: 'Audits connect scope, sampled evidence, findings and reports.'}, push);
  setLoading('Loading audit connections…');
  const audit = await safeGet(`/api/v1/audits/${encodeURIComponent(id)}`, null);
  if (!audit) return setEmpty('Audit not found, or you do not have permission to view it.');
  current.label = audit.title || 'Audit';
  renderCrumbs();

  const controls = audit.scoped_controls || [];
  const clauses = audit.scoped_clauses || [];
  const evidence = (audit.evidence || []).map((x) => ({...x, id: x.event_id || x.id, summary: x.title || x.notes || x.evidence_url || 'Audit evidence', timestamp: x.added_at}));

  explorerCanvas.innerHTML = `
    ${nodeHeader('audit', audit, audit.status || 'open')}
    ${paginatedSectionHtml('Scoped clauses', 'Clauses in this audit engagement.', clauses, (c) => gridItem(cardHtml('clause', c)), {sectionKey: 'audit-clauses'})}
    ${paginatedSectionHtml('Scoped controls', 'Controls in this audit engagement.', controls, (c) => gridItem(cardHtml('control', c)), {sectionKey: 'audit-controls'})}
    ${paginatedSectionHtml('Sampled evidence', 'Evidence attached to this audit.', evidence, (e) => gridItem(cardHtml('evidence', e, {zoom: false, open: !!e.event_id})), {sectionKey: 'audit-evidence'})}
  `;
  wireHomeControls();
}

async function loadEvidence(id, push = true) {
  enter({kind: 'evidence', id, label: 'Evidence', title: 'Evidence connections', subtitle: 'Evidence connects sources, controls, audits, artifacts, incidents and questions.'}, push);
  setLoading('Loading evidence connections…');
  const [event, audits, questions] = await Promise.all([
    safeGet(apiUrl(`/api/v1/events/${encodeURIComponent(id)}`), null),
    safeGet(`/api/v1/audits/by-event/${encodeURIComponent(id)}`, {items: []}),
    safeGet(`/api/v1/events/${encodeURIComponent(id)}/questions`, {threads: []}),
  ]);
  if (!event) return setEmpty('Evidence event not found.');
  current.label = shortText(event.summary || event.identifier || 'Evidence', 80);
  renderCrumbs();

  const source = event.source ? {id: event.source, source: event.source, label: event.source} : null;
  const auditItems = (audits.items || []).map((x) => x.audit).filter(Boolean);
  const artifactItems = (event.artifacts || []).map((x) => normaliseItem('artifact', x));
  const incidentItems = (event.incidents || []).map((x) => normaliseItem('incident', x));
  const questionItems = (questions.threads || []).map((x) => normaliseItem('question', x));

  explorerCanvas.innerHTML = `
    ${nodeHeader('evidence', event, `${event.timestamp ? fmtTs(event.timestamp) : ''} · ${event.source || ''}`.replace(/^ · | · $/g, ''))}
    ${source ? sectionHtml('Source', 'Where this evidence came from.', gridItem(cardHtml('source', source))) : ''}
    ${paginatedSectionHtml('Mapped controls', 'Controls supported by this event.', event.controls || [], (c) => gridItem(cardHtml('control', c)), {sectionKey: 'evidence-controls'})}
    ${paginatedSectionHtml('Audit usage', 'Audits that sampled this event as evidence.', auditItems, (a) => gridItem(cardHtml('audit', a)), {sectionKey: 'evidence-audits'})}
    ${paginatedSectionHtml('Artifacts', 'Captured files and payloads attached to this event.', artifactItems, (a) => gridItem(cardHtml('artifact', a)), {sectionKey: 'evidence-artifacts'})}
    ${paginatedSectionHtml('Incidents', 'External incidents created from this event.', incidentItems, (i) => gridItem(cardHtml('incident', i)), {sectionKey: 'evidence-incidents'})}
    ${paginatedSectionHtml('Questions', 'Auditor questions and replies attached to this evidence.', questionItems, (q) => gridItem(cardHtml('question', q)), {sectionKey: 'evidence-questions'})}
  `;
  wireHomeControls();
}


async function loadEffectivenessMeasure(id, push = true) {
  enter({kind: 'effectiveness_measure', id, label: 'Effectiveness Measure', title: 'Effectiveness Measure connections', subtitle: 'Effectiveness Measures connect ISMS controls to recorded metrics and their sources.'}, push);
  setLoading('Loading Effectiveness Measure connections…');
  const item = await safeGet(apiUrl(`/api/v1/isms/effectiveness-measures/${encodeURIComponent(id)}`), null);
  if (!item) return setEmpty('Effectiveness Measure not found, or you do not have permission to view it.');
  const measure = normaliseItem('effectiveness_measure', item);
  current.label = primaryText('effectiveness_measure', measure);
  renderCrumbs();

  const controls = Array.isArray(item.controls) ? item.controls : [];
  const entries = (Array.isArray(item.metric_entries) ? item.metric_entries : [])
    .map((entry) => normaliseItem('metric_entry', entry));
  const sourceMap = new Map();
  for (const entry of entries) {
    const src = String(entry.source_type || '').trim();
    if (!src || src === 'other' || sourceMap.has(src)) continue;
    sourceMap.set(src, {id: src, source: src, label: src});
  }
  const sources = Array.from(sourceMap.values());

  explorerCanvas.innerHTML = `
    ${nodeHeader('effectiveness_measure', measure, measure.target_display ? `Target ${measure.target_display}` : 'ISMS effectiveness metric')}
    ${paginatedSectionHtml('Linked controls', 'Controls this effectiveness measure is used to assess.', controls, (c) => gridItem(cardHtml('control', c)), {sectionKey: 'effectiveness-controls'})}
    ${paginatedSectionHtml('Metric sources', 'KEEN sources that have reported metric entries against this measure.', sources, (src) => gridItem(cardHtml('source', src)), {sectionKey: 'effectiveness-sources'})}
    ${paginatedSectionHtml('Recent metric entries', 'Recorded numeric or qualitative results for this measure.', entries, (entry) => gridItem(cardHtml('metric_entry', entry, {zoom: false, open: Boolean(entry.source_event_id)})), {sectionKey: 'effectiveness-metric-entries'})}
  `;
  wireHomeControls();
}

async function loadSource(sourceName, push = true) {
  enter({kind: 'source', id: sourceName, label: sourceName || 'Source', title: 'Source connections', subtitle: 'Sources connect inbound systems to evidence and mapped controls.'}, push);
  setLoading('Loading source connections…');
  const [sources, events, effectiveness] = await Promise.all([
    safeGet(apiUrl('/api/v1/sources', {}, {includeFramework: false}), {items: []}),
    safeGet(apiUrl('/api/v1/events', {source: sourceName, limit: PAGE_SIZE}), {items: []}),
    safeGet(apiUrl(`/api/v1/sources/${encodeURIComponent(sourceName)}/effectiveness-measures`), {items: []}),
  ]);
  const src = (sources.items || []).find((x) => String(x.source) === String(sourceName)) || {id: sourceName, source: sourceName, label: sourceName};
  current.label = src.label || src.source || 'Source';
  renderCrumbs();
  const effectivenessItems = (effectiveness.items || []).map((x) => normaliseItem('effectiveness_measure', x));
  explorerCanvas.innerHTML = `
    ${nodeHeader('source', src, 'Explore evidence ingested from this source.')}
    ${paginatedSectionHtml('Related Effectiveness Measures', 'Measures with metric entries recorded against this source.', effectivenessItems, (m) => gridItem(cardHtml('effectiveness_measure', m)), {sectionKey: 'source-effectiveness-measures'})}
    ${paginatedSectionHtml('Recent evidence', 'Events ingested from this source.', events.items || [], (e) => gridItem(cardHtml('evidence', e)), {sectionKey: 'source-evidence'})}
  `;
  wireHomeControls();
}

function nodeHeader(kind, item, meta = '') {
  const cfg = collectionConfig(kind);
  const title = primaryText(kind, item);
  const href = entityHref(kind, item);
  const body = item.threat_summary || item.summary || item.notes || item.description || '';
  return `<div class="home-node-header border rounded p-3 mb-4 bg-light">
    <div class="d-flex flex-wrap gap-3 align-items-start justify-content-between">
      <div class="d-flex gap-3 align-items-start min-w-0 flex-grow-1">
        <div class="home-entry-icon text-bg-${esc(cfg.tone)}"><i class="bi ${esc(cfg.icon)}" aria-hidden="true"></i></div>
        <div class="min-w-0 flex-grow-1">
          <div class="small-muted text-uppercase fw-semibold">${esc(cfg.singular)}</div>
          <h2 class="h4 mb-1 home-node-heading">${esc(title)}</h2>
          ${meta ? `<div class="small-muted">${esc(meta)}</div>` : ''}
          ${body ? `<p class="mb-0 mt-2 text-secondary">${esc(shortText(body, 260))}</p>` : ''}
        </div>
      </div>
      <a class="btn btn-outline-primary flex-shrink-0" href="${esc(href)}"${openAttrs()}><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Open this</a>
    </div>
  </div>`;
}

async function zoomTo(kind, id, push = true, options = {}) {
  const k = String(kind || '');
  try {
    if (k === 'root') return showRoot();
    if (KINDS[k]) return await loadCollection(k, push, options.offset || 0);
    if (k === 'control') return await loadControl(id, push);
    if (k === 'clause') return await loadClause(id, push);
    if (k === 'risk') return await loadRisk(id, push);
    if (k === 'pestle_item') return await loadPestleItem(id, push);
    if (k === 'interested_party') return await loadInterestedParty(id, push);
    if (k === 'audit') return await loadAudit(id, push);
    if (k === 'effectiveness_measure') return await loadEffectivenessMeasure(id, push);
    if (k === 'evidence') return await loadEvidence(id, push);
    if (k === 'source') return await loadSource(id, push);
    if (['artifact', 'incident', 'question'].includes(k)) {
      window.open(entityHref(k, {id}), '_blank', 'noopener,noreferrer');
      return;
    }
  } catch (e) {
    toast(status, `Could not load connection layer: ${String(e)}`, 'danger');
    setEmpty('Could not load this connection layer.');
  }
}

btnBack?.addEventListener('click', () => {
  if (transitionActive) return;
  const prev = stack.pop();
  if (!prev) return;
  transitionActive = true;
  animateLayerSwap(async () => {
    if (prev.kind === 'root') {
      showRoot();
      return;
    }
    current = null;
    await zoomTo(prev.kind, prev.id || '', false, {offset: prev.offset || 0});
  }, 'back').finally(() => {
    transitionActive = false;
  });
});

btnRoot?.addEventListener('click', () => {
  if (transitionActive) return;
  transitionActive = true;
  animateLayerSwap(async () => {
    stack = [];
    showRoot();
  }, 'back').finally(() => {
    transitionActive = false;
  });
});

document.getElementById('homeSearch')?.addEventListener('submit', (ev) => {
  ev.preventDefault();
  const q = String(document.getElementById('homeQ')?.value || '').trim();
  const url = new URL('/events.html', location.origin);
  if (q) url.searchParams.set('q', q);
  if (framework) url.searchParams.set('framework', framework);
  location.href = url.pathname + url.search;
});

await resolveFramework();
await loadEntryCards();
showRoot();
startLatestEvidenceTicker();
