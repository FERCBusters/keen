// One quiz: settings and controls, questions, people, schedule and rounds,
// results, attempts and grading, for quiz managers and quiz readers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (value) => JSON.parse(JSON.stringify(value));

function quizView(overrides = {}) {
  return {
    id: 'q1',
    title: 'Security basics',
    description: 'Monthly refresher',
    framework: 'A',
    status: 'draft',
    pass_mark: 70,
    questions_per_attempt: null,
    page_size: 5,
    show_scores: true,
    reveal_answers: 'after_close',
    due_days: 14,
    availability: { state: 'draft', on_activation: 'now', until: '2026-11-07' },
    schedule: {
      start_date: null,
      schedule_recurrence: 'once',
      schedule_interval: 1,
      schedule_date_rule: 'exact',
      schedule_anchor_month: null,
      schedule_until_date: null,
      schedule_next_run_date: null,
      schedule_last_run_date: null,
      label: '',
    },
    measure_id: 'm1',
    controls: [{ id: 'c1', ref: 'A.6.3', title: 'Awareness' }],
    question_count: 2,
    questions: [
      {
        id: 'qq1',
        position: 0,
        kind: 'choice',
        prompt: 'Who approves <access>?',
        explanation: 'Owners do.',
        options: [
          { id: 'op1', text: 'The owner', correct: true },
          { id: 'op2', text: 'Anyone', correct: false },
        ],
      },
      {
        id: 'qq2',
        position: 1,
        kind: 'text',
        prompt: 'Phishing signs?',
        explanation: '',
        options: [],
      },
    ],
    rounds: [
      {
        id: 'r1',
        opens_on: '2026-10-01',
        due_on: '2026-10-15',
        status: 'closed',
        scheduled: true,
        participants: 4,
        started: 3,
        pending_grading: 1,
        complete: 3,
        results_shown: true,
        recorded: true,
        average: 80,
        pass_rate: 66.7,
      },
      {
        id: 'r2',
        opens_on: '2026-10-20',
        due_on: '2026-11-03',
        status: 'open',
        scheduled: false,
        participants: 4,
        started: 0,
        pending_grading: 0,
        complete: 0,
        results_shown: false,
        recorded: false,
        average: null,
        pass_rate: null,
      },
    ],
    audience: {
      people: [
        { id: 'p1', name: 'Ada', email: 'ada@example.test', can_sit: true },
        { id: 'p2', name: 'Visitor', email: '', can_sit: false },
      ],
      roles: [{ id: 'n1', name: 'Service desk', holders: 3 }],
      size: 4,
    },
    can_manage: true,
    ...overrides,
  };
}

const ASSIGNEES = {
  people: [
    { id: 'p1', name: 'Ada', email: '', position: '', role: 'Engineering', can_sit: true },
    { id: 'p3', name: 'Ben', email: '', position: '', role: '', can_sit: false },
  ],
  accounts: [{ id: 'u9', username: 'cleo', email: '' }],
  roles: [{ id: 'n1', name: 'Service desk', node_type: 'team', holders: 3 }],
};

const RESULTS = {
  quiz: { id: 'q1', title: 'Security basics', pass_mark: 70, show_scores: true },
  can_manage: true,
  round_id: null,
  rounds: [quizView().rounds[0]],
  hidden_rounds: 1,
  summary: {
    rounds: 1,
    participants: 4,
    responses: 3,
    completion_rate: 75,
    average: 80,
    pass_rate: 66.7,
    distribution: [0, 0, 0, 0, 1, 0, 0, 1, 0, 1],
    trophies: { bronze: 1, silver: 0, gold: 1 },
  },
  questions: [
    {
      question_id: 'qq1',
      prompt: 'Who approves <access>?',
      kind: 'choice',
      asked: 3,
      answered: 3,
      success_rate: 66.7,
      average_grade: null,
      options: [
        { id: 'op1', text: 'The owner', correct: true, chosen: 2, share: 66.7 },
        { id: 'op2', text: 'Anyone', correct: false, chosen: 1, share: 33.3 },
      ],
    },
    {
      question_id: 'qq2',
      prompt: 'Phishing signs?',
      kind: 'text',
      asked: 3,
      answered: 2,
      success_rate: null,
      average_grade: 3.5,
    },
  ],
  insights: {
    hardest: [{ question_id: 'qq1', prompt: 'Who approves <access>?', success_rate: 66.7 }],
    easiest: [],
    common_wrong_answers: [
      {
        question_id: 'qq1',
        prompt: 'Who approves <access>?',
        answer: 'Anyone',
        chosen: 1,
        share: 33.3,
      },
    ],
  },
};

