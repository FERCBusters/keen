// The My quizzes tab: rounds to sit first, results as the quiz allows, and the
// person's level, streak and badges.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const PROGRESS = {
  points: 225,
  level: 2,
  name: 'Apprentice',
  floor: 150,
  next: 400,
  finished: 2,
  streak: 2,
  best_streak: 2,
  badges: [
    {
      key: 'first_quiz',
      label: 'First steps',
      description: 'Finish your first quiz.',
      earned: true,
    },
  ],
};

function item(state, overrides = {}) {
  return {
    round_id: `r-${state}`,
    quiz_id: 'q1',
    title: `Quiz ${state}`,
    description: state === 'available' ? 'Monthly <check>' : '',
    opens_on: '2026-10-20',
    due_on: '2026-11-03',
    round_status: 'open',
    state,
    days_left: null,
    attempt: null,
    result: null,
    ...overrides,
  };
}

const ITEMS = [
  item('in_progress', {
    days_left: 1,
    attempt: { id: 'a1', status: 'in_progress', question_count: 6, page_count: 2, pages_saved: 1 },
  }),
  item('available', { days_left: 0 }),
  item('upcoming'),
  item('awaiting_grading', { attempt: { id: 'a2', status: 'submitted' } }),
  item('complete', {
    attempt: { id: 'a3', status: 'complete' },
    result: { score: 92, trophy: 'gold', passed: true, pass_mark: 70 },
  }),
  item('missed', { round_status: 'closed' }),
  item('available', { round_id: 'r-later', days_left: 5 }),
];

async function account(t, { data, fail = false, features } = {}) {
  const gets = [];
  const ui = await page(t, 'account', {
    me: { features: { enabled: { quizzes: true, ...features } } },
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
      if (url.pathname === '/api/v1/me/quizzes') {
        if (fail) throw new Error('Offline');
        return data;
      }
      if (url.pathname === '/api/v1/me/saved-searches') return [];
      if (url.pathname === '/api/v1/sources/meta') return { items: [] };
      throw new Error(`Unexpected GET ${url.pathname}`);
    },
  });
  await settle(() => gets.includes('/api/v1/me/saved-searches') && ui.idle());
  return { ui, gets };
}

test('rounds to sit come first, with what to do next and results', async (t) => {
  const { ui } = await account(t, {
    data: {
      linked: true,
      person: { id: 'p1', name: 'Ada' },
      items: ITEMS,
      upcoming: [{ quiz_id: 'q2', title: 'Phishing', date: '2026-12-01' }],
      progress: PROGRESS,
    },
  });
  await settle(() => ui.el('myQuizzesRows').querySelectorAll('tr').length === ITEMS.length);
  assert.equal(ui.el('myQuizzesUnlinked').hidden, true);
  assert.ok(ui.el('myQuizzesProgress').textContent.includes('Apprentice'));
  const rows = [...ui.el('myQuizzesRows').querySelectorAll('tr')].map((tr) =>
    [...tr.cells].map((c) => c.textContent.trim().replace(/\s+/g, ' ')),
  );
  assert.deepEqual(rows[0].slice(1, 3), [
    'Carry on1 of 2 pages saved',
    'Due 2026-11-03 (in 1 day)',
  ]);
  assert.deepEqual(rows[1].slice(0, 3), [
    'Quiz availableMonthly <check>',
    'Ready to start',
    'Due 2026-11-03 (today)',
  ]);
  assert.equal(rows[2][2], 'Opens 2026-10-20');
  assert.equal(rows[3][2], 'Due 2026-11-03');
  assert.equal(rows[4][3], '92% Gold');
  assert.equal(rows[6][2], 'Due 2026-11-03 (in 5 days)');
  const links = [...ui.el('myQuizzesRows').querySelectorAll('a')].map((a) => [
    a.textContent,
    a.getAttribute('href'),
  ]);
  assert.deepEqual(links, [
    ['Carry on', '/quiz-take.html?round=r-in_progress'],
    ['Start', '/quiz-take.html?round=r-available'],
    ['Result', '/quiz-take.html?attempt=a2'],
    ['Result', '/quiz-take.html?attempt=a3'],
    ['Start', '/quiz-take.html?round=r-later'],
  ]);
  assert.equal(ui.el('myQuizzesUpcoming').textContent.trim(), 'Phishing opens 2026-12-01');
});

test('an unlinked account sees why, and a failure is reported', async (t) => {
  const { ui, gets } = await account(t, {
    data: {
      linked: false,
      person: null,
      items: [],
      upcoming: [],
      progress: { ...PROGRESS, badges: [] },
    },
  });
  await settle(() => !ui.el('myQuizzesUnlinked').hidden);
  assert.equal(ui.el('myQuizzesRows').textContent.trim(), 'No quizzes are assigned to you yet.');
  assert.equal(
    ui.el('myQuizzesUpcoming').textContent.trim(),
    'Nothing scheduled in the coming weeks.',
  );
  ui.el('myQuizzesRefresh').click();
  await settle(() => gets.filter((g) => g === '/api/v1/me/quizzes').length === 2);

  const broken = await account(t, { fail: true });
  await settle(() => broken.ui.el('myQuizzesStatus').style.display === '');
  assert.equal(broken.ui.el('myQuizzesStatus').textContent, 'Failed to load your quizzes: Offline');
});

test('with quizzes switched off the tab loads nothing', async (t) => {
  const { gets } = await account(t, { features: { quizzes: false } });
  assert.equal(gets.includes('/api/v1/me/quizzes'), false);
});
