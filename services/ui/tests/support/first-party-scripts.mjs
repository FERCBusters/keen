// The first-party browser scripts under services/ui/public, vendored code
// excluded, for checks that must hold in every one of them.
import { readdir, readFile } from 'node:fs/promises';

export const publicRoot = new URL('../../public/', import.meta.url);

/** Each first-party script as { path, source }, path relative to public/. */
export async function firstPartyScripts(dir = publicRoot) {
  const found = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const url = new URL(entry.name + (entry.isDirectory() ? '/' : ''), dir);
    if (entry.isDirectory()) {
      if (entry.name !== 'vendor') found.push(...(await firstPartyScripts(url)));
    } else if (entry.name.endsWith('.js')) {
      found.push({
        path: url.href.slice(publicRoot.href.length),
        source: await readFile(url, 'utf8'),
      });
    }
  }
  return found.sort((a, b) => a.path.localeCompare(b.path));
}
