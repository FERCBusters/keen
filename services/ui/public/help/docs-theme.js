// Apply the same palette preference as Keen before the handbook is painted.
(() => {
  const palettes = ['ink', 'ocean', 'forest', 'sunset', 'rose', 'slate', 'teal', 'purple'];
  let saved = 'ink';
  try {
    saved = localStorage.getItem('keen_theme') || saved;
  } catch {}
  const dark = saved.endsWith('-dark');
  const palette = dark ? saved.slice(0, -5) : saved;
  document.documentElement.dataset.theme = palettes.includes(palette) ? palette : 'ink';
  document.documentElement.dataset.bsTheme = dark && palettes.includes(palette) ? 'dark' : 'light';
})();
