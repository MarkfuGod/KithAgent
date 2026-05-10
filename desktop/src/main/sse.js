export function parseSseEvent(chunk) {
  const lines = String(chunk || '').split('\n');
  let type = 'message';
  let data = '';
  for (const line of lines) {
    if (line.startsWith('event:')) {
      type = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      data += line.slice(5).trim();
    }
  }
  if (!data) {
    return null;
  }
  try {
    return { type, data: JSON.parse(data) };
  } catch {
    return { type, data };
  }
}
