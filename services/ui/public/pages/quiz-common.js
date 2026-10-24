// Pieces the quiz pages share: the Keen Mitigator with its trophy, scores,
// round and sitting states, and a person's level, streak and badges.
import { esc, isoDateToDisplay } from '/app.js';

export const TROPHIES = {
  gold: { label: 'Gold', body: '#f5c542', shade: '#c98b14', shine: '#fff4c8' },
  silver: { label: 'Silver', body: '#d6dce4', shade: '#8f9bab', shine: '#ffffff' },
  bronze: { label: 'Bronze', body: '#d98f55', shade: '#9b5628', shine: '#f9dbc2' },
};

// The Mitigator's outline and purple, from keen-mitigator.png.
const INK = '#1c0144';
const PURPLE = '#673bd1';

// The sheet on the Mitigator's clipboard in keen-mitigator.png's own 1254-unit
// square, less the claws over it: painted blank, it hides the ticks and leaves
// the hand in front.
const SHEET =
  'M791 660L783 666L714 857L714 861L722 869L725 875L725 886L722 890L718 892L705 893L695 934L694 944L706 953L711 962L721 971L727 981L731 994L730 1009L723 1017L720 1018L713 1017L697 1008L689 1011L686 1053L690 1059L696 1062L949 1125L961 1125L967 1120L1089 758L1089 751L1083 745L1030 729L1022 737L1018 739L1008 740L931 716L857 695L851 692L845 686L843 674L800 661Z';

const BADGE_ICONS = {
  first_quiz: 'bi-flag',
  streak_3: 'bi-fire',
  quizzes_5: 'bi-stars',
  quizzes_10: 'bi-gem',
  early_bird: 'bi-sunrise',
  gold: 'bi-trophy',
  perfect: 'bi-bullseye',
};

const STATES = {
  in_progress: ['Carry on', 'text-bg-primary'],
  available: ['Ready to start', 'text-bg-success'],
  upcoming: ['Opens soon', 'text-bg-light'],
  awaiting_grading: ['Waiting for a grade', 'text-bg-info'],
  complete: ['Finished', 'text-bg-secondary'],
  missed: ['Missed', 'text-bg-warning'],
};

// The points of a star with `points` tips round (x, y).
function starPoints(x, y, outer, inner, points) {
  return Array.from({ length: points * 2 }, (_, i) => {
    const r = i % 2 ? inner : outer;
    const a = (Math.PI / points) * i - Math.PI / 2;
    return `${Math.round(x + r * Math.cos(a))},${Math.round(y + r * Math.sin(a))}`;
  }).join(' ');
}

// A cup handle: an outline, then the trophy's colour inside it.
function handle(path, colour) {
  return `<path d="${path}" fill="none" stroke="${INK}" stroke-width="30" stroke-linecap="round"/>
    <path d="${path}" fill="none" stroke="${colour}" stroke-width="14" stroke-linecap="round"/>`;
}

// A trophy on the Mitigator's blanked clipboard, tilted with it.
function trophySvg(trophy) {
  const t = TROPHIES[trophy];
  const cup = 'M-80 -128H80C80 -40 52 -8 0 -6C-52 -8 -80 -40 -80 -128Z';
  return `<svg class="quiz-trophy" viewBox="0 0 1254 1254" aria-hidden="true">
    <path d="${SHEET}" fill="#fdfdfd"/>
    <g transform="translate(893 900) rotate(17)">
      <g class="quiz-trophy-cup">
        ${handle('M-72 -100C-136 -110 -130 -28 -42 -34', t.body)}
        ${handle('M72 -100C136 -110 130 -28 42 -34', t.body)}
        <path d="M-18 -12H18L24 40H-24Z" fill="${t.shade}" stroke="${INK}" stroke-width="8" stroke-linejoin="round"/>
        <path d="M-62 52H62L76 102H-76Z" fill="${t.body}" stroke="${INK}" stroke-width="8" stroke-linejoin="round"/>
        <rect x="-36" y="64" width="72" height="26" rx="4" fill="${t.shade}"/>
        <rect x="-38" y="34" width="76" height="20" rx="8" fill="${t.body}" stroke="${INK}" stroke-width="8"/>
        <rect x="-88" y="100" width="176" height="28" rx="10" fill="${PURPLE}" stroke="${INK}" stroke-width="8"/>
        <path d="${cup}" fill="${t.body}"/>
        <path d="M40 -128H80C80 -40 52 -8 0 -6C28 -16 44 -52 40 -128Z" fill="${t.shade}"/>
        <path d="M-58 -112C-58 -66 -46 -40 -22 -26" fill="none" stroke="${t.shine}" stroke-width="12" stroke-linecap="round"/>
        <path d="${cup}" fill="none" stroke="${INK}" stroke-width="8" stroke-linejoin="round"/>
        <rect x="-92" y="-146" width="184" height="26" rx="11" fill="${t.body}" stroke="${INK}" stroke-width="8"/>
        <polygon points="${starPoints(0, -74, 30, 13, 5)}" fill="${t.shine}" stroke="${t.shade}" stroke-width="4" stroke-linejoin="round"/>
      </g>
      <g class="quiz-trophy-sparkles" fill="${t.shine}" stroke="${t.shade}" stroke-width="4" stroke-linejoin="round">
        <polygon points="${starPoints(-120, -150, 22, 6, 4)}"/>
        <polygon points="${starPoints(118, -152, 16, 5, 4)}"/>
        <polygon points="${starPoints(112, 50, 18, 5, 4)}"/>
      </g>
    </g>
  </svg>`;
}

