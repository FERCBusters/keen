import fs from 'node:fs';
import path from 'node:path';
import { build } from 'esbuild';

const root = process.cwd();
const sourceDir = path.join(root, 'public');
const publicDir = path.join(root, 'dist');
fs.rmSync(publicDir, { recursive: true, force: true });
fs.cpSync(sourceDir, publicDir, { recursive: true });

function shouldMinify(filePath) {
  const rel = path.relative(publicDir, filePath).replaceAll('\\', '/');
  if (!/\.(js|css)$/.test(rel)) return false;
  if (rel.startsWith('vendor/')) return false;
  if (/\.min\.(js|css)$/.test(rel)) return false;
  return true;
}

async function walk(dir, files = []) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const e of entries) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) await walk(p, files);
    else files.push(p);
  }
  return files;
}

const allFiles = await walk(publicDir);
const assets = allFiles.filter(shouldMinify);

for (const file of assets) {
  // Minify generated output only; preserve readable source files.
  await build({
    entryPoints: [file],
    outfile: file,
    minify: true,
    sourcemap: false,
    allowOverwrite: true,
    bundle: false,
    ...(file.endsWith('.js') ? { format: 'esm' } : {}),
    platform: 'browser',
    legalComments: 'eof',
  });
}

console.log(`Minified ${assets.length} JS/CSS files under dist/ (excluding public/vendor/)`);
