// Run first-party classic scripts (a <script src> without type="module") as a
// browser does: in the page's own global object, one after another, under
// their file URLs so coverage counts them. load-ui.mjs evaluates modules in a
// separate context and its `scripts` carry no file name, so neither reaches
// these files. A test replaces only the boundaries a script reaches (network,
// storage, clock, media queries) in `setup`, before the scripts run.
import { JSDOM, VirtualConsole } from 'jsdom';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import { publicRoot } from './first-party-scripts.mjs';

export async function classicPage({
  html = '',
  url = 'https://keen.example/',
  scripts = [],
  setup,
} = {}) {
  const runtimeErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (error) => {
    if (error.type === 'unhandled exception') runtimeErrors.push(error.detail || error);
  });
  const dom = new JSDOM(html, { url, runScripts: 'outside-only', virtualConsole });
  const { window } = dom;
  setup?.(window);
  const context = dom.getInternalVMContext();
  // `path` is relative to public/, as the page's <script src> resolves it.
  async function run(path) {
    const file = new URL(path, publicRoot);
    new vm.Script(await readFile(file, 'utf8'), { filename: file.href }).runInContext(context);
  }
  for (const path of scripts) await run(path);
  return {
    window,
    document: window.document,
    run,
    close: () => {
      window.close();
      if (runtimeErrors.length) throw new AggregateError(runtimeErrors, 'Unhandled page errors');
    },
  };
}
