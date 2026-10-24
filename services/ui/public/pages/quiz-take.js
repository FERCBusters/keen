// Sitting a quiz round a page at a time, then the result: the score with the
// Mitigator's trophy, the points earned and, when the quiz shows them, the
// right answers. Answers on a page are saved as the person moves on from it,
// so leaving part-way loses only the page on screen.
import { initNavbar, apiGet, apiPost, apiPut, esc, toast } from '/app.js';
import { dateText, mascotHtml, scoreText, TROPHIES } from '/pages/quiz-common.js';

const $ = (id) => document.getElementById(id);
await initNavbar();
const params = new URLSearchParams(location.search);
const status = $('status');
let attempt = null;
let page = 0;
// Answers by position, kept across pages until each page is saved.
const answers = new Map();
let started = Date.now();

function pageCount() {
  return attempt.page_count;
}

function positions(onPage = page) {
  const first = onPage * attempt.page_size;
  return attempt.questions.slice(first, first + attempt.page_size);
}

function answered(question) {
  const answer = answers.get(question.position) || {};
  return question.kind === 'choice' ? Boolean(answer.option_id) : Boolean(answer.text?.trim());
}

function renderProgress() {
  const done = attempt.questions.filter(answered).length;
  const total = attempt.questions.length;
  $('pageLabel').textContent = `Page ${page + 1} of ${pageCount()}`;
  $('answeredLabel').textContent = `${done} of ${total} answered`;
  const pct = Math.round((100 * done) / total);
  $('answeredBar').setAttribute('aria-valuenow', String(pct));
  $('answeredBar').firstElementChild.style.width = `${pct}%`;
  const onPage = new Set(positions().map((q) => q.position));
  $('dots').innerHTML = attempt.questions
    .map(
      (q) =>
        `<span class="quiz-dot${answered(q) ? ' answered' : ''}${onPage.has(q.position) ? ' current' : ''}"></span>`,
    )
    .join('');
}

function questionHtml(question) {
  const answer = answers.get(question.position) || {};
  const name = `q${question.position}`;
  const body =
    question.kind === 'choice'
      ? question.options
          .map(
            (option) => `<label class="quiz-option">
          <input class="form-check-input mt-1" type="radio" name="${name}" value="${esc(option.id)}" data-position="${question.position}"${answer.option_id === option.id ? ' checked' : ''}>
          <span>${esc(option.text)}</span></label>`,
          )
          .join('')
      : `<textarea class="form-control" rows="4" maxlength="8000" name="${name}" data-position="${question.position}" aria-label="Your answer">${esc(answer.text || '')}</textarea>`;
  return `<fieldset class="quiz-question">
      <legend class="h6 mb-3"><span class="small-muted d-block mb-1">Question ${question.position + 1} of ${attempt.questions.length}</span>${esc(question.prompt)}</legend>
      ${body}
    </fieldset>`;
}

function renderPage() {
  $('questions').innerHTML = positions().map(questionHtml).join('');
  const last = page === pageCount() - 1;
  $('back').disabled = page === 0;
  $('next').hidden = last;
  $('handIn').hidden = !last;
  renderProgress();
}

function pageAnswers(onPage = page) {
  return positions(onPage).map((question) => {
    const answer = answers.get(question.position) || {};
    return question.kind === 'choice'
      ? { position: question.position, option_id: answer.option_id || null }
      : { position: question.position, text: answer.text || '' };
  });
}

