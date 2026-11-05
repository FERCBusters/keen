// The question thread panel shared by records that take questions, driven
// directly: removed authors, a requested thread in a browser that refuses
// smooth scrolling, questions turned off, and clicks and edits that are not
// thread actions. The question queue ignores clicks that are not deletions.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';
import { page, settle } from './support/page-fixture.mjs';

const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
const thread = (extra = {}) => ({
  id: 't1',
  status: 'unanswered',
  created_by_user_id: 'u2',
  created_by_username: 'erin',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  posts: [
    {
      id: 'p1',
      body: 'Why was <b>this</b> skipped?',
      author_user_id: null,
      author_username: '',
      created_at: '2026-01-01T00:00:00Z',
      attachments: [],
    },
  ],
  ...extra,
});

async function panel(
  t,
  { me = { is_admin: true, user_id: 'u1' }, query = '', setup, confirm = true } = {},
) {
  const requests = [],
    notices = [],
    focused = [];
  const ui = await loadUi('pages/question-threads.js', {
    html: '<div id="panel"></div><div id="status"></div>',
    url: 'https://keen.example/risk.html?id=r1' + query,
    setup,
    modules: { 'pages/confirm-modal.js': { confirmAction: async () => confirm } },
    vendor: {
      apiGet: async (url) => {
        requests.push(['GET', url]);
        return { threads: [thread()] };
      },
      apiPost: async (url, body) => {
        requests.push(['POST', url, body]);
        return {};
      },
      apiPatch: async (url, body) => {
        requests.push(['PATCH', url, body]);
        return {};
      },
      apiDelete: async (url) => {
        requests.push(['DELETE', url]);
        return {};
      },
      toast: (element, message, kind) => notices.push([message, kind]),
      withFramework: (url) => url,
      safeExternalHref: (url) => url,
    },
  });
  t.after(ui.close);
  const host = ui.document.getElementById('panel');
  const threads = ui.api.mountQuestionThreads({
    panel: host,
    me,
    status: ui.document.getElementById('status'),
    base: () => '/api/v1/questions/entities/risk/r1',
    onFocus: () => focused.push(true),
  });
  return { ui, host, threads, requests, notices, focused };
}

test('a post by a removed account shows a dash for its author, with its text escaped', async (t) => {
  const { host, threads } = await panel(t);
  await threads.refresh();
  const post = host.querySelector('.question-post');
  assert.equal(post.querySelector('.fw-bold').textContent, '-');
  assert.equal(post.querySelector('.mt-2').textContent, 'Why was <b>this</b> skipped?');
  assert.equal(post.querySelector('b'), null);
});

test('a requested thread opens with a plain scroll where smooth scrolling is refused', async (t) => {
  const scrolls = [];
  const { host, threads, focused } = await panel(t, {
    query: '&thread=t1',
    setup(window) {
      window.HTMLElement.prototype.scrollIntoView = function (options) {
        // Older browsers reject the options object.
        if (options) throw new TypeError('scrollIntoView options are not supported');
        scrolls.push(this.id);
      };
    },
  });
  await threads.refresh();
  assert.deepEqual(scrolls, ['thread-t1']);
  assert.deepEqual(focused, [true]);
  assert.ok(host.querySelector('#thread-t1').classList.contains('question-thread-focus'));
});

test('with questions turned off the panel asks for nothing and offers no form', async (t) => {
  const { host, threads, requests } = await panel(t, {
    me: { is_admin: true, features: { enabled: { questions: false } } },
  });
  await threads.refresh();
  assert.deepEqual(requests, []);
  assert.equal(host.querySelector('[data-question-ask]').hidden, true);
  assert.equal(host.querySelector('[data-question-list]').textContent, 'Loading questions');
});

test('clicks and edits that are not thread actions send nothing, and a declined deletion keeps the thread', async (t) => {
  const { ui, host, threads, requests, notices } = await panel(t, { confirm: false });
  await threads.refresh();
  const sent = () => requests.filter(([method]) => method !== 'GET');
  host.querySelector('.question-post').click();
  host.querySelector('[data-question-action="status"]').click();
  const reply = host.querySelector('textarea[id^="questionReply-"]');
  reply.value = 'Draft reply';
  reply.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  await tick();
  assert.deepEqual(sent(), []);
  assert.deepEqual(notices, []);

  host.querySelector('[data-question-action="delete"]').click();
  await tick();
  assert.deepEqual(sent(), []);
  assert.deepEqual(notices, []);
  assert.ok(host.querySelector('#thread-t1'));
  const remove = host.querySelector('[data-question-action="delete"]');
  assert.equal(remove.disabled, false);
  assert.equal(remove.textContent, 'Delete');
});

test('the question queue ignores clicks that are not on a deletion', async (t) => {
  const removed = [];
  const ui = await page(t, 'questions', {
    get: async (u) => {
      if (u.pathname === '/api/v1/admin/questions')
        return {
          items: [
            { thread_id: 't1', status: 'unanswered', event_id: 'e1', event_summary: 'Login' },
          ],
        };
      throw new Error('Unexpected GET ' + u);
    },
    remove: async (u) => {
      removed.push(u.pathname);
      return {};
    },
  });
  await settle(() => ui.el('queueBody').querySelector('[data-delete-question]'));
  ui.el('queueBody').querySelector('.fw-semibold').click();
  ui.el('queueBody')
    .querySelector('a.btn-outline-primary')
    .dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true, cancelable: true }));
  await tick();
  assert.deepEqual(removed, []);
  assert.deepEqual(ui.confirmations, []);
  assert.equal(ui.el('status').style.display, 'none');
});
