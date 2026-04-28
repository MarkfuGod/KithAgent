import type { FormEvent } from 'react';
import { valueText } from '../../lib/format';
import type { ChatMessageView, ChatProgress } from '../../types';

export const starterPrompts = [
  '/profile 你现在如何理解我？',
  '/focus 我最近的注意力在哪里？',
  '/brief 总结我的工作、学习和生活线索。',
  '/plan 基于今日建议，安排接下来 30 分钟。',
];

export function ChatView({
  draft,
  isLoading,
  messages,
  onClear,
  onDraftChange,
  onRetry,
  onSubmit,
  progress,
  streamedAnswer,
  traceEvents,
}: {
  draft: string;
  isLoading: boolean;
  messages: ChatMessageView[];
  onClear: () => void;
  onDraftChange: (draft: string) => void;
  onRetry: () => void;
  onSubmit: (event?: FormEvent) => void;
  progress: ChatProgress | null;
  streamedAnswer: string;
  traceEvents: DaemonEvent[];
}) {
  return (
    <section className="chat-console">
      <div className="console-hero">
        <div>
          <p className="eyebrow">Talk with Kith</p>
          <h3>把想法说给 Kith，它会先温柔回应，需要时再整理本地线索。</h3>
          <p>你可以直接描述今天的困惑、计划或想整理的内容。Kith 会在需要画像、记忆或洞察时，轻轻展开处理过程。</p>
        </div>
        <button className="ghost" onClick={onClear} type="button">清空对话</button>
      </div>

      <div className="command-presets">
        {starterPrompts.map((prompt) => (
          <button key={prompt} onClick={() => onDraftChange(prompt)} type="button">
            {prompt}
          </button>
        ))}
      </div>

      <div className="console-stream">
        {messages.map((message) => (
          <article className={`console-entry ${message.role} ${message.failed ? 'failed' : ''}`} key={message.id}>
            <span className="entry-role">{message.role === 'user' ? '你说' : 'Kith 回应'}</span>
            {message.role === 'assistant' ? <MarkdownContent content={message.content} /> : <p>{message.content}</p>}
            {message.sources?.length ? <Sources sources={message.sources} /> : null}
          </article>
        ))}
        {isLoading && streamedAnswer && (
          <article className="console-entry assistant streaming">
            <span className="entry-role">Kith 正在回应</span>
            <MarkdownContent content={streamedAnswer} />
          </article>
        )}
        {isLoading && (
          <article className="console-entry assistant thinking">
            <span className="thinking-dot" />
            <div className="thinking-copy">
              <p>{progress?.message || 'Kith 正在查找画像、记忆和相关上下文...'}</p>
              <small>{progress ? stageLabel(progress.stage) : '准备本地上下文'}</small>
              <div className="run-trace" aria-label="Kith 运行轨迹">
                {traceItems(traceEvents, progress).map((item) => (
                  <div className={`run-trace-item ${item.tone}`} key={item.key}>
                    <b>{item.label}</b>
                    <span>{item.detail}</span>
                  </div>
                ))}
              </div>
            </div>
          </article>
        )}
      </div>

      {messages.some((message) => message.failed) && (
        <button className="ghost retry-button" onClick={onRetry} type="button">
          重试上一条问题
        </button>
      )}

      <form className="command-composer" onSubmit={onSubmit}>
        <span aria-hidden="true">写给 Kith</span>
        <textarea
          disabled={isLoading}
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              onSubmit();
            }
          }}
          placeholder="输入一个意图，例如：帮我判断今天最该推进什么。Shift + Enter 换行。"
          rows={3}
        />
        <button className="primary" disabled={isLoading || !draft.trim()} type="submit">
          {isLoading ? '分析中...' : '执行'}
        </button>
      </form>
    </section>
  );
}

