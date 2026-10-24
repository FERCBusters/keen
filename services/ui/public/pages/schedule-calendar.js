// A month calendar of dated entries, shared by scheduled audits and quizzes,
// with the month picker that moves it.
import { esc } from '/app.js';

const pad = (n) => String(n).padStart(2, '0');
const isoOf = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

// The YYYY-MM value of a month input for a date.
export function monthValue(d = new Date()) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
}

// The first and last days of a YYYY-MM month, as dates and ISO dates.
export function monthRange(value) {
  const [yRaw, mRaw] = String(value || monthValue()).split('-');
  const y = Number(yRaw) || new Date().getFullYear();
  const m = Math.max(1, Math.min(12, Number(mRaw) || new Date().getMonth() + 1));
  const start = new Date(y, m - 1, 1);
  const end = new Date(y, m, 0);
  return { start, end, startIso: isoOf(start), endIso: isoOf(end) };
}

export function shiftMonth(value, delta) {
  const [yRaw, mRaw] = String(value || monthValue()).split('-');
  return monthValue(
    new Date(Number(yRaw) || new Date().getFullYear(), (Number(mRaw) || 1) - 1 + delta, 1),
  );
}

// Draws the weeks of `month` into `el`. Each entry has a `date` and a `title`;
// `href(entry)` makes it a link and `kind(entry)` adds a modifier class. A day
// lists up to four entries and counts the rest.
export function renderMonthCalendar(el, month, entries, { href, kind, fallback = '' } = {}) {
  if (!el) return;
  const { start, end } = monthRange(month);
  const byDate = new Map();
  for (const entry of entries || []) {
    if (!byDate.has(entry.date)) byDate.set(entry.date, []);
    byDate.get(entry.date).push(entry);
  }
  const first = new Date(start);
  first.setDate(first.getDate() - first.getDay());
  const last = new Date(end);
  last.setDate(last.getDate() + (6 - last.getDay()));
  const days = [];
  for (const d = new Date(first); d <= last; d.setDate(d.getDate() + 1)) {
    const iso = isoOf(d);
    days.push({
      day: d.getDate(),
      inMonth: d.getMonth() === start.getMonth(),
      items: byDate.get(iso) || [],
    });
  }
  const entryHtml = (entry) => {
    const title = esc(entry.title || fallback);
    const extra = kind ? ` schedule-calendar-${esc(kind(entry))}` : '';
    const link = href?.(entry);
    return link
      ? `<a class="schedule-calendar-event${extra}" href="${esc(link)}" title="${title}">${title}</a>`
      : `<div class="schedule-calendar-event${extra}" title="${title}">${title}</div>`;
  };
  const dow = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  el.innerHTML = `
    <div class="schedule-calendar-grid schedule-calendar-head">${dow.map((d) => `<div>${d}</div>`).join('')}</div>
    <div class="schedule-calendar-grid">${days
      .map(
        (d) => `
      <div class="schedule-calendar-day ${d.inMonth ? '' : 'muted'}">
        <div class="schedule-calendar-date">${d.day}</div>
        ${d.items.slice(0, 4).map(entryHtml).join('')}
        ${d.items.length > 4 ? `<div class="small-muted">+${d.items.length - 4} more</div>` : ''}
      </div>`,
      )
      .join('')}</div>`;
}

// Keeps a month input and its previous/next buttons calling `onChange`, and
// starts on this month.
export function wireMonthPicker({ month, previous, next, onChange }) {
  if (month && !month.value) month.value = monthValue();
  month?.addEventListener('change', onChange);
  for (const [button, delta] of [
    [previous, -1],
    [next, 1],
  ]) {
    button?.addEventListener('click', () => {
      month.value = shiftMonth(month.value, delta);
      onChange();
    });
  }
}
