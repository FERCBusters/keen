// Presentation metadata only. The server's field lists remain authoritative for
// which values are configuration and which values are encrypted credentials.
const labels = {
  host: 'IMAP server hostname',
  port: 'TLS port',
  folder: 'Mailbox folder',
  fetch_body: 'Fetch message body',
  poll_interval_minutes: 'Collection interval (minutes, 5–1,440 in steps of 5)',
  body_max_chars: 'Maximum body characters (1–65,536)',
  base_url: 'Base URL',
  username: 'Username',
  password: 'Password',
  header_name: 'Header name',
  header_value: 'Header value',
  user_agent: 'User agent',
  auth_mode: 'Authentication method',
  token: 'Access token',
  cookie: 'Authentication cookie',
  api_token: 'API token',
  api_key: 'API key',
  token_id: 'Token ID',
  token_secret: 'Token secret',
  provider: 'Provider',
  secret_header: 'Secret header name',
  secret: 'Shared secret',
  impersonate: 'User to impersonate',
  sa_json: 'Service account JSON',
  region: 'AWS region',
  aws_access_key_id: 'AWS access key ID',
  aws_secret_access_key: 'AWS secret access key',
  aws_session_token: 'AWS session token',
};

const groups = [
  { label: 'Account credentials', keys: ['username', 'password', 'api_token'] },
  { label: 'HTTP header', keys: ['header_name', 'header_value'] },
  { label: 'API credentials', keys: ['token_id', 'token_secret'] },
  { label: 'Webhook authentication', keys: ['secret_header', 'secret'] },
  {
    label: 'AWS credentials',
    keys: ['aws_access_key_id', 'aws_secret_access_key', 'aws_session_token'],
  },
];

export function sourceFieldLabel(key) {
  return labels[key] || key.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
}

export function sourceFieldGroups(spec, hiddenFields = []) {
  const fields = new Map();
  for (const [keys, secret] of [
    [spec?.fields || [], false],
    [spec?.credential_fields || [], true],
  ]) {
    for (const key of keys) {
      if (!hiddenFields.includes(key)) fields.set(key, { key, secret });
    }
  }
  const result = [];
  while (fields.size) {
    const key = fields.keys().next().value;
    const group = groups.find((candidate) => candidate.keys.includes(key));
    const members = (group?.keys || [key]).filter((member) => fields.has(member));
    result.push({
      label: members.length > 1 ? group?.label : null,
      fields: members.map((member) => fields.get(member)),
    });
    members.forEach((member) => fields.delete(member));
  }
  return result;
}
