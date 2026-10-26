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
  ]) {
    assert.match(server, new RegExp(`add_header ${header} \\$[a-z_]+ always;`));
  }
  assert.match(server, /add_header Cache-Control \$keen_cache_control always;/);
  assert.match(conf, /map \$status \$keen_vendor_cache_control\s*\{\s*default "no-store";/);
  assert.match(conf, /~\^\/vendor\/ \$keen_vendor_cache_control;/);
  assert.match(conf, /~\^\/pages\/ "no-store";/);
});

test('the login brand image is an exact public static resource', async () => {
  const conf = await readFile(new URL('../nginx/default.conf', import.meta.url), 'utf8');
  assert.match(conf, /location = \/keen-mitigator-64\.png\s*\{\s*try_files \$uri =404;\s*\}/);
  const image = await readFile(new URL('../public/keen-mitigator-64.png', import.meta.url));
  assert.equal(image.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
});