const PEOPLE = [
  {
    person_id: 'p1',
    name: 'Ada',
    attempt_id: 'a1',
    status: 'complete',
    pages_saved: 1,
    submitted_at: '2026-10-02T09:00:00',
    score: 100,
    passed: true,
    trophy: 'gold',
  },
  {
    person_id: 'p4',
    name: 'Dev',
    attempt_id: 'a2',
    status: 'in_progress',
    pages_saved: 1,
    submitted_at: null,
    score: null,
    passed: null,
    trophy: null,
  },
  {
    person_id: 'p5',
    name: 'Eve',
    attempt_id: null,
    status: 'not_started',
    pages_saved: 0,
    submitted_at: null,
    score: null,
    passed: null,
    trophy: null,
  },
];

const ATTEMPT = {
  id: 'a1',
  status: 'submitted',
  score: null,
  trophy: null,
  submitted_at: '2026-10-02T09:00:00',
  person: { id: 'p1', name: 'Ada' },
  questions: [
    {
      position: 0,
      kind: 'choice',
      prompt: 'Who approves <access>?',
      options: [
        { id: 'op1', text: 'The owner', correct: true },
        { id: 'op2', text: 'Anyone', correct: false },
      ],
      answer: { option_id: 'op2', text: '' },
      answer_id: 'ans1',
    },
    {
      position: 1,
      kind: 'text',
      prompt: 'Phishing signs?',
      options: [],
      answer: { option_id: null, text: 'Urgency' },
      explanation: 'Two signs',
      grade: 3,
      feedback: 'Name two',
      answer_id: 'ans2',
    },
    {
      position: 2,
      kind: 'text',
      prompt: 'Anything else?',
      options: [],
      answer: { option_id: null, text: '  ' },
      explanation: '',
      grade: null,
      feedback: '',
      answer_id: 'ans3',
    },
  ],
};

const QUEUE = {
  items: [
    {
      answer_id: 'ans9',
      attempt_id: 'a3',
      round_id: 'r1',
      round_opens_on: '2026-10-01',
      prompt: 'Phishing signs?',
      guidance: 'Urgency',
      text: 'An odd link',
      submitted_at: '2026-10-03T10:00:00',
    },
  ],
  own_answers_waiting: 1,
};

async function quizPage(
  t,
  {
    view = quizView(),
    query = '&id=q1',
    patch,
    put,
    post,
    remove,
    get,
    globals,
    confirm = () => true,
  } = {},
) {
  const ui = await page(t, 'quiz', {
    me: { can_manage_quizzes: view.can_manage, can_view_quizzes: true },
    query,
    globals: { confirm, ...globals },
    patch,
    put,
    post,
    remove,
    get: async (url, body) => {
      const extra = await get?.(url, body);
      if (extra !== undefined) return extra;
      if (url.pathname === '/api/v1/quizzes/q1') return view;
      if (url.pathname === '/api/v1/controls') {
        return {
          items: [
            { id: 'c1', ref: 'A.6.3', title: 'Awareness', type: 'annex_control' },
            { id: 'c2', ref: 'A.5.10', title: '', type: 'annex_control' },
            { id: 'k1', ref: '7.3', title: 'Awareness clause', type: 'clause' },
          ],
        };
      }
      if (url.pathname === '/api/v1/quizzes/assignees') return ASSIGNEES;
      if (url.pathname === '/api/v1/quizzes/q1/grading') return QUEUE;
      if (url.pathname === '/api/v1/quizzes/q1/results') {
        return url.searchParams.get('round_id') ? { ...RESULTS, people: PEOPLE } : RESULTS;
      }
      if (url.pathname === '/api/v1/quizzes/q1/attempts/a1') return ATTEMPT;
      throw new Error(`Unexpected GET ${url.pathname}`);
    },
  });
  await settle(() => ui.el('quizMeta').textContent !== 'Loading the quiz' && ui.idle());
  return ui;
}

