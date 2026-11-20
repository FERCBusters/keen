// Holds every dependency fetched from a git host to the content reviewed.
//
// npm checks a registry package against the checksum in the lockfile, but it
// skips that check for a dependency fetched from a git host ("skipping
// integrity check for git dependency"), so an integrity field on such an
// entry vouches for nothing. npm fetches it as a tarball of the pinned
// commit, and nothing checks that the tarball is that commit's content.
// Each such package's fetched tree must therefore match the digest recorded
// when it was last reviewed (reviewed-git-packages.json), before any of its
// code runs or any of its files ship.

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const REVIEWED = new URL('./reviewed-git-packages.json', import.meta.url);

// The packages npm fetched from a git host, with the commit each is pinned to.
export function gitPackages(packages) {
  return Object.entries(packages)
    .filter(([name, entry]) => name && /^git[+:]/.test(entry.resolved ?? ''))
    .map(([name, entry]) => ({
      name: name.replace(/^node_modules\//, ''),
      commit: entry.resolved.split('#')[1] ?? '',
    }));
}

// A digest of every file under `dir`, apart from the `built` directory a
// package's own build writes. Anything other than a file or a directory is
// refused, as a link could point the build at content outside the tree.
export function treeDigest(dir, built) {
  const listing = [];
  const walk = (relative) => {
    for (const entry of fs.readdirSync(path.join(dir, relative), { withFileTypes: true })) {
      const name = relative ? `${relative}/${entry.name}` : entry.name;
      if (name === built) continue;
      if (entry.isDirectory()) walk(name);
      else if (entry.isFile()) {
        const bytes = fs.readFileSync(path.join(dir, name));
        listing.push(`${name}\0${crypto.createHash('sha256').update(bytes).digest('hex')}\n`);
      } else throw new Error(`${path.join(dir, name)} is neither a file nor a directory.`);
    }
  };
  walk('');
  listing.sort();
  return crypto.createHash('sha256').update(listing.join('')).digest('hex');
}

export function readLockfile(root) {
  const lockfile = path.join(root, 'package-lock.json');
  if (!fs.existsSync(lockfile)) {
    throw new Error(`${lockfile} is missing; the git dependencies cannot be checked.`);
  }
  return JSON.parse(fs.readFileSync(lockfile, 'utf8')).packages;
}

export function readReviewed() {
  return JSON.parse(fs.readFileSync(REVIEWED, 'utf8')).packages;
}

const RECORD = 'Review the fetched package, then run `npm run vendor:record` in services/ui.';

// Checks each installed git package against its reviewed tree. `fetched`:
// as npm fetched it, before its build has run, so the directory its build
// writes must not be there yet. Returns the names checked.
export function checkGitPackages(root, { fetched = false } = {}) {
  const reviewed = readReviewed();
  const checked = [];
  for (const { name, commit } of gitPackages(readLockfile(root))) {
    const dir = path.join(root, 'node_modules', ...name.split('/'));
    // A package that is not installed ships and runs nothing.
    if (!fs.existsSync(dir)) continue;
    const entry = reviewed[name];
    if (!entry?.tree) {
      throw new Error(`${name} is fetched from a git host and was never reviewed. ${RECORD}`);
    }
    if (entry.commit !== commit) {
      throw new Error(
        `${name} is pinned to ${commit}, but ${entry.commit} is the commit reviewed. ${RECORD}`,
      );
    }
    if (fetched && entry.built && fs.existsSync(path.join(dir, entry.built))) {
      throw new Error(`${name} arrived with ${entry.built}/, which its own build writes.`);
    }
    const digest = treeDigest(dir, entry.built);
    if (digest !== entry.tree) {
      throw new Error(
        `${name} at ${commit} has tree digest ${digest}, not the reviewed ${entry.tree}. ` +
          'Its fetched content is not what was reviewed.',
      );
    }
    checked.push(name);
  }
  return checked;
}

// Records each installed git package's commit and tree as reviewed, keeping
// the directory its build writes. Every git package must be installed.
export function recordGitPackages(root) {
  const reviewed = readReviewed();
  const packages = {};
  for (const { name, commit } of gitPackages(readLockfile(root))) {
    const dir = path.join(root, 'node_modules', ...name.split('/'));
    if (!fs.existsSync(dir)) throw new Error(`${name} is not installed; run npm ci first.`);
    const built = reviewed[name]?.built;
    packages[name] = { commit, tree: treeDigest(dir, built), ...(built ? { built } : {}) };
  }
  const document = JSON.parse(fs.readFileSync(REVIEWED, 'utf8'));
  document.packages = packages;
  fs.writeFileSync(REVIEWED, `${JSON.stringify(document, null, 2)}\n`);
  return packages;
}
