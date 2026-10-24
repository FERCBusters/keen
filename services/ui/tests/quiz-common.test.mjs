// The pieces the quiz pages share: the Mitigator's trophy, scores, states and
// a person's progress.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

async function common(t) {
  const ui = await loadUi('pages/quiz-common.js', {
    html: '<div id="out"></div>',
    vendor: { isoDateToDisplay: (iso) => `on ${iso}` },
  });
  t.after(ui.close);
  const out = ui.document.getElementById('out');
  const render = (html) => {
    out.innerHTML = html;
    return out;
  };
  return { api: ui.api, render };
}

test('the Mitigator holds up the trophy a score won, and its checklist otherwise', async (t) => {
  const { api, render } = await common(t);
  const gold = render(api.mascotHtml('gold'));
  const figure = gold.querySelector('.quiz-mascot');
  assert.equal(
    figure.getAttribute('aria-label'),
    'The Keen Mitigator with a gold trophy on its clipboard',
  );
  assert.equal(figure.querySelector('img').getAttribute('src'), '/keen-mitigator.png');
  const trophy = figure.querySelector('svg.quiz-trophy');
  assert.equal(trophy.getAttribute('viewBox'), '0 0 1254 1254');
  // The blank sheet goes down first, over the ticks, then the trophy on it.
  const sheet = trophy.firstElementChild;
  assert.deepEqual([sheet.tagName, sheet.getAttribute('fill')], ['path', '#fdfdfd']);
  assert.match(sheet.getAttribute('d'), /^M791 660L.*Z$/);
  assert.ok(trophy.querySelector('.quiz-trophy-cup').innerHTML.includes(api.TROPHIES.gold.body));
  assert.equal(trophy.querySelectorAll('.quiz-trophy-sparkles polygon').length, 3);
  assert.ok(render(api.mascotHtml('bronze')).innerHTML.includes(api.TROPHIES.bronze.shade));
  const plain = render(api.mascotHtml(null)).querySelector('.quiz-mascot');
  assert.equal(plain.getAttribute('aria-label'), 'The Keen Mitigator');
  assert.equal(plain.querySelector('svg'), null);
});

test('scores, trophies, states and dates read as text', async (t) => {
  const { api, render } = await common(t);
  assert.equal(api.scoreText(null), '-');
  assert.equal(api.scoreText(undefined), '-');
  assert.equal(api.scoreText(86.666), '87%');
  assert.equal(api.trophyBadge(null), '');
  const badge = render(api.trophyBadge('silver'));
  assert.equal(badge.textContent, 'Silver');
  assert.ok(badge.querySelector('.bi-trophy-fill'));
  assert.equal(render(api.resultHtml(null)).textContent, '-');
  assert.equal(
    render(api.resultHtml({ score: 100, trophy: 'gold' })).textContent.trim(),
    '100% Gold',
  );
  assert.equal(render(api.stateBadge('awaiting_grading')).textContent, 'Waiting for a grade');
  assert.equal(render(api.stateBadge('mystery')).textContent, 'mystery');
  assert.equal(
    render(api.roundStatusBadge('open')).querySelector('.text-bg-success').textContent,
    'open',
  );
  assert.equal(
    render(api.roundStatusBadge('closed')).querySelector('.text-bg-secondary').textContent,
    'closed',
  );
  assert.equal(api.dateText('2026-05-01'), 'on 2026-05-01');
  assert.equal(api.dateText(''), '-');
  assert.equal(render(api.percentBar(140)).querySelector('span').style.width, '100%');
  assert.equal(render(api.percentBar(null)).querySelector('span').style.width, '0%');
});

test('availability says who can take a quiz now, or when they next can', async (t) => {
  const { api } = await common(t);
  const text = (availability, manager = false) => api.availabilityText(availability, { manager });
  assert.equal(
    text({ state: 'open', until: '2026-11-07', people: 3 }),
    'Open: 3 people can take this quiz until on 2026-11-07.',
  );
  assert.equal(
    text({ state: 'open', until: '2026-11-07', people: 0 }),
    'Open until on 2026-11-07. No one assigned can sign in to take it yet.',
  );
  assert.equal(
    text({ state: 'waiting', opens_on: '2026-11-02' }),
    'No one can take this quiz until its next round opens on on 2026-11-02.',
  );
  assert.equal(
    text({ state: 'no_round' }, true),
    'No one can take this quiz until you start a round.',
  );
  assert.equal(text({ state: 'no_round' }), 'No one can take this quiz until a round starts.');
  assert.equal(text({ state: 'archived' }), 'Archived: no one can take this quiz.');
  assert.ok(
    text({ state: 'draft', on_activation: 'scheduled', opens_on: '2026-12-01' }).endsWith(
      'Activating it schedules its first round for on 2026-12-01.',
    ),
  );
  assert.ok(text({ state: 'draft', on_activation: 'nothing' }).includes('no rounds left'));
  assert.ok(
    text({ state: 'draft', on_activation: 'now', until: '2026-11-07' }).endsWith(
      'Activating it opens it straight away, until on 2026-11-07.',
    ),
  );
  assert.equal(text(null), '');
  assert.equal(
    api.availabilityShort({ state: 'open', until: '2026-11-07' }),
    'Open until on 2026-11-07',
  );
  assert.equal(
    api.availabilityShort({ state: 'waiting', opens_on: '2026-11-02' }),
    'Opens on 2026-11-02',
  );
  assert.equal(api.availabilityShort({ state: 'no_round' }), 'No round open');
  assert.equal(api.availabilityShort({ state: 'draft' }), '');
  assert.equal(api.availabilityShort(undefined), '');
});

const BADGES = [
  { key: 'first_quiz', label: 'First steps', description: 'Finish your first quiz.', earned: true },
  { key: 'odd', label: 'Odd one', description: 'Unknown badge.', earned: false },
];

test('progress shows the level, the way to the next one, the streak and badges', async (t) => {
  const { api, render } = await common(t);
  const out = render(
    api.progressHtml({
      points: 425,
      level: 3,
      name: 'Defender',
      floor: 400,
      next: 800,
      finished: 3,
      streak: 2,
      best_streak: 3,
      badges: BADGES,
    }),
  );
  assert.equal(out.querySelector('.quiz-level').textContent, '3');
  assert.ok(out.textContent.includes('425 points · 375 points to the next level'));
  assert.equal(out.querySelector('.quiz-bar span').style.width, '6.25%');
  assert.ok(out.textContent.includes('streak (best 3)'));
  const badges = [...out.querySelectorAll('.quiz-badge')];
  assert.deepEqual(
    badges.map((b) => [b.classList.contains('earned'), b.querySelector('i').className]),
    [
      [true, 'bi bi-flag'],
      [false, 'bi bi-patch-check'],
    ],
  );
  assert.ok(badges[1].textContent.includes('(not earned yet)'));
  const top = render(
    api.progressHtml({
      points: 3200,
      level: 7,
      name: 'Legend',
      floor: 3000,
      next: null,
      finished: 30,
      streak: 9,
      best_streak: 12,
      badges: [],
    }),
  );
  assert.ok(top.textContent.includes('The highest level'));
  assert.equal(top.querySelector('.quiz-bar span').style.width, '100%');
});
