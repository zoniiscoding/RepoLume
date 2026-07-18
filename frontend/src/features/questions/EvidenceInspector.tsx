import {
  Clipboard,
  ExternalLink,
  FileCode2,
  GitCommitHorizontal,
  GitPullRequest,
  Network,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Citation, CodeCitation } from "../../api/contracts";
import { Button, EmptyState } from "../../components/ui";
import { formatDate, shortSha, titleCase, trustedGitHubUrl } from "../../utils/format";

export function EvidenceInspector({
  citation,
  onClose,
}: {
  citation: Citation | null;
  onClose(): void;
}): React.JSX.Element | null {
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!citation) return;
    previousFocus.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => previousFocus.current?.focus();
  }, [citation]);

  if (!citation) return null;
  return (
    <aside
      aria-label="Evidence inspector"
      className="evidence-drawer"
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
      }}
      role="complementary"
    >
      <div className="evidence-drawer__header">
        <div>
          <p className="eyebrow">Evidence inspector</p>
          <h2>{titleCase(citation.source_type)}</h2>
        </div>
        <Button
          ref={closeRef}
          aria-label="Close evidence inspector"
          variant="quiet"
          onClick={onClose}
        >
          <X size={18} />
        </Button>
      </div>
      <div className="evidence-drawer__body">{renderCitation(citation)}</div>
    </aside>
  );
}

function renderCitation(citation: Citation): React.JSX.Element {
  switch (citation.source_type) {
    case "code":
      return <CodeEvidence citation={citation} />;
    case "commit":
      return <CommitEvidence citation={citation} />;
    case "pull_request":
      return <PullRequestEvidence citation={citation} />;
    case "caller":
      return <CallerEvidence citation={citation} />;
  }
}

function CodeEvidence({ citation }: { citation: CodeCitation }): React.JSX.Element {
  const lines = citation.supporting_excerpt.split("\n");
  return (
    <div className="evidence-stack">
      <EvidenceTitle
        icon={<FileCode2 size={17} />}
        title={citation.file_path}
        subtitle={`${shortSha(citation.commit_sha)} · lines ${citation.start_line}–${citation.end_line}`}
      />
      <div className="evidence-actions">
        <CopyButton label="Copy path" value={citation.file_path} />
        <CopyButton
          label="Copy citation"
          value={`${citation.file_path}:${citation.start_line}-${citation.end_line}@${citation.commit_sha}`}
        />
      </div>
      {citation.supporting_excerpt ? (
        <pre className="source-viewer" aria-label={`Source excerpt ${citation.file_path}`}>
          {lines.map((line, index) => (
            <code key={`${index}-${line}`}>
              <span>{citation.start_line + index}</span>
              <mark>{line || " "}</mark>
            </code>
          ))}
        </pre>
      ) : (
        <EmptyState title="Source excerpt unavailable">
          This citation no longer has a readable excerpt.
        </EmptyState>
      )}
    </div>
  );
}

function CommitEvidence({
  citation,
}: {
  citation: Extract<Citation, { source_type: "commit" }>;
}): React.JSX.Element {
  const url = trustedGitHubUrl(citation.html_url);
  return (
    <div className="evidence-stack">
      <EvidenceTitle
        icon={<GitCommitHorizontal size={17} />}
        title={shortSha(citation.commit_sha)}
        subtitle={formatDate(citation.committed_at)}
      />
      <p className="evidence-text">{citation.message}</p>
      <MetadataList
        rows={[
          ["Author", citation.author_login ?? "Not available"],
          ["Parents", citation.parent_shas.map(shortSha).join(", ") || "None"],
          ["Changed paths", String(citation.changed_paths.length)],
        ]}
      />
      {citation.patch_excerpt ? (
        <pre className="history-excerpt">{citation.patch_excerpt}</pre>
      ) : null}
      {url ? <TrustedLink url={url} label="Open commit on GitHub" /> : null}
    </div>
  );
}

function PullRequestEvidence({
  citation,
}: {
  citation: Extract<Citation, { source_type: "pull_request" }>;
}): React.JSX.Element {
  const url = trustedGitHubUrl(citation.html_url);
  return (
    <div className="evidence-stack">
      <EvidenceTitle
        icon={<GitPullRequest size={17} />}
        title={`#${citation.number} ${citation.title}`}
        subtitle={`${titleCase(citation.state)} · ${citation.author_login ?? "Unknown author"}`}
      />
      <MetadataList
        rows={[
          ["Merged", citation.merged_at ? formatDate(citation.merged_at) : "Not merged"],
          ["Changed paths", String(citation.changed_paths.length)],
          ["Merge commit", shortSha(citation.merge_commit_sha)],
        ]}
      />
      {citation.body_excerpt ? <p className="evidence-text">{citation.body_excerpt}</p> : null}
      {url ? <TrustedLink url={url} label="Open pull request on GitHub" /> : null}
    </div>
  );
}

function CallerEvidence({
  citation,
}: {
  citation: Extract<Citation, { source_type: "caller" }>;
}): React.JSX.Element {
  return (
    <div className="evidence-stack">
      <EvidenceTitle
        icon={<Network size={17} />}
        title={citation.target_qualified_name}
        subtitle={`${citation.target_file_path} · ${shortSha(citation.commit_sha)}`}
      />
      <MetadataList
        rows={[
          ["Direct caller", citation.caller_qualified_name],
          ["Caller file", citation.caller_file_path],
          ["Call site", `${citation.call_line}–${citation.call_end_line}`],
          ["Definition", `${citation.caller_start_line}–${citation.caller_end_line}`],
          ["Resolution", titleCase(citation.resolution_type)],
          ["Confidence", titleCase(citation.confidence)],
        ]}
      />
      <pre className="call-expression">{citation.call_expression}</pre>
      <p className="caller-limitation">
        Static analysis may not include dynamic or runtime-created relationships.{" "}
        {citation.limitation}
      </p>
    </div>
  );
}

function EvidenceTitle({
  icon,
  title,
  subtitle,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
}): React.JSX.Element {
  return (
    <header className="evidence-title">
      <span>{icon}</span>
      <div>
        <h3>{title}</h3>
        <p className="mono">{subtitle}</p>
      </div>
    </header>
  );
}

function MetadataList({ rows }: { rows: Array<[string, string]> }): React.JSX.Element {
  return (
    <dl className="evidence-metadata">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function TrustedLink({ url, label }: { url: string; label: string }): React.JSX.Element {
  return (
    <a className="button" href={url} rel="noopener noreferrer" target="_blank">
      <ExternalLink aria-hidden="true" size={15} />
      {label}
    </a>
  );
}

function CopyButton({ label, value }: { label: string; value: string }): React.JSX.Element {
  const [copied, setCopied] = useState(false);
  async function copy(): Promise<void> {
    await navigator.clipboard?.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }
  return (
    <Button onClick={() => void copy()} variant="quiet">
      <Clipboard aria-hidden="true" size={15} />
      {copied ? "Copied" : label}
    </Button>
  );
}
