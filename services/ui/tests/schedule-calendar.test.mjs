// The month calendar shared by scheduled audits and quizzes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

async function calendar(t, html = '<div id="cal"></div>') {
  const ui = await loadUi('pages/schedule-calendar.js', { html });
  t.after(ui.close);
  return ui;
}

test('months are read, bounded and moved', async (t) => {
  const { api } = await calendar(t);
  assert.equal(api.monthValue(new Date(2026, 0, 5)), '2026-01');
  const feb = api.monthRange('2028-02');
  assert.deepEqual([feb.startIso, feb.endIso], ['2028-02-01', '2028-02-29']);
  assert.equal(api.monthRange('2026-13').startIso, '2026-12-01');
  assert.match(api.monthRange('').startIso, /^\d{4}-\d{2}-01$/);
  assert.equal(api.shiftMonth('2026-01', -1), '2025-12');
  assert.equal(api.shiftMonth('2026-12', 1), '2027-01');
  assert.match(api.shiftMonth('', 0), /^\d{4}-\d{2}$/);
});

test('a month lists each day with up to four entries, linked and marked by kind', async (t) => {
  const { api, document } = await calendar(t);
  const el = document.getElementById('cal');
  const entries = [
    { date: '2026-03-02', title: 'Access <review>', quiz: 'q1' },
    ...Array.from({ length: 5 }, (_, n) => ({ date: '2026-03-09', title: `Round ${n}` })),
    { date: '2026-03-20', title: '' },
  ];
  api.renderMonthCalendar(el, '2026-03', entries, {
    href: (entry) => (entry.quiz ? `/quiz.html?id=${entry.quiz}` : ''),
    kind: (entry) => (entry.quiz ? 'round' : 'planned'),
    fallback: 'Quiz',
  });
  const days = [...el.querySelectorAll('.schedule-calendar-day')];
  // March 2026 starts on a Sunday and ends on a Tuesday.
  assert.equal(days.length, 35);
  assert.equal(days.filter((d) => d.classList.contains('muted')).length, 4);
  const linked = el.querySelector('a.schedule-calendar-event');
  assert.deepEqual(
    [linked.textContent, linked.getAttribute('href'), linked.className],
    ['Access <review>', '/quiz.html?id=q1', 'schedule-calendar-event schedule-calendar-round'],
  );
  const busy = days.find((d) => d.textContent.includes('Round 0'));
  assert.equal(busy.querySelectorAll('.schedule-calendar-event').length, 4);
  assert.equal(busy.querySelector('.small-muted').textContent, '+1 more');
  assert.ok(el.innerHTML.includes('schedule-calendar-planned'));
  assert.ok(
    [...el.querySelectorAll('div.schedule-calendar-event')].some((e) => e.textContent === 'Quiz'),
  );
  api.renderMonthCalendar(null, '2026-03', entries);
  api.renderMonthCalendar(el, '2026-03', null);
  assert.equal(el.querySelectorAll('.schedule-calendar-event').length, 0);
});

test('the month picker starts on this month and moves either way', async (t) => {
  const ui = await calendar(
    t,
    '<input id="month"><button id="prev"></button><button id="next"></button>',
  );
  const month = ui.document.getElementById('month');
  const seen = [];
  ui.api.wireMonthPicker({
    month,
    previous: ui.document.getElementById('prev'),
    next: ui.document.getElementById('next'),
    onChange: () => seen.push(month.value),
  });
  assert.equal(month.value, ui.api.monthValue());
  month.value = '2026-05';
  ui.document.getElementById('prev').click();
  ui.document.getElementById('next').click();
  ui.document.getElementById('next').click();
  month.value = '2027-01';
  month.dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(seen, ['2026-04', '2026-05', '2026-06', '2027-01']);
});
