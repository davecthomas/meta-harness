// The console's only way to the meta-harness: the /v1 REST API, through the edge (src/meta_harness/edge).
// In the cloud the edge needs a Google sign-in session (/auth); locally it names the operator itself.

export class ApiError extends Error {
  // `reasons`: every reason a configuration save was refused, when the service listed them.
  constructor(public status: number, public detail: string, public reasons: string[] = []) {
    super(detail);
  }
}

export async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const reasons: string[] = Array.isArray(body?.detail?.reasons) ? body.detail.reasons : [];
    const detail = body && typeof body.detail === "string" ? body.detail : reasons.join("; ") || JSON.stringify(body?.detail ?? body);
    throw new ApiError(response.status, detail || response.statusText, reasons);
  }
  return body as T;
}

export function query(params: Record<string, string | number | undefined | null>): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return pairs.length ? "?" + new URLSearchParams(pairs.map(([k, v]) => [k, String(v)])).toString() : "";
}
