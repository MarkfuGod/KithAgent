import type { Notice } from '../types';

export function ToastStack({ notices, onDismiss }: { notices: Notice[]; onDismiss: (id: string) => void }) {
  if (!notices.length) {
    return null;
  }

  return (
    <div aria-live="polite" className="toast-stack">
      {notices.map((notice) => (
        <div className={`toast ${notice.tone}`} key={notice.id}>
          <span>{notice.message}</span>
          <button aria-label="关闭提示" onClick={() => onDismiss(notice.id)} type="button">
            关闭
          </button>
        </div>
      ))}
    </div>
  );
}
