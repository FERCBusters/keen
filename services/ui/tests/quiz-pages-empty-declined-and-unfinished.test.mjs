// Quizzes with little recorded yet, changes declined or refused, results and
// attempts with nothing to show, and the lists that summarise them: the quiz
// register, My quizzes, a person's quiz history and the sitting page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { page, settle } from './support/page-fixture.mjs';

const plain = (value) => JSON.parse(JSON.stringify(value));
const pause = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));
const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';

function quizView(overrides = {}) {
  return {
    id: 'q1',
    title: 'Security basics',
    description: '',
    framework: 'A',
    status: 'active',
    pass_mark: null,
    questions_per_attempt: null,
    page_size: 5,
    show_scores: true,
    reveal_answers: 'after_close',
    due_days: 14,
    availability: { state: 'open', until: '2026-11-07', people: 2 },
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
    controls: [],
    question_count: 1,
    questions: [
      {
        id: 'qq1',
        position: 0,
        kind: 'choice',
        prompt: 'Pick one',
        explanation: '',
        options: Array.from({ length: 10 }, (_, i) => ({
          id: 'op' + i,
          text: 'Option ' + i,
          correct: i === 0,
        })),
      },
    ],
    rounds: [
      {
        id: 'r1',
        opens_on: '2026-10-01',
        due_on: '2026-10-15',
        status: 'closed',
        scheduled: false,
        participants: 3,
        started: 3,
        pending_grading: 0,
        complete: 3,
        results_shown: true,
        recorded: false,
        average: 60,
        pass_rate: null,
      },
    ],
    audience: { people: [], roles: [], size: 0 },
    can_manage: true,
    ...overrides,
  };
}
const ASSIGNEES = {
  people: [{ id: 'p1', name: 'Ada', email: '', position: '', role: 'Engineering', can_sit: true }],
  accounts: [],
  roles: [],
};
const RESULTS = {
  quiz: { id: 'q1', title: 'Security basics', pass_mark: null, show_scores: true },
  can_manage: true,
  round_id: null,
  rounds: [],
  hidden_rounds: 2,
  summary: {
    rounds: 2,
    participants: 6,
    responses: 0,
    completion_rate: null,
    average: null,
    pass_rate: null,
    distribution: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    trophies: { bronze: 0, silver: 0, gold: 0 },
  },
  questions: [
    {
      question_id: 'qq1',
      prompt: 'Pick one',
      kind: 'choice',
      asked: 0,
      answered: 0,
      success_rate: null,
      average_grade: null,
      options: [{ id: 'op0', text: 'Option 0', correct: true, chosen: 0, share: null }],
    },
  ],
  insights: { hardest: [], easiest: [], common_wrong_answers: [] },
  people: [
    {
      person_id: 'p1',
      name: 'Ada',
      attempt_id: 'a1',
      status: 'in_progress',
      pages_saved: 0,
      submitted_at: null,
      score: null,
      passed: null,
      trophy: null,
    },
  ],
};

// A quiz page whose GETs are answered from `view` unless `get` answers first.
async function quizPage(
  t,
  {
    view = quizView(),
    query = '&id=q1',
    get,
    patch,
    put,
    post,
    remove,
    confirm = true,
    setup,
    vendor,
  } = {},
) {
  const ui = await page(t, 'quiz', {
    me: { can_manage_quizzes: view.can_manage, can_view_quizzes: true },
    query,
    confirm,
    setup,
    vendor,
    patch,
    put,
    post,
    remove,
    get: async (url, body) => {
      const extra = await get?.(url, body);
      if (extra !== undefined) return extra;
      if (url.pathname === '/api/v1/quizzes/q1') return structuredClone(view);
      if (url.pathname === '/api/v1/controls') return { items: [] };
      if (url.pathname === '/api/v1/quizzes/assignees') return structuredClone(ASSIGNEES);
      if (url.pathname === '/api/v1/quizzes/q1/grading')
        return { items: [], own_answers_waiting: 0 };
      if (url.pathname === '/api/v1/quizzes/q1/results') return structuredClone(RESULTS);
      throw new Error(`Unexpected GET ${url.pathname}`);
    },
  });
  await settle(() => ui.el('quizMeta').textContent !== 'Loading the quiz' && ui.idle());
  return ui;
}
const submit = (ui, id) =>
  ui.el(id).dispatchEvent(new ui.window.Event('submit', { cancelable: true, bubbles: true }));
const tile = (ui, index) => ui.document.querySelectorAll('#summaryTiles .card-body')[index];

