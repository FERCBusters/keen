// One quiz: its settings and controls, questions, people, schedule and
// rounds, results with insights, and grading. quiz.read sees the quiz, its
// questions without their answers and results that describe a group;
// quiz.manage changes everything and sees each person's answers.
import {
  errorText,
  initNavbar,
  apiGet,
  apiPost,
  apiPatch,
  apiPut,
  apiDelete,
  confirmAction,
  esc,
  toast,
  frameworkLabel,
  getCurrentFramework,
  withFramework,
  wireIsoDateTextField,
} from '/app.js';
import { isoYearMonth, scheduleDateFields, scheduleSummary } from '/pages/schedule-fields.js';
import {
  availabilityText,
  dateText,
  percentBar,
  roundStatusBadge,
  scoreText,
  trophyBadge,
  TROPHIES,
} from '/pages/quiz-common.js';

const $ = (id) => document.getElementById(id);
const me = await initNavbar();
const framework = getCurrentFramework();
const status = $('status');
const quizId = new URLSearchParams(location.search).get('id') || '';
const api = (path = '') => `/api/v1/quizzes/${encodeURIComponent(quizId)}${path}`;
let quiz = null;
let canManage = false;
// The questions being edited, and the people, accounts and roles chosen.
let draft = [];
let chosen = { people: new Map(), accounts: new Map(), roles: new Map() };
let assignees = null;

const wires = Object.fromEntries(
  ['roundOpens', 'roundDue', 'scheduleStart', 'until'].map((id) => [
    id,
    wireIsoDateTextField($(id), $(`${id}Text`), $(`${id}Pick`), { dispatchChangeOnText: true }),
  ]),
);
const dates = scheduleDateFields(
  {
    rule: $('dateRule'),
    recurrence: $('recurrence'),
    exactFields: $('exactDateFields'),
    fuzzyFields: $('fuzzyDateFields'),
    month: $('fuzzyMonth'),
    monthLabel: $('fuzzyMonthLabel'),
    year: $('fuzzyYear'),
    start: $('scheduleStart'),
    startText: $('scheduleStartText'),
    preview: $('schedulePreview'),
  },
  { lead: 'The first round' },
);

function dateValue(id) {
  wires[id]?.syncHiddenFromText?.();
  return $(id).value;
}

function setDate(id, iso) {
  $(id).value = iso || '';
  wires[id]?.syncTextFromHidden?.();
}

function isoToday(offset = 0) {
  const day = new Date();
  day.setDate(day.getDate() + offset);
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
}

async function attempt(action, done, failure) {
  try {
    const result = await action();
    if (done) toast(status, done, 'success');
    return result;
  } catch (error) {
    toast(status, `${failure}: ${errorText(error)}`, 'danger');
    return null;
  }
}

// --- Header and overview -------------------------------------------------------------

function renderHeader() {
  document.title = `Keen - ${quiz.title}`;
  $('quizTitle').textContent = quiz.title;
  $('quizMeta').hidden = false;
  $('quizMeta').textContent = [
    frameworkLabel(quiz.framework),
    quiz.status,
    `${quiz.question_count} question${quiz.question_count === 1 ? '' : 's'}`,
    quiz.schedule.label ? scheduleSummary(quiz.schedule) : 'One-off',
  ].join(' · ');
  $('headerActions').hidden = !canManage;
  $('activate').hidden = quiz.status === 'active';
  $('archive').hidden = quiz.status !== 'active';
  $('deleteQuiz').hidden = quiz.rounds.some((round) => round.started > 0);
  for (const el of document.querySelectorAll('.manager-only')) el.hidden = !canManage;
  renderAvailability();
}

const AVAILABILITY_STYLES = {
  open: 'alert-success',
  archived: 'alert-secondary',
};

// Says on every tab whether people can take the quiz, so activating it, or a
// round closing, leaves no doubt about who can sit it.
function renderAvailability() {
  const availability = quiz.availability;
  $('availabilityBox').className =
    `alert ${AVAILABILITY_STYLES[availability.state] || 'alert-warning'} d-flex flex-wrap gap-2 align-items-center justify-content-between mb-3`;
  $('availabilityText').textContent = availabilityText(availability, { manager: canManage });
  $('startRoundNow').hidden = !(canManage && ['waiting', 'no_round'].includes(availability.state));
  $('availability').hidden = false;
}