const click = (ui, selector) => ui.document.querySelector(selector).click();
const submit = (ui, id) =>
  ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true, bubbles: true }));
const type = (ui, el, value, kind = 'input') => {
  el.value = value;
  el.dispatchEvent(new ui.window.Event(kind, { bubbles: true }));
};

test('a quiz manager sees the quiz, its controls, people, rounds and grading count', async (t) => {
  const ui = await quizPage(t);
  assert.equal(ui.el('quizTitle').textContent, 'Security basics');
  assert.equal(ui.el('quizMeta').textContent, 'A · draft · 2 questions · One-off');
  assert.equal(
    ui.el('availabilityText').textContent,
    'Draft: no one can take this quiz yet. Activating it opens it straight away, until 2026-11-07.',
  );
  assert.equal(ui.el('availability').hidden, false);
  assert.equal(ui.el('startRoundNow').hidden, true);
  assert.equal(ui.el('headerActions').hidden, false);
  assert.deepEqual(
    ['activate', 'archive', 'deleteQuiz'].map((id) => ui.el(id).hidden),
    [false, true, true],
  );
  assert.equal(ui.el('passMark').value, '70');
  assert.equal(ui.el('perAttempt').value, '');
  assert.equal(ui.el('showScores').checked, true);
  assert.equal(ui.el('title').disabled, false);
  assert.equal(ui.el('controlBadges').textContent, 'A.6.3');
  assert.equal(
    ui.el('controlBadges').querySelector('a').getAttribute('href'),
    '/control.html?id=c1&framework=A',
  );
  assert.ok(ui.el('measureInfo').textContent.includes('1 round recorded. Target: at least 70%.'));
  assert.deepEqual(
    [...ui.el('controls').options].map((o) => [o.value, o.textContent, o.selected]),
    [
      ['c1', 'A.6.3 Awareness', true],
      ['c2', 'A.5.10', false],
    ],
  );
  assert.equal(ui.document.querySelectorAll('#questionList [data-question]').length, 2);
  assert.ok(ui.el('audienceList').textContent.includes('Visitor (no account)'));
  assert.ok(ui.el('audienceList').textContent.includes('Service desk (role, 3)'));
  assert.equal(ui.el('audienceSize').textContent, '4 people can sit the next round.');
  const rows = [...ui.document.querySelectorAll('#roundRows tr')];
  assert.ok(rows[0].textContent.includes('scheduled'));
  assert.ok(rows[0].textContent.includes('3 of 4'));
  assert.ok(rows[0].textContent.includes('1 to grade'));
  assert.ok(rows[0].querySelector('[aria-label="Recorded on the measure"]'));
  assert.ok(rows[1].textContent.includes('Not yet'));
  assert.equal(rows[0].querySelector('[data-delete-round]'), null);
  assert.ok(rows[1].querySelector('[data-delete-round="r2"]'));
  assert.equal(ui.el('gradingCount').textContent, '1');
  assert.equal(ui.el('gradingCount').hidden, false);
  assert.ok(
    ui
      .el('gradingList')
      .textContent.includes('1 of your own answers wait for another quiz manager.'),
  );
  assert.equal(
    ui.el('scheduleInfo').textContent,
    'One-off: activating it opens it for 14 days. Set a first round to schedule rounds instead, or start another round by hand.',
  );
  assert.deepEqual(
    [...ui.el('pickPerson').options].map((o) => o.textContent),
    ['Choose', 'Ada (Engineering)', 'Ben - no account'],
  );
});

