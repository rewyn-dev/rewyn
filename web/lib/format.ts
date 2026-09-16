/** Formatting for a dense, scannable table (UI spec §3). */

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function money(value: number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined) return "—";
  const symbol = currency === "USD" ? "$" : `${currency} `;
  if (value === 0) return `${symbol}0`;
  if (value < 0.01) return `${symbol}${value.toFixed(4)}`;
  if (value < 1000) return `${symbol}${value.toFixed(2)}`;
  return `${symbol}${Math.round(value).toLocaleString()}`;
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString();
}

export function tokens(value: number): string {
  return value.toLocaleString();
}

export function percent(value: number): string {
  return `${value.toFixed(1)}%`;
}

export function clock(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function day(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** A day and a wall-clock time, for a timeline where both matter (UI §36). */
export function when(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return `${day(iso)}, ${date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })}`;
}

/** Successive divisions from seconds up to years. */
const DIVISIONS: { amount: number; unit: Intl.RelativeTimeFormatUnit }[] = [
  { amount: 60, unit: "second" },
  { amount: 60, unit: "minute" },
  { amount: 24, unit: "hour" },
  { amount: 7, unit: "day" },
  { amount: 4.34524, unit: "week" },
  { amount: 12, unit: "month" },
  { amount: Number.POSITIVE_INFINITY, unit: "year" },
];

export function ago(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "—";
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  let value = (new Date(iso).getTime() - now) / 1000;
  for (const division of DIVISIONS) {
    if (Math.abs(value) < division.amount)
      return formatter.format(Math.round(value), division.unit);
    value /= division.amount;
  }
  return formatter.format(Math.round(value), "year");
}

export function offset(ms: number): string {
  if (ms < 1000) return `+${Math.round(ms)}ms`;
  return `+${(ms / 1000).toFixed(2)}s`;
}

export function shortId(id: string, length = 10): string {
  const body = id.includes("_") ? id.slice(id.indexOf("_") + 1) : id;
  return body.length <= length ? body : `${body.slice(0, length)}…`;
}

export function json(value: unknown, indent = 2): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, indent);
  } catch {
    return String(value);
  }
}

export function truncate(text: string, limit = 160): string {
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

/** A stable colour per context kind, so the budget bar and legend agree. */
export const KIND_COLOURS: Record<string, string> = {
  instructions: "#7aa2f7",
  user: "#6fd3e0",
  knowledge: "#5ec27a",
  memory: "#e3b341",
  tools: "#c39cf0",
  skills: "#f09a6a",
  state: "#8b95a3",
  examples: "#a3be8c",
  runtime: "#5f7d95",
  history: "#d8a0b6",
};

export function kindColour(kind: string): string {
  return KIND_COLOURS[kind] ?? "#6b7683";
}

export function statusTone(status: string): "ok" | "fail" | "warn" | "running" | "idle" {
  switch (status) {
    case "succeeded":
    case "ok":
    case "success":
      return "ok";
    case "failed":
    case "error":
    case "denied":
      return "fail";
    case "cancelled":
    case "warning":
      return "warn";
    case "running":
    case "pending":
      return "running";
    default:
      return "idle";
  }
}
