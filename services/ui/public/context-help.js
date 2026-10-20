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
    'Check the event’s source connection, timestamp, structured fields and artifacts. Control mappings show where the event contributes evidence. Suggested controls match the event’s wording against the catalogue; they are proposals for review, nothing is mapped until a rule is saved.',
    'Create a mapping rule from an event, or tick suggested controls and propose a rule with them. Sample the event into an audit, download a PDF or artifact ZIP, or ask a question. The guide explains each action.',
  ],
  sourceInsights: [
    'Source activity insights',
    'using-events.html#source-insights',
    'Insights compare recent activity with this source’s own history: how often it usually sends events, whether it has gone quiet, which days spiked and which summaries keep repeating.',
    'A quiet source can mean a stalled collector, expired credentials or a genuinely idle system; check collection before drawing a control conclusion. Open a repeated unmapped summary to see suggested controls for a rule.',
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
    'Describe a concern in natural language. Local language matching suggests controls, similar existing risks, related assessment signals and reusable library scenarios in any framework.',
    'Analyse scenario produces suggestions; the mascot turns while it runs. Review matching terms, existing risks, control scope and ratings, then Create risk register entry saves the draft as a risk.',
  ],
  risks: [
    'Risks and treatments',
    'using-risks.html',
    'Record the risk, its owner, assessment and treatment. The Reusable Risk Library tab offers scenarios to adapt into your register. Associated controls connect treatments to evidence.',
    'Saving records your assessment. Review the selected controls and ownership as part of the treatment decision.',
  ],
  swot: [
    'SWOT analysis and objective planning',
    'using-swot.html',
    'Record context, significance and responses, then link framework controls, clauses and related records.',
    'Use quadrants, status boards and timelines to review the work. The guide explains hierarchy, questions and audit sampling.',
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
  stories: [
    'Stories',
    'using-events.html#stories',
    'A story collects the events that tell one account of what happened, in the order you choose, with a note on each step. Stories can be shared, commented on and sampled into audits.',
    'Add events from the Events list or a visualisation window, then arrange them on the timeline. The guide walks through starting, building and using a story.',
  ],
  storyFlow: [
    'Story flow chart',
    'using-events.html#build-the-story',
    'The flow chart shows the story’s items as boxes you drag into place and join with labelled arrows, for example from the sign-in to the export it led to.',
    'If you can edit the story, drag boxes, choose Link items to draw an arrow, then Save layout. Arrange by time resets the positions; the Timeline order is unchanged.',
  ],
  storyComments: [
    'Story comments and review',
    'using-events.html#share-the-story',
    'Comments let reviewers discuss a story without changing it. Questions and the changelog record who asked, answered and edited what.',
    'Share the story link with people who can read its events. The guide explains what withheld items mean for readers with narrower access.',
  ],
  visualisations: [
    'Visualisations',
    'using-operations.html#visualisation-tabs',
    'The Visualisations tab charts the records you have filtered on this page. Histogram windows can open the matching events or start a story from them.',
    'Set your filters first; the charts follow them. The guide explains each chart and how to read the counts.',
  ],
  questions: [
    'Questions and incidents',
    'using-events.html#questions-and-incidents',
    'A question asks a colleague about a record and keeps the answer with it. An incident is declared from evidence and tracked to closure.',
    'Ask from the record itself so the thread stays attached. The guide covers notifications and who can see each thread.',
  ],
  changelog: [
    'Changelog',
    'administrator-audit-trail.html#audit-trail-entity-changes',
    'The Changelog tab lists recorded changes to this record: who changed which fields, and when, with the earlier and later values.',
    'Use it to understand an unexpected edit before changing anything else. The administrative Audit trail shows the surrounding requests.',
  ],
  context: [
    'Organisational context',
    'using-isms.html#context',
    'PESTLE(E) items and interested parties record the external and internal factors your management system responds to.',
    'Link each item to the controls or clauses it influences. The guide explains how context feeds objectives and risk assessment.',
  ],
  objectives: [
    'Objectives',
    'using-isms.html#objectives',
    'An objective states a measurable outcome, its owner and its due date, linked to the measures that show progress.',
    'Choose wording you can evaluate. The guide explains how SWOT responses and measures connect to objectives.',
  ],
  documents: [
    'Policies, processes and documents',
    'using-documents.html',
    'Write a policy in KEEN, upload a document, or link one maintained elsewhere. Every version is kept and can be linked to controls.',
    'Pick the workflow that matches where the document lives. The guide covers approvals, versions and framework links.',
  ],
  peopleAssets: [
    'People, suppliers and assets',
    'using-people-assets.html',
    'The organisation chart, People directory, vendors, asset matrix and access matrix describe who and what your controls protect.',
    'Keep owners current; assets, risks and access records reference them. The guide explains each matrix.',
  ],
  measures: [
    'Effectiveness measures',
    'using-measures.html',
    'A measure defines what you observe, its unit and target. Observations arrive by hand, from sources or through the measurement webhook.',
    'Check the unit and target before recording values. The guide explains automatic zeros and how to send measurements.',
  ],
  meetings: [
    'Meetings and decisions',
    'using-isms.html#meetings',
    'Meeting minutes record attendees, decisions and actions, and can be linked to the controls and records they concern.',
    'Record the decision and its owner, then link the records it affects. The guide covers follow-up and audit use.',
  ],
  soa: [
    'Statement of Applicability',
    'using-frameworks.html#4-controls-clauses-and-applicability',
    'The Statement of Applicability records which controls are in scope, why, and what supports each decision.',
    'Review scope decisions deliberately; excluded controls need a reason. The guide explains applicability and inherited evidence.',
  ],
  adminOverview: [
    'Administration',
    'administrator.html',
    'Administration is where KEEN is connected and shaped: the Workspace panes bring evidence in (agents, API ingesters, sources and mapping, frameworks, runners, diary, retention), Access manages users and groups, and System holds feature switches and the audit trail.',
    'Most installations start with Frameworks, then Sources & evidence mapping. The administrator handbook has one chapter per pane.',
  ],
  features: [
    'Features',
    'administrator-deployment.html#installation-features',
    'Feature switches hide whole workspaces or subsections for every user of this installation. Records, evidence and audit history are kept while a feature is off.',
    'Changes save immediately and apply to everyone, including administrators. Parent switches control their subsections; user permissions still apply on top.',
  ],
  accountSecurity: [
    'Account security',
    'using-account.html#local-account-security',
    'Manage your password, authenticator apps, security keys and recovery codes, and the e-mail addresses that may link an SSO identity.',
    'Store recovery codes somewhere safe before you need them. The guide explains what sign-in looks like afterwards.',
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
  questions: 'questions',
  'my-questions': 'questions',
  stories: 'stories',
  sources: 'sources',
  source: 'sources',
  controls: 'frameworks',
  clauses: 'frameworks',
  soa: 'soa',
  audits: 'audits',
  risks: 'risks',
  pestle: 'context',
  mitigator: 'mitigator',
  'interested-party': 'context',
  'interested-parties': 'context',
  account: 'account',
  isms: 'isms',
  'isms-document': 'documents',
  'isms-asset': 'peopleAssets',
  'isms-access': 'peopleAssets',
  'isms-effectiveness-measure': 'measures',
  'isms-effectiveness-metric': 'measures',
  'isms-objective': 'objectives',
  'isms-meeting': 'meetings',
};
// Tabs that change the subject of a page. Keys are the tab buttons' ids.
const tabs = {
  flowTab: 'storyFlow',
  visualisationTab: 'swot',
  commentsTab: 'storyComments',
  swotTab: 'swot',
  objectivesTab: 'objectives',
  documentsTab: 'documents',
  orgTab: 'peopleAssets',
  assetsTab: 'peopleAssets',
  accessControlTab: 'peopleAssets',
  effectivenessTab: 'measures',
  meetingsTab: 'meetings',
  soaTab: 'soa',
  sourceEffectivenessTab: 'measures',
  'security-tab': 'accountSecurity',
  'my-questions-tab': 'questions',
  'my-audits-tab': 'audits',
  'my-risks-tab': 'risks',
  'my-isms-objectives-tab': 'objectives',
  'my-isms-role-tab': 'peopleAssets',
  'my-isms-assets-tab': 'peopleAssets',
  'my-meetings-tab': 'meetings',
  libraryTab: 'risks',
  newRiskScenarioTab: 'risks',
  controlPestleTab: 'context',
  clausePestleTab: 'context',
  soaPestleTab: 'context',
  soaInterestedPartiesTab: 'context',
  soaSwotTab: 'swot',
  soaIsmsObjectivesTab: 'objectives',
  soaIsmsDocumentsTab: 'documents',
  soaIsmsEffectivenessTab: 'measures',
  soaIsmsOrgAssetsTab: 'peopleAssets',
  controlRisksTab: 'risks',
  soaRisksTab: 'risks',
  controlEvidenceTab: 'events',
  sourceEventsTab: 'events',
  sourceInsightsTab: 'sourceInsights',
};
// Tabs that recur across pages are recognised by their id's ending.
const tabEndings = [
  ['VisualisationsTab', 'visualisations'],
  ['ChangelogTab', 'changelog'],
  ['changelogTab', 'changelog'],
  ['QuestionsTab', 'questions'],
  ['questionsTab', 'questions'],
];
function activeTabTopic() {
  const tab = document.querySelector(
    '.nav-tabs .nav-link.active[role="tab"], .nav-tabs [role="tab"][aria-selected="true"]',
  );
  const id = tab?.id || '';
  return own(tabs, id) || tabEndings.find(([ending]) => id.endsWith(ending))?.[1] || null;
}
const panes = {
  'pane-overview': 'adminOverview',
  'pane-features': 'features',
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
    if (page === 'admin') key = own(panes, pane?.id) || 'sources';
    else {
      const swot = ['/swot.html', '/isms-swot.html'].includes(location.pathname);
      key =
        activeTabTopic() ||
        (swot ? 'swot' : own(pages, page)) ||
        (page.startsWith('isms') ? 'isms' : 'start');
    }
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
