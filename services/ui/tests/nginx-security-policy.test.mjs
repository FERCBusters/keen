import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

test('cache policies preserve inherited security headers on every location and error status', async () => {
  const conf = await readFile(new URL('../nginx/default.conf', import.meta.url), 'utf8');
  const server = conf.slice(conf.indexOf('server {'));
  const locations = server.slice(server.indexOf('location '));
  assert.doesNotMatch(locations, /\badd_header\b/);
  for (const header of [
    'Content-Security-Policy',
    'X-Frame-Options',
    'X-Content-Type-Options',
    'Referrer-Policy',
    'Strict-Transport-Security',
    'Cross-Origin-Opener-Policy',
    'Cross-Origin-Embedder-Policy',
    'Permissions-Policy',
    'X-XSS-Protection',
  ]) {
    assert.match(server, new RegExp(`add_header ${header} \\$[a-z_]+ always;`));
  }
  assert.match(server, /add_header Cache-Control \$keen_cache_control always;/);
  // Browsers' XSS auditors are retired; any that remain are switched off.
  assert.match(
    conf,
    /map \$upstream_http_x_xss_protection \$x_xss_protection_value \{\s*default "";\s*"" "0";/,
  );
  assert.match(conf, /map \$status \$keen_vendor_cache_control\s*\{\s*default "no-store";/);
  assert.match(conf, /~\^\/vendor\/ \$keen_vendor_cache_control;/);
  // The design system changes with Keen at a stable URL, so browsers check it
  // before each use. nginx applies the first matching pattern.
  const cachePolicy = conf.slice(conf.indexOf('map $uri $keen_cache_control'));
  const designSystem = cachePolicy.indexOf('~^/vendor/mosp-design-system/ "no-cache";');
  assert.notEqual(designSystem, -1);
  assert.ok(designSystem < cachePolicy.indexOf('~^/vendor/ $keen_vendor_cache_control;'));
  assert.match(conf, /~\^\/pages\/ "no-store";/);
});

test('the login brand image is an exact public static resource', async () => {
  const conf = await readFile(new URL('../nginx/default.conf', import.meta.url), 'utf8');
  assert.match(conf, /location = \/keen-mitigator-64\.png\s*\{\s*try_files \$uri =404;\s*\}/);
  const image = await readFile(new URL('../public/keen-mitigator-64.png', import.meta.url));
  assert.equal(image.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
});

test('a path beginning with // is sent home, or refused for the API, before any location is chosen', async () => {
  const conf = (await readFile(new URL('../nginx/default.conf', import.meta.url), 'utf8')).replace(
    /#.*$/gm,
    '',
  );
  const server = conf.slice(conf.indexOf('server {'));
  const serverLevel = server.slice(0, server.indexOf('location '));
  // By the rewrite phase $uri is merged and decoded; $request_uri is the path as sent.
  assert.match(serverLevel, /if \(\$request_uri ~ "\^\/\/"\) \{\s*return 301 \/;\s*\}/);
  // An API request under "//" gets 404, since a redirect would turn a POST into
  // a GET of the home page. It comes first because the 301 rule matches it too.
  const api = serverLevel.search(/if \(\$request_uri ~ "\^\/\/\+api\/"\) \{\s*return 404;\s*\}/);
  assert.notEqual(api, -1);
  assert.ok(api < serverLevel.indexOf('return 301 /;'));
  const rules = [
    ...serverLevel.matchAll(/if \(\$request_uri ~ "([^"]+)"\) \{\s*return (\d+)[^;]*;/g),
  ];
  const answer = (path) => rules.find(([, pattern]) => new RegExp(pattern).test(path))?.[2];
  for (const path of ['//api/v1/auth/login', '///api/v1/events?q=1', '//api/']) {
    assert.equal(answer(path), '404', path);
  }
  for (const path of ['//evil.example/', '//evil.example/api/x', '//api', '//apiary/']) {
    assert.equal(answer(path), '301', path);
  }
  for (const path of ['/api/v1/auth/login', '/api//v1/events', '/controls.html?next=//api/']) {
    assert.equal(answer(path), undefined, path);
  }
  // A redirect that echoes the path would hand the browser "//host" again.
  assert.doesNotMatch(conf, /return 30\d [^;]*\$(?:request_)?uri/);
  // A relative Location keeps the browser's scheme, host and port behind a proxy.
  assert.match(serverLevel, /absolute_redirect off;/);
  assert.doesNotMatch(conf, /merge_slashes\s+off/);
  assert.doesNotMatch(conf, /\$uri ~ "\^\/\/"/);
});
