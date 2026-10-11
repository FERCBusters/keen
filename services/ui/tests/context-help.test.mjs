import test from 'node:test';
import assert from 'node:assert/strict';
import { access } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

function dialogs(window) {
  window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
    this.dispatchEvent(new window.Event('close'));
  };
}

test('contextual help is lazy, initialisation is idempotent, and modified clicks keep the link', async (t) => {
  const ui = await loadUi('help-launcher.js', {
    html: '<a href="/help/" data-help-topic="page">Help</a>',
    setup: dialogs,
  });
  t.after(ui.close);
  let loads = 0,
    opened = 0;
  const load = async () => {
    loads++;
    return {
      openContextHelp() {
        opened++;
      },
    };
  };
  ui.api.initContextHelp(load);
  ui.api.initContextHelp(load);
  assert.equal(loads, 0);
  const trigger = ui.document.querySelector('a');
  trigger.dispatchEvent(
    new ui.window.MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true }),
  );
  assert.equal(loads, 0);
  trigger.click();
  await settle(() => opened === 1);
  assert.equal(loads, 1);
  assert.equal(trigger.hasAttribute('aria-busy'), false);
});

test('failed optional help load preserves a documentation escape route', async (t) => {
  const ui = await loadUi('help-launcher.js', {
    html: '<a href="/help/" data-help-topic="page">Help</a>',
    setup: dialogs,
  });
  t.after(ui.close);
  ui.api.initContextHelp(async () => {
    throw new Error('offline');
  });
  const trigger = ui.document.querySelector('a');
  trigger.click();
  await settle(() => !trigger.hasAttribute('data-help-topic'));
  assert.equal(trigger.getAttribute('href'), '/help/');
  assert.equal(trigger.target, '_blank');
  assert.match(trigger.rel, /noopener/);
});

test('source help uses fixed guide URLs, leaves form values private, and restores focus', async (t) => {
  const ui = await loadUi('context-help.js', {
    html: '<form data-help-source="elasticsearch"><input type="password" value="private-test-value"><a href="/help/" data-help-topic="connection">Help</a></form>',
    setup: dialogs,
  });
  t.after(ui.close);
  const trigger = ui.document.querySelector('a');
  trigger.focus();
  ui.api.openContextHelp(trigger);
  const drawer = ui.document.querySelector('dialog');
  assert.equal(drawer.open, true);
  assert.equal(
    drawer.querySelector('a').getAttribute('href'),
    '/help/administrator-elasticsearch.html',
  );
  assert.equal(drawer.textContent.includes('private-test-value'), false);
  drawer.querySelector('button').click();
  assert.equal(ui.document.querySelector('dialog'), null);
  assert.equal(ui.document.activeElement, trigger);
  ui.document.querySelector('form').dataset.helpSource = 'javascript:alert(1)';
  trigger.dataset.helpTopic = '__proto__';
  const safe = ui.api.resolveHelp(trigger);
  assert.equal(safe.guide, null);
  assert.equal(safe.url, '/help/using-start.html');
});

test('admin help follows the selected pane and mapping tab', async (t) => {
  const ui = await loadUi('context-help.js', {
    html: '<body data-page="admin"><div id="adminTabContent"><section class="tab-pane active" id="pane-retention"></section></div><button id="hub-mapping" aria-selected="false"></button><a data-help-topic="page">Help</a></body>',
  });
  t.after(ui.close);
  const trigger = ui.document.querySelector('a');
  assert.equal(ui.api.resolveHelp(trigger).url, '/help/administrator-retention.html');
  ui.document.querySelector('section').id = 'pane-evidence-config';
  ui.document.getElementById('hub-mapping').setAttribute('aria-selected', 'true');
  assert.equal(ui.api.resolveHelp(trigger).url, '/help/using-mapping.html');
});

test('every supported source help link resolves to a published guide', async (t) => {
  const ui = await loadUi('context-help.js', {
    html: '<div data-help-source=""><a data-help-topic="inputs">Help</a></div>',
  });
  t.after(ui.close);
  for (const source of [
    'elasticsearch',
    'loki',
    'github',
    'forgejo',
    'gitea',
    'gitlab',
    'rss',
    'jenkins',
    'taiga',
    'redmine',
    'bookstack',
    'riskledger',
    'google_workspace',
    'cloudwatch_logs',
    'webhook',
    'keen-agent',
  ]) {
    ui.document.querySelector('div').dataset.helpSource = source;
    const topic = ui.api.resolveHelp(ui.document.querySelector('a'));
    assert.ok(topic.guide, source);
    await access(new URL('../public' + topic.guide.url, import.meta.url));
  }
});

test('the real lazy module opens page help and handles keyboard dismissal cleanup', async (t) => {
  const ui = await loadUi('help-launcher.js', {
    html: '<body data-page="events"><a href="/help/" data-help-topic="page">Help</a></body>',
    setup: dialogs,
    globals: {
      fetch() {
        throw new Error('Help must not fetch application data');
      },
    },
  });
  t.after(ui.close);
  ui.api.initContextHelp();
  const trigger = ui.document.querySelector('a');
  trigger.click();
  await settle(() => !!ui.document.querySelector('dialog[open]'));
  const dialog = ui.document.querySelector('dialog');
  assert.equal(dialog.querySelector('h2').textContent, 'Review evidence');
  assert.equal(dialog.querySelector('a').getAttribute('href'), '/help/using-events.html');
  // Native Escape closes the dialog; exercise its close-event cleanup here.
  dialog.close();
  assert.equal(ui.document.querySelector('dialog'), null);
  assert.equal(ui.document.activeElement, trigger);
});
