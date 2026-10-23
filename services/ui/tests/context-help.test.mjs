import test from 'node:test';
import assert from 'node:assert/strict';
import { access, readFile } from 'node:fs/promises';
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
  // The overview introduces the whole area; Features has its own topic.
  ui.document.querySelector('section').id = 'pane-overview';
  assert.equal(ui.api.resolveHelp(trigger).title, 'Administration');
  assert.equal(ui.api.resolveHelp(trigger).url, '/help/administrator.html');
  ui.document.querySelector('section').id = 'pane-features';
  assert.equal(
    ui.api.resolveHelp(trigger).url,
    '/help/administrator-deployment.html#installation-features',
  );
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

test('page help follows the page and the active tab', async (t) => {
  const ui = await loadUi('context-help.js', {
    html: '<body data-page="stories"><ul class="nav nav-tabs" role="tablist"><li><button class="nav-link active" role="tab" id="timelineTab">Timeline</button></li><li><button class="nav-link" role="tab" id="flowTab">Flow chart</button></li><li><button class="nav-link" role="tab" id="commentsTab">Comments</button></li><li><button class="nav-link" role="tab" id="questionsTab">Questions</button></li><li><button class="nav-link" role="tab" id="changelogTab">Changelog</button></li></ul><a data-help-topic="page">Help</a></body>',
  });
  t.after(ui.close);
  const trigger = ui.document.querySelector('a');
  const select = (id) => {
    for (const tab of ui.document.querySelectorAll('[role="tab"]')) {
      tab.classList.toggle('active', tab.id === id);
      tab.setAttribute('aria-selected', String(tab.id === id));
    }
    return ui.api.resolveHelp(trigger);
  };
  assert.equal(select('timelineTab').url, '/help/using-events.html#stories');
  assert.equal(select('flowTab').title, 'Story flow chart');
  assert.equal(select('flowTab').url, '/help/using-events.html#build-the-story');
  assert.equal(select('commentsTab').url, '/help/using-events.html#share-the-story');
  assert.equal(select('questionsTab').url, '/help/using-events.html#questions-and-incidents');
  assert.equal(
    select('changelogTab').url,
    '/help/administrator-audit-trail.html#audit-trail-entity-changes',
  );
  // Tabs that are not a subject of their own keep the page topic.
  ui.document.getElementById('timelineTab').id = 'detailsTab';
  assert.equal(select('detailsTab').url, '/help/using-events.html#stories');
  // Other pages: ISMS tabs, recurring visualisation tabs and the account security tab.
  const cases = [
    ['isms', 'documentsTab', '/help/using-documents.html'],
    ['isms', 'assetsTab', '/help/using-people-assets.html'],
    ['isms', 'effectivenessTab', '/help/using-measures.html'],
    ['isms', 'overviewTab', '/help/using-isms.html'],
    ['events', 'eventsVisualisationsTab', '/help/using-operations.html#visualisation-tabs'],
    ['events', 'eventsRegisterTab', '/help/using-events.html'],
    ['source', 'sourceInsightsTab', '/help/using-events.html#source-insights'],
    ['source', 'sourceEventsTab', '/help/using-events.html'],
    ['risks', 'riskVisualisationsTab', '/help/using-operations.html#visualisation-tabs'],
    ['account', 'security-tab', '/help/using-account.html#local-account-security'],
    ['account', 'preferences-tab', '/help/using-account.html'],
    ['pestle', 'pestleItemsTab', '/help/using-isms.html#context'],
    ['questions', 'none', '/help/using-events.html#questions-and-incidents'],
    ['isms-document', 'documentDetailsTab', '/help/using-documents.html'],
    ['soa', 'soaControlsTab', '/help/using-frameworks.html#4-controls-clauses-and-applicability'],
    ['controls', 'controlRequirementsTab', '/help/using-frameworks.html#control-requirements'],
    ['controls', 'controlEvidenceTab', '/help/using-events.html'],
  ];
  for (const [page, tab, url] of cases) {
    ui.document.body.dataset.page = page;
    ui.document.getElementById('detailsTab').id = tab;
    assert.equal(select(tab).url, url, `${page} / ${tab}`);
    ui.document.getElementById(tab).id = 'detailsTab';
  }
});

test('SWOT pages keep the SWOT guide for their own visualisation tab', async (t) => {
  const ui = await loadUi('context-help.js', {
    html: '<body data-page="isms"><ul class="nav nav-tabs"><li><button class="nav-link active" role="tab" id="visualisationTab">Views</button></li></ul><a data-help-topic="page">Help</a></body>',
    url: 'https://keen.example/swot.html',
  });
  t.after(ui.close);
  assert.equal(ui.api.resolveHelp(ui.document.querySelector('a')).url, '/help/using-swot.html');
});

test('every help topic points at a published chapter and anchor', async (t) => {
  const ui = await loadUi('context-help.js', {
    html: '<body data-page="home"><a data-help-topic="page">Help</a></body>',
  });
  t.after(ui.close);
  const trigger = ui.document.querySelector('a');
  const keys = [
    'start',
    'sources',
    'connection',
    'inputs',
    'mapping',
    'events',
    'frameworks',
    'audits',
    'auditTrail',
    'mitigator',
    'risks',
    'swot',
    'isms',
    'account',
    'agents',
    'retention',
    'integrations',
    'identity',
    'scheduling',
    'diary',
    'stories',
    'storyFlow',
    'storyComments',
    'visualisations',
    'questions',
    'changelog',
    'context',
    'objectives',
    'documents',
    'peopleAssets',
    'measures',
    'meetings',
    'soa',
    'accountSecurity',
    'adminOverview',
    'features',
    'sourceInsights',
    'controlRequirements',
  ];
  for (const key of keys) {
    trigger.dataset.helpTopic = key;
    const { url, title } = ui.api.resolveHelp(trigger);
    assert.ok(title, key);
    const [file, anchor] = url.split('#');
    const path = new URL('../public' + file, import.meta.url);
    const html = await readFile(path, 'utf8');
    if (anchor) assert.ok(html.includes(`id="${anchor}"`), `${key}: #${anchor} in ${file}`);
  }
});
