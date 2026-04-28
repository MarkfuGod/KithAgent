import { useEffect, useMemo, useState } from 'react';
import { formatBytes } from '../../lib/format';

const sourcePresets = [
  { label: '文档 Documents', path: '~/Documents', hint: '长期资料、笔记、PDF' },
  { label: '桌面 Desktop', path: '~/Desktop', hint: '最近正在处理的文件' },
  { label: '下载 Downloads', path: '~/Downloads', hint: '下载资料和待整理文件' },
  { label: '项目 Projects', path: '~/Projects', hint: '代码和项目文件' },
  { label: '学习资料', path: '~/Documents/学习资料', hint: '课程、论文、读书资料' },
  { label: '当前 Kith 项目', path: '~/Downloads/KithAgent', hint: '调试当前应用' },
];

export type PrivacyViewProps = {
  isTriageReviewLoading: boolean;
  isSourcesSaving: boolean;
  onLoadTriageFiles: (prefix: string) => Promise<{ files: TriageFile[]; total_files: number }>;
  onSaveSources: () => Promise<void>;
  onSourceDraftChange: (value: string) => void;
  onRefreshTriageReview: () => Promise<void>;
  onTriageDecision: (prefix: string | string[], status: 'high' | 'skip') => Promise<void>;
  sourceDraft: string;
  sourceSaveStatus: string;
  sources: SourceSettings;
  triageSyncStatus: string;
  triageClusters: TriageCluster[];
};

type TriageFileListState = {
  files: TriageFile[];
  totalFiles: number;
  loading: boolean;
  error: string;
  selectedPaths: Set<string>;
};