test('settings, controls and status changes save through the quiz', async (t) => {
  const patches = [];
  const assigned = [];
  const ui = await quizPage(t, {
    globals: {
      location: { search: '?framework=A&id=q1', hash: '', assign: (u) => assigned.push(u) },
    },
    patch: async (url, body) => {
      patches.push(plain(body));
      if (body.title === 'Fails') throw new Error('title is too long');
      const availability = {
        active: { state: 'open', until: '2026-11-07', people: 4 },
        archived: { state: 'archived' },
      }[body.status];
      return quizView({
        status: body.status || 'draft',
        title: body.title || 'Security basics',
        ...(availability ? { availability } : {}),
      });
    },
    remove: async (url) => {
      assert.equal(url.pathname, '/api/v1/quizzes/q1');
      return { ok: true };
    },
  });
  type(ui, ui.el('title'), ' Fails ');
  submit(ui, 'settingsForm');
  await settle(() => ui.el('status').textContent.includes('title is too long'));
  type(ui, ui.el('title'), 'Refresher');
  ui.el('passMark').value = '';
  ui.el('perAttempt').value = '3';
  ui.el('showScores').checked = false;
  ui.el('revealAnswers').value = 'never';
  submit(ui, 'settingsForm');
  await settle(() => ui.el('status').textContent === 'Settings saved');
  assert.deepEqual(patches[1], {
    title: 'Refresher',
    description: 'Monthly refresher',
    pass_mark: null,
    questions_per_attempt: 3,
    page_size: 5,
    due_days: 14,
    show_scores: false,
    reveal_answers: 'never',
  });
  ui.el('controls').options[1].selected = true;
  ui.el('saveControls').click();
  await settle(() => ui.el('status').textContent === 'Controls saved');
  assert.deepEqual(patches[2], { controls: ['c1', 'c2'] });
  ui.el('activate').click();
  await settle(() => ui.el('status').textContent === 'Quiz activated and open until 2026-11-07.');
  assert.deepEqual(patches[3], { status: 'active' });
  assert.equal(
    ui.el('availabilityText').textContent,
    'Open: 4 people can take this quiz until 2026-11-07.',
  );
  assert.ok(ui.el('availabilityBox').classList.contains('alert-success'));
  assert.equal(ui.el('archive').hidden, false);
  ui.el('archive').click();
  await settle(() => ui.el('status').textContent === 'Quiz archived');
  assert.deepEqual(patches[4], { status: 'archived' });
});

test('an active quiz nobody can take says so, and a round starts from the notice', async (t) => {
  const posts = [];
  let view = quizView({ status: 'active', availability: { state: 'no_round' } });
  const ui = await quizPage(t, {
    get: async (url) => (url.pathname === '/api/v1/quizzes/q1' ? view : undefined),
    post: async (url, body) => {
      posts.push([url.pathname, plain(body)]);
      view = quizView({
        status: 'active',
        availability: { state: 'open', until: '2026-11-07', people: 1 },
      });
      return { participants: 1 };
    },
  });
  assert.equal(
    ui.el('availabilityText').textContent,
    'No one can take this quiz until you start a round.',
  );
  assert.ok(ui.el('availabilityBox').classList.contains('alert-warning'));
  assert.equal(ui.el('startRoundNow').hidden, false);
  ui.el('startRoundNow').click();
  await settle(() => ui.el('status').textContent === 'Round started for 1 people');
  // The server opens it today, due after the quiz's days to answer.
  assert.deepEqual(posts, [['/api/v1/quizzes/q1/rounds', {}]]);
  assert.equal(
    ui.el('availabilityText').textContent,
    'Open: 1 person can take this quiz until 2026-11-07.',
  );
  assert.equal(ui.el('startRoundNow').hidden, true);
});

test('activating says when people can take the quiz', async (t) => {
  for (const [availability, message] of [
    [
      { state: 'waiting', opens_on: '2026-11-02' },
      'Quiz activated. Its first round opens on 2026-11-02.',
    ],
    [{ state: 'no_round' }, 'Quiz activated. No one can take it until you start a round.'],
  ]) {
    const ui = await quizPage(t, {
      patch: async () => quizView({ status: 'active', availability }),
    });
    ui.el('activate').click();
    await settle(() => ui.el('status').textContent === message);
  }
  const waiting = await quizPage(t, {
    view: quizView({
      status: 'active',
      availability: { state: 'waiting', opens_on: '2026-11-02' },
    }),
  });
  assert.equal(
    waiting.el('availabilityText').textContent,
    'No one can take this quiz until its next round opens on 2026-11-02.',
  );
  assert.equal(waiting.el('startRoundNow').hidden, false);
});

