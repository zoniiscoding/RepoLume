import { CheckCircle2, CircleAlert, Clock3, GitBranch, RefreshCw } from "lucide-react";
import type { IndexingStatus } from "../../api/contracts";
import { StatusBadge } from "../../components/StatusBadge";
import { InlineAlert, Panel } from "../../components/ui";
import { formatDate, shortSha, titleCase } from "../../utils/format";
import { isIndexingStatus } from "../../utils/indexing";

const STAGES = [
  "queued",
  "cloning",
  "discovering",
  "parsing",
  "chunking",
  "building_graph",
  "embedding",
  "validating_index",
  "activating_index",
];

export function IndexingStatusPanel({ status }: { status: IndexingStatus }): React.JSX.Element {
  const active = isIndexingStatus(status.repository_status);
  const stageIndex = status.stage ? STAGES.indexOf(status.stage) : -1;
  return (
    <Panel className="indexing-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Indexing</p>
          <h2>Repository index state</h2>
        </div>
        <StatusBadge status={status.job_status ?? status.repository_status} />
      </div>
      {active ? (
        <InlineAlert tone="neutral">
          <Clock3 aria-hidden="true" size={16} />
          The last validated index stays available until this replacement is fully activated.
        </InlineAlert>
      ) : null}
      {status.error_code || status.safe_error_message ? (
        <InlineAlert tone="error">
          <CircleAlert aria-hidden="true" size={16} />
          {status.safe_error_message ?? "The latest indexing attempt could not complete."}
        </InlineAlert>
      ) : null}
      <ol className="stage-list">
        {STAGES.map((stage, index) => {
          const completed = stageIndex > index || (!active && status.searchable);
          const current = stage === status.stage;
          return (
            <li
              key={stage}
              className={
                current ? "stage-list__item stage-list__item--current" : "stage-list__item"
              }
            >
              {completed ? (
                <CheckCircle2 aria-hidden="true" size={16} />
              ) : (
                <span className="stage-list__dot" />
              )}
              <span>{titleCase(stage)}</span>
            </li>
          );
        })}
      </ol>
      <dl className="metadata-grid">
        <Metadata
          label="Target branch"
          value={status.indexed_branch ?? "Awaiting target"}
          icon={<GitBranch size={14} />}
        />
        <Metadata
          label="Target commit"
          value={shortSha(status.latest_remote_commit_sha ?? status.active_commit_sha)}
          mono
        />
        <Metadata label="Requested mode" value={titleCase(status.requested_mode)} />
        <Metadata label="Actual mode" value={titleCase(status.actual_mode)} />
        <Metadata label="Changed files" value={String(status.changed_file_count)} />
        <Metadata label="Reused chunks" value={String(status.reused_chunk_count)} />
        <Metadata label="Re-embedded" value={String(status.reembedded_chunk_count)} />
        <Metadata label="Graph rebuilt" value={status.graph_rebuilt ? "Yes" : "No"} />
        <Metadata
          label="Last update"
          value={formatDate(status.completed_at ?? status.heartbeat_at)}
        />
      </dl>
      {status.full_rebuild_reason ? (
        <p className="indexing-panel__fallback">
          <RefreshCw aria-hidden="true" size={14} /> Full rebuild:{" "}
          {titleCase(status.full_rebuild_reason)}
        </p>
      ) : null}
    </Panel>
  );
}

function Metadata({
  label,
  value,
  mono = false,
  icon,
}: {
  label: string;
  value: string;
  mono?: boolean;
  icon?: React.ReactNode;
}): React.JSX.Element {
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "mono" : ""}>
        {icon}
        {value}
      </dd>
    </div>
  );
}
