import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const items = [
  {
    thread_id: 't1',
    status: 'answered',
    unread: true,
    event_id: 'e1',
    event_summary: '<img src=x onerror=alert(1)> Login',
    event_source: 'auth',
    event_timestamp: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    created_by_username: 'erin',
  },
  {
    thread_id: 't2',
    status: 'reviewing',
    target_type: 'pestle_item',
    target_label: 'Privacy law',
    target_ref: 'P-1',
    target_url: '/pestle_item.html?id=p1&thread=t2#questions',
  },
  { thread_id: 't3' },
];

async function questionsPage(t, name, options = {}) {
  const state = {
    items: structuredClone(options.items ?? items),
    removed: [],
    statuses: [],
    bell: 0,
  };
  const ui = await page(t, name, {
    me: options.me || { is_admin: true },
    globals: options.globals,
    setup(window) {
      // The design system's navbar exposes this hook for the question bell.
      window.keenRefreshBell = () => (state.bell += 1);
    },
    get: async (u) => {
      if (u.pathname === '/api/v1/admin/questions') {
        state.statuses.push(u.searchParams.get('status'));
        if (options.fail) throw new Error('Queue offline');
        return { items: state.items };
      }
      if (u.pathname === '/api/v1/me/questions') {
        if (options.fail) throw new Error('Mine offline');
        return { items: state.items };
      }
      throw new Error('Unexpected GET ' + u);
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.deleteFail) throw new Error('Delete refused');
      state.items = state.items.filter((i) => !u.pathname.endsWith('/' + i.thread_id));
      return { ok: true };
    },
  });
  await settle(() => ui.calls.length > 1);
  await new Promise((r) => setTimeout(r, 5));
  return { ui, state };
}

test('administrators see the question queue with safe titles and target links', async (t) => {
  const { ui } = await questionsPage(t, 'questions');
  assert.equal(ui.el('adminCard').style.display, '');
  assert.equal(ui.el('userCard').style.display, 'none');
  const rows = [...ui.el('queueBody').querySelectorAll('tr')];
  assert.equal(rows.length, 3);
  assert.equal(ui.el('queueBody').querySelector('img'), null);
  assert.match(rows[0].textContent, /<img src=x onerror=alert\(1\)> Login/);
  assert.match(rows[0].textContent, /auth · 2026-01-01T00:00:00Z/);
  assert.match(rows[1].textContent, /pestle item · P-1/);
  assert.deepEqual(
    [...rows[2].cells].slice(1, 4).map((c) => c.textContent.replace(/\s+/g, ' ').trim()),
    ['-', '(no summary) -', '-'],
  );
  assert.match(rows[1].querySelector('.badge').textContent, /reviewing/);
  assert.match(rows[2].querySelector('.badge').textContent, /unanswered/);
  const hrefs = rows.map((r) => r.querySelector('a.btn').getAttribute('href'));
  assert.equal(hrefs[0], '/event.html?id=e1&thread=t1&framework=A#questions');
  assert.equal(hrefs[1], '/pestle_item.html?id=p1&thread=t2&framework=A#questions');
});

test('the admin queue filters by status and refreshes', async (t) => {
  const { ui, state } = await questionsPage(t, 'questions', { items: [] });
  assert.match(ui.el('queueBody').textContent, /No matching questions/);
  ui.el('queueStatus').value = 'answered';
  ui.el('queueStatus').dispatchEvent(new ui.window.Event('change'));
  await settle(() => state.statuses.length === 2);
  assert.deepEqual(state.statuses, [null, 'answered']);
  ui.el('btnRefresh').click();
  await settle(() => state.statuses.length === 3);
});

test('question deletion confirms and reloads the queue', async (t) => {
  const cancelled = await questionsPage(t, 'questions', { globals: { confirm: () => false } });
  cancelled.ui.document.querySelector('[data-delete-question="t1"]').click();
  assert.deepEqual(cancelled.state.removed, []);
  const { ui, state } = await questionsPage(t, 'questions', { globals: { confirm: () => true } });
  ui.document.querySelector('[data-delete-question="t2"]').click();
  await settle(() => ui.el('queueBody').querySelectorAll('tr').length === 2);
  assert.deepEqual(state.removed, ['/api/v1/questions/t2']);
  assert.equal(ui.el('status').textContent, 'Question deleted');
  const failed = await questionsPage(t, 'questions', {
    deleteFail: true,
    globals: { confirm: () => true },
  });
  failed.ui.document.querySelector('[data-delete-question="t1"]').click();
  await settle(() => failed.ui.el('status').textContent === 'Delete refused');
});

test('non-admin users see their own threads with unread replies highlighted', async (t) => {
  const { ui } = await questionsPage(t, 'questions', { me: { can_question_events: true } });
  assert.equal(ui.el('userCard').style.display, '');
  assert.equal(ui.el('adminCard').style.display, 'none');
  const rows = [...ui.el('questionsTbody').querySelectorAll('tr')];
  assert.ok(rows[0].classList.contains('table-warning'));
  assert.match(rows[0].textContent, /new reply/);
  assert.equal(ui.document.querySelector('[data-delete-question]'), null);
  assert.equal(ui.el('questionsTableWrap').style.display, '');
  ui.el('btnRefreshUser').click();
  await settle(() => ui.calls.filter((c) => c.url === '/api/v1/me/questions').length === 2);
});

test('users with delete permission can remove their threads; empty and failed lists are explained', async (t) => {
  const { ui, state } = await questionsPage(t, 'questions', {
    me: { can_delete_questions: true },
    items: [items[0]],
    globals: { confirm: () => true },
  });
  ui.document.querySelector('[data-delete-question="t1"]').click();
  await settle(() => ui.el('questionsEmpty').style.display === '');
  assert.deepEqual(state.removed, ['/api/v1/questions/t1']);
  assert.equal(ui.el('questionsTableWrap').style.display, 'none');
  const failed = await questionsPage(t, 'questions', { fail: true });
  assert.match(failed.ui.el('status').textContent, /Queue offline/);
  assert.match(failed.ui.el('status').className, /alert/);
});
