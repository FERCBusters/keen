// Sitting a quiz: a page at a time with a save as each page is left, the
// hand-in, and the result with the Mitigator's trophy and the review.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const QUESTIONS = [
  {
    position: 0,
    kind: 'choice',
    prompt: 'Who approves <access>?',
    options: [
      { id: 'o1', text: 'The owner' },
      { id: 'o2', text: 'Anyone' },
    ],
    answer: { option_id: null, text: '' },
  },
  {
    position: 1,
    kind: 'choice',
    prompt: 'Report a lost laptop?',
    options: [
      { id: 'o3', text: 'Straight away' },
      { id: 'o4', text: 'Next week' },
    ],
    answer: { option_id: null, text: '' },
  },
  {
    position: 2,
    kind: 'text',
    prompt: 'Name a phishing sign.',
    options: [],
    answer: { option_id: null, text: '' },
  },
];

function attempt(overrides = {}) {
  return {
    id: 'a1',
    quiz_id: 'q1',
    round_id: 'r1',
    title: 'Security basics',
    description: 'Monthly refresher',
    status: 'in_progress',
    round_status: 'open',
    opens_on: '2026-10-20',
    due_on: '2026-11-03',
    question_count: 3,
    page_size: 2,
    page_count: 2,
    pages_saved: 0,
    score_visible: false,
    review: null,
    questions: structuredClone(QUESTIONS),
    ...overrides,
  };
}

async function sitting(
  t,
  { query = '&round=r1', start = attempt(), get, put, post, confirm } = {},
) {
  const ui = await page(t, 'quiz-take', {
    me: { can_view_quizzes: false },
    query,
    globals: { confirm: confirm || (() => true) },
    setup(window) {
      // The clock ticks once straight away rather than every second.
      window.setInterval = (fn) => {
        fn();
        return 0;
      };
    },
    get,
    put,
    post:
      post ||
      (async (url, body) => {
        if (url.pathname.endsWith('/attempt')) return start;
        throw new Error(`Unexpected POST ${url.pathname} ${JSON.stringify(body)}`);
      }),
  });
  await settle(
    () =>
      ui.el('quizMeta').textContent !== 'Loading the quiz' || ui.el('status').style.display === '',
  );
  const prompts = () =>
    [...ui.document.querySelectorAll('#questions legend')].map((l) => l.lastChild.textContent);
  const choose = (position, option) => {
    const input = ui.document.querySelector(`input[name="q${position}"][value="${option}"]`);
    input.checked = true;
    input.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  };
  const write = (position, text) => {
    const area = ui.document.querySelector(`textarea[name="q${position}"]`);
    area.value = text;
    area.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  };
  return { ui, prompts, choose, write };
}

