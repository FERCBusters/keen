// Shared app.js helpers at the edges of their input: malformed or missing
// values from records and evidence, and hostile markup and links.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

const badges = [];
const ui = await loadUi('app.js', {
  vendor: {
    apiGet: async (path) => ({ path }),
    sourceBadgeHtml: (source, meta, href) => {
      badges.push({ source, href });
      return '';
    },
  },
});
const a = ui.api;
const doc = ui.document;
test.after(() => ui.close());

const render = (html) => {
  doc.body.innerHTML = html;
  return doc.body;
};

test('API paths with malformed percent escapes pass through, and their dot segments are still refused', async () => {
  // A segment that does not decode is sent as written: the browser does not
  // read it as "." or "..".
  assert.deepEqual(await a.apiGet('/api/v1/sources/%E0%A4%A/insights'), {
    path: '/api/v1/sources/%E0%A4%A/insights',
  });
  assert.deepEqual(await a.apiGet('/api/v1/sources/%zz%2e%2e'), {
    path: '/api/v1/sources/%zz%2e%2e',
  });
  await assert.rejects(a.apiGet('/api/v1/sources/%/../me'), /Invalid API path/);
  await assert.rejects(a.apiGet('/api/v1/%E0%A4%A/%2e%2e/me'), /Invalid API path/);
});

test('a source badge links to its source register page, encoded, unless the page gives a link', () => {
  badges.length = 0;
  a.sourceBadgeHtml(' github ');
  a.sourceBadgeHtml('a&b"<c>');
  a.sourceBadgeHtml(null);
  a.sourceBadgeHtml('');
  a.sourceBadgeHtml('loki', {}, '/source.html?source=loki&framework=A');
  assert.deepEqual(
    badges.map((b) => b.href),
    [
      '/source.html?source=github',
      '/source.html?source=a%26b%22%3Cc%3E',
      '',
      '',
      '/source.html?source=loki&framework=A',
    ],
  );
});

test('a navigation without a link list still gains the Stories link', () => {
  assert.deepEqual([...a.withStoriesLink(null).map((link) => link.key)], ['stories']);
  assert.deepEqual([...a.withStoriesLink({ key: 'audits' }).map((link) => link.key)], ['stories']);
});

test('rich-text links keep local paths and fragments in place and drop missing or unparsable addresses', () => {
  const body = render(
    a.sanitizeRichTextHtml(
      '<a>Plain</a><a href="/events.html?id=1">Local</a><a href="#notes">Jump</a>' +
        '<a href="https://[bad">Broken</a><a href="mailto:dana@example.test">Mail</a>',
    ),
  );
  const links = [...body.querySelectorAll('a')].map((link) => [
    link.textContent,
    link.getAttribute('href'),
    link.getAttribute('target'),
  ]);
  assert.deepEqual(links, [
    ['Plain', null, null],
    ['Local', '/events.html?id=1', null],
    ['Jump', '#notes', null],
    ['Broken', null, null],
    ['Mail', 'mailto:dana@example.test', null],
  ]);
});

test('rich text drops comments, including markup hidden in them, and writes strike as s', () => {
  assert.equal(
    a.sanitizeRichTextHtml('<p>Keep</p><!-- <img src=x onerror=alert(1)> --><strike>old</strike>'),
    '<p>Keep</p><s>old</s>',
  );
  // A value that only looks like rich text in a comment shows the empty marker.
  assert.equal(
    a.renderRichTextContent('<!-- <p>draft</p> -->'),
    '<span class="small-muted">-</span>',
  );
  assert.equal(a.renderRichTextContent('<!-- <p>draft</p> -->', '<em>None</em>'), '<em>None</em>');
});

test('a missing plain-text value shows the empty markup', () => {
  assert.equal(a.plainTextToRichTextHtml(null, '<em>None</em>'), '<em>None</em>');
  assert.equal(a.plainTextToRichTextHtml(undefined), '');
  assert.equal(a.plainTextToRichTextHtml(0, '-'), '-');
});

test('editor helpers given no field do nothing and report an empty value', async (t) => {
  const started = [];
  const u = await loadUi('app.js', {
    html: '<textarea id="kept" data-rich-text="1">Kept</textarea>',
    setup(window) {
      window.Wysi = (options) => started.push(options.el.id);
    },
  });
  t.after(() => u.close());
  assert.equal(u.api.syncRichTextEditor(null), '');
  assert.equal(u.api.syncRichTextEditor(undefined), '');
  // A form the page did not find starts no editor.
  assert.deepEqual([...u.api.initRichTextEditors(null)], []);
  assert.deepEqual(started, []);
  assert.doesNotThrow(() => u.api.setRichTextEditorValue('missing', '<p>Lost</p>'));
  assert.equal(u.document.getElementById('kept').value, 'Kept');
});

