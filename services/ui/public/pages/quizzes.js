// The quiz register for the framework in the navigation: each quiz with its
// schedule and average, a calendar of rounds, and a form for a new quiz.
import {
  errorText,
  initNavbar,
  apiGet,
  apiPost,
  esc,
  toast,
  getCurrentFramework,
  withFramework,
} from '/app.js';
import { monthRange, renderMonthCalendar, wireMonthPicker } from '/pages/schedule-calendar.js';
import { availabilityShort, dateText, scoreText } from '/pages/quiz-common.js';

const $ = (id) => document.getElementById(id);
const me = await initNavbar();
const framework = getCurrentFramework();
const status = $('status');
const canManage = Boolean(me?.is_admin || me?.can_manage_quizzes);
const canView = Boolean(canManage || me?.can_view_quizzes);
const quizHref = (id) => withFramework(`/quiz.html?id=${encodeURIComponent(id)}`, framework);

const STATUS_STYLES = {
  draft: 'text-bg-light',
  active: 'text-bg-success',
  archived: 'text-bg-secondary',
};

function scheduleCell(quiz) {
  const schedule = quiz.schedule;
  if (!schedule.label) return '<span class="small-muted">One-off</span>';
  const next = schedule.schedule_next_run_date
    ? `<div class="small-muted">Next round ${esc(dateText(schedule.schedule_next_run_date))}</div>`
    : '';
  return `${esc(schedule.label)}${next}`;
}

function averageCell(quiz) {
  if (quiz.average === null) return '<span class="small-muted">-</span>';
  return `${esc(scoreText(quiz.average))} <span class="small-muted">of ${esc(quiz.responses)}</span>`;
}

function rowHtml(quiz) {
  const open = quiz.open_rounds
    ? ` <span class="badge text-bg-primary">${esc(quiz.open_rounds)} open</span>`
    : '';
  const available = availabilityShort(quiz.availability);
  return `<tr>
    <td class="wrap"><a class="fw-semibold" href="${esc(quizHref(quiz.id))}">${esc(quiz.title)}</a>${quiz.description ? `<div class="small-muted">${esc(quiz.description)}</div>` : ''}</td>
    <td><span class="badge ${STATUS_STYLES[quiz.status]}">${esc(quiz.status)}</span>${open}${available ? `<div class="small-muted">${esc(available)}</div>` : ''}</td>
    <td>${esc(quiz.question_count)}</td>
    <td>${scheduleCell(quiz)}</td>
    <td>${esc(quiz.audience_size)}</td>
    <td>${averageCell(quiz)}</td>
  </tr>`;
}

async function loadList() {
  try {
    const data = await apiGet(`/api/v1/quizzes?framework=${encodeURIComponent(framework)}`);
    $('meta').textContent = `${data.items.length} quiz${data.items.length === 1 ? '' : 'zes'}`;
    $('rows').innerHTML = data.items.length
      ? data.items.map(rowHtml).join('')
      : `<tr><td colspan="6" class="small-muted p-4">No quizzes yet.${canManage ? ' Create one with New quiz.' : ''}</td></tr>`;
  } catch (error) {
    $('meta').textContent = '-';
    toast(status, `Failed to load quizzes: ${errorText(error)}`, 'danger');
  }
}

async function loadCalendar() {
  const month = $('month').value;
  const range = monthRange(month);
  const query = new URLSearchParams({ framework, start: range.startIso, end: range.endIso });
  try {
    const data = await apiGet(`/api/v1/quizzes/calendar?${query}`);
    const entries = [
      ...data.rounds.map((round) => ({ ...round, date: round.opens_on, kind: 'round' })),
      ...data.occurrences.map((occurrence) => ({ ...occurrence, kind: 'planned' })),
    ];
    renderMonthCalendar($('calendar'), month, entries, {
      href: (entry) => quizHref(entry.quiz_id),
      kind: (entry) => entry.kind,
    });
  } catch (error) {
    toast(status, `Failed to load the calendar: ${errorText(error)}`, 'danger');
  }
}

$('showCreate').hidden = !canManage;
$('showCreate').addEventListener('click', () => {
  $('createCard').hidden = false;
  $('createTitle').focus();
});
$('cancelCreate').addEventListener('click', () => {
  $('createCard').hidden = true;
});
$('createForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const passMark = $('createPassMark').value;
  try {
    const quiz = await apiPost('/api/v1/quizzes', {
      title: $('createTitle').value.trim(),
      description: $('createDescription').value.trim(),
      framework,
      pass_mark: passMark === '' ? null : Number(passMark),
    });
    location.assign(quizHref(quiz.id));
  } catch (error) {
    toast(status, `Failed to create the quiz: ${errorText(error)}`, 'danger');
  }
});
wireMonthPicker({
  month: $('month'),
  previous: $('prevMonth'),
  next: $('nextMonth'),
  onChange: loadCalendar,
});

if (canView) {
  await Promise.all([loadList(), loadCalendar()]);
} else {
  $('meta').textContent = '-';
  toast(
    status,
    'Seeing every quiz needs quiz.read. The quizzes assigned to you are under My quizzes.',
    'info',
  );
}