function renderSettings() {
  $('title').value = quiz.title;
  $('description').value = quiz.description;
  $('passMark').value = quiz.pass_mark ?? '';
  $('perAttempt').value = quiz.questions_per_attempt ?? '';
  $('pageSize').value = quiz.page_size;
  $('dueDays').value = quiz.due_days;
  $('showScores').checked = quiz.show_scores;
  $('revealAnswers').value = quiz.reveal_answers;
  for (const field of $('settingsForm').elements) field.disabled = !canManage;
  $('controlBadges').innerHTML = quiz.controls.length
    ? quiz.controls
        .map(
          (c) =>
            `<a class="badge badge-soft text-decoration-none me-1 mb-1" href="${esc(withFramework(`/control.html?id=${encodeURIComponent(c.id)}`, framework))}" title="${esc(c.title || c.ref)}">${esc(c.ref)}</a>`,
        )
        .join('')
    : '<span class="small-muted">No controls linked yet.</span>';
  const recorded = quiz.rounds.filter((round) => round.recorded).length;
  $('measureInfo').innerHTML =
    `<a href="${esc(withFramework(`/isms-effectiveness-measure.html?id=${encodeURIComponent(quiz.measure_id)}`, framework))}">Quiz: ${esc(quiz.title)}</a>
    <div class="small-muted mt-1">${recorded} round${recorded === 1 ? '' : 's'} recorded. Target: ${quiz.pass_mark === null ? 'none' : `at least ${esc(quiz.pass_mark)}%`}.</div>`;
}

async function loadControlChoices() {
  const data = await attempt(
    () => apiGet(`/api/v1/controls?framework=${encodeURIComponent(framework)}&limit=5000`),
    null,
    'Failed to load the controls',
  );
  if (!data) return;
  const linked = new Set(quiz.controls.map((c) => c.id));
  $('controls').innerHTML = data.items
    .filter((c) => c.type !== 'clause')
    .map(
      (c) =>
        `<option value="${esc(c.id)}"${linked.has(c.id) ? ' selected' : ''}>${esc(`${c.ref} ${c.title || ''}`.trim())}</option>`,
    )
    .join('');
}

async function saveSettings(changes, done) {
  const saved = await attempt(() => apiPatch(api(), changes), done, 'Not saved');
  if (saved) render(saved);
  return saved;
}

$('settingsForm').addEventListener('submit', (ev) => {
  ev.preventDefault();
  const number = (id) => ($(id).value === '' ? null : Number($(id).value));
  saveSettings(
    {
      title: $('title').value.trim(),
      description: $('description').value,
      pass_mark: number('passMark'),
      questions_per_attempt: number('perAttempt'),
      page_size: number('pageSize'),
      due_days: number('dueDays'),
      show_scores: $('showScores').checked,
      reveal_answers: $('revealAnswers').value,
    },
    'Settings saved',
  );
});
$('saveControls').addEventListener('click', () =>
  saveSettings(
    { controls: [...$('controls').selectedOptions].map((option) => option.value) },
    'Controls saved',
  ),
);
const ACTIVATED = {
  open: (a) => `Quiz activated and open until ${dateText(a.until)}.`,
  waiting: (a) => `Quiz activated. Its first round opens on ${dateText(a.opens_on)}.`,
  no_round: () => 'Quiz activated. No one can take it until you start a round.',
};
$('activate').addEventListener('click', async () => {
  const saved = await saveSettings({ status: 'active' }, null);
  if (saved) toast(status, ACTIVATED[saved.availability.state](saved.availability), 'success');
});
$('archive').addEventListener('click', async () => {
  if (
    !(await confirmAction({
      title: 'Archive this quiz',
      message: 'Its open rounds close and its schedule stops. Archive the quiz?',
      confirmLabel: 'Archive',
      danger: true,
    }))
  )
    return;
  saveSettings({ status: 'archived' }, 'Quiz archived');
});
$('deleteQuiz').addEventListener('click', async () => {
  if (
    !(await confirmAction({
      title: 'Delete this quiz',
      message: 'Delete the quiz and its effectiveness measure?',
      confirmLabel: 'Delete',
      danger: true,
    }))
  )
    return;
  const done = await attempt(() => apiDelete(api()), null, 'Not deleted');
  if (done) location.assign(withFramework('/quizzes.html', framework));
});

// --- Questions ------------------------------------------------------------------------

function blankQuestion(kind) {
  return kind === 'choice'
    ? {
        kind,
        prompt: '',
        explanation: '',
        // No option starts as the right one, so the editor chooses it.
        options: [
          { text: '', correct: false },
          { text: '', correct: false },
        ],
      }
    : { kind, prompt: '', explanation: '', options: [] };
}

