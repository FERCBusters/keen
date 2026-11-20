// Every dependency fetched from a git host is held to the tree reviewed. npm
// skips the lockfile integrity check for such a dependency and fetches it as a
// tarball nothing checks, so the image build compares each one's fetched tree
// with the digest recorded at review before running any of its code, and the
// vendoring does again before copying any of its files.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const ui = new URL('../', import.meta.url);
const lock = JSON.parse(fs.readFileSync(new URL('package-lock.json', ui), 'utf8'));
const reviewed = JSON.parse(
  fs.readFileSync(new URL('scripts/reviewed-git-packages.json', ui), 'utf8'),
).packages;
const { gitPackages, treeDigest } = await import(new URL('scripts/vendor-guard.mjs', ui));
const SCRIPTS = [
  'vendor-guard.mjs',
  'check-git-packages.mjs',
  'record-git-packages.mjs',
  'vendor-assets.mjs',
];

test('every git dependency in the lockfile was reviewed at its pinned commit', () => {
  const pinned = gitPackages(lock.packages);
  // MOSP carries an integrity field, which npm ignores for a git dependency:
  // it is held to its reviewed tree like Wysi, which carries none.
  assert.deepEqual(pinned.map(({ name }) => name).sort(), [
    '@fercbusters/mosp-design-system',
    'wysi',
  ]);
  assert.ok(lock.packages['node_modules/@fercbusters/mosp-design-system'].integrity);
  for (const { name, commit } of pinned) {
    assert.equal(reviewed[name]?.commit, commit, `${name} was not reviewed at ${commit}`);
    assert.match(reviewed[name].tree, /^[0-9a-f]{64}$/);
  }
  assert.equal(reviewed['@fercbusters/mosp-design-system'].built, 'dist');
  assert.deepEqual(
    gitPackages({
      '': {},
      'node_modules/a': { resolved: 'git+ssh://git@github.com/o/a.git#abc' },
      'node_modules/b': { resolved: 'git+https://x/b.git#def', integrity: 'sha512-z' },
      'node_modules/registry': { resolved: 'https://registry.npmjs.org/r.tgz', integrity: 'x' },
      'node_modules/local': { resolved: 'node_modules/local', link: true },
    }),
    [
      { name: 'a', commit: 'abc' },
      { name: 'b', commit: 'def' },
    ],
  );
});

function scratch({ packages = {}, files = {}, entries = {} } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'keen-git-packages-'));
  fs.mkdirSync(path.join(root, 'scripts'));
  for (const name of SCRIPTS) {
    fs.copyFileSync(new URL(`scripts/${name}`, ui), path.join(root, 'scripts', name));
  }
  fs.writeFileSync(
    path.join(root, 'scripts', 'reviewed-git-packages.json'),
    JSON.stringify({ comment: 'test', packages: entries }),
  );
  if (packages)
    fs.writeFileSync(path.join(root, 'package-lock.json'), JSON.stringify({ packages }));
  for (const [name, content] of Object.entries(files)) write(root, name, content);
  return root;
}

function write(root, name, content) {
  const file = path.join(root, 'node_modules', ...name.split('/'));
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
}

function run(root, script, ...args) {
  try {
    const out = execFileSync(process.execPath, [path.join(root, 'scripts', script), ...args], {
      cwd: root,
      stdio: 'pipe',
    });
    return { ok: true, out: String(out) };
  } catch (error) {
    return { ok: false, out: String(error.stderr) };
  }
}

const recorded = (root) =>
  JSON.parse(fs.readFileSync(path.join(root, 'scripts', 'reviewed-git-packages.json'), 'utf8'))
    .packages;

const DESIGN = {
  'node_modules/@o/design': {
    resolved: 'git+ssh://git@github.com/o/design.git#c1',
    integrity: 'sha512-z',
  },
  'node_modules/editor': { resolved: 'git+ssh://git@github.com/o/editor.git#e1' },
};
const FETCHED = {
  '@o/design/package.json': '{}',
  '@o/design/src/app.js': 'app()',
  'editor/dist/editor.js': 'editor()',
};

test('a fetched tree is checked against the one recorded at review', (t) => {
  const root = scratch({
    packages: DESIGN,
    files: FETCHED,
    entries: { '@o/design': { built: 'dist' } },
  });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.equal(run(root, 'record-git-packages.mjs').ok, true);
  assert.deepEqual(Object.keys(recorded(root)), ['@o/design', 'editor']);
  assert.equal(recorded(root)['@o/design'].built, 'dist');
  assert.equal(recorded(root)['@o/design'].commit, 'c1');
  assert.match(
    run(root, 'check-git-packages.mjs', '--fetched').out,
    /match their reviewed trees: @o\/design, editor/,
  );

  // Its own build's output is left out, but must not arrive with the fetch.
  write(root, '@o/design/dist/app.js', 'built()');
  assert.equal(run(root, 'check-git-packages.mjs').ok, true);
  assert.match(
    run(root, 'check-git-packages.mjs', '--fetched').out,
    /@o\/design arrived with dist\/, which its own build writes/,
  );
  fs.rmSync(path.join(root, 'node_modules', '@o', 'design', 'dist'), { recursive: true });

  // Changed, added and renamed content are each refused.
  for (const [change, undo] of [
    [
      () => write(root, 'editor/dist/editor.js', 'evil()'),
      () => write(root, 'editor/dist/editor.js', 'editor()'),
    ],
    [
      () => write(root, 'editor/dist/extra.js', 'x'),
      () => fs.rmSync(path.join(root, 'node_modules/editor/dist/extra.js')),
    ],
    [
      () =>
        fs.renameSync(
          path.join(root, 'node_modules/editor/dist/editor.js'),
          path.join(root, 'node_modules/editor/dist/other.js'),
        ),
      () =>
        fs.renameSync(
          path.join(root, 'node_modules/editor/dist/other.js'),
          path.join(root, 'node_modules/editor/dist/editor.js'),
        ),
    ],
  ]) {
    change();
    assert.match(
      run(root, 'check-git-packages.mjs').out,
      /editor at e1 has tree digest [0-9a-f]{64}, not the reviewed/,
    );
    undo();
    assert.equal(run(root, 'check-git-packages.mjs').ok, true);
  }
});

