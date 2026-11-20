// Checks each dependency fetched from a git host against the tree recorded
// when it was reviewed. With --fetched, as npm fetched it: the image build
// runs this after `npm ci --ignore-scripts` and before any of their code runs.
import { checkGitPackages } from './vendor-guard.mjs';

const checked = checkGitPackages(process.cwd(), { fetched: process.argv.includes('--fetched') });
console.log(`Git dependencies match their reviewed trees: ${checked.join(', ') || 'none'}`);
