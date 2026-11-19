// Browser code from a dependency fetched from a git host is bound to reviewed
// content. npm records no checksum for such a dependency, so the lockfile
// cannot detect a fetch that returns different content; the vendoring script
// compares each file it ships from one with a digest recorded at review.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const ui = new URL('../', import.meta.url);
const lock = JSON.parse(fs.readFileSync(new URL('package-lock.json', ui), 'utf8'));
const reviewed = JSON.parse(
  fs.readFileSync(new URL('scripts/reviewed-vendor-files.json', ui), 'utf8'),
).files;
const script = new URL('scripts/vendor-assets.mjs', ui).pathname;

// Packages fetched from a git host whose lockfile entry carries no checksum.
function unchecksummed(packages) {
  return Object.entries(packages)
    .filter(([name, entry]) => name && entry.resolved?.startsWith('git') && !entry.integrity)
    .map(([name]) => name.replace(/^node_modules\//, ''));
}

test('every file shipped from a dependency the lockfile cannot checksum is reviewed', () => {
  const unchecked = unchecksummed(lock.packages);
  // Wysi is one: npm records no integrity for it.
  assert.ok(unchecked.includes('wysi'));
  for (const name of unchecked) {
    const files = Object.keys(reviewed).filter((file) => file.startsWith(`${name}/`));
    assert.ok(files.length > 0, `${name} ships files with no reviewed digest`);
  }
  for (const digest of Object.values(reviewed)) assert.match(digest, /^[0-9a-f]{64}$/);
  // The check finds a git dependency added without a reviewed file.
  assert.deepEqual(
    unchecksummed({
      '': {},
      'node_modules/other': { resolved: 'git+ssh://git@github.com/o/other.git#abc' },
      'node_modules/checked': { resolved: 'git+ssh://x#y', integrity: 'sha512-z' },
      'node_modules/registry': { resolved: 'https://registry.npmjs.org/r.tgz' },
    }),
    ['other'],
  );
});

function vendorTree(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'keen-vendor-'));
  for (const [name, content] of Object.entries(files)) {
    const file = path.join(root, 'node_modules', ...name.split('/'));
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, content);
  }
  return root;
}

function vendor(root) {
  try {
    execFileSync(process.execPath, [script], { cwd: root, stdio: 'pipe' });
    return '';
  } catch (error) {
    return String(error.stderr);
  }
}

test('the build stops when a fetched file is not the reviewed content', (t) => {
  const root = vendorTree({
    'wysi/dist/wysi.min.css': '.wysi{}',
    'wysi/dist/wysi.min.js': 'window.Wysi = () => {};',
  });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const refusal = vendor(root);
  const digest = crypto.createHash('sha256').update('.wysi{}').digest('hex');
  assert.match(
    refusal,
    new RegExp(`wysi/dist/wysi\\.min\\.css has SHA-256 ${digest}, not the reviewed`),
  );
  assert.equal(fs.existsSync(path.join(root, 'public', 'vendor', 'wysi')), false);
});

test('the build stops when a required file is missing', (t) => {
  const root = vendorTree({});
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.match(vendor(root), /Required vendored asset is missing: .*wysi\.min\.css/);
});