test('a sitting saves each page as it is left and hands in the last', async (t) => {
  const saved = [];
  let handedIn;
  const { ui, prompts, choose, write } = await sitting(t, {
    put: async (url, body) => {
      saved.push([url.pathname, JSON.parse(JSON.stringify(body))]);
      return { id: 'a1', pages_saved: 1 };
    },
    post: async (url, body) => {
      if (url.pathname.endsWith('/attempt')) return attempt();
      handedIn = [url.pathname, JSON.parse(JSON.stringify(body))];
      return attempt({
        status: 'complete',
        score_visible: true,
        score: 100,
        passed: true,
        pass_mark: 70,
        trophy: 'gold',
        review: 'at_close',
        questions: [],
        reward: { points: 175 },
      });
    },
    confirm: () => true,
  });
  assert.equal(
    ui.calls.find((c) => c.method === 'POST').url,
    '/api/v1/me/quizzes/rounds/r1/attempt',
  );
  assert.equal(ui.el('quizTitle').textContent, 'Security basics');
  assert.equal(ui.el('quizMeta').textContent, 'Due 2026-11-03 · Monthly refresher');
  assert.equal(ui.el('timer').textContent, '0:00');
  assert.deepEqual(prompts(), ['Who approves <access>?', 'Report a lost laptop?']);
  assert.equal(ui.el('pageLabel').textContent, 'Page 1 of 2');
  assert.equal(ui.el('back').disabled, true);
  assert.equal(ui.el('handIn').hidden, true);

  choose(0, 'o1');
  assert.equal(ui.el('answeredLabel').textContent, '1 of 3 answered');
  assert.equal(ui.el('answeredBar').getAttribute('aria-valuenow'), '33');
  assert.equal(ui.document.querySelectorAll('.quiz-dot.answered').length, 1);
  assert.equal(ui.document.querySelectorAll('.quiz-dot.current').length, 2);

  ui.el('pageForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('pageLabel').textContent === 'Page 2 of 2');
  assert.deepEqual(saved[0], [
    '/api/v1/me/quizzes/attempts/a1/pages/0',
    {
      answers: [
        { position: 0, option_id: 'o1' },
        { position: 1, option_id: null },
      ],
    },
  ]);
  assert.deepEqual(prompts(), ['Name a phishing sign.']);
  assert.equal(ui.el('handIn').hidden, false);
  assert.equal(ui.el('next').hidden, true);

  write(2, 'Urgent tone');
  // Going back keeps what was chosen on the earlier page.
  ui.el('back').click();
  assert.equal(ui.document.querySelector('input[value="o1"]').checked, true);
  ui.el('pageForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => saved.length === 2 && ui.el('pageLabel').textContent === 'Page 2 of 2');
  assert.equal(ui.document.querySelector('textarea[name="q2"]').value, 'Urgent tone');

  ui.el('handIn').click();
  await settle(() => !ui.el('result').hidden);
  assert.deepEqual(handedIn, [
    '/api/v1/me/quizzes/attempts/a1/submit',
    { page: 1, answers: [{ position: 2, text: 'Urgent tone' }] },
  ]);
  assert.equal(ui.el('sitting').hidden, true);
  assert.equal(ui.el('resultHeading').textContent, 'Gold trophy!');
  assert.ok(ui.el('mascot').querySelector('svg.quiz-trophy'));
  assert.equal(ui.el('resultScore').querySelector('.quiz-score').textContent, '100%');
  assert.ok(ui.el('resultScore').textContent.includes('pass mark 70% · passed'));
  assert.equal(
    ui.el('resultNote').textContent,
    'Your answers and the right ones appear here once the round closes.',
  );
  assert.equal(ui.el('reward').textContent.trim(), '+175 points');
  assert.equal(ui.el('reviewCard').hidden, true);
});

test('handing in with questions unanswered asks first', async (t) => {
  let asked = '';
  const posts = [];
  const { ui } = await sitting(t, {
    start: attempt({ pages_saved: 2 }),
    confirm: (message) => {
      asked = message;
      return false;
    },
    post: async (url) => {
      posts.push(url.pathname);
      return attempt({ pages_saved: 2 });
    },
  });
  // Every page was saved, so the sitting carries on from the last one.
  assert.equal(ui.el('pageLabel').textContent, 'Page 2 of 2');
  ui.el('handIn').click();
  assert.equal(asked, '3 questions are unanswered. Hand in anyway?');
  assert.deepEqual(posts, ['/api/v1/me/quizzes/rounds/r1/attempt']);
});

test('a page that fails to save stays on screen, and so does a failed hand-in', async (t) => {
  const failing = { save: true, handIn: true };
  const resumed = () =>
    attempt({
      pages_saved: 1,
      questions: QUESTIONS.map((q) =>
        q.position === 0 ? { ...q, answer: { option_id: 'o2', text: '' } } : q,
      ),
    });
  const { ui, choose, write } = await sitting(t, {
    start: resumed(),
    put: async () => {
      if (failing.save) throw new Error('offline');
      return { id: 'a1', pages_saved: 1 };
    },
    post: async (url) => {
      if (url.pathname.endsWith('/attempt')) return resumed();
      if (failing.handIn) throw new Error('round closed');
      return attempt({ status: 'submitted', reward: { points: 100 } });
    },
  });
  // The first page was saved, so the sitting carries on from the second.
  assert.equal(ui.el('pageLabel').textContent, 'Page 2 of 2');
  ui.el('back').click();
  assert.equal(ui.document.querySelector('input[value="o2"]').checked, true);
  choose(1, 'o3');
  ui.el('pageForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent.includes('not saved'));
  assert.equal(ui.el('status').textContent, 'Your answers were not saved: Error: offline');
  assert.equal(ui.el('pageLabel').textContent, 'Page 1 of 2');
  assert.equal(ui.el('next').disabled, false);

  failing.save = false;
  ui.el('pageForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('pageLabel').textContent === 'Page 2 of 2');
  write(2, 'Spoofed sender');
  ui.el('handIn').click();
  await settle(() => ui.el('status').textContent.includes('not handed in'));
  assert.equal(ui.el('status').textContent, 'Your answers were not handed in: Error: round closed');
  assert.equal(ui.el('handIn').disabled, false);
  failing.handIn = false;
  ui.el('handIn').click();
  await settle(() => !ui.el('result').hidden);
  assert.equal(ui.el('resultHeading').textContent, 'Waiting for a grade');
  assert.equal(
    ui.el('resultNote').textContent,
    'An assessor grades your written answers; your score appears here once they have.',
  );
  assert.equal(ui.el('mascot').querySelector('svg'), null);
  assert.equal(ui.el('reward').textContent.trim(), '+100 points');
});

test('a finished attempt opens on its result, with the review when answers show', async (t) => {
  const review = attempt({
    status: 'complete',
    round_status: 'closed',
    score_visible: true,
    score: 40,
    passed: false,
    pass_mark: 60,
    trophy: null,
    review: 'shown',
    reward: { points: 100 },
    questions: [
      {
        ...QUESTIONS[0],
        options: [
          { id: 'o1', text: 'The owner', correct: true },
          { id: 'o2', text: 'Anyone', correct: false },
        ],
        answer: { option_id: 'o2', text: '' },
        explanation: 'Owners approve access.',
        correct: false,
      },
      {
        ...QUESTIONS[2],
        position: 1,
        answer: { option_id: null, text: 'Urgency' },
        grade: 3.5,
        feedback: 'Name two',
        pending: false,
        explanation: '',
      },
      {
        ...QUESTIONS[2],
        position: 2,
        answer: { option_id: null, text: '' },
        grade: null,
        pending: true,
        explanation: '',
      },
    ],
  });
  const { ui } = await sitting(t, {
    query: '&attempt=a1',
    get: async (url) => {
      assert.equal(url.pathname, '/api/v1/me/quizzes/attempts/a1');
      return review;
    },
  });
  assert.equal(ui.el('resultHeading').textContent, 'Not this time');
  assert.ok(ui.el('resultScore').textContent.includes('pass mark 60% · not passed'));
  assert.equal(ui.el('resultNote').textContent, '');
  assert.equal(ui.el('reviewCard').hidden, false);
  const options = [...ui.document.querySelectorAll('#review .quiz-option')];
  assert.deepEqual(
    options.map((o) => o.className),
    ['quiz-option right', 'quiz-option wrong'],
  );
  assert.ok(options[1].textContent.includes('Your answer'));
  const text = ui.el('review').textContent;
  assert.ok(text.includes('Owners approve access.'));
  assert.ok(text.includes('Grade 3.5 / 5 Name two'));
  assert.ok(text.includes('No answer'));
  assert.ok(text.includes('Waiting for a grade.'));
});

test('result headings follow what the quiz shows', async (t) => {
  for (const [overrides, heading, note] of [
    [
      { score_visible: false },
      'Handed in',
      'Thanks for taking part. This quiz keeps scores with its managers.',
    ],
    [
      {
        score_visible: true,
        score: 45,
        passed: null,
        pass_mark: null,
        trophy: null,
        review: 'at_close',
      },
      'Finished',
      'Your answers and the right ones appear here once the round closes.',
    ],
    [
      {
        score_visible: true,
        score: 80,
        passed: true,
        pass_mark: 50,
        trophy: 'silver',
        review: 'never',
      },
      'Silver trophy!',
      'This quiz keeps its answers with its managers.',
    ],
    [
      {
        score_visible: true,
        score: 90,
        passed: true,
        pass_mark: 50,
        trophy: 'gold',
        review: 'superseded',
      },
      'Gold trophy!',
      "The quiz's next round has opened, so this round's answers are put away.",
    ],
    [
      {
        score_visible: true,
        score: 60,
        passed: false,
        pass_mark: 70,
        trophy: null,
        review: 'other_round',
      },
      'Not this time',
      "Your answers come back here once you hand in this quiz's other open round.",
    ],
  ]) {
    const { ui } = await sitting(t, {
      query: '&attempt=a1',
      get: async () => attempt({ status: 'complete', ...overrides }),
    });
    assert.equal(ui.el('resultHeading').textContent, heading);
    assert.equal(ui.el('resultNote').textContent, note);
    assert.equal(ui.el('reward').innerHTML, '');
  }
});

test('a closed round, a missing link and a refused round explain themselves', async (t) => {
  const closed = await sitting(t, { start: attempt({ round_status: 'closed' }) });
  assert.equal(
    closed.ui.el('status').textContent,
    'This round closed before you handed in your answers.',
  );
  assert.equal(closed.ui.el('sitting').hidden, true);

  const bare = await sitting(t, { query: '' });
  assert.equal(
    bare.ui.el('status').textContent,
    'Open a quiz from My quizzes on your account page.',
  );

  const refused = await sitting(t, {
    post: async () => {
      throw new Error('Quiz round not found');
    },
  });
  assert.equal(
    refused.ui.el('status').textContent,
    'This quiz cannot be opened: Error: Quiz round not found',
  );
});