function tick() {
  const seconds = Math.floor((Date.now() - started) / 1000);
  $('timer').textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function startSitting() {
  for (const question of attempt.questions) answers.set(question.position, question.answer);
  // Carry on from the first page not yet saved.
  page = Math.min(attempt.pages_saved, pageCount() - 1);
  $('sitting').hidden = false;
  $('timerWrap').hidden = false;
  started = Date.now();
  window.setInterval(tick, 1000);
  renderPage();
}

function headline(view) {
  if (view.status === 'submitted') return 'Waiting for a grade';
  if (!view.score_visible) return 'Handed in';
  if (view.trophy) return `${TROPHIES[view.trophy].label} trophy!`;
  if (view.passed === false) return 'Not this time';
  return 'Finished';
}

function scoreLine(view) {
  if (!view.score_visible) return '';
  const pass =
    view.pass_mark === null
      ? ''
      : ` · pass mark ${esc(view.pass_mark)}% · ${view.passed ? 'passed' : 'not passed'}`;
  return `<div class="quiz-score">${esc(scoreText(view.score))}</div><div class="small-muted mt-1">${esc(view.question_count)} questions${pass}</div>`;
}

// Why a handed-in attempt shows no review of its answers.
const REVIEW_NOTES = {
  at_close: 'Your answers and the right ones appear here once the round closes.',
  never: 'This quiz keeps its answers with its managers.',
  superseded: "The quiz's next round has opened, so this round's answers are put away.",
  other_round: "Your answers come back here once you hand in this quiz's other open round.",
};

function resultNote(view) {
  if (view.status === 'submitted') {
    return 'An assessor grades your written answers; your score appears here once they have.';
  }
  if (!view.score_visible)
    return 'Thanks for taking part. This quiz keeps scores with its managers.';
  return REVIEW_NOTES[view.review] || '';
}

function reviewHtml(question) {
  const chosen = question.answer.option_id;
  const body =
    question.kind === 'choice'
      ? question.options
          .map((option) => {
            const mark = option.correct ? ' right' : option.id === chosen ? ' wrong' : '';
            const yours =
              option.id === chosen ? ' <span class="badge text-bg-light">Your answer</span>' : '';
            return `<div class="quiz-option${mark}"><span>${esc(option.text)}${yours}</span></div>`;
          })
          .join('')
      : `<div class="whitespace-pre-wrap border rounded p-2">${esc(question.answer.text) || '<span class="small-muted">No answer</span>'}</div>
         ${question.pending ? '<div class="small-muted mt-2">Waiting for a grade.</div>' : ''}
         ${question.grade !== null && question.grade !== undefined ? `<div class="mt-2"><span class="badge text-bg-primary">Grade ${esc(question.grade)} / 5</span> ${esc(question.feedback)}</div>` : ''}`;
  const explanation = question.explanation
    ? `<div class="small-muted mt-2"><i class="bi bi-lightbulb me-1" aria-hidden="true"></i>${esc(question.explanation)}</div>`
    : '';
  return `<div class="quiz-question"><div class="fw-semibold mb-2">${esc(question.position + 1)}. ${esc(question.prompt)}</div>${body}${explanation}</div>`;
}

function showResult(view) {
  $('sitting').hidden = true;
  $('timerWrap').hidden = true;
  $('result').hidden = false;
  $('mascot').innerHTML = mascotHtml(view.score_visible ? view.trophy : null);
  $('resultHeading').textContent = headline(view);
  $('resultScore').innerHTML = scoreLine(view);
  $('resultNote').textContent = resultNote(view);
  $('reward').innerHTML = view.reward
    ? `<span class="badge rounded-pill text-bg-warning fs-6"><i class="bi bi-star-fill me-1" aria-hidden="true"></i>+${esc(view.reward.points)} points</span>`
    : '';
  $('reviewCard').hidden = view.review !== 'shown';
  if (view.review === 'shown') {
    $('reviewNote').textContent = 'Right answers are marked in green.';
    $('review').innerHTML = view.questions.map(reviewHtml).join('');
  }
}

function render(view) {
  attempt = view;
  document.title = `Keen - ${view.title}`;
  $('quizTitle').textContent = view.title;
  $('quizMeta').hidden = false;
  $('quizTitle').textContent = view.title;
  $('quizMeta').textContent =
    `Due ${dateText(view.due_on)}${view.description ? ` · ${view.description}` : ''}`;
  if (view.status !== 'in_progress') {
    showResult(view);
  } else if (view.round_status !== 'open') {
    toast(status, 'This round closed before you handed in your answers.', 'warning');
  } else {
    startSitting();
  }
}

async function save(onPage) {
  return apiPut(`/api/v1/me/quizzes/attempts/${encodeURIComponent(attempt.id)}/pages/${onPage}`, {
    answers: pageAnswers(onPage),
  });
}

$('questions').addEventListener('input', (ev) => {
  const position = Number(ev.target.dataset.position);
  if (ev.target.type === 'radio') answers.set(position, { option_id: ev.target.value });
  else answers.set(position, { text: ev.target.value });
  renderProgress();
});

$('pageForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  $('next').disabled = true;
  try {
    await save(page);
    page += 1;
    renderPage();
    window.scrollTo?.(0, 0);
  } catch (error) {
    toast(status, `Your answers were not saved: ${String(error)}`, 'danger');
  }
  $('next').disabled = false;
});

$('back').addEventListener('click', () => {
  page -= 1;
  renderPage();
});

$('handIn').addEventListener('click', async () => {
  const unanswered = attempt.questions.filter((q) => !answered(q)).length;
  if (
    unanswered &&
    !confirm(
      `${unanswered} question${unanswered === 1 ? ' is' : 's are'} unanswered. Hand in anyway?`,
    )
  )
    return;
  $('handIn').disabled = true;
  try {
    const view = await apiPost(
      `/api/v1/me/quizzes/attempts/${encodeURIComponent(attempt.id)}/submit`,
      { page, answers: pageAnswers() },
    );
    render(view);
  } catch (error) {
    toast(status, `Your answers were not handed in: ${String(error)}`, 'danger');
    $('handIn').disabled = false;
  }
});

async function load() {
  const attemptId = params.get('attempt');
  const roundId = params.get('round');
  try {
    if (attemptId) {
      render(await apiGet(`/api/v1/me/quizzes/attempts/${encodeURIComponent(attemptId)}`));
    } else if (roundId) {
      render(await apiPost(`/api/v1/me/quizzes/rounds/${encodeURIComponent(roundId)}/attempt`, {}));
    } else {
      toast(status, 'Open a quiz from My quizzes on your account page.', 'warning');
    }
  } catch (error) {
    toast(status, `This quiz cannot be opened: ${String(error)}`, 'danger');
  }
}

await load();