function readOnlyQuestionHtml(question, index) {
  const options = question.options.length
    ? `<ul class="mb-0 mt-2">${question.options.map((o) => `<li>${esc(o.text)}</li>`).join('')}</ul>`
    : '<div class="small-muted mt-2">Answered in writing.</div>';
  return `<div class="quiz-question"><div class="fw-semibold">${index + 1}. ${esc(question.prompt)}</div>${options}</div>`;
}

function editorOptionHtml(option, q, o) {
  return `<div class="input-group input-group-sm mb-2">
      <span class="input-group-text"><input class="form-check-input mt-0" type="radio" name="correct${q}" data-q="${q}" data-o="${o}" data-field="correct" aria-label="Right answer"${option.correct ? ' checked' : ''}></span>
      <input class="form-control" maxlength="2000" data-q="${q}" data-o="${o}" data-field="text" value="${esc(option.text)}" placeholder="Option ${o + 1}" aria-label="Option ${o + 1}">
      <button class="btn btn-outline-danger" type="button" data-remove-option="${q}:${o}" aria-label="Remove option ${o + 1}"><i class="bi bi-x-lg" aria-hidden="true"></i></button>
    </div>`;
}

function editorQuestionHtml(question, q) {
  const choice = question.kind === 'choice';
  const options = choice
    ? `<div class="small-muted mb-1">Options; mark the right answer.</div>${question.options.map((option, o) => editorOptionHtml(option, q, o)).join('')}
       <button class="btn btn-sm btn-outline-secondary" type="button" data-add-option="${q}"${question.options.length >= 10 ? ' disabled' : ''}>Add option</button>`
    : '';
  return `<div class="quiz-question" data-question="${q}">
      <div class="d-flex flex-wrap gap-2 align-items-center justify-content-between mb-2">
        <span class="fw-semibold">${q + 1}. ${choice ? 'Multiple choice' : 'Written answer'}</span>
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-secondary" type="button" data-move="${q}:-1" aria-label="Move up"${q === 0 ? ' disabled' : ''}><i class="bi bi-arrow-up" aria-hidden="true"></i></button>
          <button class="btn btn-outline-secondary" type="button" data-move="${q}:1" aria-label="Move down"${q === draft.length - 1 ? ' disabled' : ''}><i class="bi bi-arrow-down" aria-hidden="true"></i></button>
          <button class="btn btn-outline-danger" type="button" data-remove-question="${q}" aria-label="Remove question"><i class="bi bi-trash" aria-hidden="true"></i></button>
        </div>
      </div>
      <textarea class="form-control mb-2" rows="2" maxlength="4000" data-q="${q}" data-field="prompt" placeholder="The question" aria-label="Question ${q + 1}">${esc(question.prompt)}</textarea>
      ${options}
      <textarea class="form-control mt-2" rows="2" maxlength="4000" data-q="${q}" data-field="explanation" placeholder="${choice ? 'Why the right answer is right, shown when people review their answers' : 'What a good answer covers: guidance for graders, shown when people review their answers'}" aria-label="Explanation for question ${q + 1}">${esc(question.explanation)}</textarea>
    </div>`;
}

function renderQuestions() {
  if (!canManage) {
    $('questionList').innerHTML = quiz.questions.length
      ? quiz.questions.map(readOnlyQuestionHtml).join('')
      : '<div class="small-muted">No questions yet.</div>';
    return;
  }
  $('questionList').innerHTML = draft.length
    ? draft.map(editorQuestionHtml).join('')
    : '<div class="small-muted">No questions yet. Add multiple-choice or written questions, then save.</div>';
}

$('questionList').addEventListener('input', (ev) => {
  const { q, o, field } = ev.target.dataset;
  if (field === undefined) return;
  const question = draft[Number(q)];
  if (field === 'correct') {
    question.options.forEach((option, index) => (option.correct = index === Number(o)));
  } else if (o !== undefined) {
    question.options[Number(o)].text = ev.target.value;
  } else {
    question[field] = ev.target.value;
  }
});

$('questionList').addEventListener('click', (ev) => {
  const button = ev.target.closest('button');
  if (!button) return;
  const { addOption, removeOption, move, removeQuestion } = button.dataset;
  if (addOption !== undefined) {
    draft[Number(addOption)].options.push({ text: '', correct: false });
  } else if (removeOption !== undefined) {
    const [q, o] = removeOption.split(':').map(Number);
    draft[q].options.splice(o, 1);
  } else if (move !== undefined) {
    const [q, delta] = move.split(':').map(Number);
    [draft[q], draft[q + delta]] = [draft[q + delta], draft[q]];
  } else if (removeQuestion !== undefined) {
    draft.splice(Number(removeQuestion), 1);
  }
  renderQuestions();
});

