import test from 'node:test';
import assert from 'node:assert/strict';
import {MS_PER_DAY, histStepMs, histNextFinerInterval, histBucketToUtcMs, isoAddDays, diffDaysInclusive, isoDateFromUtcMs, utcMsFromIsoDate} from '../public/pages/visualisation/dates.js';
import {truncateMiddleText, wrapLabelLines} from '../public/pages/visualisation/labels.js';
import {loadUi} from './support/load-ui.mjs';

test('UTC ranges cross leap days and year boundaries without local timezone drift', () => {
  assert.equal(isoAddDays('2024-02-28', 1), '2024-02-29');
  assert.equal(isoAddDays('2026-12-31', 1), '2027-01-01');
  assert.equal(diffDaysInclusive('2026-10-03', '2026-10-05'), 3);
  assert.equal(isoDateFromUtcMs(utcMsFromIsoDate('2026-10-04') + MS_PER_DAY), '2026-10-05');
  assert.equal(isoAddDays('invalid', 1), null);
  assert.equal(diffDaysInclusive('', '2026-10-05'), null);
});

test('histogram drill-down preserves UTC bucket boundaries', () => {
  for (const [interval, step, next] of [['day', 86400000, 'hour'], ['hour', 3600000, 'minute'], ['minute', 60000, 'second'], ['second', 1000, 'second']]) {
    assert.equal(histStepMs(interval), step);
    assert.equal(histNextFinerInterval(interval), next);
  }
  assert.equal(histBucketToUtcMs('2026-10-09', 'day'), Date.UTC(2026, 9, 9));
  assert.equal(histBucketToUtcMs('2026-10-09T01:02:03Z', 'second'), Date.UTC(2026, 9, 9, 1, 2, 3));
  assert.ok(Number.isNaN(histBucketToUtcMs('', 'day')));
});

test('graph labels retain useful ends and bound wrapped line counts', () => {
  assert.equal(truncateMiddleText('abcdefghijk', 7), 'abc…ijk');
  assert.equal(truncateMiddleText('abc', 7), 'abc');
  assert.deepEqual(wrapLabelLines('  alpha   beta ', 10, 3), ['alpha beta']);
  assert.deepEqual(wrapLabelLines('', 10), []);
  const lines = wrapLabelLines('averylongunbrokentoken followed by more words', 8, 2);
  assert.equal(lines.length, 2);
  assert.ok(lines.at(-1).endsWith('…'));
});

test('embedded markup honors allowed modes, escapes titles, and mounts only once', async t => {
  const ui = await loadUi('pages/visualisation/embedded-markup.js', {html: '<div data-keen-visualisation-mount data-viz-modes="heatmap,invalid,graph" data-show-distribution="0"></div>'});
  t.after(ui.close);
  const mount = ui.document.querySelector('[data-keen-visualisation-mount]');
  mount.dataset.vizTitle = '<img src=x onerror=alert(1)>';
  ui.api.ensureEmbeddedVisualisationMarkup();
  assert.deepEqual([...mount.querySelectorAll('#vizSelect option')].map(x => x.value), ['heatmap', 'graph']);
  assert.equal(mount.querySelector('img'), null);
  assert.equal(mount.querySelector('h2').textContent, '<img src=x onerror=alert(1)>');
  const selector = mount.querySelector('#vizSelect');
  ui.api.ensureEmbeddedVisualisationMarkup();
  assert.equal(mount.querySelector('#vizSelect'), selector);
});
