export const DEFAULT_CHAT_PATH = '/chat/completions';

export function normalizeApiBaseUrl(rawHost, rawPath = DEFAULT_CHAT_PATH) {
  let host = String(rawHost || '').trim().replace(/\/+$/, '');
  const path = String(rawPath || '').trim();
  if (!host) return '';

  const stripCompletionEndpoint = (value) => {
    let base = value.replace(/\/+$/, '');
    for (const suffix of ['/chat/completions', '/responses', '/completions']) {
      if (base.endsWith(suffix)) {
        base = base.slice(0, -suffix.length).replace(/\/+$/, '');
        break;
      }
    }
    return base;
  };

  host = stripCompletionEndpoint(host);
  const normalizedPath = path.replace(/^\/+/, '').replace(/\/+$/, '');
  if (normalizedPath && path !== DEFAULT_CHAT_PATH && !host.endsWith(`/${normalizedPath}`)) {
    host = stripCompletionEndpoint(`${host}/${normalizedPath}`);
  }
  return stripCompletionEndpoint(host);
}
