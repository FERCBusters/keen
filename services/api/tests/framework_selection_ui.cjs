// Exercise the real module and DOM harness, including pagination and selection.
// Kept as the backend suite's entry point; no extracted functions or DOM stubs.
const { execFileSync } = require('node:child_process');
const path = require('node:path');
execFileSync(process.execPath, [
  '--experimental-vm-modules', '--test',
  '--test-name-pattern=organisation|catalogue|framework cards',
  path.resolve(__dirname, '../../ui/tests/framework-editor.test.mjs'),
], { stdio: 'inherit' });