test('a quiz without started rounds can be deleted', async (t) => {
  const assigned = [];
  let confirmed = false;
  const ui = await quizPage(t, {
    view: quizView({ rounds: [] }),
    confirm: () => confirmed,
    globals: {
      location: { search: '?framework=A&id=q1', hash: '', assign: (u) => assigned.push(u) },
    },
    remove: async () => ({ ok: true }),
  });
  assert.equal(ui.el('deleteQuiz').hidden, false);
  ui.el('deleteQuiz').click();
  assert.deepEqual(assigned, []);
  confirmed = true;
  ui.el('deleteQuiz').click();
  await settle(() => assigned.length === 1);
  assert.deepEqual(assigned, ['/quizzes.html?framework=A']);
  assert.equal(ui.document.querySelector('#roundRows').textContent.trim(), 'No rounds yet.');
});

test('the question editor adds, edits, reorders and removes, then saves in order', async (t) => {
  const puts = [];
  const ui = await quizPage(t, {
    put: async (url, body) => {
      puts.push([url.pathname, plain(body)]);
      return quizView();
    },
  });
  ui.el('addChoice').click();
  ui.el('addWritten').click();
  const card = (n) => ui.document.querySelector(`[data-question="${n}"]`);
  assert.equal(ui.document.querySelectorAll('#questionList [data-question]').length, 4);
  type(ui, card(2).querySelector('[data-field="prompt"]'), 'Where are keys kept?');
  type(ui, card(2).querySelector('[data-o="0"][data-field="text"]'), 'Vault');
  type(ui, card(2).querySelector('[data-o="1"][data-field="text"]'), 'Desk');
  card(2).querySelector('[data-add-option="2"]').click();
  type(ui, card(2).querySelector('[data-o="2"][data-field="text"]'), 'Car');
  const radio = card(2).querySelector('[data-o="2"][data-field="correct"]');
  radio.checked = true;
  radio.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  card(2).querySelector('[data-remove-option="2:0"]').click();
  type(ui, card(3).querySelector('[data-field="explanation"]'), 'A good answer names two.');
  type(ui, card(3).querySelector('[data-field="prompt"]'), 'Why?');
  card(3).querySelector('[data-move="3:-1"]').click();
  card(0).querySelector('[data-remove-question="0"]').click();
  // Clicks outside a button and inputs without a field change nothing.
  card(0).click();
  card(0).dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  ui.el('saveQuestions').click();
  await settle(() => ui.el('status').textContent === 'Questions saved');
  const [path, body] = puts[0];
  assert.equal(path, '/api/v1/quizzes/q1/questions');
  assert.deepEqual(
    body.questions.map((q) => [q.kind, q.prompt, q.options.map((o) => [o.text, o.correct])]),
    [
      ['text', 'Phishing signs?', []],
      ['text', 'Why?', []],
      [
        'choice',
        'Where are keys kept?',
        [
          ['Desk', false],
          ['Car', true],
        ],
      ],
    ],
  );
  assert.equal(body.questions[1].explanation, 'A good answer names two.');
});