test('the sanitiser returns nothing in a realm without a document', async (t) => {
  const u = await loadUi('app.js', { globals: { document: undefined } });
  t.after(() => u.close());
  assert.equal(u.api.sanitizeRichTextHtml('<p>Hello</p><img src=x onerror=alert(1)>'), '');
});

test('badge text on a background that is not a hex colour stays dark', () => {
  for (const colour of ['red', '#12345g', '#0000zz', '#abcd', '', null])
    assert.equal(a.idealTextColor(colour), '#111827', String(colour));
});

test('a blank name falls back, and a missing fallback reads as blank', () => {
  assert.equal(a.userDisplayName('', 'Fallback'), 'Fallback');
  assert.equal(a.userDisplayName(0, 'Fallback'), 'Fallback');
  assert.equal(a.userDisplayName('', null), '');
  assert.equal(a.userDisplayName({}, null), '');
  assert.equal(a.userDisplayName({ email: '' }, null), '');
});

test('user pills show a dash for an empty placeholder and omit a blank title', () => {
  assert.equal(a.userPillHtml(null, { empty: '' }), '<span class="small-muted">-</span>');
  const pill = render(a.userPillHtml('Dana', { title: '   ' })).firstElementChild;
  assert.equal(pill.textContent, 'Dana');
  assert.equal(pill.hasAttribute('title'), false);
});

test('a single user or none renders as a pill list or the empty text', () => {
  const list = render(a.userPillsHtml('<b>dana</b>')).firstElementChild;
  assert.equal(list.className, 'user-pill-list');
  assert.deepEqual(
    [...list.querySelectorAll('.user-pill')].map((p) => p.textContent),
    ['<b>dana</b>'],
  );
  assert.equal(list.querySelector('b'), null);
  assert.equal(
    a.userPillsHtml(null, { empty: 'Nobody' }),
    '<span class="small-muted">Nobody</span>',
  );
});

test('an audit without a status counts as open', () => {
  for (const audit of [null, {}, { status: null }, '']) {
    assert.equal(a.auditStatusValue(audit), 'open');
    assert.equal(a.auditStatusLabel(audit), 'Open');
    assert.equal(a.isAuditActive(audit), true);
    assert.equal(a.isAuditLocked(audit), false);
  }
});

test('event sampling switched off stops event samples only', () => {
  const me = { is_admin: true, features: { enabled: { event_sampling: false } } };
  assert.equal(a.canSampleIntoAudit(me, 'event'), false);
  assert.equal(a.canSampleIntoAudit(me, ''), false);
  assert.equal(a.canSampleIntoAudit(me, 'risk'), true);
});

test('a SWOT analysis is sampled by those who can view the ISMS', () => {
  assert.equal(
    a.canSampleIntoAudit({ can_manage_audits: true, can_view_isms: true }, 'isms_swot'),
    true,
  );
  assert.equal(
    a.canSampleIntoAudit({ can_manage_audits: true, can_sample_isms: true }, 'isms_swot'),
    false,
  );
});

test('editing is refused without an audit, even for administrators', () => {
  assert.equal(a.canEditAuditForUser({ is_admin: true }, null), false);
  assert.equal(a.canEditAuditForUser({ can_admin_audits: true }, undefined), false);
});

test('remembering no audit clears the remembered audit', async (t) => {
  const globals = {};
  const u = await loadUi('app.js', {
    html: '<select id="pick"><option value="a1">One</option><option value="a2">Two</option></select>',
    globals,
    setup(window) {
      globals.sessionStorage = window.sessionStorage;
    },
  });
  t.after(() => u.close());
  const select = u.document.getElementById('pick');
  u.api.rememberSamplingAudit('a2');
  u.api.preselectSamplingAudit(select);
  assert.equal(select.value, 'a2');
  u.api.rememberSamplingAudit(null);
  assert.equal(globals.sessionStorage.getItem('keen.samplingAudit'), '');
  select.value = 'a1';
  u.api.preselectSamplingAudit(select);
  assert.equal(select.value, 'a1');
});

