// Browser code from a dependency fetched from a git host is bound to reviewed
// content. npm records no checksum for such a dependency, so the lockfile
// cannot detect a fetch that returns different content; the vendoring script
// compares each file it ships from one, whichever copy step names it, with a
// digest recorded at review.
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
const { packageOf, unchecksummed } = await import(new URL('scripts/vendor-guard.mjs', ui));

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
  assert.equal(packageOf('wysi/dist/wysi.min.js'), 'wysi');
  assert.equal(packageOf('@scope/name/dist/app.js'), '@scope/name');
});

function vendorTree(files, packages = lock.packages) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'keen-vendor-'));
  if (packages)
    fs.writeFileSync(path.join(root, 'package-lock.json'), JSON.stringify({ packages }));
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

test('the build checks every file it ships from such a dependency, not only listed ones', (t) => {
  // Were Bootstrap fetched from a git host, the files its existing copy steps
  // ship would need reviewed digests too.
  const packages = {
    ...lock.packages,
    'node_modules/bootstrap': { resolved: 'git+ssh://git@github.com/twbs/bootstrap.git#abc' },
  };
  const root = vendorTree({ 'bootstrap/dist/css/bootstrap.min.css': 'body{}' }, packages);
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.match(
    vendor(root),
    /bootstrap\/dist\/css\/bootstrap\.min\.css has SHA-256 [0-9a-f]{64}, not the reviewed \(none recorded\)/,
  );
  assert.equal(fs.existsSync(path.join(root, 'public', 'vendor', 'bootstrap')), false);
});

test('without the lockfile the build stops', (t) => {
  const root = vendorTree({}, null);
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.match(vendor(root), /package-lock\.json is missing; the vendored files cannot be checked/);
});
