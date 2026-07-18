import { ArrowUp, BotMessageSquare, FileSearch, Network, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, useParams } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { Citation, QuestionResponse, Repository } from "../../api/contracts";
import { useAuth } from "../../auth/useAuth";
import { MarkdownAnswer } from "../../components/MarkdownAnswer";
import { StatusBadge } from "../../components/StatusBadge";
import { Button, EmptyState, InlineAlert, Panel, Textarea } from "../../components/ui";
import { shortSha, titleCase } from "../../utils/format";
import { EvidenceInspector } from "./EvidenceInspector";

interface TranscriptItem {
  question: string;
  response: QuestionResponse;
}

const examples = [
  "Where is repository authorization enforced before indexing?",
  "What calls IndexingWorker.process_job?",
  "Why was stale-event rejection added?",
];

export function QuestionWorkspacePage(): React.JSX.Element {
  const { repositoryId } = useParams();
  const [searchParams] = useSearchParams();
  const { accessToken } = useAuth();
  const [repository, setRepository] = useState<Repository | null>(null);
  const [question, setQuestion] = useState(searchParams.get("question") ?? "");
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const canSubmit = question.trim().length >= 3 && !loading && Boolean(repository?.searchable);

  useEffect(() => {
    if (!accessToken || !repositoryId) return;
    const controller = new AbortController();
    void api
      .getRepository(accessToken, repositoryId, controller.signal)
      .then(setRepository)
      .catch(() => setError("This repository is unavailable or access was revoked."));
    return () => controller.abort();
  }, [accessToken, repositoryId]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const submitLabel = useMemo(
    () => (loading ? "Generating grounded answer" : "Ask repository"),
    [loading],
  );

  async function submit(): Promise<void> {
    if (!accessToken || !repositoryId || !canSubmit) return;
    const submitted = question.trim();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const response = await api.askQuestion(
        accessToken,
        repositoryId,
        submitted,
        controller.signal,
      );
      setItems((current) => [...current, { question: submitted, response }]);
      setQuestion("");
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setError(
          caught instanceof ApiError
            ? explainQuestionError(caught)
            : "The answer service is temporarily unavailable.",
        );
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
    }
  }

  function onComposerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <section className="workspace page">
      <header className="workspace__context">
        <div>
          <p className="eyebrow">Repository question</p>
          <h1>{repository?.github_full_name ?? "Loading repository"}</h1>
        </div>
        {repository ? (
          <div className="workspace__context-meta">
            <StatusBadge status={repository.indexing_status} />
            <span className="mono">{shortSha(repository.active_commit_sha)}</span>
          </div>
        ) : null}
      </header>
      {error ? <InlineAlert tone="error">{error}</InlineAlert> : null}
      {repository && !repository.searchable ? (
        <InlineAlert tone="warning">
          This repository does not have an active searchable index. You can review its indexing
          status and try again after a successful activation.
        </InlineAlert>
      ) : null}
      <div
        className={selectedCitation ? "workspace-grid workspace-grid--inspector" : "workspace-grid"}
      >
        <div className="workspace-content">
          <main className="transcript" aria-label="Question workspace">
            {items.length === 0 ? (
              <WorkspaceEmpty onChoose={setQuestion} />
            ) : (
              items.map((item, index) => (
                <TranscriptEntry
                  key={`${index}-${item.question}`}
                  item={item}
                  onCitation={setSelectedCitation}
                />
              ))
            )}
            {loading ? <GeneratingState onCancel={() => abortRef.current?.abort()} /> : null}
          </main>
          <section className="composer" aria-label="Ask a repository question">
            <label htmlFor="question-input">
              <span className="sr-only">
                Question about {repository?.github_full_name ?? "repository"}
              </span>
            </label>
            <Textarea
              id="question-input"
              maxLength={4096}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={onComposerKeyDown}
              placeholder="Ask about the active repository index…"
              value={question}
            />
            <div className="composer__footer">
              <span>Ctrl/Cmd + Enter to submit · 4,096 character limit</span>
              <div className="button-row">
                {loading ? (
                  <Button onClick={() => abortRef.current?.abort()} variant="quiet">
                    Cancel
                  </Button>
                ) : null}
                <Button
                  aria-label={submitLabel}
                  disabled={!canSubmit}
                  loading={loading}
                  onClick={() => void submit()}
                  variant="primary"
                >
                  <ArrowUp aria-hidden="true" size={16} />
                  Ask
                </Button>
              </div>
            </div>
          </section>
        </div>
        <EvidenceInspector citation={selectedCitation} onClose={() => setSelectedCitation(null)} />
      </div>
    </section>
  );
}