test('loading markup falls back to its default message, a full-size spinner and one column', () => {
  const inline = render(a.loadingSpinnerHtml('')).firstElementChild;
  assert.equal(inline.textContent.trim(), 'Loading data');
  const large = render(a.loadingSpinnerHtml('Fetching <b>', { small: false }));
  assert.equal(large.querySelector('[role="status"]').className, 'spinner-border');
  assert.equal(large.querySelector('b'), null);
  assert.match(large.textContent, /Fetching <b>/);
  for (const colspan of ['wide', 0, null, -3]) {
    const row = render(`<table><tbody>${a.tableLoadingHtml(colspan)}</tbody></table>`);
    assert.equal(row.querySelector('td').getAttribute('colspan'), '1', String(colspan));
  }
  assert.doesNotThrow(() => a.showInlineLoading(null, 'Loading'));
});

test('an arrow link with a blank title is labelled Open link', () => {
  const link = render(a.arrowLinkHtml('https://example.test/a', { title: '' })).firstElementChild;
  assert.equal(link.title, 'Open link');
  assert.equal(link.getAttribute('aria-label'), 'Open link');
});

test('a link without a scheme or leading slash is refused', () => {
  for (const url of ['example.test/login', 'www.example.test', '%%'])
    assert.equal(a.safeExternalHref(url), '', url);
});

test('titled links accept a single address or records naming it as url, href or link', () => {
  const single = render(a.titledLinksHtml('https://example.test/a'));
  assert.deepEqual(
    [...single.querySelectorAll('a')].map((l) => [l.getAttribute('href'), l.textContent, l.title]),
    [['https://example.test/a', 'https://example.test/a', 'Open link']],
  );
  const many = render(
    a.titledLinksHtml(
      [
        { href: '/clause.html?id=1', label: 'Clause <b>4.1</b>' },
        { link: 'https://example.test/b' },
        { name: 'No address' },
        'javascript:alert(1)',
      ],
      { title: 'Evidence' },
    ),
  );
  assert.deepEqual(
    [...many.querySelectorAll('a')].map((l) => [l.getAttribute('href'), l.textContent, l.title]),
    [
      ['/clause.html?id=1', 'Clause <b>4.1</b>', 'Evidence 1'],
      ['https://example.test/b', 'https://example.test/b', 'Evidence 2'],
    ],
  );
  assert.equal(many.querySelector('b'), null);
  assert.equal(a.titledLinksHtml(null, { empty: '<span>-</span>' }), '<span>-</span>');
});

test('a page size of zero or none pages by 50', () => {
  for (const limit of [0, null, '']) {
    const s = a.offsetPagerState({ total: 120, limit });
    assert.deepEqual([s.limit, s.end, s.nextOffset], [50, 50, 50], String(limit));
  }
});

test('pagers without buttons are left alone', () => {
  for (const pager of [null, {}, { prev: 'prev', next: 3 }]) {
    assert.equal(a.setOffsetPagerDisabled(pager, { total: 5 }).total, 5);
    assert.doesNotThrow(() => a.wireOffsetPagerButtons(pager, () => assert.fail('paged')));
  }
});

test('a click that reaches a disabled pager button does not change the page', () => {
  doc.body.innerHTML = '<button id="p"></button><button id="n"></button>';
  const prev = doc.getElementById('p');
  const next = doc.getElementById('n');
  const pages = [];
  a.wireOffsetPagerButtons({ prev: [prev], next: [next] }, (direction) => pages.push(direction));
  prev.disabled = true;
  next.disabled = true;
  // A click dispatched by script reaches the listener on a disabled button.
  for (const button of [prev, next])
    button.dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.deepEqual(pages, []);
  prev.disabled = false;
  next.disabled = false;
  for (const button of [prev, next])
    button.dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.deepEqual(pages, ['prev', 'next']);
});

test('a metric without a target shows its value without a threshold state', () => {
  for (const target_value of [null, undefined, '']) {
    const measure = { threshold_operator: 'gte', target_value };
    assert.equal(a.effectivenessMetricThresholdState(measure, { metric_value: 5 }), null);
    assert.equal(a.effectivenessMetricThresholdBadgeHtml(measure, { metric_value: 5 }), '');
    const value = render(
      a.effectivenessMetricValueHtml({ metric_value: 5 }, measure),
    ).firstElementChild;
    assert.equal(value.className, 'fw-semibold');
    assert.equal(value.textContent, '5');
  }
});