$('addChoice').addEventListener('click', () => {
  draft.push(blankQuestion('choice'));
  renderQuestions();
});
$('addWritten').addEventListener('click', () => {
  draft.push(blankQuestion('text'));
  renderQuestions();
});
$('saveQuestions').addEventListener('click', async () => {
  const saved = await attempt(
    () => apiPut(api('/questions'), { questions: draft }),
    'Questions saved',
    'Questions not saved',
  );
  if (saved) render(saved);
});

// --- People ----------------------------------------------------------------------------

function chips(map, kind, note) {
  return [...map]
    .map(
      ([id, entry]) =>
        `<span class="badge text-bg-light border me-1 mb-1">${esc(entry.name)}${note(entry)} <button class="btn btn-link btn-sm p-0 ms-1" type="button" data-unassign="${kind}:${esc(id)}" aria-label="Remove ${esc(entry.name)}"><i class="bi bi-x" aria-hidden="true"></i></button></span>`,
    )
    .join('');
}

function renderAudience() {
  const people = chips(chosen.people, 'people', (p) =>
    p.can_sit === false
      ? ' <span class="text-warning" title="No Keen account to sit quizzes with">(no account)</span>'
      : '',
  );
  const accounts = chips(
    chosen.accounts,
    'accounts',
    () => ' <span class="small-muted">(account)</span>',
  );
  const roles = chips(
    chosen.roles,
    'roles',
    (r) => ` <span class="small-muted">(role, ${esc(r.holders)})</span>`,
  );
  $('audienceList').innerHTML =
    people + accounts + roles || '<span class="small-muted">Nobody yet.</span>';
  $('audienceSize').textContent = `${quiz.audience.size} people can sit the next round.`;
}

function setChosen(audience) {
  chosen = {
    people: new Map(audience.people.map((p) => [p.id, p])),
    accounts: new Map(),
    roles: new Map(audience.roles.map((r) => [r.id, r])),
  };
}

function fillPickers() {
  const options = (items, label) =>
    `<option value="">Choose</option>${items.map((item) => `<option value="${esc(item.id)}">${esc(label(item))}</option>`).join('')}`;
  $('pickPerson').innerHTML = options(
    assignees.people,
    (p) => `${p.name}${p.role ? ` (${p.role})` : ''}${p.can_sit ? '' : ' - no account'}`,
  );
  $('pickAccount').innerHTML = options(assignees.accounts, (a) => a.username);
  $('pickRole').innerHTML = options(assignees.roles, (r) => `${r.name} (${r.holders})`);
}

async function loadAssignees() {
  assignees = await attempt(
    () => apiGet('/api/v1/quizzes/assignees'),
    null,
    'Failed to load people',
  );
  if (assignees) fillPickers();
}

for (const [button, picker, kind, source, shape] of [
  ['addPerson', 'pickPerson', 'people', 'people', (p) => p],
  ['addAccount', 'pickAccount', 'accounts', 'accounts', (a) => ({ ...a, name: a.username })],
  ['addRole', 'pickRole', 'roles', 'roles', (r) => r],
]) {
  $(button).addEventListener('click', () => {
    const entry = assignees?.[source].find((item) => item.id === $(picker).value);
    if (!entry) return;
    chosen[kind].set(entry.id, shape(entry));
    renderAudience();
  });
}

$('audienceList').addEventListener('click', (ev) => {
  const button = ev.target.closest('[data-unassign]');
  if (!button) return;
  const [kind, id] = button.dataset.unassign.split(':');
  chosen[kind].delete(id);
  renderAudience();
});

$('saveAudience').addEventListener('click', async () => {
  const audience = await attempt(
    () =>
      apiPut(api('/audience'), {
        person_ids: [...chosen.people.keys()],
        user_ids: [...chosen.accounts.keys()],
        org_node_ids: [...chosen.roles.keys()],
      }),
    'People saved',
    'People not saved',
  );
  if (!audience) return;
  quiz.audience = audience;
  setChosen(audience);
  renderAudience();
  await loadAssignees();
});

// --- Schedule and rounds -----------------------------------------------------------------