export function PrivacyView({
  isTriageReviewLoading,
  isSourcesSaving,
  onLoadTriageFiles,
  onSaveSources,
  onSourceDraftChange,
  onRefreshTriageReview,
  onTriageDecision,
  sourceDraft,
  sourceSaveStatus,
  sources,
  triageSyncStatus,
  triageClusters,
}: PrivacyViewProps) {
  const selectedPaths = parseSourceDraft(sourceDraft);
  const cleanupClusters = useMemo(
    () => triageClusters.filter((cluster) => cluster.recommendation === 'exclude' || cluster.recommendation === 'review').slice(0, 16),
    [triageClusters],
  );
  const recommendedSkipPrefixes = useMemo(
    () => cleanupClusters.filter((cluster) => cluster.recommendation === 'exclude').map((cluster) => cluster.prefix),
    [cleanupClusters],
  );
  const [selectedSkipPrefixes, setSelectedSkipPrefixes] = useState<Set<string>>(() => new Set(recommendedSkipPrefixes));
  const [expandedPrefixes, setExpandedPrefixes] = useState<Set<string>>(() => new Set());
  const [clusterFiles, setClusterFiles] = useState<Record<string, TriageFileListState>>({});
  const selectedSkipClusters = cleanupClusters.filter((cluster) => selectedSkipPrefixes.has(cluster.prefix));
  const reviewCount = cleanupClusters.filter((cluster) => cluster.recommendation === 'review').length;

  useEffect(() => {
    setSelectedSkipPrefixes(new Set(recommendedSkipPrefixes));
  }, [recommendedSkipPrefixes]);

  useEffect(() => {
    const availablePrefixes = new Set(cleanupClusters.map((cluster) => cluster.prefix));
    setExpandedPrefixes((current) => new Set([...current].filter((prefix) => availablePrefixes.has(prefix))));
    setClusterFiles((current) => Object.fromEntries(Object.entries(current).filter(([prefix]) => availablePrefixes.has(prefix))));
  }, [cleanupClusters]);

  function togglePreset(path: string) {
    const normalizedPath = path.trim();
    const nextPaths = selectedPaths.includes(normalizedPath)
      ? selectedPaths.filter((item) => item !== normalizedPath)
      : [...selectedPaths, normalizedPath];
    onSourceDraftChange(nextPaths.join('\n'));
  }

  function useRecommendedSet() {
    onSourceDraftChange(['~/Documents', '~/Desktop', '~/Downloads'].join('\n'));
  }

  function toggleSkipPrefix(prefix: string) {
    setSelectedSkipPrefixes((current) => {
      const next = new Set(current);
      if (next.has(prefix)) {
        next.delete(prefix);
      } else {
        next.add(prefix);
      }
      return next;
    });
  }

  function toggleExpandedPrefix(prefix: string) {
    const shouldExpand = !expandedPrefixes.has(prefix);
    setExpandedPrefixes((current) => {
      const next = new Set(current);
      if (next.has(prefix)) {
        next.delete(prefix);
      } else {
        next.add(prefix);
      }
      return next;
    });
    if (shouldExpand && !clusterFiles[prefix]?.files.length && !clusterFiles[prefix]?.loading) {
      void loadClusterFiles(prefix);
    }
  }

  async function loadClusterFiles(prefix: string) {
    setClusterFiles((current) => ({
      ...current,
      [prefix]: {
        files: current[prefix]?.files || [],
        totalFiles: current[prefix]?.totalFiles || 0,
        loading: true,
        error: '',
        selectedPaths: current[prefix]?.selectedPaths || new Set(),
      },
    }));

    try {
      const result = await onLoadTriageFiles(prefix);
      setClusterFiles((current) => {
        const previousSelection = current[prefix]?.selectedPaths || new Set<string>();
        const availablePaths = new Set(result.files.map((file) => file.path));
        return {
          ...current,
          [prefix]: {
            files: result.files,
            totalFiles: result.total_files,
            loading: false,
            error: '',
            selectedPaths: new Set([...previousSelection].filter((path) => availablePaths.has(path))),
          },
        };
      });
    } catch (error) {
      setClusterFiles((current) => ({
        ...current,
        [prefix]: {
          files: current[prefix]?.files || [],
          totalFiles: current[prefix]?.totalFiles || 0,
          loading: false,
          error: errorMessage(error),
          selectedPaths: current[prefix]?.selectedPaths || new Set(),
        },
      }));
    }
  }

  function toggleFileSelection(prefix: string, path: string) {
    setClusterFiles((current) => {
      const list = current[prefix];
      if (!list) return current;
      const selectedPaths = new Set(list.selectedPaths);
      if (selectedPaths.has(path)) {
        selectedPaths.delete(path);
      } else {
        selectedPaths.add(path);
      }
      return { ...current, [prefix]: { ...list, selectedPaths } };
    });
  }

  function selectVisibleFiles(prefix: string) {
    setClusterFiles((current) => {
      const list = current[prefix];
      if (!list) return current;
      return { ...current, [prefix]: { ...list, selectedPaths: new Set(list.files.map((file) => file.path)) } };
    });
  }

  function clearSelectedFiles(prefix: string) {
    setClusterFiles((current) => {
      const list = current[prefix];
      if (!list) return current;
      return { ...current, [prefix]: { ...list, selectedPaths: new Set() } };
    });
  }

  async function applySelectedFiles(prefix: string, status: 'high' | 'skip') {
    const selectedPaths = [...(clusterFiles[prefix]?.selectedPaths || new Set<string>())];
    if (!selectedPaths.length) return;
    await onTriageDecision(selectedPaths, status);
    clearSelectedFiles(prefix);
  }

  function selectAllCleanupClusters() {
    setSelectedSkipPrefixes(new Set(cleanupClusters.map((cluster) => cluster.prefix)));
  }

  function resetRecommendedSkips() {
    setSelectedSkipPrefixes(new Set(recommendedSkipPrefixes));
  }

  async function applySelectedSkips() {
    await onTriageDecision([...selectedSkipPrefixes], 'skip');
  }

  return (
    <section className="privacy-page">
      <article className="panel trust-panel">
        <p className="eyebrow">资料权限</p>
        <h3>Kith 只理解你允许的范围。</h3>
        <p>
          每行一个目录。建议从 Documents、Desktop、学习资料目录开始。浏览历史只会在 First Insight 中按标题、域名和统计聚合。
        </p>
        <div className="source-presets" aria-label="常用索引目录预设">
          {sourcePresets.map((preset) => (
            <button
              className={selectedPaths.includes(preset.path) ? 'source-preset selected' : 'source-preset'}
              key={preset.path}
              onClick={() => togglePreset(preset.path)}
              type="button"
            >
              <strong>{preset.label}</strong>
              <small>{preset.path}</small>
              <em>{preset.hint}</em>
            </button>
          ))}
        </div>
        <button className="ghost source-recommended" onClick={useRecommendedSet} type="button">
          使用推荐组合：Documents + Desktop + Downloads
        </button>
        <textarea value={sourceDraft} onChange={(event) => onSourceDraftChange(event.target.value)} rows={8} />
        <button className="primary" disabled={isSourcesSaving} onClick={onSaveSources} type="button">
          {isSourcesSaving ? '保存中...' : '保存资料范围'}
        </button>
        {sourceSaveStatus ? <small className="inline-operation-status">{sourceSaveStatus}</small> : null}
        <small>当前已配置 {sources.watch_paths?.length || 0} 个目录。保存后可能需要重新扫描或重启服务。</small>
      </article>

      <article className="panel trust-panel triage-review-panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">索引分诊</p>
            <h3>需要你确认的目录</h3>
          </div>
          <button className="ghost" disabled={isTriageReviewLoading} onClick={onRefreshTriageReview} type="button">
            {isTriageReviewLoading ? '同步中...' : '同步'}
          </button>
        </div>
        {triageSyncStatus ? <small className="inline-operation-status">{triageSyncStatus}</small> : null}
        <p>
          像文件清理工具一样处理：Kith 认为明显是工具、缓存、依赖的目录会默认打勾；边界目录默认不勾，需要你确认。
          点击“排除已勾选”后，这些目录会被标成 skip，不再进入总结。
        </p>
        {cleanupClusters.length ? (
          <>
            <div className="triage-cleaner-summary">
              <div>
                <strong>{recommendedSkipPrefixes.length}</strong>
                <small>默认勾选</small>
              </div>
              <div>
                <strong>{reviewCount}</strong>
                <small>需要确认</small>
              </div>
              <div>
                <strong>{selectedSkipClusters.length}</strong>
                <small>本次将排除</small>
              </div>
            </div>
            <div className="triage-cleaner-actions">
              <button className="primary" disabled={isTriageReviewLoading || selectedSkipPrefixes.size === 0} onClick={applySelectedSkips} type="button">
                排除已勾选
              </button>
              <button className="ghost" disabled={isTriageReviewLoading} onClick={resetRecommendedSkips} type="button">
                只勾选推荐项
              </button>
              <button className="ghost" disabled={isTriageReviewLoading} onClick={selectAllCleanupClusters} type="button">
                全选本页
              </button>
            </div>
            <div className="triage-review-list">
              {cleanupClusters.map((cluster) => {
                const checked = selectedSkipPrefixes.has(cluster.prefix);
                const isRecommended = cluster.recommendation === 'exclude';
                const isExpanded = expandedPrefixes.has(cluster.prefix);
                const fileList = clusterFiles[cluster.prefix];
                const selectedFileCount = fileList?.selectedPaths.size || 0;
                return (
                  <article className={`triage-review-card cleaner ${checked ? 'checked' : ''}`} key={cluster.prefix}>
                    <label className="triage-cleaner-check">
                      <input
                        checked={checked}
                        disabled={isTriageReviewLoading}
                        onChange={() => toggleSkipPrefix(cluster.prefix)}
                        type="checkbox"
                      />
                      <span>{checked ? '将跳过' : '待确认'}</span>
                    </label>
                    <button
                      aria-expanded={isExpanded}
                      className="triage-card-main"
                      onClick={() => toggleExpandedPrefix(cluster.prefix)}
                      type="button"
                    >
                      <div className="triage-card-title">
                        <strong>{cluster.directory}</strong>
                        <em>{isRecommended ? '默认建议排除' : '需要你确认'}</em>
                      </div>
                      <small>
                        {cluster.total.toLocaleString()} 个文件 · {formatBytes(cluster.total_size)} · {describeClusterReason(cluster.reason)}
                      </small>
                      <div className="triage-status-mix">
                        {Object.entries(cluster.statuses || {}).map(([status, count]) => (
                          <span key={status}>{status}:{count}</span>
                        ))}
                      </div>
                      <small className="triage-expand-hint">
                        {isExpanded ? '收起文件列表' : `点击展开文件列表，可细选 ${cluster.total.toLocaleString()} 个文件`}
                      </small>
                    </button>
                    <div className="triage-review-actions">
                      <button disabled={isTriageReviewLoading} onClick={() => onTriageDecision(cluster.prefix, 'high')} type="button">
                        保留并纳入总结
                      </button>
                    </div>
                    {isExpanded ? (
                      <div className="triage-samples">
                        <div className="triage-file-toolbar">
                          <span>
                            已选 {selectedFileCount} 个
                            {fileList?.totalFiles ? ` · 已加载 ${fileList.files.length}/${fileList.totalFiles}` : ''}
                          </span>
                          <button disabled={!fileList?.files.length} onClick={() => selectVisibleFiles(cluster.prefix)} type="button">全选本页</button>
                          <button disabled={!selectedFileCount} onClick={() => clearSelectedFiles(cluster.prefix)} type="button">清空</button>
                          <button disabled={!selectedFileCount || isTriageReviewLoading} onClick={() => applySelectedFiles(cluster.prefix, 'skip')} type="button">排除所选</button>
                          <button disabled={!selectedFileCount || isTriageReviewLoading} onClick={() => applySelectedFiles(cluster.prefix, 'high')} type="button">纳入所选</button>
                        </div>
                        {fileList?.loading ? <p>正在读取目录里的文件...</p> : null}
                        {fileList?.error ? (
                          <div className="triage-file-error">
                            <span>{fileList.error}</span>
                            <button onClick={() => loadClusterFiles(cluster.prefix)} type="button">重试</button>
                          </div>
                        ) : null}
                        {fileList?.files.length ? (
                          <div className="triage-file-list">
                            {fileList.files.map((file) => (
                              <label className="triage-file-row" key={file.path}>
                                <input
                                  checked={fileList.selectedPaths.has(file.path)}
                                  disabled={isTriageReviewLoading}
                                  onChange={() => toggleFileSelection(cluster.prefix, file.path)}
                                  type="checkbox"
                                />
                                <span>
                                  <strong>{file.path}</strong>
                                  <small>
                                    {file.file_type || '(no suffix)'} · {formatBytes(file.size_bytes)} · {file.status}
                                  </small>
                                </span>
                              </label>
                            ))}
                          </div>
                        ) : null}
                        {!fileList?.loading && !fileList?.files.length && !fileList?.error ? <p>这组目录暂时没有可展示的文件。</p> : null}
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          </>
        ) : (
          <p className="empty">暂无待确认目录。后台会继续自动分诊；你也可以到原始 Dashboard 查看完整列表。</p>
        )}
      </article>
    </section>
  );
}

function parseSourceDraft(value: string) {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

function describeClusterReason(reason: string) {
  if (reason === 'needs triage or user confirmation') {
    return '需要分诊或用户确认';
  }
  if (reason === 'mixed signal') {
    return '信号混合，需要确认';
  }
  return reason || '需要确认';
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
