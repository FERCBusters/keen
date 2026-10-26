// Load complete first-party ES modules into an isolated DOM. Only the external
// design-system/network boundary is substituted; Keen functions run unchanged.
import { JSDOM, VirtualConsole } from 'jsdom';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
const publicRoot = new URL('../../public/', import.meta.url);
export async function loadUi(
  path,
  {
    html = '',
    vendor = {},
    url = 'https://keen.example/events.html?framework=F1',
    globals = {},
    setup,
    scripts = [],
    // Replacements for individual first-party modules, keyed by their path
    // under public/ (for example 'pages/confirm-modal.js').
    modules = {},
  } = {},
) {
  const runtimeErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (error) => {
    if (error.type === 'unhandled exception') runtimeErrors.push(error.detail || error);
  });
  const dom = new JSDOM(html, { url, virtualConsole });
  const { window } = dom;
  setup?.(window);
  const esc = (value) =>
    String(value ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  const unsupported = () => {
    throw new Error('Unexpected design-system/network call');
  };
  const external = {
    esc,
    getCurrentFramework: () => 'F1',
    getLoadedFrameworkCatalog: () => null,
    getUiConfig: () => ({}),
    apiGet: unsupported,
    apiPost: unsupported,
    apiPut: unsupported,
    apiDelete: unsupported,
    fmtTs: (value) => String(value),
    safeExternalHref: unsupported,
    toast: unsupported,
    errorText: (err) => {
      console.debug('STUB-ERRORTEXT', err);
      return String(err?.message || err || '');
    },
    sourceBadgeHtml: unsupported,
    initNavbar: unsupported,
    initCollapsibleFilterSections: unsupported,
    ...vendor,
  };
  const context = vm.createContext({
    window,
    document: window.document,
    Node: window.Node,
    Event: window.Event,
    CustomEvent: window.CustomEvent,
    Element: window.Element,
    HTMLElement: window.HTMLElement,
    HTMLSelectElement: window.HTMLSelectElement,
    SVGElement: window.SVGElement,
    Option: window.Option,
    location: window.location,
    history: window.history,
    navigator: window.navigator,
    confirm: () => true,
    CSS: { escape: (value) => String(value).replace(/[^a-zA-Z0-9_-]/g, (c) => '\\' + c) },
    URL,
    URLSearchParams,
    structuredClone,
    console,
    setTimeout,
    clearTimeout,
    ...globals,
  });
  for (const source of scripts) vm.runInContext(source, context);
  const cache = new Map();
  const vendorModule = new vm.SyntheticModule(
    Object.keys(external),
    function () {
      for (const [k, v] of Object.entries(external)) this.setExport(k, v);
    },
    { context },
  );
  async function moduleFor(url) {
    if (!cache.has(url.href)) {
      cache.set(
        url.href,
        readFile(url, 'utf8').then(
          (source) =>
            new vm.SourceTextModule(source, {
              context,
              identifier: url.href,
              importModuleDynamically: async (specifier, parent) => {
                const child = await linker(specifier, parent);
                if (child.status === 'unlinked') await child.link(linker);
                await child.evaluate();
                return child;
              },
            }),
        ),
      );
    }
    return cache.get(url.href);
  }
  async function linker(specifier, parent) {
    if (specifier === '/vendor/mosp-design-system/app.js') return vendorModule;
    const next = specifier.startsWith('/')
      ? new URL(specifier.slice(1), publicRoot)
      : new URL(specifier, parent.identifier);
    if (!next.href.startsWith(publicRoot.href)) throw new Error('Unexpected module ' + specifier);
    const replacement = modules[next.href.slice(publicRoot.href.length)];
    if (replacement) {
      const key = next.href;
      if (!cache.has(key)) {
        cache.set(
          key,
          Promise.resolve(
            new vm.SyntheticModule(
              Object.keys(replacement),
              function () {
                for (const [k, v] of Object.entries(replacement)) this.setExport(k, v);
              },
              { context },
            ),
          ),
        );
      }
      return cache.get(key);
    }
    return moduleFor(next);
  }
  const module = await moduleFor(new URL(path, publicRoot));
  // Let VM link the whole graph; recursively linking dependencies races when
  // sibling modules share an in-flight dependency.
  await module.link(linker);
  await module.evaluate();
  return {
    api: module.namespace,
    window,
    document: window.document,
    close: () => {
      window.close();
      if (runtimeErrors.length) throw new AggregateError(runtimeErrors, 'Unhandled page errors');
    },
  };
}
