// Records each dependency fetched from a git host, at its pinned commit, as
// reviewed. Run it after `npm ci` and after reviewing what changed in each.
import { recordGitPackages } from './vendor-guard.mjs';

for (const [name, entry] of Object.entries(recordGitPackages(process.cwd()))) {
  console.log(`${name} ${entry.commit} ${entry.tree}`);
}