function renderSchedule() {
  const schedule = quiz.schedule;
  const start = schedule.schedule_next_run_date || schedule.start_date || '';
  $('dateRule').value = schedule.schedule_date_rule;
  setDate('scheduleStart', start);
  $('recurrence').value = schedule.schedule_recurrence;
  $('interval').value = String(schedule.schedule_interval);
  setDate('until', schedule.schedule_until_date);
  const month = isoYearMonth(start);
  $('fuzzyMonth').value = String(schedule.schedule_anchor_month || month.month);
  $('fuzzyYear').value = String(month.year);
  dates.update();
  $('scheduleInfo').textContent = schedule.label
    ? `${scheduleSummary(schedule)}. Next round: ${dateText(schedule.schedule_next_run_date)}. Each round stays open for ${quiz.due_days} days.`
    : `One-off: activating it opens it for ${quiz.due_days} days. Set a first round to schedule rounds instead, or start another round by hand.`;
  for (const field of $('scheduleForm').elements) field.disabled = !canManage;
}

// A round whose result is on the measure stays closed: reopening it for a
// latecomer would give their score away to anyone comparing the averages.
function roundActions(round) {
  const toggle =
    round.status === 'open'
      ? `<button class="btn btn-sm btn-outline-secondary" type="button" data-round-status="${esc(round.id)}:closed">Close</button>`
      : round.recorded
        ? ''
        : `<button class="btn btn-sm btn-outline-secondary" type="button" data-round-status="${esc(round.id)}:open">Reopen</button>`;
  const remove = round.started
    ? ''
    : `<button class="btn btn-sm btn-outline-danger" type="button" data-delete-round="${esc(round.id)}" aria-label="Delete round"><i class="bi bi-trash" aria-hidden="true"></i></button>`;
  return `<div class="d-inline-flex gap-1 align-items-center">
      <input class="form-control form-control-sm" type="date" value="${esc(round.due_on)}" data-round-due="${esc(round.id)}" aria-label="Due date" style="width:9.5rem">
      ${toggle}${remove}</div>`;
}

function averageCell(round) {
  if (!round.results_shown) {
    return '<span class="small-muted" title="Shown once the round closes with every response graded and at least three finished">Not yet</span>';
  }
  const recorded = round.recorded
    ? ' <i class="bi bi-check2-circle text-success" title="Recorded on the measure" aria-label="Recorded on the measure"></i>'
    : '';
  return `${esc(scoreText(round.average))}${recorded}`;
}

function renderRounds() {
  $('roundRows').innerHTML = quiz.rounds.length
    ? quiz.rounds
        .map(
          (round) => `<tr>
        <td>${esc(dateText(round.opens_on))}${round.scheduled ? ' <span class="badge text-bg-light">scheduled</span>' : ''}</td>
        <td>${esc(dateText(round.due_on))}</td>
        <td>${roundStatusBadge(round.status)}</td>
        <td>${esc(round.complete)} of ${esc(round.participants)}${round.pending_grading ? ` <span class="badge text-bg-info">${esc(round.pending_grading)} to grade</span>` : ''}</td>
        <td>${averageCell(round)}</td>
        <td class="text-end manager-only"${canManage ? '' : ' hidden'}>${roundActions(round)}</td>
      </tr>`,
        )
        .join('')
    : '<tr><td colspan="6" class="small-muted p-4">No rounds yet.</td></tr>';
  setDate('roundOpens', isoToday());
  setDate('roundDue', isoToday(quiz.due_days));
}

async function refresh(message) {
  const fresh = await attempt(() => apiGet(api()), message, 'Failed to reload the quiz');
  if (fresh) render(fresh);
}

// Today, due after the quiz's days to answer: the server fills in both dates.
$('startRoundNow').addEventListener('click', async () => {
  const started = await attempt(() => apiPost(api('/rounds'), {}), null, 'The round did not start');
  if (started) await refresh(`Round started for ${started.participants} people`);
});

$('roundForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const started = await attempt(
    () =>
      apiPost(api('/rounds'), { opens_on: dateValue('roundOpens'), due_on: dateValue('roundDue') }),
    null,
    'The round did not start',
  );
  if (started) await refresh(`Round started for ${started.participants} people`);
});