function traceItems(events: DaemonEvent[], progress: ChatProgress | null) {
  const scopedEvents = progress
    ? events.filter((event) => {
      const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {};
      const requestId = typeof data.request_id === 'string' ? data.request_id : '';
      if (event.type === 'assistant.progress' || event.type === 'assistant.tool' || event.type.startsWith('llm.')) {
        return requestId === progress.requestId;
      }
      return true;
    })
    : events;
  const items = scopedEvents
    .slice(0, 18)
    .reverse()
    .map((event, index) => describeTraceEvent(event, index))
    .filter((item): item is TraceItem => Boolean(item));
  if (progress && !items.some((item) => item.stage === progress.stage)) {
    items.unshift({
      key: `progress-${progress.stage}`,
      stage: progress.stage,
      label: stageLabel(progress.stage),
      detail: progress.message,
      tone: 'active',
    });
  }
  return items.length ? items : [{
    key: 'waiting',
    label: '准备上下文',
    detail: '等待 daemon 返回检索、模型和来源事件。',
    tone: 'active',
  }];
}

type TraceItem = {
  key: string;
  stage?: string;
  label: string;
  detail: string;
  tone: 'active' | 'success' | 'warning' | 'neutral';
};

function describeTraceEvent(event: DaemonEvent, index: number): TraceItem | null {
  const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {};
  if (event.type === 'assistant.progress') {
    return {
      key: `${event.type}-${valueText(data.stage)}-${index}`,
      stage: valueText(data.stage) || 'working',
      label: stageLabel(valueText(data.stage) || 'working'),
      detail: valueText(data.message) || '正在处理请求。',
      tone: 'active',
    };
  }
  if (event.type === 'assistant.tool') {
    const status = valueText(data.status);
    return {
      key: `${event.type}-${valueText(data.name)}-${index}`,
      label: valueText(data.label) || readableToolName(valueText(data.name)),
      detail: [
        traceValue(data.detail, '工具步骤已更新'),
        data.elapsed_ms === undefined ? '' : `${traceValue(data.elapsed_ms, '?')} ms`,
      ].filter(Boolean).join(' · '),
      tone: status === 'warning' || status === 'error' ? 'warning' : status === 'completed' ? 'success' : 'active',
    };
  }
  if (event.type === 'llm.request') {
    return {
      key: `${event.type}-${valueText(data.call_id)}-${index}`,
      label: '模型调用',
      detail: `${traceValue(data.provider, 'provider')} / ${traceValue(data.model, 'model')}，${traceValue(data.message_count, '?')} 条上下文消息。`,
      tone: 'active',
    };
  }
  if (event.type === 'llm.response') {
    const usage = data.usage && typeof data.usage === 'object' ? data.usage as Record<string, unknown> : {};
    return {
      key: `${event.type}-${valueText(data.call_id)}-${index}`,
      label: '模型返回',
      detail: `${traceValue(data.model, 'model')} · prompt ${traceValue(usage.prompt_tokens, '-')} / completion ${traceValue(usage.completion_tokens, '-')}`,
      tone: 'success',
    };
  }
  if (event.type === 'llm.error') {
    return {
      key: `${event.type}-${valueText(data.call_id)}-${index}`,
      label: '模型错误',
      detail: valueText(data.error) || '模型请求失败。',
      tone: 'warning',
    };
  }
  return null;
}

function traceValue(value: unknown, fallback: string) {
  const text = valueText(value);
  return text === '暂无' ? fallback : text;
}

function readableToolName(name: string) {
  const labels: Record<string, string> = {
    profile_facts: '读取画像事实',
    recent_files: '读取近期文件',
    user_profile: '读取用户画像',
    context_briefs: '读取行为洞察',
    directory_context: '解析目录上下文',
    exact_file_search: '精确文件查找',
    rag_search: 'RAG 混合检索',
    desktop_llm: 'Desktop 模型调用',
    'profile.summary': '后端画像 skill',
    'memory.review': '后端记忆 skill',
    'assistant.insights': '后端洞察 skill',
    'sources.get': '后端资料范围 skill',
  };
  return labels[name] || '工具调用';
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    start: '接收请求',
    memory: '读取记忆',
    retrieval: '检索证据',
    'file-search': '查找文件',
    compose: '组织上下文',
    llm: '生成回答',
    finalize: '整理来源',
    fallback: '备用回答',
    'backend-skills': '请求后端技能',
  };
  return labels[stage] || stage;
}

