export function shortSha(value: string | null | undefined): string {
  return value ? value.slice(0, 7) : "—";
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "Not available";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Not available"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "Not measured";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KiB", "MiB", "GiB"];
  let scaled = value / 1024;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[unit]}`;
}

export function titleCase(value: string | null | undefined): string {
  return (value ?? "not available")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function trustedGitHubUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" &&
      (url.hostname === "github.com" || url.hostname === "www.github.com")
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}