test('a one-question quiz without a pass mark, controls, recorded rounds or grading shows each empty state', async (t) => {
  const ui = await quizPage(t);
  assert.equal(ui.el('quizMeta').textContent, 'A · active · 1 question · One-off');
  assert.equal(ui.el('passMark').value, '');
  assert.equal(ui.el('controlBadges').textContent, 'No controls linked yet.');
  assert.match(ui.el('measureInfo').textContent, /0 rounds recorded\. Target: none\./);
  // A shown average that is not on the measure carries no recorded mark.
  const average = ui.document.querySelectorAll('#roundRows tr')[0].cells[4];
  assert.equal(average.textContent, '60%');
  assert.equal(average.querySelector('i'), null);
  // A question with ten options takes no more.
  assert.equal(ui.document.querySelector('[data-add-option="0"]').disabled, true);
  // A person who signs in without a linked username has no account note.
  assert.deepEqual(
    [...ui.el('pickPerson').options].map((o) => o.textContent),
    ['Choose', 'Ada (Engineering)'],
  );
  assert.equal(ui.el('gradingList').textContent, 'Nothing to grade.');
  assert.equal(ui.el('gradingCount').hidden, true);
});

test('a control without a title is labelled by its reference', async (t) => {
  const ui = await quizPage(t, {
    view: quizView({ controls: [{ id: 'c2', ref: 'A.5.10', title: '' }] }),
  });
  const badge = ui.el('controlBadges').querySelector('a');
  assert.equal(badge.textContent, 'A.5.10');
  assert.equal(badge.getAttribute('title'), 'A.5.10');
});

test('declining to archive or to delete a round changes nothing', async (t) => {
  const sent = [];
  const record = (method) => async (url) => {
    sent.push([method, url.pathname]);
    return quizView();
  };
  const ui = await quizPage(t, {
    view: quizView({ rounds: [{ ...quizView().rounds[0], started: 0 }] }),
    confirm: false,
    patch: record('PATCH'),
    remove: record('DELETE'),
  });
  ui.el('archive').click();
  ui.document.querySelector('[data-delete-round="r1"]').click();
  await settle(() => ui.confirmations.length === 2);
  await pause();
  assert.deepEqual(
    ui.confirmations.map((c) => c.title),
    ['Archive this quiz', 'Delete this round'],
  );
  assert.deepEqual(sent, []);
  assert.equal(ui.el('status').textContent, '');
});

test('a refused change of people keeps the chosen people on screen', async (t) => {
  const ui = await quizPage(t, {
    put: async () => {
      throw new Error('A person needs a Keen account');
    },
  });
  ui.el('pickPerson').value = 'p1';
  ui.el('addPerson').click();
  ui.el('saveAudience').click();
  await settle(() => ui.el('status').textContent !== '');
  assert.equal(ui.el('status').textContent, 'People not saved: A person needs a Keen account');
  assert.match(ui.el('audienceList').textContent, /^Ada/);
  assert.equal(ui.el('audienceSize').textContent, '0 people can sit the next round.');
  assert.equal(ui.calls.filter((c) => c.url === '/api/v1/quizzes/assignees').length, 1);
});

test('a schedule saved with its dates and interval left blank sends none of them', async (t) => {
  const patches = [];
  const ui = await quizPage(t, {
    patch: async (url, body) => {
      patches.push(plain(body));
      return quizView();
    },
  });
  ui.el('dateRule').value = 'exact';
  ui.el('scheduleStart').value = '';
  ui.el('recurrence').value = 'weekly';
  ui.el('interval').value = '';
  ui.el('until').value = '';
  submit(ui, 'scheduleForm');
  await settle(() => ui.el('status').textContent === 'Schedule saved');
  assert.deepEqual(patches[0], {
    start_date: null,
    schedule_date_rule: 'exact',
    schedule_anchor_month: null,
    schedule_recurrence: 'weekly',
    schedule_interval: 1,
    schedule_until_date: null,
  });
});