function MarkdownContent({ content }: { content: string }) {
  const blocks = parseMarkdownBlocks(content);
  return (
    <div className="markdown-message">
      {blocks.map((block, index) => {
        const key = `${block.type}-${index}`;
        if (block.type === 'heading') {
          return <h4 key={key}>{renderInlineMarkdown(block.text)}</h4>;
        }
        if (block.type === 'quote') {
          return <blockquote key={key}>{renderInlineMarkdown(block.text)}</blockquote>;
        }
        if (block.type === 'list') {
          return (
            <ul key={key}>
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{renderInlineMarkdown(item)}</li>
              ))}
            </ul>
          );
        }
        if (block.type === 'table') {
          const [header, ...rows] = block.rows;
          return (
            <div className="markdown-table-wrap" key={key}>
              <table>
                {header ? (
                  <thead>
                    <tr>
                      {header.map((cell, cellIndex) => (
                        <th key={`${key}-h-${cellIndex}`}>{renderInlineMarkdown(cell)}</th>
                      ))}
                    </tr>
                  </thead>
                ) : null}
                <tbody>
                  {rows.map((row, rowIndex) => (
                    <tr key={`${key}-r-${rowIndex}`}>
                      {row.map((cell, cellIndex) => (
                        <td key={`${key}-r-${rowIndex}-${cellIndex}`}>{renderInlineMarkdown(cell)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        return <p key={key}>{renderInlineMarkdown(block.text)}</p>;
      })}
    </div>
  );
}

type MarkdownBlock =
  | { type: 'heading' | 'quote' | 'paragraph'; text: string }
  | { type: 'list'; items: string[] }
  | { type: 'table'; rows: string[][] };

function parseMarkdownBlocks(content: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];
  let listItems: string[] = [];
  let tableRows: string[][] = [];

  function flushParagraph() {
    if (paragraph.length) {
      blocks.push({ type: 'paragraph', text: paragraph.join(' ') });
      paragraph = [];
    }
  }

  function flushList() {
    if (listItems.length) {
      blocks.push({ type: 'list', items: listItems });
      listItems = [];
    }
  }

  function flushTable() {
    if (tableRows.length) {
      blocks.push({ type: 'table', rows: tableRows.filter((row) => !row.every((cell) => /^:?-{3,}:?$/.test(cell))) });
      tableRows = [];
    }
  }

  for (const rawLine of content.split('\n')) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      flushTable();
      continue;
    }

    const headingMatch = line.match(/^#{1,4}\s+(.+)$/);
    if (headingMatch) {
      flushParagraph();
      flushList();
      flushTable();
      blocks.push({ type: 'heading', text: headingMatch[1] });
      continue;
    }

    if (isTableLine(line)) {
      flushParagraph();
      flushList();
      tableRows.push(parseTableRow(line));
      continue;
    }

    const listMatch = line.match(/^[-*]\s+(.+)$/);
    if (listMatch) {
      flushParagraph();
      flushTable();
      listItems.push(listMatch[1]);
      continue;
    }

    const quoteMatch = line.match(/^>\s?(.+)$/);
    if (quoteMatch) {
      flushParagraph();
      flushList();
      flushTable();
      blocks.push({ type: 'quote', text: quoteMatch[1] });
      continue;
    }

    flushList();
    flushTable();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();
  flushTable();
  return blocks;
}

function isTableLine(line: string) {
  return line.includes('|') && line.split('|').length >= 3;
}

function parseTableRow(line: string) {
  return line
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function renderInlineMarkdown(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

function Sources({ sources }: { sources: Array<Record<string, unknown>> }) {
  return (
    <details className="message-sources">
      <summary>查看 {sources.length} 个来源</summary>
      <div>
        {sources.slice(0, 6).map((source, index) => (
          <div className="source-chip" key={`${valueText(source.id || source.path || source.title)}-${index}`}>
            <strong>{valueText(source.title || source.path || source.source || `来源 ${index + 1}`)}</strong>
            <small>{valueText(source.summary || source.detail || source.kind || source.category)}</small>
          </div>
        ))}
      </div>
    </details>
  );
}