test('a new pin, or a git dependency never reviewed, stops the build until recorded', (t) => {
  const root = scratch({
    packages: DESIGN,
    files: FETCHED,
    entries: { '@o/design': { built: 'dist' } },
  });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.match(
    run(root, 'check-git-packages.mjs').out,
    /@o\/design is fetched from a git host and was never reviewed/,
  );
  run(root, 'record-git-packages.mjs');
  const bumped = {
    ...DESIGN,
    'node_modules/editor': { resolved: 'git+ssh://git@github.com/o/editor.git#e2' },
  };
  fs.writeFileSync(path.join(root, 'package-lock.json'), JSON.stringify({ packages: bumped }));
  assert.match(
    run(root, 'check-git-packages.mjs').out,
    /editor is pinned to e2, but e1 is the commit reviewed/,
  );
  assert.match(run(root, 'record-git-packages.mjs').out, /editor e2 [0-9a-f]{64}/);
  assert.equal(run(root, 'check-git-packages.mjs').ok, true);
  // A package that is not installed runs and ships nothing; it cannot be
  // recorded without being looked at.
  fs.rmSync(path.join(root, 'node_modules', 'editor'), { recursive: true });
  assert.equal(run(root, 'check-git-packages.mjs').ok, true);
  assert.match(
    run(root, 'record-git-packages.mjs').out,
    /editor is not installed; run npm ci first/,
  );
});

test('a link in a fetched tree is refused', (t) => {
  const root = scratch({ packages: DESIGN, files: FETCHED });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.symlinkSync('/etc/passwd', path.join(root, 'node_modules', 'editor', 'dist', 'link.js'));
  assert.match(
    run(root, 'record-git-packages.mjs').out,
    /link\.js is neither a file nor a directory/,
  );
  assert.throws(
    () => treeDigest(path.join(root, 'node_modules', 'editor')),
    /neither a file nor a directory/,
  );
});

const WYSI = { 'node_modules/wysi': { resolved: 'git+ssh://git@github.com/mdbassit/Wysi.git#b4' } };
const WYSI_FILES = { 'wysi/dist/wysi.min.css': '.wysi{}', 'wysi/dist/wysi.min.js': 'Wysi()' };

test('vendoring copies nothing from a git dependency whose tree is not the reviewed one', (t) => {
  const root = scratch({ packages: WYSI, files: WYSI_FILES });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  run(root, 'record-git-packages.mjs');
  assert.equal(run(root, 'vendor-assets.mjs').ok, true);
  assert.equal(
    fs.readFileSync(path.join(root, 'public/vendor/wysi/wysi.min.js'), 'utf8'),
    'Wysi()',
  );
  fs.rmSync(path.join(root, 'public'), { recursive: true });
  write(root, 'wysi/dist/wysi.min.js', 'evil()');
  assert.match(run(root, 'vendor-assets.mjs').out, /wysi at b4 has tree digest/);
  assert.equal(fs.existsSync(path.join(root, 'public')), false);
});

test('the build stops when a required file is missing, or without the lockfile', (t) => {
  const missing = scratch({ packages: WYSI });
  const unlocked = scratch({ packages: null });
  t.after(() => {
    fs.rmSync(missing, { recursive: true, force: true });
    fs.rmSync(unlocked, { recursive: true, force: true });
  });
  assert.match(
    run(missing, 'vendor-assets.mjs').out,
    /Required vendored asset is missing: .*wysi\.min\.css/,
  );
  assert.match(
    run(unlocked, 'vendor-assets.mjs').out,
    /package-lock\.json is missing; the git dependencies cannot be checked/,
  );
  assert.match(run(unlocked, 'check-git-packages.mjs').out, /package-lock\.json is missing/);
});

test('the image build checks the git dependencies before running any of their code', () => {
  const dockerfile = fs.readFileSync(new URL('Dockerfile', ui), 'utf8');
  const install = dockerfile.indexOf('RUN npm ci --ignore-scripts');
  const check = dockerfile.indexOf('RUN node scripts/check-git-packages.mjs --fetched');
  const build = dockerfile.indexOf(
    'RUN npm --prefix node_modules/@fercbusters/mosp-design-system run build',
  );
  assert.ok(install >= 0 && install < check && check < build);
  assert.equal(dockerfile.match(/npm (ci|install)\b/g).length, 1);
});
