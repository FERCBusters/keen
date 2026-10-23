// Apply the same palette preference as Keen before the handbook is painted.
(() => {
  const palettes = ['purple', 'ocean', 'forest', 'sunset', 'rose', 'slate', 'teal'];
  let saved = 'purple';
  try {
    saved = localStorage.getItem('keen_theme') || saved;
  } catch {}
  const dark = saved.endsWith('-dark');
  const palette = dark ? saved.slice(0, -5) : saved;
  document.documentElement.dataset.theme = palettes.includes(palette) ? palette : 'purple';
  document.documentElement.dataset.bsTheme = dark && palettes.includes(palette) ? 'dark' : 'light';
})();