test('people are assigned from People, accounts and roles', async (t) => {
  const puts = [];
  const ui = await quizPage(t, {
    put: async (url, body) => {
      puts.push(plain(body));
      return {
        people: [{ id: 'p9', name: 'cleo', email: '', can_sit: true }],
        roles: [],
        size: 1,
      };
    },
  });
  ui.el('pickPerson').value = 'p3';
  ui.el('addPerson').click();
  ui.el('pickAccount').value = 'u9';
  ui.el('addAccount').click();
  ui.el('pickRole').value = '';
  ui.el('addRole').click();
  assert.ok(ui.el('audienceList').textContent.includes('cleo (account)'));
  click(ui, '[data-unassign="people:p2"]');
  click(ui, '[data-unassign="roles:n1"]');
  // A click elsewhere in the list changes nothing.
  ui.el('audienceList').click();
  ui.el('saveAudience').click();
  await settle(() => ui.el('status').textContent === 'People saved' && ui.idle());
  assert.deepEqual(puts[0], { person_ids: ['p1', 'p3'], user_ids: ['u9'], org_node_ids: [] });
  assert.equal(ui.el('audienceList').textContent.trim(), 'cleo');
  assert.equal(ui.el('audienceSize').textContent, '1 people can sit the next round.');
  const empty = await quizPage(t, {
    view: quizView({ audience: { people: [], roles: [], size: 0 } }),
  });
  assert.equal(empty.el('audienceList').textContent, 'Nobody yet.');
});

test('rounds start, close, reopen, move and go, and the schedule saves', async (t) => {
  const calls = [];
  let view = quizView();
  // A round that closed without a result can reopen; a recorded one cannot.
  view.rounds.push({
    ...view.rounds[0],
    id: 'r3',
    due_on: '2026-10-10',
    pending_grading: 0,
    complete: 2,
    results_shown: false,
    recorded: false,
  });
  const ui = await quizPage(t, {
    get: async (url) => (url.pathname === '/api/v1/quizzes/q1' ? view : undefined),
    post: async (url, body) => {
      calls.push(['POST', url.pathname, plain(body)]);
      return { participants: 4 };
    },
    patch: async (url, body) => {
      calls.push(['PATCH', url.pathname, plain(body)]);
      return view;
    },
    remove: async (url) => {
      calls.push(['DELETE', url.pathname]);
      return { ok: true };
    },
  });
  assert.match(ui.el('roundOpens').value, /^\d{4}-\d{2}-\d{2}$/);
  ui.el('roundOpens').value = '2026-11-01';
  ui.el('roundDue').value = '2026-11-15';
  submit(ui, 'roundForm');
  await settle(() => ui.el('status').textContent === 'Round started for 4 people');
  assert.deepEqual(calls[0], [
    'POST',
    '/api/v1/quizzes/q1/rounds',
    { opens_on: '2026-11-01', due_on: '2026-11-15' },
  ]);
  click(ui, '[data-round-status="r2:closed"]');
  await settle(() => ui.el('status').textContent === 'Round closed');
  assert.deepEqual(calls[1], ['PATCH', '/api/v1/quizzes/q1/rounds/r2', { status: 'closed' }]);
  assert.equal(ui.document.querySelector('[data-round-status="r1:open"]'), null);
  click(ui, '[data-round-status="r3:open"]');
  await settle(() => ui.el('status').textContent === 'Round reopened');
  assert.deepEqual(calls[2], [
    'PATCH',
    '/api/v1/quizzes/q1/rounds/r3',
    { status: 'open', due_on: '2026-10-10' },
  ]);
  type(ui, ui.document.querySelector('[data-round-due="r2"]'), '2026-11-10', 'change');
  await settle(() => ui.el('status').textContent.startsWith('Due date moved'));
  assert.deepEqual(calls[3], ['PATCH', '/api/v1/quizzes/q1/rounds/r2', { due_on: '2026-11-10' }]);
  click(ui, '[data-delete-round="r2"]');
  await settle(() => ui.el('status').textContent === 'Round deleted');
  assert.deepEqual(calls[4], ['DELETE', '/api/v1/quizzes/q1/rounds/r2']);
  // Other changes in the table, and clicks outside its buttons, do nothing.
  ui.el('roundRows').dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  ui.el('roundRows').click();

  ui.el('dateRule').value = 'exact';
  ui.el('scheduleStart').value = '2026-12-01';
  ui.el('recurrence').value = 'monthly';
  ui.el('interval').value = '2';
  ui.el('until').value = '2027-06-30';
  submit(ui, 'scheduleForm');
  await settle(() => ui.el('status').textContent === 'Schedule saved');
  assert.deepEqual(calls[5], [
    'PATCH',
    '/api/v1/quizzes/q1',
    {
      start_date: '2026-12-01',
      schedule_date_rule: 'exact',
      schedule_anchor_month: null,
      schedule_recurrence: 'monthly',
      schedule_interval: 2,
      schedule_until_date: '2027-06-30',
    },
  ]);
  ui.el('clearSchedule').click();
  await settle(() => ui.el('status').textContent === 'Scheduling stopped');
  assert.deepEqual(calls[6][2], { start_date: null, schedule_recurrence: 'once' });
  view = quizView({
    schedule: {
      ...quizView().schedule,
      start_date: '2026-11-02',
      schedule_recurrence: 'monthly',
      schedule_date_rule: 'first_monday_of_month',
      schedule_anchor_month: 11,
      schedule_next_run_date: '2026-11-02',
      label: 'Monthly',
    },
  });
  ui.el('saveControls').click();
  await settle(() => ui.el('status').textContent === 'Controls saved');
  assert.ok(ui.el('scheduleInfo').textContent.includes('Next round: 2026-11-02'));
  assert.ok(ui.el('quizMeta').textContent.endsWith('Monthly · first Monday of Nov'));
});

