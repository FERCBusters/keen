// Page help when the browser has no modal dialog and when the reader clicks
// again or leaves while the drawer loads, and the guide each page and admin
// pane offers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { access, readdir, readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const publicRoot = new URL('../public/', import.meta.url);
const pageHtml = (name) => readFile(new URL(name, publicRoot), 'utf8');

// jsdom has HTMLDialogElement without its modal methods.
function modalDialogs(window) {
  window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
    this.dispatchEvent(new window.Event('close'));
  };
}

// The launcher with a help module whose arrival the test controls.
async function launcher(t, { setup = modalDialogs } = {}) {
  const ui = await loadUi('help-launcher.js', {
    html: '<body><p id="text">Text</p><a href="/help/" data-help-topic="page">Help</a></body>',
    setup,
  });
  t.after(ui.close);
  const loads = [];
  const opened = [];
  ui.api.initContextHelp(() => {
    let resolve;
    const loaded = new Promise((r) => (resolve = r));
    loads.push(() => resolve({ openContextHelp: (trigger) => opened.push(trigger) }));
    return loaded;
  });
  const trigger = ui.document.querySelector('a');
  // Whether the page let the click follow the link.
  const click = (target = trigger, init = {}) =>
    target.dispatchEvent(
      new ui.window.MouseEvent('click', { bubbles: true, cancelable: true, ...init }),
    );
  return { ...ui, trigger, loads, opened, click };
}

test('browsers without a modal dialog follow the Help link itself', async (t) => {
  for (const [name, setup] of [
    ['no showModal', () => {}],
    ['no HTMLDialogElement', (window) => delete window.HTMLDialogElement],
  ]) {
    const ui = await launcher(t, { setup });
    assert.equal(ui.click(), true, name);
    assert.equal(ui.loads.length, 0, name);
    assert.equal(ui.trigger.hasAttribute('aria-busy'), false, name);
    assert.equal(ui.trigger.dataset.helpTopic, 'page', name);
  }
});

test('modified, secondary and already handled clicks keep the ordinary link', async (t) => {
  const ui = await launcher(t);
  for (const init of [
    { button: 1 },
    { ctrlKey: true },
    { metaKey: true },
    { shiftKey: true },
    { altKey: true },
  ])
    assert.equal(ui.click(ui.trigger, init), true, JSON.stringify(init));
  ui.trigger.addEventListener('click', (event) => event.preventDefault(), { once: true });
  ui.click();
  // Clicks elsewhere, including ones aimed at the document itself, are not help.
  assert.equal(ui.click(ui.document.getElementById('text')), true);
  assert.equal(ui.click(ui.document), true);
  assert.equal(ui.loads.length, 0);
  // A plain click on the same link opens the drawer instead of the link.
  assert.equal(ui.click(), false);
  assert.equal(ui.loads.length, 1);
});

test('a second click while help loads waits for the first', async (t) => {
  const ui = await launcher(t);
  assert.equal(ui.click(), false);
  assert.equal(ui.trigger.getAttribute('aria-busy'), 'true');
  // The second click neither follows the link nor loads help again.
  assert.equal(ui.click(), false);
  assert.equal(ui.loads.length, 1);
  ui.loads[0]();
  await settle(() => !ui.trigger.hasAttribute('aria-busy'));
  assert.deepEqual(ui.opened, [ui.trigger]);
  // Once it has opened, a later click loads it again.
  ui.click();
  assert.equal(ui.loads.length, 2);
});

test('help that arrives after its link has left the page opens nothing', async (t) => {
  const ui = await launcher(t);
  ui.click();
  ui.trigger.remove();
  ui.loads[0]();
  await settle(() => !ui.trigger.hasAttribute('aria-busy'));
  assert.deepEqual(ui.opened, []);
});

// The navigation bar adds each page's Help link, as app.js does.
function pageHelpLink(document) {
  const help = document.createElement('a');
  help.dataset.helpTopic = 'page';
  document.getElementById('navbar').append(help);
  return help;
}

