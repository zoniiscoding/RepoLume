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
    if (
      url.protocol !== "https:" ||
      url.hostname !== "github.com" ||
      url.port ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    ) {
      return null;
    }
    const parts = url.pathname.split("/").filter(Boolean);
    const owner = parts[0] ?? "";
    const repository = parts[1] ?? "";
    const resource = parts[2] ?? "";
    const identifier = parts[3] ?? "";
    const validRepository =
      /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(owner) &&
      /^[A-Za-z0-9._-]{1,100}$/.test(repository);
    const validIdentifier =
      (resource === "commit" && /^[0-9a-f]{40}$/.test(identifier)) ||
      (resource === "pull" && /^[1-9][0-9]*$/.test(identifier));
    return validRepository && validIdentifier && parts.length === 4 ? url.toString() : null;
  } catch {
    return null;
  }
}

export function trustedAvatarUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    const trustedHost =
      url.hostname === "avatars.githubusercontent.com" ||
      url.hostname === "lh3.googleusercontent.com";
    return url.protocol === "https:" &&
      trustedHost &&
      !url.port &&
      !url.username &&
      !url.password &&
      !url.hash
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}
