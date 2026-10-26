import { featureEnabled } from '/pages/feature-availability.js';
import { errorText, initNavbar, apiGet, esc, getCurrentFramework, withFramework } from '/app.js';

// HTML and unversioned modules must come from the same UI release. A browser
// can still hold HTML cached before the server's no-store policy was deployed.
function ensureHomeLayout() {
  const required = [
    'homeRefresh',
    'homeTasks',
    'homeKpis',
    'homeAttention',
    'homeActivity',
    'homeSpark',
    'homeSparkNote',
    'homeActivitySources',
    'homeSources',
    'homeSourcesMore',
    'homeOverview',
    'homeFrameworkName',
  ];
  if (required.every((id) => document.getElementById(id))) return true;
  const url = new URL(location.href);
  const revision = '20261026-home-5';
  if (url.searchParams.get('__keen_home') !== revision) {
    // Preserve the chosen framework and other parameters. A different URL
    // bypasses a fresh cached document without creating a reload loop.
    url.searchParams.set('__keen_home', revision);
    location.replace(url.pathname + url.search + url.hash);
  } else {
    const message = document.createElement('div');
    message.className = 'alert alert-warning m-4';
    message.setAttribute('role', 'alert');
    message.textContent =
      'The home page and its scripts are from different UI versions. Please reload without cache. If this persists, rebuild and redeploy the Keen UI and clear any reverse-proxy HTML cache.';
    (document.querySelector('main') || document.body).prepend(message);
  }
  return false;
}

