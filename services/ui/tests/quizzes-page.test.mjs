// The quiz register: quizzes in the framework, their calendar and a new quiz.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const QUIZZES = [
  {
    id: 'q1',
    title: 'Security <basics>',
    description: 'Monthly refresher',
    status: 'active',
    pass_mark: 70,
    question_count: 5,
    schedule: { label: 'Monthly', schedule_next_run_date: '2026-11-02' },
    open_rounds: 1,
    rounds: 3,
    availability: { state: 'open', until: '2026-11-05', people: 6 },
    average: 82.5,
    responses: 12,
    audience_size: 6,
    control_count: 2,
  },
  {
    id: 'q2',
    title: 'Draft quiz',
    description: '',
    status: 'draft',
    pass_mark: null,
    question_count: 0,
    schedule: { label: '', schedule_next_run_date: null },
    open_rounds: 0,
    rounds: 0,
    availability: { state: 'draft', on_activation: 'now', until: '2026-11-07' },
    average: null,
    responses: 0,
    audience_size: 0,
    control_count: 0,
  },
  {
    id: 'q3',
    title: 'Ended',
    description: '',
    status: 'archived',
    pass_mark: null,
    question_count: 2,
    schedule: { label: 'Yearly', schedule_next_run_date: null },
    open_rounds: 0,
    rounds: 1,
    availability: { state: 'archived' },
    average: null,
    responses: 0,
    audience_size: 2,
    control_count: 0,
  },
];

async function register(
  t,
  { me = { can_view_quizzes: true }, items = QUIZZES, get, post, globals } = {},
) {
  const requests = [];
  const ui = await page(t, 'quizzes', {
    me,
    globals,
    post,
    get:
      get ||
      (async (url) => {
        requests.push(url.pathname + url.search);
        if (url.pathname === '/api/v1/quizzes') return { framework: 'A', items };
        return {
          rounds: [
            {
              id: 'r1',
              quiz_id: 'q1',
              title: 'Security',
              opens_on: '2026-10-05',
              due_on: '2026-10-19',
              status: 'open',
            },
          ],
          occurrences: [{ date: '2026-10-20', quiz_id: 'q1', title: 'Security' }],
        };
      }),
  });
  await settle(() => ui.el('meta').textContent !== 'Loading' && ui.idle());
  return { ui, requests };
}

test('quizzes list with their schedule, people and average', async (t) => {
  const { ui, requests } = await register(t);
  assert.equal(requests[0], '/api/v1/quizzes?framework=A');
  assert.match(requests[1], /^\/api\/v1\/quizzes\/calendar\?framework=A&start=\d{4}-\d{2}-01&end=/);
  assert.equal(ui.el('meta').textContent, '3 quizzes');
  const rows = [...ui.document.querySelectorAll('#rows tr')].map((tr) =>
    [...tr.cells].map((cell) => cell.textContent.trim().replace(/\s+/g, ' ')),
  );
  assert.deepEqual(rows[0], [
    'Security <basics>Monthly refresher',
    'active 1 openOpen until 2026-11-05',
    '5',
    'MonthlyNext round 2026-11-02',
    '6',
    '83% of 12',
  ]);
  assert.deepEqual(rows[1].slice(1, 4), ['draft', '0', 'One-off']);
  assert.deepEqual(rows[2].slice(3, 6), ['Yearly', '2', '-']);
  assert.equal(
    ui.document.querySelector('#rows a').getAttribute('href'),
    '/quiz.html?id=q1&framework=A',
  );
  assert.equal(ui.el('showCreate').hidden, true);
});

test('the calendar shows rounds and planned openings, month by month', async (t) => {
  const months = [];
  const { ui } = await register(t, {
    get: async (url) => {
      if (url.pathname === '/api/v1/quizzes') return { framework: 'A', items: [] };
      months.push(url.searchParams.get('start'));
      return {
        rounds: [
          {
            id: 'r1',
            quiz_id: 'q1',
            title: 'Round',
            opens_on: '2026-03-02',
            due_on: '2026-03-16',
            status: 'closed',
          },
        ],
        occurrences: [{ date: '2026-03-09', quiz_id: 'q1', title: 'Planned' }],
      };
    },
  });
  assert.equal(ui.document.querySelector('#rows').textContent.trim(), 'No quizzes yet.');
  ui.el('month').value = '2026-03';
  ui.el('month').dispatchEvent(new ui.window.Event('change'));
  await settle(() => months.length === 2 && ui.idle());
  assert.equal(months[1], '2026-03-01');
  const round = ui.document.querySelector('#calendar .schedule-calendar-round');
  assert.equal(round.getAttribute('href'), '/quiz.html?id=q1&framework=A');
  assert.equal(round.textContent, 'Round');
  assert.equal(
    ui.document.querySelector('#calendar .schedule-calendar-planned').textContent,
    'Planned',
  );
});

test('quiz managers create a quiz and open it', async (t) => {
  const posts = [];
  const assigned = [];
  const { ui } = await register(t, {
    me: { can_manage_quizzes: true },
    items: [],
    globals: { location: { search: '?framework=A', assign: (url) => assigned.push(url) } },
    post: async (url, body) => {
      posts.push(JSON.parse(JSON.stringify(body)));
      if (posts.length === 1) throw new Error('title is required');
      return { id: 'new' };
    },
  });
  assert.equal(
    ui.document.querySelector('#rows').textContent.trim(),
    'No quizzes yet. Create one with New quiz.',
  );
  assert.equal(ui.el('showCreate').hidden, false);
  ui.el('showCreate').click();
  assert.equal(ui.el('createCard').hidden, false);
  ui.el('createTitle').value = ' Phishing ';
  ui.el('createPassMark').value = '';
  ui.el('createForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent.includes('title is required'));
  ui.el('createPassMark').value = '75';
  ui.el('createDescription').value = 'Spotting it';
  ui.el('createForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => assigned.length === 1);
  assert.deepEqual(posts, [
    { title: 'Phishing', description: '', framework: 'A', pass_mark: null },
    { title: 'Phishing', description: 'Spotting it', framework: 'A', pass_mark: 75 },
  ]);
  assert.deepEqual(assigned, ['/quiz.html?id=new&framework=A']);
  ui.el('cancelCreate').click();
  assert.equal(ui.el('createCard').hidden, true);
});

test('without quiz.read the page points to My quizzes, and failures are reported', async (t) => {
  const { ui, requests } = await register(t, { me: {} });
  assert.deepEqual(requests, []);
  assert.equal(
    ui.el('status').textContent,
    'Seeing every quiz needs quiz.read. The quizzes assigned to you are under My quizzes.',
  );
  const broken = await register(t, {
    get: async () => {
      throw new Error('boom');
    },
  });
  assert.ok(broken.ui.el('status').textContent.startsWith('Failed to'));
  assert.equal(broken.ui.el('meta').textContent, '-');
});
