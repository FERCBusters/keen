// The My quizzes tab on the account page: the rounds assigned to the signed-in
// person, what to do next, their results and their level, streak and badges.
import { errorText, apiGet, esc } from '/app.js';
import { dateText, progressHtml, resultHtml, stateBadge } from '/pages/quiz-common.js';

const sittingHref = (item) =>
  item.attempt && item.state !== 'in_progress'
    ? `/quiz-take.html?attempt=${encodeURIComponent(item.attempt.id)}`
    : `/quiz-take.html?round=${encodeURIComponent(item.round_id)}`;

function action(item) {
  if (item.state === 'available') {
    return `<a class="btn btn-sm btn-primary" href="${esc(sittingHref(item))}">Start</a>`;
  }
  if (item.state === 'in_progress') {
    return `<a class="btn btn-sm btn-primary" href="${esc(sittingHref(item))}">Carry on</a>`;
  }
  if (item.attempt) {
    return `<a class="btn btn-sm btn-outline-primary" href="${esc(sittingHref(item))}">Result</a>`;
  }
  return '';
}

function when(item) {
  if (item.state === 'upcoming') return `Opens ${dateText(item.opens_on)}`;
  if (item.days_left !== null) {
    const days =
      item.days_left === 0 ? 'today' : `in ${item.days_left} day${item.days_left === 1 ? '' : 's'}`;
    return `Due ${dateText(item.due_on)} (${days})`;
  }
  return `Due ${dateText(item.due_on)}`;
}

function progressNote(item) {
  const attempt = item.attempt;
  if (item.state !== 'in_progress' || !attempt) return '';
  return `<div class="small-muted">${esc(attempt.pages_saved)} of ${esc(attempt.page_count)} pages saved</div>`;
}

function rowHtml(item) {
  return `<tr>
    <td class="wrap"><div class="fw-semibold">${esc(item.title)}</div>${item.description ? `<div class="small-muted">${esc(item.description)}</div>` : ''}</td>
    <td>${stateBadge(item.state)}${progressNote(item)}</td>
    <td class="small-muted">${esc(when(item))}</td>
    <td>${resultHtml(item.result)}</td>
    <td class="text-end">${action(item)}</td>
  </tr>`;
}

export function mountMyQuizzes(root) {
  const el = (id) => root.querySelector(`#${id}`);
  let loaded = false;
  async function load() {
    el('myQuizzesStatus').style.display = 'none';
    el('myQuizzesRows').innerHTML = '<tr><td colspan="5" class="p-4 small-muted">Loading</td></tr>';
    try {
      const data = await apiGet('/api/v1/me/quizzes');
      el('myQuizzesProgress').innerHTML = progressHtml(data.progress);
      el('myQuizzesUnlinked').hidden = data.linked;
      el('myQuizzesRows').innerHTML = data.items.length
        ? data.items.map(rowHtml).join('')
        : '<tr><td colspan="5" class="p-4 small-muted">No quizzes are assigned to you yet.</td></tr>';
      el('myQuizzesUpcoming').innerHTML = data.upcoming.length
        ? data.upcoming
            .map(
              (entry) =>
                `<li>${esc(entry.title)} <span class="small-muted">opens ${esc(dateText(entry.date))}</span></li>`,
            )
            .join('')
        : '<li class="small-muted">Nothing scheduled in the coming weeks.</li>';
      loaded = true;
    } catch (error) {
      el('myQuizzesStatus').className = 'alert alert-danger';
      el('myQuizzesStatus').style.display = '';
      el('myQuizzesStatus').textContent = `Failed to load your quizzes: ${errorText(error)}`;
    }
  }
  el('myQuizzesRefresh').addEventListener('click', load);
  return { load, loadOnce: () => (loaded ? undefined : load()) };
}
