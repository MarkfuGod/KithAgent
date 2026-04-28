import type { FirstInsightState } from '../types';

export const firstInsightStorageKey = 'kith:first-insight:v1';

export const firstInsightStages = [
  { id: 'prepare', label: '准备本地服务', threshold: 8 },
  { id: 'answers', label: '整理你的回答', threshold: 20 },
  { id: 'browser', label: '聚合浏览信号', threshold: 36 },
  { id: 'files', label: '读取文件索引', threshold: 52 },
  { id: 'profile', label: '生成第一版画像', threshold: 70 },
  { id: 'memory', label: '写入可校正记忆', threshold: 86 },
  { id: 'refresh', label: '刷新今日建议', threshold: 100 },
] as const;

export type FirstInsightStageId = (typeof firstInsightStages)[number]['id'];

export function readFirstInsightState(): FirstInsightState {
  try {
    const saved = window.localStorage.getItem(firstInsightStorageKey);
    if (saved === 'completed') {
      return saved;
    }
  } catch {
    // localStorage can be unavailable in constrained WebViews; default to showing onboarding.
  }
  return 'pending';
}

export function persistFirstInsightState(state: FirstInsightState) {
  try {
    window.localStorage.setItem(firstInsightStorageKey, state);
  } catch {
    // Non-fatal: the current session state still prevents repeated prompts.
  }
}

export function describeFirstInsightStage(stageId: FirstInsightStageId, includeBrowserHistory: boolean) {
  if (stageId === 'prepare') {
    return '确认本地 daemon 可用，并创建这次画像任务。';
  }
  if (stageId === 'answers') {
    return '把你的角色、目标、兴趣和当前关注整理成可理解的结构。';
  }
  if (stageId === 'browser') {
    return includeBrowserHistory
      ? '只聚合浏览标题、域名和访问统计，不读取正文、Cookie、session 或 token。'
      : '已跳过浏览历史聚合，只使用你的回答和本地文件索引。';
  }
  if (stageId === 'files') {
    return '从已授权目录的索引里提取最近活动、文件夹模式和可行动线索。';
  }
  if (stageId === 'profile') {
    return '把显式回答和本地信号合成第一版个人画像。';
  }
  if (stageId === 'memory') {
    return '把画像拆成之后可以确认、纠正或隐藏的记忆。';
  }
  return '重新拉取今日、记忆和资料状态，让新画像马上可见。';
}