test('every page with the navigation bar names a guide of its own', async (t) => {
  const ui = await loadUi('context-help.js');
  t.after(ui.close);
  const names = (await readdir(publicRoot)).filter((name) => name.endsWith('.html')).sort();
  const checked = [];
  for (const name of names) {
    const served = new ui.window.DOMParser().parseFromString(await pageHtml(name), 'text/html');
    if (!served.getElementById('navbar')) continue;
    ui.document.replaceChild(
      ui.document.adoptNode(served.documentElement),
      ui.document.documentElement,
    );
    ui.window.history.replaceState(null, '', '/' + name);
    const help = pageHelpLink(ui.document);
    // With no tab of its own chosen, the page's guide applies.
    for (const tab of ui.document.querySelectorAll('[role="tab"]')) {
      tab.classList.remove('active');
      tab.setAttribute('aria-selected', 'false');
    }
    const page = ui.document.body.dataset.page;
    assert.ok(page, `${name} names its page`);
    const topic = ui.api.resolveHelp(help);
    if (page !== 'home') assert.notEqual(topic.title, 'Getting started', `${name} (${page})`);
    await access(new URL('.' + topic.url.split('#')[0], publicRoot));
    checked.push(name);
  }
  assert.ok(checked.length >= 40, `expected the application pages, found ${checked.length}`);
  assert.ok(checked.includes('risk-wizard.html'));
});

const ADMIN_PANES = {
  'pane-overview': 'Administration',
  'pane-features': 'Features',
  'pane-evidence-config': 'Sources and inputs',
  'pane-agents': 'Keen Agents',
  'pane-integrations': 'Custom API ingesters',
  'pane-framework-editor': 'Frameworks and scope',
  'pane-ingest': 'Run and schedule collection',
  'pane-diary': 'Diary evidence',
  'pane-retention': 'Evidence retention',
  'pane-users': 'Accounts and access',
  'pane-groups': 'Accounts and access',
  'pane-remap': 'Map evidence to controls',
  'pane-audit': 'Audit trail and change history',
};

test('the admin page always has one pane active, and each pane has its guide', async (t) => {
  const ui = await loadUi('context-help.js', {
    html: await pageHtml('admin.html'),
    url: 'https://keen.example/admin.html',
  });
  t.after(ui.close);
  const help = pageHelpLink(ui.document);
  const panes = [...ui.document.querySelectorAll('#adminTabContent > .tab-pane')];
  assert.deepEqual(panes.map((pane) => pane.id).sort(), Object.keys(ADMIN_PANES).sort());
  const active = () => [...ui.document.querySelectorAll('#adminTabContent > .tab-pane.active')];
  assert.deepEqual(
    active().map((pane) => pane.id),
    ['pane-overview'],
  );
  // Bootstrap's tab switch moves the active class from one pane to the next.
  for (const pane of panes) {
    for (const other of panes) other.classList.toggle('active', other === pane);
    assert.equal(ui.api.resolveHelp(help).title, ADMIN_PANES[pane.id], pane.id);
  }
});

test('the SWOT page keeps its own guide on tabs without a topic of their own', async (t) => {
  const open = async (name) => {
    const ui = await loadUi('context-help.js', {
      html: await pageHtml(name),
      url: 'https://keen.example/' + name,
    });
    t.after(ui.close);
    const help = pageHelpLink(ui.document);
    const select = (id) => {
      for (const tab of ui.document.querySelectorAll('.nav-tabs [role="tab"]'))
        tab.classList.toggle('active', tab.id === id);
      return ui.api.resolveHelp(help);
    };
    return select;
  };
  const swot = await open('swot.html');
  assert.equal(swot('detailsTab').url, '/help/using-swot.html');
  assert.equal(swot('relatedTab').title, 'SWOT analysis and objective planning');
  assert.equal(swot('questionsTab').url, '/help/using-events.html#questions-and-incidents');
  // Other ISMS pages share its page name and keep the ISMS guide.
  const assurance = await open('assurance.html');
  assert.equal(assurance('peopleTab').url, '/help/using-isms.html');
});

test('opening help while its drawer is open keeps the one drawer', async (t) => {
  const ui = await loadUi('context-help.js', {
    html: '<body><a data-help-topic="events">Events help</a><a data-help-topic="risks">Risks help</a></body>',
    setup: modalDialogs,
  });
  t.after(ui.close);
  const [events, risks] = ui.document.querySelectorAll('a');
  const drawers = () =>
    [...ui.document.querySelectorAll('dialog')].map((d) => d.querySelector('h2').textContent);
  ui.api.openContextHelp(events);
  ui.api.openContextHelp(risks);
  assert.deepEqual(drawers(), ['Review evidence']);
  ui.document.querySelector('dialog button').click();
  assert.deepEqual(drawers(), []);
  ui.api.openContextHelp(risks);
  assert.deepEqual(drawers(), ['Risks and treatments']);
});
