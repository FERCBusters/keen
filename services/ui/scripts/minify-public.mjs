import fs from "node:fs";
import path from "node:path";
import { build } from "esbuild";

const root = process.cwd();
const publicDir = path.join(root, "public");

function shouldMinify(filePath) {
  const rel = path.relative(publicDir, filePath).replaceAll("\\", "/");
  if (!rel.endsWith(".js")) return false;
  if (rel.startsWith("vendor/")) return false;
  if (rel.endsWith(".min.js")) return false;
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
const jsFiles = allFiles.filter(shouldMinify);

for (const file of jsFiles) {
  // Minify each file in place (no bundling)
  await build({
    entryPoints: [file],
    outfile: file,
    minify: true,
    sourcemap: false,
    allowOverwrite: true,
    bundle: false,
    format: "esm",
    platform: "browser",
    legalComments: "eof"
  });
}

console.log(`Minified ${jsFiles.length} JS files under public/ (excluding public/vendor/)`);