test('a missing metric entry shows the fallback text as text', () => {
  const body = render(
    a.effectivenessMetricValueHtml(
      null,
      { threshold_operator: 'gte', target_value: 1 },
      {
        fallback: 'No entry <b>yet</b>',
      },
    ),
  );
  assert.equal(body.firstElementChild.className, 'small-muted');
  assert.equal(body.textContent, 'No entry <b>yet</b>');
  assert.equal(body.querySelector('b'), null);
});

test('changelog entries fall back to readable defaults and cut long values', () => {
  const html = a.entityChangelogHtml([
    {
      changes: [{ field: 'notes', before: 'x'.repeat(1001), after: 'short' }],
      request_method: 'PATCH',
      request_path: '/api/v1/risks/<r1>',
    },
    { summary: 'Imported', changes: null, request_path: '/api/v1/import' },
  ]);
  const body = render(html);
  const [first, second] = body.querySelectorAll('.list-group-item');
  assert.equal(first.querySelector('.fw-semibold').textContent, 'Entity changed');
  const before = first.querySelector('tbody td:nth-child(2)').textContent;
  assert.equal(before.length, 1001);
  assert.ok(before.endsWith('…'));
  assert.equal(first.querySelector('.badge').textContent, 'PATCH /api/v1/risks/<r1>');
  assert.equal(second.querySelector('.badge').textContent.trim(), '/api/v1/import');
  assert.match(second.textContent, /No field-level changes recorded\./);
  assert.match(second.querySelector('.small-muted').textContent, /system · updated/);
  assert.equal(
    a.entityChangelogHtml(null, { empty: 'Nothing <i>yet</i>' }),
    '<div class="small-muted p-3">Nothing &lt;i&gt;yet&lt;/i&gt;</div>',
  );
});

test('sampling a record that has not been saved asks for it to be saved first', async (t) => {
  const toasts = [];
  const requests = [];
  const u = await loadUi('app.js', {
    html: '<div id="status"></div>',
    vendor: {
      toast: (el, message, type) => toasts.push([el?.id, message, type]),
      apiGet: async (path) => {
        requests.push(path);
        return { items: [] };
      },
    },
  });
  t.after(() => u.close());
  const statusEl = u.document.getElementById('status');
  for (const [entityType, entityId] of [
    ['risk', null],
    [null, 'r1'],
  ])
    await u.api.openAuditSampleModal({ me: { is_admin: true }, entityType, entityId, statusEl });
  assert.deepEqual(toasts, [
    ['status', 'Save this item before sampling it into an audit.', 'warning'],
    ['status', 'Save this item before sampling it into an audit.', 'warning'],
  ]);
  assert.deepEqual(requests, []);
  assert.equal(u.document.getElementById('entityAuditSampleModal'), null);
});

test('the sampling dialog on a page without a framework lists and starts audits without one', async (t) => {
  const requests = [];
  const u = await loadUi('app.js', {
    url: 'https://keen.example/risk.html?id=r1',
    vendor: {
      toast: () => {},
      apiGet: async (path) => {
        requests.push(path);
        return { items: [{ id: 'a1', title: 'Annual', status: 'open' }] };
      },
    },
  });
  t.after(() => u.close());
  await u.api.openAuditSampleModal({ me: { is_admin: true }, entityType: 'risk', entityId: 'r1' });
  assert.deepEqual(requests, ['/api/v1/audits?limit=500']);
  assert.equal(
    u.document.getElementById('entityAuditSampleCreate').getAttribute('href'),
    '/audit-wizard.html',
  );
  assert.equal(u.document.getElementById('entityAuditSampleSelect').value, 'a1');
});

test('rich-text links to this site survive on a page without an origin of its own', async (t) => {
  // A file or sandboxed frame has the opaque origin "null", against which
  // no address can be resolved; local paths and fragments need no resolving.
  const u = await loadUi('app.js', { url: 'file:///keen/notes.html' });
  t.after(() => u.close());
  assert.equal(u.window.location.origin, 'null');
  assert.equal(
    u.api.sanitizeRichTextHtml('<a href="/events.html">Local</a><a href="#notes">Jump</a>'),
    '<a href="/events.html">Local</a><a href="#notes">Jump</a>',
  );
});

test('a changelog with nowhere to show it makes no request', async (t) => {
  const requests = [];
  const u = await loadUi('app.js', {
    vendor: {
      apiGet: async (path) => {
        requests.push(path);
        return { items: [] };
      },
    },
  });
  t.after(() => u.close());
  assert.equal(await u.api.loadEntityChangelog(null, '/api/v1/risks/r1/changelog'), null);
  assert.deepEqual(requests, []);
});
