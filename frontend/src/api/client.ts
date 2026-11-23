// fetch wrapper: base /api (proxied to the backend), Bearer token, 401 logs out,
// other non-2xx responses throw ApiError(status, detail).

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

let token: string | null = null;
let onUnauthorized: () => void = () => {};

/** Set by AuthContext: the current token and what to do on any 401. */
export function configureAuth(next: { token: string | null; onUnauthorized: () => void }): void {
  token = next.token;
  onUnauthorized = next.onUnauthorized;
}

export interface RequestOptions {
  method?: string;
  json?: unknown;
  form?: Record<string, string>;
  multipart?: FormData; // fetch sets the multipart boundary itself
  signal?: AbortSignal;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = new URLSearchParams(opts.form);
  } else if (opts.multipart) {
    body = opts.multipart;
  }
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`/api${path}`, { method: opts.method ?? "GET", headers, body, signal: opts.signal });
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError(401, null);
  }
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      // not JSON; keep null
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T; // DELETE: no body
  return (await res.json()) as T;
}