$('roundRows').addEventListener('click', async (ev) => {
  const button = ev.target.closest('button');
  if (!button) return;
  if (button.dataset.roundStatus) {
    const [id, next] = button.dataset.roundStatus.split(':');
    const due = document.querySelector(`[data-round-due="${CSS.escape(id)}"]`).value;
    const changed = await attempt(
      () =>
        apiPatch(
          api(`/rounds/${encodeURIComponent(id)}`),
          next === 'open' ? { status: next, due_on: due } : { status: next },
        ),
      null,
      'The round did not change',
    );
    if (changed) await refresh(next === 'open' ? 'Round reopened' : 'Round closed');
  } else if (button.dataset.deleteRound) {
    if (
      !(await confirmAction({
        title: 'Delete this round',
        message: 'Delete the round with its schedule and the answers handed in for it?',
        confirmLabel: 'Delete',
        danger: true,
      }))
    )
      return;
    const removed = await attempt(
      () => apiDelete(api(`/rounds/${encodeURIComponent(button.dataset.deleteRound)}`)),
      null,
      'The round was not deleted',
    );
    if (removed) await refresh('Round deleted');
  }
});

$('roundRows').addEventListener('change', async (ev) => {
  const id = ev.target.dataset.roundDue;
  if (!id) return;
  const moved = await attempt(
    () => apiPatch(api(`/rounds/${encodeURIComponent(id)}`), { due_on: ev.target.value }),
    null,
    'The due date did not change',
  );
  if (moved) await refresh('Due date moved; people are reminded again before it');
});

$('scheduleForm').addEventListener('submit', (ev) => {
  ev.preventDefault();
  saveSettings(
    {
      start_date: dates.startForSubmit(wires.scheduleStart) || null,
      schedule_date_rule: $('dateRule').value,
      schedule_anchor_month: dates.anchorMonth(),
      schedule_recurrence: $('recurrence').value,
      schedule_interval: Number($('interval').value) || 1,
      schedule_until_date: dateValue('until') || null,
    },
    'Schedule saved',
  );
});
$('clearSchedule').addEventListener('click', () =>
  saveSettings({ start_date: null, schedule_recurrence: 'once' }, 'Scheduling stopped'),
);

// --- Results ----------------------------------------------------------------------------

function tile(label, value, note = '') {
  return `<div class="col-6 col-lg-3"><div class="card h-100"><div class="card-body"><div class="small-muted">${esc(label)}</div><div class="h3 mb-0">${esc(value)}</div>${note ? `<div class="small-muted">${esc(note)}</div>` : ''}</div></div></div>`;
}

function insightList(title, items, line) {
  if (!items?.length) return '';
  return `<div class="mb-3"><div class="fw-semibold mb-1">${esc(title)}</div><ul class="mb-0">${items.map((item) => `<li>${line(item)}</li>`).join('')}</ul></div>`;
}

function questionStatHtml(question) {
  const rate = question.success_rate;
  const grade =
    question.average_grade === null ? '' : ` · average grade ${esc(question.average_grade)} / 5`;
  const options = (question.options || [])
    .map(
      (option) =>
        `<div class="d-flex gap-2 align-items-center small"><span style="width:45%" class="${option.correct ? 'fw-semibold text-success' : ''}">${option.correct ? '<i class="bi bi-check-lg" aria-label="Right answer"></i> ' : ''}${esc(option.text)}</span><span class="flex-grow-1">${percentBar(option.share)}</span><span class="small-muted" style="width:4.5rem">${esc(option.chosen)} (${esc(option.share ?? 0)}%)</span></div>`,
    )
    .join('');
  return `<div><div class="d-flex justify-content-between gap-2"><span class="fw-semibold">${esc(question.prompt)}</span><span class="small-muted text-nowrap">${rate === null ? 'Not marked yet' : `${esc(rate)}%`}</span></div>
      ${percentBar(rate)}<div class="small-muted mb-1">${esc(question.answered)} of ${esc(question.asked)} answered${grade}</div>${options}</div>`;
}

function personRowHtml(person) {
  const open = person.attempt_id
    ? `<button class="btn btn-sm btn-outline-primary" type="button" data-attempt="${esc(person.attempt_id)}">Open</button>`
    : '';
  const label = {
    not_started: 'Not started',
    in_progress: `Started (${person.pages_saved} pages saved)`,
    submitted: 'Waiting for a grade',
    complete: 'Finished',
  }[person.status];
  return `<tr><td>${esc(person.name)}</td><td>${esc(label)}</td><td class="small-muted">${esc(person.submitted_at ? dateText(person.submitted_at.slice(0, 10)) : '-')}</td>
    <td>${person.score === null ? '-' : `${esc(scoreText(person.score))} ${trophyBadge(person.trophy)}`}</td><td class="text-end">${open}</td></tr>`;
}

