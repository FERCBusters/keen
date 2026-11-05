// demo-notice.js runs as a deferred classic script on the sign-in page and the
// application pages. It asks the public sign-in metadata whether Keen is a demo
// and shows a notice the reader can dismiss for the tab; the API enforces the
// expiry itself.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { classicPage } from './support/classic-page.mjs';
import { settle } from './support/page-fixture.mjs';

const loginHtml = await readFile(new URL('../public/login.html', import.meta.url), 'utf8');
const EXPIRES = '2026-11-20T09:30:00+00:00';
const END = Date.parse(EXPIRES);
const ACTIVE_KEY = `keen-demo-dismissed:${EXPIRES}:false`;
const ENDED_KEY = `keen-demo-dismissed:${EXPIRES}:true`;
const ENDED = 'DEMO ENDED - access is closed and this instance is scheduled for deletion. ';
const TIMER = 41;

// What /api/v1/auth/methods reports for a demo installation.
const demo = (overrides = {}) => ({
  enabled: true,
  expires_at: EXPIRES,
  consultation_url: 'https://consult.example/keen',
  expired: false,
  ...overrides,
});
const answer =
  (body, { ok = true, status = 200 } = {}) =>
  async () => ({ ok, status, json: async () => body });

async function demoPage(t, { methods = answer({ demo: demo() }), now = END - 1, setup } = {}) {
  const requests = [];
  const timers = [];
  const cleared = [];
  const ui = await classicPage({
    html: loginHtml,
    url: 'https://keen.example/login.html',
    scripts: ['demo-notice.js'],
    setup(window) {
      // The network: the public sign-in metadata request.
      window.fetch = async (url, options) => {
        // Copied out of the page's realm for comparison.
        requests.push([url, { ...options }]);
        return methods();
      };
      // The clock: the current time and the notice's refresh timer.
      window.Date.now = () => now;
      window.setInterval = (callback, ms) => {
        timers.push([callback, ms]);
        return TIMER;
      };
      window.clearInterval = (id) => cleared.push(id);
      setup?.(window);
    },
  });
  t.after(ui.close);
  const notice = () => ui.document.getElementById('keen-demo-notice');
  return {
    ...ui,
    requests,
    timers,
    cleared,
    notice,
    message: () => notice().querySelector('strong').textContent,
    setNow: (value) => (now = value),
    shown: () => settle(() => notice()),
    // The script has asked for the metadata and acted on the answer.
    idle: async () => {
      await settle(() => requests.length > 0);
      await new Promise((resolve) => setTimeout(resolve, 0));
    },
  };
}

const activeText = (window, expires = EXPIRES) =>
  `DEMO ONLY - ends ${new window.Date(expires).toLocaleString()}. Use fictional data only; do not enter sensitive business information, personal data or credentials. `;

function blockSessionStorage(window) {
  Object.defineProperty(window, 'sessionStorage', {
    configurable: true,
    get() {
      throw new window.DOMException('The operation is insecure.', 'SecurityError');
    },
  });
}

test('the sign-in page loads the notice as a deferred classic script', () => {
  const { document } = new JSDOM(loginHtml).window;
  const script = document.querySelector('script[src="/demo-notice.js"]');
  assert.equal(script.parentElement.tagName, 'HEAD');
  assert.equal(script.hasAttribute('defer'), true);
  assert.equal(script.hasAttribute('type'), false);
});

test('an active demo shows its end, a consultation link and a dismiss button first on the page', async (t) => {
  const ui = await demoPage(t);
  await ui.shown();
  assert.deepEqual(ui.requests, [['/api/v1/auth/methods', { cache: 'no-store' }]]);
  const notice = ui.notice();
  assert.equal(ui.document.body.firstElementChild, notice);
  assert.equal(notice.tagName, 'ASIDE');
  assert.equal(notice.getAttribute('role'), 'note');
  assert.equal(ui.message(), activeText(ui.window));
  const link = notice.querySelector('a');
  assert.equal(link.getAttribute('href'), 'https://consult.example/keen');
  assert.equal(link.textContent, 'Discuss a Keen installation.');
  const close = notice.querySelector('button');
  assert.equal(close.type, 'button');
  assert.equal(close.textContent, '×');
  assert.equal(close.getAttribute('aria-label'), 'Dismiss demo notice');
  assert.equal(close.title, 'Dismiss for this tab');
  // The sign-in form is still on the page below it.
  assert.ok(ui.document.getElementById('loginForm'));
  assert.deepEqual(
    ui.timers.map(([, ms]) => ms),
    [30000],
  );
});