test('results show the group, insights and each person, and grading saves', async (t) => {
  const grades = [];
  const ui = await quizPage(t, {
    put: async (url, body) => {
      grades.push([url.pathname, plain(body)]);
      return { answer: {}, attempt: {} };
    },
    // Once graded, the attempt reopens finished with its trophy.
    get: async (url) =>
      url.pathname === '/api/v1/quizzes/q1/attempts/a1' && grades.length
        ? { ...ATTEMPT, status: 'complete', score: 92, trophy: 'gold' }
        : undefined,
  });
  ui.el('quizResultsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('summaryTiles').textContent.includes('80%'));
  assert.ok(ui.el('hiddenRounds').textContent.startsWith('1 round not shown yet'));
  assert.equal(ui.document.querySelectorAll('#histogram > div').length, 10);
  assert.ok(ui.el('trophyCounts').textContent.includes('Gold'));
  const insights = ui.el('insights').textContent;
  assert.ok(insights.includes('Hardest questions'));
  assert.ok(insights.includes('"Anyone" for Who approves <access>?'));
  assert.ok(ui.el('questionStats').textContent.includes('Not marked yet'));
  assert.ok(ui.el('questionStats').textContent.includes('average grade 3.5 / 5'));
  assert.equal(ui.el('peopleCard').hidden, true);

  ui.el('resultRound').value = 'r1';
  ui.el('resultRound').dispatchEvent(new ui.window.Event('change'));
  await settle(() => !ui.el('peopleCard').hidden);
  const people = [...ui.document.querySelectorAll('#peopleRows tr')].map((tr) =>
    [...tr.cells].map((c) => c.textContent.trim()),
  );
  assert.deepEqual(people[0].slice(0, 4), ['Ada', 'Finished', '2026-10-02', '100% Gold']);
  assert.equal(people[1][1], 'Started (1 pages saved)');
  assert.equal(people[2][1], 'Not started');

  click(ui, '[data-attempt="a1"]');
  await settle(() => ui.el('attemptTitle').textContent === 'Ada: waiting for a grade');
  const body = ui.el('attemptBody');
  assert.deepEqual(
    [...body.querySelectorAll('.quiz-option')].map((o) => o.className),
    ['quiz-option right', 'quiz-option wrong'],
  );
  assert.ok(body.textContent.includes('Guidance: Two signs'));
  assert.ok(body.textContent.includes('A blank answer scores nothing.'));
  const form = body.querySelector('[data-grade-answer="ans2"]');
  assert.equal(form.elements.grade.value, '3');
  form.elements.grade.value = '4.5';
  form.elements.feedback.value = 'Better';
  form.dispatchEvent(new ui.window.Event('submit', { cancelable: true, bubbles: true }));
  await settle(() => grades.length === 1 && ui.idle());
  assert.deepEqual(grades[0], [
    '/api/v1/quizzes/q1/answers/ans2/grade',
    { grade: 4.5, feedback: 'Better' },
  ]);
  await settle(() => ui.el('attemptTitle').textContent === 'Ada: 92%');
  assert.ok(body.textContent.startsWith('Gold trophy · Handed in 2026-10-02'));

  const queued = ui.el('gradingList').querySelector('[data-grade-answer="ans9"]');
  assert.ok(ui.el('gradingList').textContent.includes('Guidance: Urgency'));
  queued.elements.grade.value = '2';
  queued.dispatchEvent(new ui.window.Event('submit', { cancelable: true, bubbles: true }));
  await settle(() => grades.length === 2 && ui.idle());
  assert.deepEqual(grades[1][1], { grade: 2, feedback: '' });
  // A click in the people table outside its buttons does nothing.
  ui.el('peopleRows').click();
});

test('a quiz reader sees the quiz without answers or changes', async (t) => {
  const view = quizView({
    can_manage: false,
    status: 'active',
    availability: { state: 'no_round' },
    audience: { size: 4 },
    questions: [
      {
        id: 'qq1',
        position: 0,
        kind: 'choice',
        prompt: 'Who approves?',
        options: [{ id: 'op1', text: 'The owner' }],
      },
      { id: 'qq2', position: 1, kind: 'text', prompt: 'Why?', options: [] },
    ],
  });
  const ui = await quizPage(t, {
    view,
    query: '&id=q1#results',
    get: async (url) =>
      url.pathname === '/api/v1/quizzes/q1/results'
        ? { ...RESULTS, can_manage: false, insights: { hardest: [], easiest: [] }, questions: [] }
        : undefined,
  });
  assert.equal(ui.el('headerActions').hidden, true);
  assert.equal(
    ui.el('availabilityText').textContent,
    'No one can take this quiz until a round starts.',
  );
  assert.equal(ui.el('startRoundNow').hidden, true);
  assert.equal(ui.el('title').disabled, true);
  assert.equal(ui.el('dateRule').disabled, true);
  assert.ok(ui.el('questionList').textContent.includes('Answered in writing.'));
  assert.equal(ui.document.querySelector('#questionList input'), null);
  assert.equal(ui.document.querySelector('.manager-only').hidden, true);
  assert.equal(ui.document.querySelector('#roundRows td:last-child').hidden, true);
  ui.el('quizResultsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('insights').textContent !== '');
  assert.equal(ui.el('insights').textContent, 'Insights appear once people have answered.');
  assert.equal(ui.el('questionStats').textContent, 'No answers to show yet.');
  const none = await quizPage(t, {
    view: quizView({ can_manage: false, audience: { size: 0 }, questions: [] }),
  });
  assert.equal(none.el('questionList').textContent, 'No questions yet.');
});

test('a missing or unreadable quiz, and failed requests, are reported', async (t) => {
  const bare = await page(t, 'quiz', { me: {}, query: '' });
  await settle(() => bare.el('status').style.display === '');
  assert.equal(bare.el('status').textContent, 'No quiz was named; open one from the Quizzes page.');

  const refused = await page(t, 'quiz', {
    me: {},
    query: '&id=q1',
    get: async () => {
      throw new Error('quiz.read permission required');
    },
  });
  await settle(() => refused.el('status').style.display === '');
  assert.equal(
    refused.el('status').textContent,
    'Failed to load the quiz: Error: quiz.read permission required',
  );

  const failing = await quizPage(t, {
    view: quizView({ questions: [], audience: { people: [], roles: [], size: 0 } }),
    get: async (url) => {
      if (['/api/v1/controls', '/api/v1/quizzes/q1/grading'].includes(url.pathname))
        throw new Error('down');
      if (url.pathname === '/api/v1/quizzes/assignees') throw new Error('down');
      return undefined;
    },
    post: async () => {
      throw new Error('Activate the quiz before starting a round');
    },
  });
  assert.ok(failing.el('status').textContent.startsWith('Failed to'));
  assert.equal(failing.el('questionList').textContent.trim().startsWith('No questions yet.'), true);
  submit(failing, 'roundForm');
  await settle(() => failing.el('status').textContent.includes('did not start'));
  failing.el('addPerson').click();
});
