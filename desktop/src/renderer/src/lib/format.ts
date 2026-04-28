export function compactJson(value: unknown) {
  if (!value || typeof value !== 'object') {
    return '';
  }
  return JSON.stringify(value, null, 2);
}

export function listFromText(value: string) {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function formatBytes(bytes = 0) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatRelativeTime(timestamp = 0) {
  if (!timestamp) return '暂无记录';
  const seconds = Math.max(0, Date.now() / 1000 - timestamp);
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} 分钟前`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时前`;
  return `${Math.round(seconds / 86400)} 天前`;
}

export function formatElapsed(seconds: number) {
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return remainingSeconds ? `${minutes} 分 ${remainingSeconds} 秒` : `${minutes} 分钟`;
}

export function describeFactStatus(status: ProfileFact['status']) {
  if (status === 'confirmed') return '已确认';
  if (status === 'rejected') return '不准确';
  if (status === 'hidden') return '已隐藏';
  return '推断';
}

export function valueText(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '暂无';
  }
  if (Array.isArray(value)) {
    return value.map(valueText).join('、');
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}