function renderResults(data) {
  const summary = data.summary;
  const select = $('resultRound');
  const current = select.value;
  select.innerHTML = `<option value="">All rounds</option>${data.rounds
    .map(
      (round) =>
        `<option value="${esc(round.id)}">${esc(dateText(round.opens_on))} to ${esc(dateText(round.due_on))} (${esc(round.status)})</option>`,
    )
    .join('')}`;
  select.value = current;
  $('hiddenRounds').textContent = data.hidden_rounds
    ? `${data.hidden_rounds} round${data.hidden_rounds === 1 ? '' : 's'} not shown yet: results appear once a round closes with every response graded and at least three finished.`
    : '';
  $('summaryTiles').innerHTML = [
    tile('Average score', scoreText(summary.average), `${summary.responses} completed responses`),
    tile(
      'Pass rate',
      summary.pass_rate === null ? '-' : `${summary.pass_rate}%`,
      data.quiz.pass_mark === null ? 'No pass mark' : `Pass mark ${data.quiz.pass_mark}%`,
    ),
    tile(
      'Completion',
      summary.completion_rate === null ? '-' : `${summary.completion_rate}%`,
      `${summary.participants} assigned across ${summary.rounds} round${summary.rounds === 1 ? '' : 's'}`,
    ),
    tile('Rounds', summary.rounds),
  ].join('');
  const peak = Math.max(1, ...summary.distribution);
  $('histogram').innerHTML = summary.distribution
    .map(
      (count, index) =>
        `<div style="height:${Math.round((100 * count) / peak)}%" title="${index * 10}-${index * 10 + 10}%: ${count}"></div>`,
    )
    .join('');
  $('trophyCounts').innerHTML = Object.entries(summary.trophies)
    .map(([trophy, count]) => `<span class="me-2">${trophyBadge(trophy)} ${esc(count)}</span>`)
    .join('');
  const insights = data.insights;
  $('insights').innerHTML =
    insightList(
      'Hardest questions',
      insights.hardest,
      (i) => `${esc(i.prompt)} <span class="small-muted">${esc(i.success_rate)}%</span>`,
    ) +
      insightList(
        'Best known',
        insights.easiest,
        (i) => `${esc(i.prompt)} <span class="small-muted">${esc(i.success_rate)}%</span>`,
      ) +
      insightList(
        'Most common wrong answers',
        insights.common_wrong_answers,
        (i) =>
          `"${esc(i.answer)}" for ${esc(i.prompt)} <span class="small-muted">${esc(i.chosen)} (${esc(i.share)}%)</span>`,
      ) || '<span class="small-muted">Insights appear once people have answered.</span>';
  $('questionStats').innerHTML = data.questions.length
    ? data.questions.map(questionStatHtml).join('')
    : '<span class="small-muted">No answers to show yet.</span>';
  $('peopleCard').hidden = !data.people;
  if (data.people) $('peopleRows').innerHTML = data.people.map(personRowHtml).join('');
}

async function loadResults() {
  const round = $('resultRound').value;
  const data = await attempt(
    () => apiGet(api(`/results${round ? `?round_id=${encodeURIComponent(round)}` : ''}`)),
    null,
    'Failed to load the results',
  );
  if (data) renderResults(data);
}

$('resultRound').addEventListener('change', loadResults);
$('quizResultsTab').addEventListener('shown.bs.tab', loadResults);

// --- Attempts and grading -----------------------------------------------------------------

function gradeControls(answerId, question) {
  const grades = [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5]
    .map((g) => `<option value="${g}"${question.grade === g ? ' selected' : ''}>${g}</option>`)
    .join('');
  return `<form class="row g-2 align-items-end mt-2" data-grade-answer="${esc(answerId)}">
      <div class="col-4 col-md-3"><label class="form-label small-muted mb-1">Grade</label><select class="form-select form-select-sm" name="grade" required><option value="">Choose</option>${grades}</select></div>
      <div class="col-8 col-md-7"><label class="form-label small-muted mb-1">Feedback</label><input class="form-control form-control-sm" name="feedback" maxlength="4000" value="${esc(question.feedback || '')}"></div>
      <div class="col-12 col-md-2"><button class="btn btn-sm btn-primary w-100" type="submit">Save</button></div>
    </form>`;
}