test('dates typed into the round fields are the dates a new round uses', async (t) => {
  const posts = [];
  const ui = await quizPage(t, {
    // The design system keeps each typed date in step with its ISO field.
    vendor: {
      wireIsoDateTextField: (hidden, text) => ({
        syncTextFromHidden: () => (text.value = hidden.value),
        syncHiddenFromText: () => {
          hidden.value = text.value;
          return true;
        },
      }),
    },
    post: async (url, body) => {
      posts.push(plain(body));
      return { participants: 2 };
    },
  });
  // Today and the due date are written into the text fields too.
  assert.match(ui.el('roundOpensText').value, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(ui.el('roundOpensText').value, ui.el('roundOpens').value);
  assert.equal(ui.el('roundDueText').value, ui.el('roundDue').value);
  ui.el('roundOpensText').value = '2026-12-01';
  ui.el('roundDueText').value = '2026-12-15';
  submit(ui, 'roundForm');
  await settle(() => ui.el('status').textContent === 'Round started for 2 people');
  assert.deepEqual(posts, [{ opens_on: '2026-12-01', due_on: '2026-12-15' }]);
});

test('results before anyone finishes show dashes, plural rounds and empty shares', async (t) => {
  const ui = await quizPage(t);
  ui.el('quizResultsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('summaryTiles').textContent !== '');
  assert.equal(
    ui.el('hiddenRounds').textContent,
    '2 rounds not shown yet: results appear once a round closes with every response graded and at least three finished.',
  );
  assert.deepEqual(
    [1, 2].map((i) => [...tile(ui, i).children].map((c) => c.textContent)),
    [
      ['Pass rate', '-', 'No pass mark'],
      ['Completion', '-', '6 assigned across 2 rounds'],
    ],
  );
  assert.match(ui.el('questionStats').textContent, /Option 0\s*0 \(0%\)/);
  assert.equal(ui.el('peopleRows').querySelector('tr').cells[2].textContent, '-');
});

test('results for one round with every round shown leave out the hidden-round note', async (t) => {
  const ui = await quizPage(t, {
    get: async (url) =>
      url.pathname === '/api/v1/quizzes/q1/results'
        ? {
            ...RESULTS,
            hidden_rounds: 0,
            summary: { ...RESULTS.summary, rounds: 1, completion_rate: 50, pass_rate: 40 },
            quiz: { ...RESULTS.quiz, pass_mark: 70 },
          }
        : undefined,
  });
  ui.el('quizResultsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('summaryTiles').textContent !== '');
  assert.equal(ui.el('hiddenRounds').textContent, '');
  assert.deepEqual(
    [1, 2].map((i) => [...tile(ui, i).children].map((c) => c.textContent)),
    [
      ['Pass rate', '40%', 'Pass mark 70%'],
      ['Completion', '50%', '6 assigned across 1 round'],
    ],
  );
});

test("an unfinished attempt by a deleted person opens in the dialog with each answer's state", async (t) => {
  const shown = [];
  const attempt = {
    id: 'a1',
    status: 'in_progress',
    score: null,
    trophy: null,
    submitted_at: null,
    person: null,
    questions: [
      {
        position: 0,
        kind: 'choice',
        prompt: 'Pick one',
        options: [
          { id: 'op0', text: 'Right', correct: true },
          { id: 'op1', text: 'Chosen', correct: false },
          { id: 'op2', text: HOSTILE, correct: false },
        ],
        answer: { option_id: 'op1', text: '' },
        answer_id: 'ans1',
      },
      {
        position: 1,
        kind: 'text',
        prompt: 'Explain',
        options: [],
        answer: { option_id: null, text: '' },
        explanation: '',
        grade: null,
        feedback: '',
        answer_id: 'ans2',
      },
    ],
  };
  const ui = await quizPage(t, {
    setup(window) {
      window.bootstrap = {
        Modal: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
    get: async (url) =>
      url.pathname === '/api/v1/quizzes/q1/attempts/a1' ? structuredClone(attempt) : undefined,
  });
  ui.el('quizResultsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('peopleRows').querySelector('[data-attempt="a1"]'));
  ui.el('peopleRows').querySelector('[data-attempt="a1"]').click();
  await settle(() => shown.length === 1);
  assert.deepEqual(shown, ['attemptModal']);
  assert.equal(ui.el('attemptTitle').textContent, 'Deleted person: waiting for a grade');
  const body = ui.el('attemptBody');
  assert.equal(body.firstElementChild.textContent, 'Handed in not yet');
  assert.deepEqual(
    [...body.querySelectorAll('.quiz-option')].map((o) => o.className),
    ['quiz-option right', 'quiz-option wrong', 'quiz-option'],
  );
  assert.equal(body.querySelector('img'), null);
  assert.equal(ui.window.pwned, undefined);
  assert.match(body.textContent, /No answer/);
  assert.match(body.textContent, /A blank answer scores nothing\./);
  assert.equal(body.dataset.attempt, 'a1');
});

test('an attempt that cannot be opened leaves the dialog closed', async (t) => {
  const shown = [];
  const ui = await quizPage(t, {
    setup(window) {
      window.bootstrap = {
        Modal: { getOrCreateInstance: () => ({ show: () => shown.push('modal') }) },
      };
    },
    get: async (url) => {
      if (url.pathname === '/api/v1/quizzes/q1/attempts/a1') throw new Error('Attempt removed');
      return undefined;
    },
  });
  ui.el('quizResultsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('peopleRows').querySelector('[data-attempt="a1"]'));
  ui.el('peopleRows').querySelector('[data-attempt="a1"]').click();
  await settle(() => ui.el('status').textContent !== '');
  assert.equal(ui.el('status').textContent, 'Failed to open the attempt: Attempt removed');
  assert.deepEqual(shown, []);
  assert.equal(ui.el('attemptBody').dataset.attempt, undefined);
});

test('a grading item without guidance shows only the answer', async (t) => {
  const ui = await quizPage(t, {
    get: async (url) =>
      url.pathname === '/api/v1/quizzes/q1/grading'
        ? {
            items: [
              {
                answer_id: 'ans9',
                round_opens_on: '2026-10-01',
                prompt: 'Explain',
                guidance: '',
                text: 'An answer',
              },
            ],
            own_answers_waiting: 0,
          }
        : undefined,
  });
  const list = ui.el('gradingList');
  assert.equal(ui.el('gradingCount').textContent, '1');
  assert.doesNotMatch(list.textContent, /Guidance/);
  assert.match(list.textContent, /An answer/);
  assert.ok(list.querySelector('[data-grade-answer="ans9"]'));
});

test("the address's tab opens once the quiz has loaded", async (t) => {
  const shown = [];
  await quizPage(t, {
    query: '&id=q1#grading',
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
  });
  assert.deepEqual(shown, ['quizGradingTab']);
});

test('the quiz register counts a single quiz in the singular', async (t) => {
  const ui = await page(t, 'quizzes', {
    me: { can_view_quizzes: true },
    get: async (url) =>
      url.pathname === '/api/v1/quizzes'
        ? {
            framework: 'A',
            items: [
              {
                ...quizView(),
                open_rounds: 0,
                audience_size: 0,
                average: null,
                responses: 0,
                control_count: 0,
              },
            ],
          }
        : { rounds: [], occurrences: [] },
  });
  await settle(() => ui.el('meta').textContent !== 'Loading' && ui.idle());
  assert.equal(ui.el('meta').textContent, '1 quiz');
});

test('My quizzes loads once however often its tab is shown, until Refresh', async (t) => {
  const gets = [];
  const ui = await page(t, 'account', {
    me: { features: { enabled: { quizzes: true } } },
    query: '#my-quizzes',
    setup(window) {
      window.bootstrap = {
        Tab: {
          getOrCreateInstance: (el) => ({
            show: () => el.dispatchEvent(new el.ownerDocument.defaultView.Event('shown.bs.tab')),
          }),
        },
      };
    },
    get: async (url) => {
      gets.push(url.pathname);
      if (url.pathname === '/api/v1/me/quizzes')
        return {
          linked: true,
          person: { id: 'p1', name: 'Ada' },
          items: [],
          upcoming: [],
          progress: {
            points: 0,
            level: 1,
            name: 'Starter',
            floor: 0,
            next: 100,
            finished: 0,
            streak: 0,
            best_streak: 0,
            badges: [],
          },
        };
      if (url.pathname === '/api/v1/me/saved-searches') return [];
      if (url.pathname === '/api/v1/sources/meta') return { items: [] };
      throw new Error(`Unexpected GET ${url.pathname}`);
    },
  });
  const loads = () => gets.filter((g) => g === '/api/v1/me/quizzes').length;
  await settle(() => loads() === 1 && ui.idle());
  ui.el('my-quizzes-tab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await pause();
  assert.equal(loads(), 1);
  ui.el('myQuizzesRefresh').click();
  await settle(() => loads() === 2);
});

async function history(t, apiGet) {
  const ui = await loadUi('pages/person-quizzes.js', {
    html: '<section id="host"></section>',
    vendor: {
      isoDateToDisplay: (iso) => iso,
      withFramework: (url) => url,
      fmtTs: (value) => value,
      apiGet,
    },
  });
  t.after(() => ui.close());
  return { ui, host: ui.document.getElementById('host') };
}
const historyData = (items, extra = {}) => ({
  eligible: true,
  assignments: [],
  assignments_total: 0,
  assignments_next_offset: null,
  items,
  total: items.length,
  next_offset: null,
  ...extra,
});

test("a person's quiz history shows unfinished, ungraded and unscored rounds plainly", async (t) => {
  const round = (overrides) => ({
    quiz_id: 'q1',
    title: 'Security basics',
    opens_on: '2026-10-01',
    due_on: '2026-10-15',
    round_status: 'closed',
    completed_at: null,
    score: null,
    passed: null,
    ...overrides,
  });
  const { ui, host } = await history(t, async () =>
    historyData([
      round({ status: 'expired' }),
      round({ status: 'complete', completed_at: '2026-10-03T09:00:00', score: 40, passed: false }),
      round({ status: 'complete', completed_at: '2026-10-04T09:00:00', score: 90, passed: null }),
      round({ status: 'complete', completed_at: '2026-10-05T09:00:00', score: 80, passed: true }),
    ]),
  );
  await ui.api.personQuizzes(host, 'pe1').load();
  const rows = [...host.querySelectorAll('tbody tr')].map((tr) =>
    [...tr.cells].map((c) => c.textContent),
  );
  assert.deepEqual(
    rows.map((r) => r.slice(2)),
    [
      ['expiredclosed', '—', '—'],
      ['Completedclosed', '2026-10-03T09:00:00', '40% · Below pass mark'],
      ['Completedclosed', '2026-10-04T09:00:00', '90% · No pass mark'],
      ['Completedclosed', '2026-10-05T09:00:00', '80% · Passed'],
    ],
  );
});

test("a person's quiz history shows only the latest page asked for", async (t) => {
  const answers = [];
  const { ui, host } = await history(
    t,
    (url) => new Promise((resolve, reject) => answers.push({ url, resolve, reject })),
  );
  const history_ = ui.api.personQuizzes(host, 'pe1');
  const first = history_.load();
  await settle(() => answers.length === 1);
  answers[0].resolve(
    historyData([], {
      assignments: [{ id: 'q1', title: 'First' }],
      assignments_total: 60,
      assignments_next_offset: 50,
    }),
  );
  await first;
  // Next is asked for, then Previous before Next has answered.
  host.querySelector('[data-assignment-pages] button').click();
  await settle(() => answers.length === 2);
  const second = history_.load();
  await settle(() => answers.length === 3);
  answers[2].resolve(
    historyData([], { assignments: [{ id: 'q3', title: 'Latest' }], assignments_total: 60 }),
  );
  await second;
  // The earlier, slower answer and failure are both set aside.
  answers[1].resolve(
    historyData([], { assignments: [{ id: 'q2', title: 'Stale' }], assignments_total: 60 }),
  );
  await pause();
  assert.match(host.textContent, /Latest/);
  assert.doesNotMatch(host.textContent, /Stale/);

  const third = history_.load();
  await settle(() => answers.length === 4);
  const fourth = history_.load();
  await settle(() => answers.length === 5);
  answers[4].resolve(
    historyData([], { assignments: [{ id: 'q5', title: 'Newest' }], assignments_total: 60 }),
  );
  await fourth;
  answers[3].reject(new Error('Offline'));
  await third;
  assert.match(host.textContent, /Newest/);
  assert.doesNotMatch(host.textContent, /could not be loaded/);
});

async function result(t, view) {
  const ui = await page(t, 'quiz-take', {
    me: { can_view_quizzes: false },
    query: '&attempt=a1',
    get: async (url) => {
      assert.equal(url.pathname, '/api/v1/me/quizzes/attempts/a1');
      return view;
    },
  });
  await settle(() => ui.el('quizMeta').textContent !== 'Loading the quiz');
  return ui;
}

test('a reviewed result marks the right and chosen options and leaves the rest plain', async (t) => {
  const ui = await result(t, {
    id: 'a1',
    title: 'Security basics',
    description: '',
    status: 'complete',
    round_status: 'closed',
    due_on: '2026-11-03',
    question_count: 1,
    score_visible: true,
    score: 0,
    passed: null,
    pass_mark: null,
    trophy: null,
    review: 'shown',
    reward: { points: 10 },
    questions: [
      {
        position: 0,
        kind: 'choice',
        prompt: 'Pick one',
        options: [
          { id: 'o1', text: 'Right', correct: true },
          { id: 'o2', text: 'Chosen', correct: false },
          { id: 'o3', text: 'Other', correct: false },
        ],
        answer: { option_id: 'o2', text: '' },
        explanation: '',
      },
    ],
  });
  // Without a description the meta line is the due date alone.
  assert.equal(ui.el('quizMeta').textContent, 'Due 2026-11-03');
  assert.deepEqual(
    [...ui.el('review').querySelectorAll('.quiz-option')].map((o) => [o.className, o.textContent]),
    [
      ['quiz-option right', 'Right'],
      ['quiz-option wrong', 'Chosen Your answer'],
      ['quiz-option', 'Other'],
    ],
  );
});
