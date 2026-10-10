// Presentation-only setup profiles. Adapters keep their existing input schema.
// Preserve imported multi-feed sources until an administrator splits them.
export function sourceSetupProfile(source, connection = null) {
  if (source !== 'rss' || (connection?.inputs?.feeds?.length || 0) > 1) return null;
  return {
    inlineInput: true,
    hiddenFields: ['base_url'],
    hint: 'Enter one feed URL and its optional credentials. Save the source, then collect evidence or add a mapping rule.',
    input: {label: 'Feed URL', value: connection?.inputs?.feeds?.[0]?.url || ''},
    prepare(value, configuration, inputs) {
      const url = new URL(value.trim());
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.hash) {
        throw new Error('Use an HTTP or HTTPS feed URL without embedded credentials or a fragment.');
      }
      // The RSS transport binds credentials to this origin to prevent leakage.
      configuration.base_url = url.origin;
      return {...inputs, feeds: [{...inputs.feeds?.[0], url: value.trim()}]};
    },
  };
}
