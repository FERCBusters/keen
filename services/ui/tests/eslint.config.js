// Flags HTML insertion that bypasses esc() or the shared sanitiser; run it with
// `bash lint-ui.sh` from the repository root, which file patterns are relative
// to. Sites that predate the check are listed in
// eslint-suppressions.json, which new code leaves alone; see TESTING.md.
import noUnsanitized from 'eslint-plugin-no-unsanitized';

// Functions whose result is safe to insert as HTML: esc() escapes text, and the
// rich-text helpers escape plain text or pass HTML through the allowlist
// sanitiser.
const escape = {
  methods: [
    'esc',
    'sanitizeRichTextHtml',
    'renderRichTextContent',
    'plainTextToRichTextHtml',
    'richTextEditorValue',
  ],
};

const rules = {
  'no-unsanitized/method': ['error', { escape }],
  'no-unsanitized/property': ['error', { escape }],
};

export default [
  {
    linterOptions: { reportUnusedDisableDirectives: 'error' },
  },
  {
    files: ['services/ui/public/**/*.js'],
    ignores: ['services/ui/public/vendor/**', 'services/ui/public/help/**'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'module' },
    plugins: { 'no-unsanitized': noUnsanitized },
    rules,
  },
  {
    // The documentation's scripts are classic scripts.
    files: ['services/ui/public/help/**/*.js'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'script' },
    plugins: { 'no-unsanitized': noUnsanitized },
    rules,
  },
];
