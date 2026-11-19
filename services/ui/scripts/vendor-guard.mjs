// Which shipped files the lockfile cannot vouch for.
//
// npm records no checksum for a dependency fetched from a git host, so the
// lockfile cannot detect a fetch that returns different content. Every file
// the build ships from such a package must match a digest recorded when it was
// reviewed (reviewed-vendor-files.json); the build applies this to each file
// it copies, whichever copy step names it.

// Packages fetched from a git host whose lockfile entry carries no checksum.
export function unchecksummed(packages) {
  return Object.entries(packages)
    .filter(([name, entry]) => name && entry.resolved?.startsWith('git') && !entry.integrity)
    .map(([name]) => name.replace(/^node_modules\//, ''));
}

// The package a path under node_modules belongs to, scoped or not.
export function packageOf(name) {
  const parts = name.split('/');
  return parts[0].startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
}
