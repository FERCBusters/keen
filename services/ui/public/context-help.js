// Explicit guide associations. Text and URLs are authored here, never taken from form data.
const topics = {
  start: [
    'Your assurance workspace',
    'using-start.html',
    'Collect evidence, map it to framework controls, and review what it supports. Framework scope determines which controls you work with.',
    'Choose a framework, then explore its evidence and coverage.',
  ],
  sources: [
    'Sources and inputs',
    'administrator-sources.html',
    'A source identifies an endpoint or account and its credentials. Inputs select the remote activity to collect. Mapping rules associate collected events with controls.',
    'Save a source, add its inputs, then collect evidence and inspect an event before creating a mapping rule.',
  ],
  connection: [
    'Define or edit a source',
    'administrator-sources.html',
    'The source name identifies where evidence came from. Endpoint and credentials belong to this connection. Saved credentials are encrypted on the server.',
    'Save stores these settings. Add inputs to choose what to collect; enabled sources with inputs can be collected on their schedule. RSS setup includes its feed input.',
  ],
  inputs: [
    'Choose what to collect',
    'administrator-sources.html',
    'An input selects a feed, account, repository, query or other remote collection within this source.',
    'Save makes the input available to scheduled and manual collection. Use Collect now to fetch evidence, then add a mapping rule to relate it to controls.',
  ],
  mapping: [
    'Map evidence to controls',
    'using-mapping.html',
    'A mapping rule selects events and associates them with the controls you choose. Preview the matches and confirm the source, input and framework.',
    'Save applies the rule to future matching evidence. Apply saved rule to past evidence also reconciles the rule’s earlier mappings, removing associations it no longer supports.',
  ],
  events: [
    'Review evidence',
    'using-events.html',
    'Check the event’s source connection, timestamp, structured fields and artifacts. Control mappings show where the event contributes evidence.',
    'Create a mapping rule from an event, sample it into an audit, download a PDF or artifact ZIP, or ask a question. The guide explains each action. Use Visualisations on the Events list to explore the data.',
  ],
  frameworks: [
    'Frameworks and scope',
    'using-frameworks.html',
    'Frameworks organise controls and clauses. Scope determines which requirements your organisation includes. Cross-references can share evidence across frameworks.',
    'Review scope and inherited mappings deliberately when assessing coverage.',
  ],
  audits: [
    'Audits and sampling',
    'using-audits.html',
    'An audit groups a review, its scope, evidence samples and findings. Sampling records the evidence selected for that review.',
    'Check the audit scope and selected evidence before saving your sample or recording a finding.',
  ],
  auditTrail: [
    'Audit trail and change history',
    'administrator-audit-trail.html',
    'Entity changelog explains changes to controls, clauses and risks. Request audit trail shows recorded API activity, response status, timing and client metadata.',
    'Filter by user and the relevant entity or request path, then compare timestamps and field differences when investigating a change.',
  ],
  mitigator: [
    'KEEN Mitigator',
    'using-risks.html#mitigator',
    'Describe a concern in natural language. Local language matching suggests controls, similar existing risks and related assessment signals. KEEN-AF scope also offers reusable library candidates.',
    'Analyse scenario produces suggestions. Review matching terms, existing risks, control scope and ratings before saving an assessment.',
  ],
  risks: [
    'Risks and treatments',
    'using-risks.html',
    'Record the risk, its owner, assessment and treatment. The Reusable Risk Library tab offers scenarios to adapt into your register. Associated controls connect treatments to evidence.',
    'Saving records your assessment. Review the selected controls and ownership as part of the treatment decision.',
  ],
  isms: [
    'ISMS records',
    'using-isms.html',
    'Maintain organisational records such as assets, documents, objectives, meetings and effectiveness measures alongside your evidence.',
    'Check owners, related records and scope before saving. Use the record’s history where available to review changes.',
  ],
  account: [
    'Your account',
    'using-account.html',
    'Manage account preferences and the account settings available for your authentication method.',
    'Keep recovery information securely when setting up multi-factor authentication.',
  ],
  agents: [
    'KEEN Agents',
    'administrator-agents.html',
    'Register an individual collector or create an enrollment profile for a fleet. Each agent receives its own evidence-submission credential.',
    'Save one-time credentials securely. Revoking a profile also revokes credentials issued through that profile.',
  ],
  retention: [
    'Evidence retention',
    'administrator-retention.html',
    'Retention controls how much evidence KEEN keeps. Audit protection and stored-file cleanup are part of the retention workflow.',
    'Preview the affected records before saving a policy or confirming a purge. Read the retention guide before deleting evidence.',
  ],
  integrations: [
    'Custom API ingesters',
    'administrator-api-ingesters.html',
    'A connection supplies endpoint and authentication settings. The ingester defines requests, pagination and how response fields become evidence.',
    'Preview the response and field mappings before enabling scheduled collection.',
  ],
  identity: [
    'Accounts and access',
    'administrator-identity.html',
    'Accounts, groups and permissions control access to KEEN. Administrators manage local account security and authentication settings.',
    'Check the effective permissions before saving. Deactivating an account invalidates its existing sessions.',
  ],
  scheduling: [
    'Run and schedule collection',
    'administrator-scheduling.html',
    'Collection fetches evidence from configured, enabled sources and inputs. A run reports its progress and any skipped inputs.',
    'Running an ingester collects evidence. Use historical mapping to apply a saved mapping rule to evidence already stored.',
  ],
  diary: [
    'Diary Evidence',
    'administrator-diary.html',
    'Record a human observation as evidence, with enough context for a reviewer to understand its source and relevance.',
    'Saving creates an evidence record. Check its timestamp and content before submitting.',
  ],
};
const sourceGuides = {
  elasticsearch: ['Elasticsearch', 'elasticsearch'],
  loki: ['Loki', 'loki'],
  github: ['GitHub', 'github'],
  forgejo: ['Forgejo', 'forgejo'],
  gitea: ['Gitea', 'gitea'],
  gitlab: ['GitLab', 'gitlab'],
  rss: ['RSS / Atom', 'rss'],
  jenkins: ['Jenkins', 'jenkins'],
  taiga: ['Taiga', 'taiga'],
  redmine: ['Redmine', 'redmine'],
  bookstack: ['BookStack', 'bookstack'],
  riskledger: ['Risk Ledger', 'riskledger'],
  google_workspace: ['Google Workspace', 'google-workspace'],
  cloudwatch_logs: ['Amazon CloudWatch Logs', 'cloudwatch'],
  webhook: ['Incoming webhooks', 'webhooks'],
  'keen-agent': ['KEEN Agent', 'agents'],
};
const pages = {
  home: 'start',
  events: 'events',
  questions: 'events',
  sources: 'sources',
  source: 'sources',
  controls: 'frameworks',
  clauses: 'frameworks',
  soa: 'frameworks',
  audits: 'audits',
  risks: 'risks',
  pestle: 'risks',
  mitigator: 'mitigator',
  'interested-party': 'risks',
  'interested-parties': 'risks',
  account: 'account',
};
const panes = {
  'pane-evidence-config': 'sources',
  'pane-agents': 'agents',
  'pane-integrations': 'integrations',
  'pane-framework-editor': 'frameworks',
  'pane-ingest': 'scheduling',
  'pane-diary': 'diary',
  'pane-retention': 'retention',
  'pane-users': 'identity',
  'pane-groups': 'identity',
  'pane-remap': 'mapping',
  'pane-audit': 'auditTrail',
};
const own = (map, key) => (Object.hasOwn(map, key) ? map[key] : null);
export function resolveHelp(trigger) {
  let key = trigger.dataset.helpTopic;
  if (key === 'page') {
    const page = document.body.dataset.page || '';
    const pane = document.querySelector('#adminTabContent > .tab-pane.active');
    key =
      page === 'admin'
        ? own(panes, pane?.id) || 'sources'
        : own(pages, page) || (page.startsWith('isms') ? 'isms' : 'start');
  }
  if (key === 'sources' && document.querySelector('#hub-mapping[aria-selected="true"]'))
    key = 'mapping';
  const topic = own(topics, key) || topics.start;
  const source = trigger.closest('[data-help-source]')?.dataset.helpSource;
  const guide = own(sourceGuides, source);
  return {
    title: topic[0],
    url: '/help/' + topic[1],
    summary: topic[2],
    action: topic[3],
    guide: guide ? { title: guide[0], url: '/help/administrator-' + guide[1] + '.html' } : null,
  };
}