if (ensureHomeLayout()) {
  const me = await initNavbar();

  let framework = getCurrentFramework();
  let overview = null;
  let frameworkInfo = null;
  // 30-day activity overview, reused for the sparkline and the source list.
  let activity = null;

  // Render sentinel: a failed load is reported, never dressed up as "no data".
  const LOAD_FAILED = { failed: true };

  async function safeGet(url, fallback = null) {
    try {
      return await apiGet(url);
    } catch (err) {
      if (fallback === null) {
        // Permissions and missing records read like the not-found layer;
        // transport and other failures get the load-failure reporting.
        if (/\b(403|404)\b/.test(errorText(err))) return null;
        console.debug(`Home load failed: ${url}`, err);
        return LOAD_FAILED;
      }
      return fallback;
    }
  }

  // The catalog is fetched at most once per page load; the navbar also keeps
  // its own copy.
  let catalogData = null;

  async function loadCatalog() {
    if (catalogData) return catalogData;
    catalogData = await safeGet('/api/v1/frameworks', null);
    return catalogData;
  }

  async function resolveFramework() {
    framework = getCurrentFramework();
    if (framework) return framework;

    const catalog = await loadCatalog();
    const items = Array.isArray(catalog?.items) ? catalog.items : [];
    const selected = String(
      catalog?.default ||
        items.find((x) => x?.is_default)?.slug ||
        items.find((x) => Number(x?.control_count || 0) > 0)?.slug ||
        items[0]?.slug ||
        '',
    ).trim();
    framework = selected || getCurrentFramework(selected) || '';
    return framework;
  }

  function apiUrl(path, params = {}, { includeFramework = true } = {}) {
    const u = new URL(String(path || '/'), window.location.origin);
    if (includeFramework && framework) u.searchParams.set('framework', framework);
    for (const [key, value] of Object.entries(params || {})) {
      if (value === undefined || value === null || value === '') continue;
      u.searchParams.set(key, String(value));
    }
    return `${u.pathname}${u.search}`;
  }

  function homeLink(href) {
    return esc(withFramework(href, framework));
  }
  function homeNumber(value) {
    return Number.isFinite(value) ? value.toLocaleString() : '-';
  }

  // Every role-permitted shortcut renders in a compact grid: the navigation is
  // the point of this card, so nothing is graduated behind a discloser.
  function renderHomeTasks() {
    const admin = me?.is_admin;
    const tasks = [
      [admin || me?.can_view_events, 'bi-search', 'Review evidence', '/events.html'],
      [admin, 'bi-diagram-3', 'Create an evidence mapping', '/admin.html#evidence-config'],
      [
        admin || me?.can_manage_audits,
        'bi-clipboard2-check',
        'Plan or run an audit',
        '/audits.html',
      ],
      [
        !admin && me?.can_view_audits && !me?.can_manage_audits,
        'bi-clipboard2-check',
        'Review audits',
        '/audits.html',
      ],
      [
        admin || me?.can_view_risks,
        'bi-exclamation-triangle',
        'Review the risk register',
        '/risks.html',
      ],
      [
        admin || me?.can_manage_isms,
        'bi-speedometer2',
        'Record an effectiveness measurement',
        '/isms.html?tab=effectiveness',
      ],
      [
        !admin && me?.can_view_isms && !me?.can_manage_isms,
        'bi-speedometer2',
        'Review effectiveness measures',
        '/isms.html?tab=effectiveness',
      ],
      [
        admin || me?.can_view_isms,
        'bi-folder2-open',
        admin || me?.can_manage_isms
          ? 'Manage documents and assets'
          : 'Review documents and assets',
        '/isms.html',
      ],
    ];
    const item = ([, icon, label, href]) =>
      `<a class="home-task" href="${homeLink(href)}"><i class="bi ${icon}" aria-hidden="true"></i><span>${esc(label)}</span><i class="bi bi-arrow-right ms-auto" aria-hidden="true"></i></a>`;
    document.getElementById('homeTasks').innerHTML =
      tasks
        .filter((t) => t[0])
        .map(item)
        .join('') || '<p class="small-muted mb-0">No shortcuts are available for your role.</p>';
    document.getElementById('homeSearch').hidden = !(admin || me?.can_view_events);
  }

  // Coverage ring: an SVG progress ring using the theme accent; described for
  // screen readers with role="img" and an explicit label.
  function coverageRing(percent) {
    const clamped = Math.min(Math.max(percent, 0), 100);
    const radius = 18;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference * (1 - clamped / 100);
    return (
      `<svg class="home-kpi-ring" viewBox="0 0 44 44" role="img" aria-label="${esc(percent)}% of in-scope controls covered by evidence">` +
      `<circle class="home-kpi-ring-track" cx="22" cy="22" r="${radius}" fill="none"/>` +
      `<circle class="home-kpi-ring-value" cx="22" cy="22" r="${radius}" fill="none" stroke-linecap="round" stroke-dasharray="${circumference.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}" transform="rotate(-90 22 22)"/></svg>`
    );
  }

  function kpi(label, value, detail, href, ringPercent = null) {
    const ring = ringPercent === null ? '' : coverageRing(ringPercent);
    return `<a class="home-kpi text-decoration-none" href="${homeLink(href)}"><span class="small">${esc(label)}</span><div class="home-kpi-main"><strong>${esc(value)}</strong>${ring}</div><span class="small text-secondary">${esc(detail)}</span></a>`;
  }

  function renderOverview() {
    const kpis = document.getElementById('homeKpis'),
      attention = document.getElementById('homeAttention');
    document.getElementById('homeFrameworkName').textContent =
      frameworkInfo?.name || framework || 'No framework selected';
    if (!overview) {
      kpis.innerHTML = '';
      attention.innerHTML =
        '<p class="text-secondary mb-0">The overview could not be loaded. Use Refresh to try again; workspace links remain available.</p>';
      return;
    }
    const cards = [];
    const coverage = overview.coverage;
    if (overview.control_count > 0) {
      const percent = coverage?.in_scope
        ? Math.round((100 * coverage.with_evidence) / coverage.in_scope)
        : null;
      cards.push(
        kpi(
          'Control evidence coverage',
          percent === null ? homeNumber(overview.control_count) + ' controls' : `${percent}%`,
          coverage
            ? `${coverage.with_evidence} of ${coverage.in_scope} in-scope controls have evidence`
            : "Explore this framework's controls",
          '/controls.html',
          percent,
        ),
      );
    }
    if (overview.clause_count > 0)
      cards.push(
        kpi(
          'Framework clauses',
          homeNumber(overview.clause_count),
          'Explore the clause hierarchy',
          '/clauses.html',
        ),
      );
    if (overview.events) {
      cards.push(
        kpi(
          'Events mapped',
          homeNumber(overview.events.mapped),
          'Attributed to this framework',
          '/events.html',
        ),
      );
      cards.push(
        kpi(
          'Unmapped events',
          homeNumber(overview.events.unmapped),
          'Not mapped to this framework',
          '/events.html?unmapped=true',
        ),
      );
    }
    if (!cards.length)
      cards.push(
        kpi(
          'Framework structure',
          'No entries yet',
          "Add this framework's requirements to begin",
          me?.is_admin ? '/admin.html#framework-editor' : '/help/using.html',
        ),
      );
    kpis.innerHTML = cards.join('');
    // Attention pills only carry live findings; a zero count is absence, not work.
    const counts = [
      [coverage?.without_evidence, 'control gap', 'control gaps', '/controls.html?gaps=1'],
      [overview.overdue_audits, 'overdue audit', 'overdue audits', '/audits.html'],
      [
        overview.measures_outside_threshold,
        'measure outside threshold',
        'measures outside threshold',
        '/isms.html?tab=effectiveness',
      ],
      [
        overview.collectors_with_failures,
        'API collector with failures · all frameworks',
        'API collectors with failures · all frameworks',
        '/admin.html#integrations',
      ],
    ].filter(([count]) => Number.isFinite(count) && count > 0);
    const highlights = counts
      .map(
        ([count, singular, plural, href]) =>
          `<a class="badge rounded-pill text-bg-warning text-decoration-none me-1 mb-2 p-2" href="${homeLink(href)}">${homeNumber(count)} ${esc(count === 1 ? singular : plural)}</a>`,
      )
      .join('');
    const items = overview.attention || [];
    const unmapped =
      overview.events?.unmapped > 0
        ? `<a class="home-attention-item" href="${homeLink('/events.html?unmapped=true')}"><strong>${homeNumber(overview.events.unmapped)} unmapped event${overview.events.unmapped === 1 ? '' : 's'}</strong><div class="small text-secondary">Review unmapped events and create rules for the evidence you need.</div></a>`
        : '';
    attention.innerHTML =
      highlights +
      `<div class="home-attention-items">${unmapped}${items.map((item) => `<a class="home-attention-item" href="${homeLink(item.href)}"><strong>${esc(item.label)}</strong><div class="small text-secondary">${esc(item.detail)}</div></a>`).join('')}</div>` +
      (!items.length && !unmapped
        ? '<p class="small text-secondary mb-2">No attention items found in the checks available to your account.</p>'
        : '<p class="small text-secondary mt-2 mb-1">Showing up to five items per category. Open an item to review it.</p>');
  }
  // The overview band fetches the 30-day activity overview in the same
  // request round as the framework overview; the chart endpoint enforces the
  // same server-side boundary the client gate reflects.
  const maySeeEvents = () =>
    Boolean(me?.is_admin || me?.can_view_events) && featureEnabled(me, 'evidence');

  async function loadOverview() {
    const button = document.getElementById('homeRefresh');
    button.disabled = true;
    try {
      const [catalog, data, timeline] = await Promise.all([
        loadCatalog(),
        safeGet(apiUrl('/api/v1/home')),
        maySeeEvents()
          ? safeGet(
              apiUrl('/api/v1/charts/event_volume_timeseries', {
                days: 30,
                breakdown: 'source',
              }),
            )
          : Promise.resolve(null),
      ]);
      if (data?.failed) {
        // renderOverview shows its own load-failure text; workspace links and
        // tasks stay available.
        overview = null;
        activity = null;
        renderPage();
        return;
      }
      frameworkInfo = catalog?.items?.find((item) => item.slug === framework) || frameworkInfo;
      overview = data;
      activity = timeline?.failed ? { failed: true } : timeline;
      renderPage();
    } finally {
      button.disabled = false;
    }
  }

  function renderActivity() {
    const section = document.getElementById('homeActivity');
    const sparkHolder = document.getElementById('homeSpark');
    const note = document.getElementById('homeSparkNote');
    const sourcesPanel = document.getElementById('homeActivitySources');
    const list = document.getElementById('homeSources');
    const moreHolder = document.getElementById('homeSourcesMore');
    if (!overview?.events || !activity) {
      section.hidden = true;
      return;
    }
    if (activity.failed) {
      section.hidden = false;
      sourcesPanel.hidden = true;
      sparkHolder.removeAttribute('aria-label');
      sparkHolder.innerHTML = '';
      note.textContent = 'The evidence activity summary could not be loaded.';
      return;
    }
    section.hidden = false;
    const items = Array.isArray(activity.items) ? activity.items : [];
    const totals = items.map((item) => Number(item?.total) || 0);
    const collected = totals.reduce((sum, value) => sum + value, 0);
    const mapped = items.reduce((sum, item) => sum + (Number(item?.mapped) || 0), 0);
    const peak = Math.max(0, ...totals);
    const peakDay = peak > 0 ? String(items[totals.indexOf(peak)]?.bucket || '') : '';
    if (collected > 0 && items.length >= 2) {
      const width = 320;
      const height = 64;
      const pad = 4;
      const max = Math.max(peak, 1);
      const points = totals.map((value, i) => [
        pad + (i * (width - 2 * pad)) / (items.length - 1),
        pad + (1 - value / max) * (height - 2 * pad),
      ]);
      const line = points
        .map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(2)} ${p[1].toFixed(2)}`)
        .join(' ');
      const area = `${line} L${width - pad} ${height - pad} L${pad} ${height - pad} Z`;
      sparkHolder.innerHTML =
        `<svg class="home-spark-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">` +
        `<path class="home-spark-area" d="${area}"/><path class="home-spark-line" d="${line}" vector-effect="non-scaling-stroke"/></svg>`;
      sparkHolder.setAttribute(
        'aria-label',
        `Evidence collected per day over the last 30 days, peaking at ${homeNumber(peak)} on ${esc(peakDay)}; ${homeNumber(collected)} collected and ${homeNumber(mapped)} mapped`,
      );
      note.textContent = `${homeNumber(collected)} events collected · ${homeNumber(mapped)} mapped to this framework in the last 30 days`;
    } else {
      sparkHolder.removeAttribute('aria-label');
      sparkHolder.innerHTML = '';
      note.textContent = 'No events collected in the last 30 days.';
    }
    const metas = Array.isArray(activity.sources) ? activity.sources : [];
    const top = metas.filter((m) => Number.isFinite(Number(m?.total))).slice(0, 4);
    if (top.length) {
      sourcesPanel.hidden = false;
      list.innerHTML = top
        .map((m) => {
          const name = String(m.label || m.source || '').trim() || 'Unknown source';
          // Source tints are the account's own palette; only a plain sRGB hex
          // may reach an inline style.
          const validColor = /^#[0-9a-fA-F]{6}$/.test(String(m.color || ''));
          const dot = validColor ? ` style="background:${esc(String(m.color))}"` : '';
          return `<li><a href="${homeLink(`/events.html?source=${encodeURIComponent(String(m.source || ''))}`)}"><span class="home-source-dot"${dot} aria-hidden="true"></span><span class="home-source-name">${esc(name)}</span><span class="home-source-count">${homeNumber(Number(m.total))}</span></a></li>`;
        })
        .join('');
      moreHolder.innerHTML = `<a class="link-subtle" href="${homeLink('/sources.html')}">All sources</a>`;
    } else {
      sourcesPanel.hidden = true;
      list.innerHTML = '';
      moreHolder.innerHTML = '';
    }
  }

  function renderPage() {
    renderHomeTasks();
    renderOverview();
    renderActivity();
  }

  document.getElementById('homeSearch')?.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const q = String(document.getElementById('homeQ')?.value || '').trim();
    const url = new URL('/events.html', location.origin);
    if (q) url.searchParams.set('q', q);
    if (framework) url.searchParams.set('framework', framework);
    location.href = url.pathname + url.search;
  });

  document.getElementById('homeRefresh')?.addEventListener('click', async () => {
    await loadOverview();
  });

  await resolveFramework();
  await loadOverview();
}
