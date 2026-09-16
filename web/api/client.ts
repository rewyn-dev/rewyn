/**
 * The API client (UI spec §52).
 *
 * One base path, both surfaces. Failures are turned into `ProblemDetail`
 * (UI §48) so a screen can render the recovery action instead of a status
 * code, and every request is abortable so a fast tab switch does not paint
 * the previous tab's data.
 */

import type { ProblemDetail } from "./types";

export const BASE = "/console/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetail;

  constructor(status: number, problem: ProblemDetail) {
    super(problem.error);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
}

function asProblem(status: number, body: unknown): ProblemDetail {
  const fallback: ProblemDetail = {
    error: status === 0 ? "The console is not reachable" : `Request failed (${status})`,
    detail:
      status === 0
        ? "The console server stopped responding. Check the terminal running `rewyn ui`."
        : "The server rejected this request.",
    action: null,
    href: null,
  };
  if (!body || typeof body !== "object") return fallback;
  const record = body as Record<string, unknown>;
  // FastAPI wraps raised problems in `detail`; ours are returned flat.
  const payload = (
    typeof record.detail === "object" && record.detail !== null ? record.detail : record
  ) as Record<string, unknown>;
  if (typeof payload.error !== "string") {
    return typeof record.detail === "string" ? { ...fallback, detail: record.detail } : fallback;
  }
  return {
    error: payload.error,
    detail: typeof payload.detail === "string" ? payload.detail : "",
    action: typeof payload.action === "string" ? payload.action : null,
    href: typeof payload.href === "string" ? payload.href : null,
  };
}

export type Params = Record<string, string | number | boolean | null | undefined>;

export function query(params: Params = {}): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>("POST", path, body, signal);
}

export async function get<T>(path: string, params?: Params, signal?: AbortSignal): Promise<T> {
  return request<T>("GET", path, undefined, signal, params);
}

/** Removing a saved view returns 204, so there is nothing to parse. */
export async function del(path: string, signal?: AbortSignal): Promise<void> {
  await request<null>("DELETE", path, undefined, signal);
}

async function request<T>(
  method: "GET" | "POST" | "DELETE",
  path: string,
  body?: unknown,
  signal?: AbortSignal,
  params?: Params,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}${query(params)}`, {
      method,
      signal,
      headers:
        body === undefined
          ? { accept: "application/json" }
          : { accept: "application/json", "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch (cause) {
    if ((cause as Error)?.name === "AbortError") throw cause;
    throw new ApiError(0, asProblem(0, null));
  }
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) throw new ApiError(response.status, asProblem(response.status, parsed));
  return parsed as T;
}