test('the notice says the demo has ended once the clock reaches its end', async (t) => {
  const ui = await demoPage(t, { now: END - 60000 });
  await ui.shown();
  const [[refresh]] = ui.timers;
  ui.setNow(END - 1);
  refresh();
  assert.equal(ui.message(), activeText(ui.window));
  ui.setNow(END);
  refresh();
  assert.equal(ui.message(), ENDED);
});

test('a demo that has already ended says so when the page opens', async (t) => {
  const ui = await demoPage(t, { now: END });
  await ui.shown();
  assert.equal(ui.message(), ENDED);
});

test('dismissing the notice removes it and keeps it away for the rest of the tab session', async (t) => {
  const ui = await demoPage(t);
  await ui.shown();
  ui.notice().querySelector('button').click();
  assert.equal(ui.notice(), null);
  assert.deepEqual(ui.cleared, [TIMER]);
  assert.equal(ui.window.sessionStorage.getItem(ACTIVE_KEY), '1');

  // The tab's next page, with what the first one stored.
  const next = await demoPage(t, {
    setup: (window) => window.sessionStorage.setItem(ACTIVE_KEY, '1'),
  });
  await next.idle();
  assert.equal(next.notice(), null);
  assert.deepEqual(next.timers, []);
});

test('a notice dismissed while the demo ran returns once it has ended', async (t) => {
  const ui = await demoPage(t, {
    now: END,
    setup: (window) => window.sessionStorage.setItem(ACTIVE_KEY, '1'),
  });
  await ui.shown();
  assert.equal(ui.message(), ENDED);
  ui.notice().querySelector('button').click();
  assert.equal(ui.window.sessionStorage.getItem(ENDED_KEY), '1');

  const next = await demoPage(t, {
    now: END,
    setup: (window) => window.sessionStorage.setItem(ENDED_KEY, '1'),
  });
  await next.idle();
  assert.equal(next.notice(), null);
});

test('with session storage blocked the notice still shows and can be dismissed', async (t) => {
  const ui = await demoPage(t, { setup: blockSessionStorage });
  await ui.shown();
  assert.equal(ui.message(), activeText(ui.window));
  ui.notice().querySelector('button').click();
  assert.equal(ui.notice(), null);
  assert.deepEqual(ui.cleared, [TIMER]);
});

test('no notice shows outside demo mode or when the metadata cannot be read', async (t) => {
  const outcomes = {
    'not a demo': answer({
      demo: { enabled: false, expires_at: null, consultation_url: null, expired: false },
    }),
    'no demo metadata': answer({ local_enabled: true }),
    // A demo answer behind a failure status is still a failure.
    'failure status': answer({ demo: demo() }, { ok: false, status: 503 }),
    'unreadable body': async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError('Unexpected token < in JSON');
      },
    }),
    offline: async () => {
      throw new TypeError('Failed to fetch');
    },
  };
  for (const [name, methods] of Object.entries(outcomes)) {
    const ui = await demoPage(t, { methods });
    await ui.idle();
    assert.equal(ui.notice(), null, name);
    assert.deepEqual(ui.timers, [], name);
    assert.equal(ui.document.body.firstElementChild.className, 'visually-hidden-focusable', name);
  }
});

test('the consultation link shows only for an HTTPS address the browser can read', async (t) => {
  for (const consultation_url of [
    'http://consult.example/keen',
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    // The API's settings accept these two; the browser's URL parser refuses them.
    'https://consult example/keen',
    'https://consult.example:99999/keen',
    null,
    '',
  ]) {
    const ui = await demoPage(t, { methods: answer({ demo: demo({ consultation_url }) }) });
    await ui.shown();
    const label = JSON.stringify(consultation_url);
    assert.equal(ui.message(), activeText(ui.window), label);
    assert.equal(ui.notice().querySelector('a'), null, label);
    assert.ok(ui.notice().querySelector('button'), label);
  }
});

test('markup in the consultation address or expiry stays inert', async (t) => {
  const consultation_url =
    'https://consult.example/"><img src=x onerror="alert(1)"><script>alert(2)</script>';
  const ui = await demoPage(t, { methods: answer({ demo: demo({ consultation_url }) }) });
  await ui.shown();
  const link = ui.notice().querySelector('a');
  assert.equal(link.getAttribute('href'), new URL(consultation_url).href);
  assert.equal(link.textContent, 'Discuss a Keen installation.');
  assert.equal(ui.notice().querySelector('img, script'), null);

  const expires = '<img src=x onerror="alert(1)">';
  const odd = await demoPage(t, { methods: answer({ demo: demo({ expires_at: expires }) }) });
  await odd.shown();
  assert.equal(odd.message(), activeText(odd.window, expires));
  assert.equal(odd.notice().querySelector('img, script'), null);
});
