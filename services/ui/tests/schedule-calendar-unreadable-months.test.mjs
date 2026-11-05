// The schedule calendar's month picker where the browser keeps a month box as
// free text: an unreadable or out-of-range month shows a month, and moving
// on continues from the month the calendar shows.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const pad = (n) => String(n).padStart(2, '0');
const month = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
const now = new Date();

async function picker(t) {
  const ui = await loadUi('pages/schedule-calendar.js', {
    html: '<input id="month"><button id="prev"></button><button id="next"></button>',
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  const seen = [];
  ui.api.wireMonthPicker({
    month: el('month'),
    previous: el('prev'),
    next: el('next'),
    onChange: () => seen.push(el('month').value),
  });
  return { api: ui.api, el, seen };
}

test('a month typed as free text shows this year or this month in its place', async (t) => {
  const { api } = await picker(t);
  assert.equal(month(api.monthRange('May 2026').start), month(now));
  assert.equal(api.monthRange('2026-xy').startIso, `2026-${pad(now.getMonth() + 1)}-01`);
  assert.equal(api.monthRange('xx-05').startIso, `${now.getFullYear()}-05-01`);
});

test('moving from an unreadable month continues from the month the calendar shows', async (t) => {
  const { el, seen } = await picker(t);
  el('month').value = 'May 2026';
  el('next').click();
  assert.deepEqual(seen, [month(new Date(now.getFullYear(), now.getMonth() + 1, 1))]);
  // A month past December shows as December, so the next month is January.
  el('month').value = '2026-13';
  el('next').click();
  // A month of 00 is unreadable, so the calendar shows this month of 2026.
  el('month').value = '2026-00';
  el('prev').click();
  assert.deepEqual(seen.slice(1), ['2027-01', month(new Date(2026, now.getMonth() - 1, 1))]);
});
