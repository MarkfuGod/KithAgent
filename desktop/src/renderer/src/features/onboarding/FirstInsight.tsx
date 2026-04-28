import { type MouseEvent, useEffect, useMemo, useRef, useState } from 'react';
import { formatElapsed } from '../../lib/format';
import { describeFirstInsightStage, firstInsightStages } from '../../lib/firstInsight';
import type { FirstInsightState, OnboardingDraft } from '../../types';

const firstInsightPresets = {
  roles: ['创作者', '开发者', '学生', '研究者', '产品经理', '设计师', '创业者', '内容运营'],
  goals: ['整理本地文件', '理解我的兴趣', '提醒下一步', '形成长期记忆', '辅助学习研究', '管理项目推进', '发现可清理资料', '生成今日行动建议'],
  interests: ['AI 工具', '本地自动化', '产品设计', '编程开发', '知识管理', '效率系统', '视觉创作', '商业分析', '游戏/互动', '视频内容'],
  currentFocus: ['把 Kith 做成真正懂我的本地助理', '完成一个产品原型', '准备考试/学习计划', '梳理个人知识库', '推进当前项目', '整理下载和桌面文件', '找到今天最该做的事'],
};

function parsePresetValues(value: string) {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function FirstInsightModal({
  draft,
  firstInsightState,
  isGenerating,
  onAskKith,
  onClose,
  onDismiss,
  onDraftChange,
  onReviewMemory,
  onRunOnboarding,
  result,
}: {
  draft: OnboardingDraft;
  firstInsightState: FirstInsightState;
  isGenerating: boolean;
  onAskKith: (prompt: string) => void;
  onClose: () => void;
  onDismiss: () => void;
  onDraftChange: (draft: OnboardingDraft) => void;
  onReviewMemory: () => void;
  onRunOnboarding: () => Promise<void>;
  result: OnboardingBootstrapResult | null;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const isComplete = firstInsightState === 'completed' || Boolean(result);
  const [isClosing, setIsClosing] = useState(false);

  const closeWithAnimation = useMemo(
    () => () => {
      if (isGenerating) return;
      setIsClosing(true);
      window.setTimeout(onClose, 220);
    },
    [isGenerating, onClose],
  );

  useEffect(() => {
    if (!isComplete || isGenerating) {
      return undefined;
    }
    const timer = window.setTimeout(closeWithAnimation, 3200);
    return () => window.clearTimeout(timer);
  }, [closeWithAnimation, isComplete, isGenerating]);

  useEffect(() => {
    dialogRef.current?.querySelector<HTMLElement>('button, input, textarea, select')?.focus();
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !isGenerating) {
        if (isComplete) {
          closeWithAnimation();
        } else {
          onDismiss();
        }
      }
      if (event.key !== 'Tab' || !dialogRef.current) {
        return;
      }

      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [closeWithAnimation, isComplete, isGenerating, onDismiss]);

  function handleBackdropMouseDown(event: MouseEvent<HTMLDivElement>) {
    if (event.currentTarget === event.target && !isGenerating) {
      if (isComplete) {
        closeWithAnimation();
      } else {
        onDismiss();
      }
    }
  }

  return (
    <div className={`first-insight-overlay ${isClosing ? 'closing' : ''}`} onMouseDown={handleBackdropMouseDown}>
      <section
        aria-describedby="first-insight-description"
        aria-labelledby="first-insight-title"
        aria-modal="true"
        className={`first-insight-dialog ${isClosing ? 'closing' : ''}`}
        ref={dialogRef}
        role="dialog"
      >
        <div className="first-insight-heading">
          <div>
            <p className="eyebrow">First launch setup</p>
            <h2 id="first-insight-title">{isComplete ? '第一版画像已完成，正在回到今日页。' : '先用 5 分钟，让 Kith 真正开始认识你。'}</h2>
            <p id="first-insight-description">
              这不是注册流程，而是一次本地、可校正的首次画像。完成后今日页会直接出现更贴近你的建议。
            </p>
          </div>
          <button className="ghost" disabled={isGenerating} onClick={isComplete ? closeWithAnimation : onDismiss} type="button">
            {isComplete ? '完成' : '稍后'}
          </button>
        </div>
        <FirstInsightPanel
          draft={draft}
          firstInsightState={firstInsightState}
          isGenerating={isGenerating}
          mode="modal"
          onAskKith={onAskKith}
          onClose={closeWithAnimation}
          onDraftChange={onDraftChange}
          onReviewMemory={onReviewMemory}
          onRunOnboarding={onRunOnboarding}
          result={result}
        />
      </section>
    </div>
  );
}

export function FirstInsightPanel({
  draft,
  firstInsightState,
  isGenerating,
  mode = 'card',
  onAskKith,
  onClose,
  onDraftChange,
  onReviewMemory,
  onRunOnboarding,
  result,
}: {
  draft: OnboardingDraft;
  firstInsightState: FirstInsightState;
  isGenerating: boolean;
  mode?: 'card' | 'modal' | 'compact';
  onAskKith: (prompt: string) => void;
  onClose: () => void;
  onDraftChange: (draft: OnboardingDraft) => void;
  onReviewMemory: () => void;
  onRunOnboarding: () => Promise<void>;
  result: OnboardingBootstrapResult | null;
}) {
  const isComplete = firstInsightState === 'completed' || Boolean(result);

  return (
    <section className={`onboarding-card first-insight-panel ${mode}`}>
      <div className="onboarding-copy">
        <p className="eyebrow">5-8 minute first insight</p>
        <h3>{isComplete ? '第一版画像已经准备好。' : '先让 Kith 在几分钟内“有点懂你”。'}</h3>
        <p>
          这一步只读取你的回答、已授权文件索引，以及可选的 Chrome/Edge/Brave/Arc 浏览标题与域名聚合。
          不碰 cookies、session、token 或网页正文。
        </p>
        <div className="timeline">
          <span>0-1m 回答问题</span>
          <span>1-3m 聚合浏览信号</span>
          <span>3-5m 生成画像</span>
          <span>5-8m 出现建议</span>
        </div>
      </div>

      <div className="onboarding-form">
        {isComplete && result ? (
          <FirstInsightCompletion
            onAskKith={onAskKith}
            onClose={onClose}
            onReviewMemory={onReviewMemory}
            result={result}
          />
        ) : (
          <>
            <PresetField
              label="你的角色"
              options={firstInsightPresets.roles}
              placeholder="选择几个，或点“其他，自填”补充..."
              value={draft.roles}
              onChange={(roles) => onDraftChange({ ...draft, roles })}
            />
            <PresetField
              label="最近目标"
              multiline
              options={firstInsightPresets.goals}
              placeholder="选择目标，或写下你自己的目标..."
              value={draft.goals}
              onChange={(goals) => onDraftChange({ ...draft, goals })}
            />
            <PresetField
              label="兴趣关键词"
              options={firstInsightPresets.interests}
              placeholder="选择兴趣，或直接输入关键词..."
              value={draft.interests}
              onChange={(interests) => onDraftChange({ ...draft, interests })}
            />
            <PresetField
              label="当前关注"
              options={firstInsightPresets.currentFocus}
              placeholder="选择当前关注，或自己填写..."
              value={draft.current_focus}
              onChange={(current_focus) => onDraftChange({ ...draft, current_focus })}
            />
            <div className="onboarding-options">
              <label>
                <span>建议频率</span>
                <select
                  value={draft.suggestion_cadence}
                  onChange={(event) => onDraftChange({ ...draft, suggestion_cadence: event.target.value })}
                >
                  <option value="daily">每天轻提示</option>
                  <option value="weekly">每周总结</option>
                  <option value="quiet">尽量安静</option>
                </select>
              </label>
              <label className="browser-toggle">
                <input
                  checked={draft.include_browser_history}
                  onChange={(event) => onDraftChange({ ...draft, include_browser_history: event.target.checked })}
                  type="checkbox"
                />
                <span>允许聚合浏览标题、域名和访问统计</span>
              </label>
            </div>
            <button className="primary wide" disabled={isGenerating} onClick={() => onRunOnboarding()} type="button">
              {isGenerating ? '正在生成第一版画像...' : '开始 5-8 分钟画像'}
            </button>
          </>
        )}

        {(isGenerating || result) && (
          <FirstInsightProgress
            includeBrowserHistory={draft.include_browser_history}
            isGenerating={isGenerating}
            result={result}
          />
        )}
      </div>
    </section>
  );
}

function PresetField({
  label,
  multiline = false,
  onChange,
  options,
  placeholder,
  value,
}: {
  label: string;
  multiline?: boolean;
  onChange: (value: string) => void;
  options: string[];
  placeholder: string;
  value: string;
}) {
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const selectedValues = useMemo(() => parsePresetValues(value), [value]);
  const selectedSet = useMemo(() => new Set(selectedValues), [selectedValues]);

  function toggleOption(option: string) {
    const nextValues = selectedSet.has(option)
      ? selectedValues.filter((item) => item !== option)
      : [...selectedValues, option];
    onChange(nextValues.join('，'));
  }

  function focusCustomInput() {
    inputRef.current?.focus();
  }

  return (
    <label className="preset-field">
      <span>{label}</span>
      <div className="preset-options" aria-label={`${label}预设选项`}>
        {options.map((option) => (
          <button
            className={selectedSet.has(option) ? 'preset-chip selected' : 'preset-chip'}
            key={option}
            onClick={() => toggleOption(option)}
            type="button"
          >
            {option}
          </button>
        ))}
        <button className="preset-chip custom" onClick={focusCustomInput} type="button">
          其他，自填
        </button>
      </div>
      {multiline ? (
        <textarea
          ref={(node) => {
            inputRef.current = node;
          }}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          rows={2}
        />
      ) : (
        <input
          ref={(node) => {
            inputRef.current = node;
          }}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
        />
      )}
    </label>
  );
}

function FirstInsightCompletion({
  onAskKith,
  onClose,
  onReviewMemory,
  result,
}: {
  onAskKith: (prompt: string) => void;
  onClose: () => void;
  onReviewMemory: () => void;
  result: OnboardingBootstrapResult;
}) {
  const firstAction = result.next_actions?.[0] || '基于第一版画像，帮我规划今天最值得做的三件事。';

  return (
    <div className="onboarding-result complete">
      <strong>第一版认知已生成</strong>
      <small>
        {result.topics.length} 个主题 · {result.profile_facts.length} 条记忆 ·
        浏览记录 {result.browser_history.enabled ? `${result.browser_history.entries_count} 条聚合` : '未启用'}
      </small>
      <div className="next-actions">
        {(result.next_actions || []).slice(0, 3).map((action) => (
          <button className="ghost" key={action} onClick={() => onAskKith(action)} type="button">
            {action}
          </button>
        ))}
        <button className="primary" onClick={() => onAskKith(firstAction)} type="button">
          去问 Kith
        </button>
        <button className="ghost" onClick={onReviewMemory} type="button">
          校正记忆
        </button>
        <button className="ghost" onClick={onClose} type="button">
          完成，回到今日
        </button>
      </div>
    </div>
  );
}

function FirstInsightProgress({
  includeBrowserHistory,
  isGenerating,
  result,
}: {
  includeBrowserHistory: boolean;
  isGenerating: boolean;
  result: OnboardingBootstrapResult | null;
}) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    if (!isGenerating) {
      setElapsedSeconds(0);
      return undefined;
    }

    setElapsedSeconds(0);
    const timer = window.setInterval(() => {
      setElapsedSeconds((seconds) => seconds + 1);
    }, 1000);

    return () => window.clearInterval(timer);
  }, [isGenerating]);

  const progress = useMemo(() => {
    if (result && !isGenerating) {
      return 100;
    }
    if (!isGenerating) {
      return 0;
    }
    if (elapsedSeconds < 60) {
      return Math.round(8 + (elapsedSeconds / 60) * 34);
    }
    if (elapsedSeconds < 180) {
      return Math.round(42 + ((elapsedSeconds - 60) / 120) * 30);
    }
    return Math.min(84, Math.round(72 + Math.log1p(elapsedSeconds - 180) * 2.8));
  }, [elapsedSeconds, isGenerating, result]);

  const activeStageIndex = useMemo(() => {
    let index = 0;
    firstInsightStages.forEach((stage, stageIndex) => {
      if (progress >= stage.threshold) {
        index = stageIndex;
      }
    });
    return index;
  }, [progress]);

  const activeStage = firstInsightStages[activeStageIndex];
  const isSettling = isGenerating && !result && elapsedSeconds >= 180;
  const activityDetail = isSettling
    ? '后端仍在等待模型返回或写入本地记忆。最后一段只在真实完成后点亮，不再用假进度填满。'
    : describeFirstInsightStage(activeStage.id, includeBrowserHistory);
  const activeStageLabel = isSettling ? '等待真实结果' : activeStage.label;

  return (
    <div aria-live="polite" className="first-insight-progress">
      <div className="progress-copy">
        <div>
          <span>{isSettling ? '后台仍在运行' : isGenerating ? '正在处理' : '已完成'}</span>
          <strong>{activeStageLabel}</strong>
          <small>{activityDetail}</small>
        </div>
        <em>{isGenerating ? `已等待 ${formatElapsed(elapsedSeconds)}` : '100%'}</em>
      </div>
      <div
        aria-label={`画像生成进度 ${progress}%`}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={progress}
        className="progress-track"
        role="progressbar"
      >
        <div className="progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="progress-steps">
        {firstInsightStages.map((stage, index) => (
          <span
            className={index < activeStageIndex || progress === 100 ? 'done' : index === activeStageIndex ? 'active' : ''}
            key={stage.id}
          >
            {stage.label}
          </span>
        ))}
      </div>
    </div>
  );
}