let active;
export function openContextHelp(trigger) {
  if (active?.open) return;
  const topic = resolveHelp(trigger);
  const dialog = document.createElement('dialog');
  dialog.className = 'keen-help-drawer';
  dialog.setAttribute('aria-labelledby', 'keen-help-title');
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const close = element('button', 'Close', 'btn btn-outline-secondary btn-sm');
  close.type = 'button';
  close.autofocus = true;
  close.addEventListener('click', () => dialog.close());
  const header = element('div', '', 'd-flex align-items-center justify-content-between gap-3 mb-4');
  header.append(element('span', 'KEEN · Page help', 'small text-secondary'), close);
  const title = element('h2', topic.title, 'h4');
  title.id = 'keen-help-title';
  const links = element('div', '', 'd-grid gap-2 mt-4');
  for (const [text, href] of [
    [
      topic.guide ? topic.guide.title + ' setup guide ↗' : 'Go to docs ↗',
      topic.guide?.url || topic.url,
    ],
    ['Browse all Help ↗', '/help/'],
  ]) {
    const link = element('a', text, 'btn btn-outline-primary');
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    links.append(link);
  }
  dialog.append(
    header,
    title,
    element('p', topic.summary),
    element('h3', 'Before you continue', 'keen-help-section-title'),
    element('p', topic.action),
    links,
    element(
      'p',
      'Guides open in a new tab so you can keep your place here.',
      'small text-secondary mt-3',
    ),
  );
  dialog.addEventListener(
    'close',
    () => {
      dialog.remove();
      active = null;
      if (trigger.isConnected) trigger.focus();
    },
    { once: true },
  );
  document.body.append(dialog);
  active = dialog;
  dialog.showModal();
}
