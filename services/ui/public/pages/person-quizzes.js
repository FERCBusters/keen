import { isoDateToDisplay, apiGet, esc, fmtTs, withFramework, getCurrentFramework } from '/app.js';

export function personQuizzes(host, id) {
  let offset = 0,
    assignmentsOffset = 0,
    generation = 0;
  const href = (quiz) =>
    withFramework(
      `/quiz.html?id=${encodeURIComponent(quiz.id || quiz.quiz_id)}`,
      getCurrentFramework(),
    );
  async function load() {
    const request = ++generation;
    host.hidden = false;
    host.innerHTML =
      '<h2 class="h5">Quizzes</h2><p role="status">Loading quiz assignments and results…</p>';
    try {
      const data = await apiGet(
        `/api/v1/isms/people/${encodeURIComponent(id)}/quizzes?offset=${offset}&assignments_offset=${assignmentsOffset}`,
      );
      if (request !== generation) return;
      const status = {
        not_started: 'Not started',
        in_progress: 'In progress',
        submitted: 'Awaiting grading',
        complete: 'Completed',
      };
      host.innerHTML = `<h2 class="h5">Quizzes</h2><p class="small-muted">Named assignments and results are visible to quiz managers. ${data.eligible ? '' : 'This person has no active linked account to sit quizzes.'}</p><h3 class="h6">Current assignments (${Number(data.assignments_total)})</h3>${data.assignments.length ? `<ul>${data.assignments.map((q) => `<li><a href="${esc(href(q))}">${esc(q.title)}</a></li>`).join('')}</ul>` : '<p class="small-muted">No current assignments.</p>'}<div data-assignment-pages class="d-flex gap-2 mb-4"></div><h3 class="h6">Rounds and results (${Number(data.total)})</h3><div class="table-responsive"><table class="table table-sm"><thead><tr><th>Quiz</th><th>Opens / due</th><th>Status</th><th>Completed</th><th>Result</th></tr></thead><tbody>${data.items.map((q) => `<tr><td><a href="${esc(href(q))}">${esc(q.title)}</a></td><td>${esc(isoDateToDisplay(q.opens_on))} / ${esc(isoDateToDisplay(q.due_on))}</td><td>${esc(status[q.status] || q.status)}<div class="small-muted">${esc(q.round_status)}</div></td><td>${q.completed_at ? esc(fmtTs(q.completed_at)) : '—'}</td><td>${q.score == null ? '—' : `${Number(q.score)}% · ${q.passed == null ? 'No pass mark' : q.passed ? 'Passed' : 'Below pass mark'}`}</td></tr>`).join('') || '<tr><td colspan="5" class="small-muted">No assigned rounds or completed attempts.</td></tr>'}</tbody></table></div><div data-result-pages class="d-flex gap-2"></div>`;
      for (const [selector, current, next, set] of [
        [
          '[data-assignment-pages]',
          assignmentsOffset,
          data.assignments_next_offset,
          (n) => {
            assignmentsOffset = n;
          },
        ],
        [
          '[data-result-pages]',
          offset,
          data.next_offset,
          (n) => {
            offset = n;
          },
        ],
      ]) {
        for (const [label, n] of [
          ['Previous', current ? Math.max(0, current - 50) : null],
          ['Next', next],
        ]) {
          if (n == null) continue;
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'btn btn-sm btn-outline-secondary';
          button.textContent = label;
          button.onclick = () => {
            set(n);
            load();
          };
          host.querySelector(selector).append(button);
        }
      }
    } catch {
      if (request !== generation) return;
      host.innerHTML =
        '<h2 class="h5">Quizzes</h2><p role="status">Quiz assignments and results could not be loaded.</p><button type="button" class="btn btn-sm btn-outline-primary">Retry</button>';
      host.querySelector('button').onclick = load;
    }
  }
  return { load };
}