// The Keen Mitigator, with `trophy` on its clipboard when the score won one.
export function mascotHtml(trophy) {
  const label = TROPHIES[trophy]
    ? `The Keen Mitigator with a ${TROPHIES[trophy].label.toLowerCase()} trophy on its clipboard`
    : 'The Keen Mitigator';
  return `<div class="quiz-mascot" role="img" aria-label="${esc(label)}">
    <img src="/keen-mitigator.png" alt="">${TROPHIES[trophy] ? trophySvg(trophy) : ''}
  </div>`;
}

export function trophyBadge(trophy) {
  const t = TROPHIES[trophy];
  if (!t) return '';
  return `<span class="badge rounded-pill" style="background:${t.body};color:#212529;"><i class="bi bi-trophy-fill me-1" aria-hidden="true"></i>${t.label}</span>`;
}

export function scoreText(score) {
  return score === null || score === undefined ? '-' : `${Number(score).toFixed(0)}%`;
}

// A score with its trophy, or "-" when there is none to show.
export function resultHtml(result) {
  if (!result) return '<span class="small-muted">-</span>';
  return `<span class="fw-semibold">${esc(scoreText(result.score))}</span> ${trophyBadge(result.trophy)}`;
}

export function stateBadge(state) {
  const [label, style] = STATES[state] || [state, 'text-bg-light'];
  return `<span class="badge ${style}">${esc(label)}</span>`;
}

export function roundStatusBadge(status) {
  return `<span class="badge ${status === 'open' ? 'text-bg-success' : 'text-bg-secondary'}">${esc(status)}</span>`;
}

export function dateText(iso) {
  return iso ? isoDateToDisplay(iso) : '-';
}

const people = (n) => `${n} ${n === 1 ? 'person' : 'people'}`;

// Whether people can take a quiz now, and if not, when they can, from the
// availability the API reports. Quiz managers are told what to do next.
export function availabilityText(availability, { manager = false } = {}) {
  const a = availability || {};
  switch (a.state) {
    case 'open':
      return a.people
        ? `Open: ${people(a.people)} can take this quiz until ${dateText(a.until)}.`
        : `Open until ${dateText(a.until)}. No one assigned can sign in to take it yet.`;
    case 'waiting':
      return `No one can take this quiz until its next round opens on ${dateText(a.opens_on)}.`;
    case 'no_round':
      return manager
        ? 'No one can take this quiz until you start a round.'
        : 'No one can take this quiz until a round starts.';
    case 'archived':
      return 'Archived: no one can take this quiz.';
    case 'draft':
      if (a.on_activation === 'scheduled') {
        return `Draft: no one can take this quiz yet. Activating it schedules its first round for ${dateText(a.opens_on)}.`;
      }
      if (a.on_activation === 'nothing') {
        return 'Draft: no one can take this quiz yet. Its schedule has no rounds left, so start a round once it is active.';
      }
      return `Draft: no one can take this quiz yet. Activating it opens it straight away, until ${dateText(a.until)}.`;
    default:
      return '';
  }
}

// The same, in a few words for a list.
export function availabilityShort(availability) {
  const a = availability || {};
  if (a.state === 'open') return `Open until ${dateText(a.until)}`;
  if (a.state === 'waiting') return `Opens ${dateText(a.opens_on)}`;
  if (a.state === 'no_round') return 'No round open';
  return '';
}

// A horizontal bar for a percentage.
export function percentBar(value) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return `<div class="quiz-bar" role="presentation"><span style="width:${pct}%"></span></div>`;
}

// A person's level, points towards the next level, streak and badges.
export function progressHtml(progress) {
  const next = progress.next;
  const span = next ? next - progress.floor : 1;
  const done = next ? progress.points - progress.floor : 1;
  const towards = next ? `${next - progress.points} points to the next level` : 'The highest level';
  return `<div class="d-flex flex-wrap align-items-center gap-3">
      <div class="quiz-level" title="Level ${esc(progress.level)}">${esc(progress.level)}</div>
      <div class="flex-grow-1" style="min-width:12rem;">
        <div class="fw-semibold">${esc(progress.name)}</div>
        ${percentBar((100 * done) / span)}
        <div class="small-muted mt-1">${esc(progress.points)} points · ${esc(towards)}</div>
      </div>
      <div class="text-center"><div class="h4 mb-0"><i class="bi bi-fire text-danger" aria-hidden="true"></i> ${esc(progress.streak)}</div><div class="small-muted">streak (best ${esc(progress.best_streak)})</div></div>
      <div class="text-center"><div class="h4 mb-0">${esc(progress.finished)}</div><div class="small-muted">finished</div></div>
    </div>
    <div class="d-flex flex-wrap gap-2 mt-3">${progress.badges
      .map(
        (badge) =>
          `<span class="quiz-badge${badge.earned ? ' earned' : ''}" title="${esc(badge.description)}"><i class="bi ${BADGE_ICONS[badge.key] || 'bi-patch-check'}" aria-hidden="true"></i>${esc(badge.label)}${badge.earned ? '' : '<span class="visually-hidden"> (not earned yet)</span>'}</span>`,
      )
      .join('')}</div>`;
}