function attemptQuestionHtml(question) {
  const chosenId = question.answer.option_id;
  if (question.kind === 'choice') {
    const options = question.options
      .map(
        (o) =>
          `<div class="quiz-option${o.correct ? ' right' : o.id === chosenId ? ' wrong' : ''}">${esc(o.text)}${o.id === chosenId ? ' <span class="badge text-bg-light">chosen</span>' : ''}</div>`,
      )
      .join('');
    return `<div class="quiz-question"><div class="fw-semibold mb-2">${question.position + 1}. ${esc(question.prompt)}</div>${options}</div>`;
  }
  const text = question.answer.text;
  return `<div class="quiz-question"><div class="fw-semibold mb-2">${question.position + 1}. ${esc(question.prompt)}</div>
      <div class="whitespace-pre-wrap border rounded p-2">${esc(text) || '<span class="small-muted">No answer</span>'}</div>
      ${question.explanation ? `<div class="small-muted mt-2">Guidance: ${esc(question.explanation)}</div>` : ''}
      ${text.trim() ? gradeControls(question.answer_id, question) : '<div class="small-muted mt-2">A blank answer scores nothing.</div>'}</div>`;
}

async function openAttempt(attemptId) {
  const view = await attempt(
    () => apiGet(api(`/attempts/${encodeURIComponent(attemptId)}`)),
    null,
    'Failed to open the attempt',
  );
  if (!view) return;
  $('attemptTitle').textContent =
    `${view.person ? view.person.name : 'Deleted person'}: ${view.status === 'complete' ? scoreText(view.score) : 'waiting for a grade'}`;
  const trophy = view.trophy ? `${TROPHIES[view.trophy].label} trophy · ` : '';
  $('attemptBody').innerHTML =
    `<div class="small-muted">${trophy}Handed in ${esc(view.submitted_at ? dateText(view.submitted_at.slice(0, 10)) : 'not yet')}</div>${view.questions.map(attemptQuestionHtml).join('')}`;
  $('attemptBody').dataset.attempt = attemptId;
  window.bootstrap?.Modal.getOrCreateInstance($('attemptModal')).show();
}

$('peopleRows').addEventListener('click', (ev) => {
  const button = ev.target.closest('[data-attempt]');
  if (button) openAttempt(button.dataset.attempt);
});

async function grade(form) {
  const graded = await attempt(
    () =>
      apiPut(api(`/answers/${encodeURIComponent(form.dataset.gradeAnswer)}/grade`), {
        grade: Number(form.elements.grade.value),
        feedback: form.elements.feedback.value,
      }),
    'Grade saved',
    'Grade not saved',
  );
  return graded;
}

$('attemptBody').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  if (await grade(ev.target)) {
    await openAttempt($('attemptBody').dataset.attempt);
    await loadResults();
  }
});

function gradingItemHtml(item) {
  return `<div class="quiz-question"><div class="small-muted mb-1">Round opened ${esc(dateText(item.round_opens_on))}</div>
      <div class="fw-semibold mb-2">${esc(item.prompt)}</div>
      ${item.guidance ? `<div class="small-muted mb-2">Guidance: ${esc(item.guidance)}</div>` : ''}
      <div class="whitespace-pre-wrap border rounded p-2">${esc(item.text)}</div>
      ${gradeControls(item.answer_id, {})}</div>`;
}

async function loadGrading() {
  const queue = await attempt(
    () => apiGet(api('/grading')),
    null,
    'Failed to load the grading list',
  );
  if (!queue) return;
  $('gradingCount').hidden = !queue.items.length;
  $('gradingCount').textContent = String(queue.items.length);
  const own = queue.own_answers_waiting
    ? `<div class="small-muted">${queue.own_answers_waiting} of your own answers wait for another quiz manager.</div>`
    : '';
  $('gradingList').innerHTML =
    (queue.items.length
      ? queue.items.map(gradingItemHtml).join('')
      : '<div class="small-muted">Nothing to grade.</div>') + own;
}

$('gradingList').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  if (await grade(ev.target)) await loadGrading();
});

// --- Loading ---------------------------------------------------------------------------

function render(view) {
  quiz = view;
  canManage = view.can_manage;
  draft = structuredClone(view.questions);
  renderHeader();
  renderSettings();
  renderQuestions();
  renderSchedule();
  renderRounds();
  if (canManage) {
    setChosen(view.audience);
    renderAudience();
  }
}

async function load() {
  if (!quizId) {
    toast(status, 'No quiz was named; open one from the Quizzes page.', 'warning');
    return;
  }
  const view = await attempt(() => apiGet(api()), null, 'Failed to load the quiz');
  if (!view) return;
  render(view);
  if (canManage) {
    await Promise.all([loadControlChoices(), loadAssignees(), loadGrading()]);
  }
  const tab = document.querySelector(`#quizTabs [data-tab-hash="${CSS.escape(location.hash)}"]`);
  if (tab) window.bootstrap?.Tab.getOrCreateInstance(tab).show();
}

await load();
