import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './load-ui.mjs';

export async function settle(check) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.fail('The page did not reach the expected state');
}

// These adapters represent the external design system only. API calls must be
// explicitly handled by each test; unrecognised calls fail instead of returning {}.
export async function page(
  t,
  name,
  {
    get,
    post,
    patch,
    put,
    remove,
    me = { is_admin: true },
    query = '',
    globals = {},
    setup,
    scripts = [],
    htmlName = name,
    frameworks = [{ slug: 'A', name: 'Framework A', control_count: 2, has_clauses: true }],
    // Replacements for individual design-system functions, such as a
    // controllable debounce or saved-search lookup.
    vendor = {},
    // What the shared confirm dialog answers: true, false, or a function of
    // the confirmAction options. Every call is recorded in `confirmations`.
    confirm = true,
    modules = {},
  } = {},
) {
  const calls = [],
    unexpected = [],
    confirmations = [];
  // Requests the page has started and not yet seen answered, so a test can
  // wait for pages that load their sections in parallel.
  let inflight = 0;
  const route = (method, handler) => async (url, body) => {
    calls.push({ method, url, body });
    if (!handler) throw new Error(`Unexpected ${method} ${url}`);
    const parsed = new URL(url, 'https://keen.example');
    // Shared navbar catalog, independent of the page's API fixture.
    if (method === 'GET' && parsed.pathname === '/api/v1/frameworks')
      return { items: frameworks, default: 'A' };
    inflight += 1;
    try {
      return await handler(parsed, body);
    } catch (error) {
      if (String(error).includes('Unexpected ')) unexpected.push(String(error));
      throw error;
    } finally {
      inflight -= 1;
    }
  };
  let win;
  const html = await readFile(
    new URL('../../public/' + htmlName + '.html', import.meta.url),
    'utf8',
  );
  const ui = await loadUi('pages/' + name + '.js', {
    html,
    scripts,
    modules: {
      'pages/confirm-modal.js': {
        confirmAction: async (options) => {
          confirmations.push(options);
          return typeof confirm === 'function' ? !!confirm(options) : !!confirm;
        },
      },
      ...modules,
    },
    url: 'https://keen.example/' + name + '.html?framework=A' + query,
    setup(window) {
      win = window;
      window.HTMLElement.prototype.scrollIntoView = () => {};
      setup?.(window);
    },
    globals: {
      getComputedStyle: (el) => win.getComputedStyle(el),
      requestAnimationFrame: (fn) => win.setTimeout(fn, 0),
      cancelAnimationFrame: (id) => win.clearTimeout(id),
      TextEncoder,
      TextDecoder,
      performance,
      queueMicrotask,
      FormData,
      Blob,
      setInterval,
      clearInterval,
      localStorage: undefined,
      ...globals,
    },
    vendor: {
      collapseToggleButtonHtml: () => '<button type="button">Toggle</button>',
      initNavbar: async () => me,
      initCollapsibleFilterSections: () => {},
      enableTableSorting: () => {},
      apiPostForm: route('POST', post),
      wireIsoDateTextField: () => {},
      isoDateToDisplay: (s) => s || '',
      dateFormatPlaceholder: () => 'yyyy-mm-dd',
      apiGet: route('GET', get),
      apiPost: route('POST', post),
      apiPatch: route('PATCH', patch),
      apiPut: route('PUT', put),
      apiDelete: route('DELETE', remove),
      fmtTsFilename: (s) => s,
      getSourceMeta: async () => ({}),
      applyTheme: () => {},
      THEMES: ['ink', 'dark'],
      getCurrentFramework: () => 'A',
      getFrameworkCatalog: async () => ({ items: [{ slug: 'A', name: 'Framework A' }] }),
      debounce: (fn) => fn,
      shorten: (s, n) => String(s).slice(0, n),
      tsSortKey: (s) => s || '',
      toast: (el, message, type) => {
        if (el) {
          el.textContent = message;
          el.dataset.alert = type;
          el.style.display = '';
        }
      },
      withFramework: (path, fw) => {
        const u = new URL(path, 'https://keen.example');
        u.searchParams.set('framework', fw);
        return u.pathname + u.search + u.hash;
      },
      qs: (key, fallback) => new URL(win.location.href).searchParams.get(key) ?? fallback,
      qbool: (key, fallback) =>
        new URL(win.location.href).searchParams.has(key)
          ? new URL(win.location.href).searchParams.get(key) === 'true'
          : fallback,
      qint: (key, fallback) => Number(new URL(win.location.href).searchParams.get(key) ?? fallback),
      setQuery: (values) => {
        const u = new URL(win.location.href);
        for (const [k, v] of Object.entries(values))
          v === null ? u.searchParams.delete(k) : u.searchParams.set(k, String(v));
        win.history.replaceState({}, '', u);
      },
      isoDateToDmy: (s) => (s ? s.split('-').reverse().join('/') : ''),
      dmyToIsoDate: (s) =>
        /^\d{2}\/\d{2}\/\d{4}$/.test(s) ? s.split('/').reverse().join('-') : '',
      wireIsoDmyDateField: () => {},
      attachSavedSearchAutocomplete: () => {},
      resolveSavedSearchUrl: () => null,
      showInlineLoading: (el, text) => {
        if (el) el.textContent = text;
      },
      showTableLoading: (el, n, text) => {
        if (el) el.textContent = text;
      },
      sourceLabel: (s, meta) => meta?.[s]?.label || s,
      sourceBadgeHtml: (s) => String(s),
      upstreamLinkHtml: () => '',
      safeExternalHref: (s) => (/^https?:\/\//.test(s) ? s : ''),
      ...vendor,
    },
  });
  t.after(() => {
    ui.close();
    assert.deepEqual(unexpected, [], 'Every API request must have an explicit fixture');
  });
  return {
    ...ui,
    calls,
    confirmations,
    idle: () => inflight === 0,
    el: (id) => ui.document.getElementById(id),
  };
}

// Chooses `value` in the pill picker for the select `id`, as a user clicking
// its chip would; the picker reports the change on the select.
export function pickFilter(ui, id, value) {
  const chip = ui.document.querySelector(
    `#${id}FilterPillModal .keen-filter-pill-grid [data-value="${value}"]`,
  );
  if (!chip) throw new Error(`No ${id} option ${value}`);
  chip.click();
}

// The chips a pill picker offers, as [value, label] pairs.
export function filterChips(ui, id) {
  return [
    ...ui.document.querySelectorAll(`#${id}FilterPillModal .keen-filter-pill-grid [data-value]`),
  ].map((chip) => [
    chip.dataset.value,
    chip.querySelector('.keen-filter-pill-chip-text').textContent,
  ]);
}

// The chips currently chosen in a pill picker.
export function chosenChips(ui, id) {
  return [
    ...ui.document.querySelectorAll(
      `[data-filter-pill-for="${id}"] .keen-filter-pill-inline [data-value]`,
    ),
  ].map((chip) => [
    chip.dataset.value,
    chip.querySelector('.keen-filter-pill-chip-text').textContent,
  ]);
}