function WorkspaceEmpty({ onChoose }: { onChoose(value: string): void }): React.JSX.Element {
  return (
    <EmptyState title="Ask about the active index">
      <span>
        Answers cite current code, GitHub history, or static callers. Repository content remains
        inert data.
      </span>
      <div className="suggestion-list">
        {examples.map((example) => (
          <button key={example} onClick={() => onChoose(example)}>
            {example}
            <ArrowUp aria-hidden="true" size={15} />
          </button>
        ))}
      </div>
    </EmptyState>
  );
}

function GeneratingState({ onCancel }: { onCancel(): void }): React.JSX.Element {
  return (
    <Panel className="generating-state">
      <BotMessageSquare aria-hidden="true" size={18} />
      <div>
        <strong>Preparing a grounded answer</strong>
        <p>RepoLume is using only the active repository evidence and approved tools.</p>
      </div>
      <Button onClick={onCancel} variant="quiet">
        Cancel
      </Button>
    </Panel>
  );
}

function TranscriptEntry({
  item,
  onCitation,
}: {
  item: TranscriptItem;
  onCitation(citation: Citation): void;
}): React.JSX.Element {
  const responseTone =
    item.response.answerability === "answered"
      ? "success"
      : item.response.answerability === "partially_answered"
        ? "warning"
        : "neutral";
  return (
    <article className="transcript-entry">
      <div className="question-block">
        <p className="eyebrow">Question</p>
        <p>{item.question}</p>
      </div>
      <div className="answer-block">
        <div className="answer-block__header">
          <div>
            <p className="eyebrow">Answer</p>
            <StatusBadge status={item.response.answerability} />
          </div>
          <span className="muted">
            {item.response.duration_ms} ms · {item.response.tool_call_count} tools
          </span>
        </div>
        {item.response.answerability !== "answered" ? (
          <InlineAlert tone={responseTone === "warning" ? "warning" : "neutral"}>
            {titleCase(item.response.answerability)}: this response is intentionally limited by
            available evidence.
          </InlineAlert>
        ) : null}
        <MarkdownAnswer>{item.response.answer}</MarkdownAnswer>
        <CitationList citations={item.response.citations} onCitation={onCitation} />
        <ToolTrace trace={item.response.trace} />
      </div>
    </article>
  );
}

function CitationList({
  citations,
  onCitation,
}: {
  citations: Citation[];
  onCitation(citation: Citation): void;
}): React.JSX.Element {
  if (citations.length === 0)
    return <p className="muted evidence-empty">No supporting citation was returned.</p>;
  return (
    <section className="citation-list" aria-label="Supporting evidence">
      <p className="eyebrow">Evidence</p>
      {citations.map((citation, index) => (
        <button key={citation.evidence_id} onClick={() => onCitation(citation)}>
          <span>[{index + 1}]</span>
          {citation.source_type === "code" ? (
            <>
              <FileSearch aria-hidden="true" size={15} />
              {citation.file_path}:{citation.start_line}
            </>
          ) : citation.source_type === "caller" ? (
            <>
              <Network aria-hidden="true" size={15} />
              {citation.caller_qualified_name}
            </>
          ) : (
            <>
              <RotateCcw aria-hidden="true" size={15} />
              {citation.source_type === "commit"
                ? shortSha(citation.commit_sha)
                : `PR #${citation.number}`}
            </>
          )}
        </button>
      ))}
    </section>
  );
}

function ToolTrace({ trace }: { trace: QuestionResponse["trace"] }): React.JSX.Element | null {
  if (trace.length === 0) return null;
  return (
    <details className="tool-trace">
      <summary>Safe tool trace ({trace.length})</summary>
      <ol>
        {trace.map((step) => (
          <li key={step.step}>
            <span>{step.tool}</span>
            <span>{step.status}</span>
            <span>{step.duration_ms} ms</span>
            <span>{step.result_count} evidence</span>
            {step.failure_code ? <span>{step.failure_code}</span> : null}
          </li>
        ))}
      </ol>
    </details>
  );
}

function explainQuestionError(error: ApiError): string {
  if (error.status === 401) return "Your session expired. Sign in again to continue.";
  if (error.status === 404) return "This repository is unavailable or your access was revoked.";
  if (error.status === 422)
    return "Questions must be between 3 characters and the supported request limit.";
  if (error.status === 503)
    return "The repository answer service is temporarily unavailable. Your question is still in the composer.";
  return "The question could not be completed. Try again if the problem persists.";
}
