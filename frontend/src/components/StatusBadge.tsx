import { titleCase } from "../utils/format";

export function StatusBadge({ status }: { status: string | null }): React.JSX.Element {
  const value = status ?? "not_available";
  const tone = value.includes("ready")
    ? "ready"
    : value.includes("fail") || value.includes("revoked") || value.includes("delet")
      ? "failed"
      : value.includes("stale") || value.includes("supersed") || value.includes("retry")
        ? "warning"
        : value.includes("queue") || value.includes("index") || value.includes("build")
          ? "active"
          : "neutral";
  return <span className={`status-badge status-badge--${tone}`}>{titleCase(value)}</span>;
}
