// The console's only way to the meta-harness: the /v1 REST API, through the edge (src/meta_harness/edge).
// In the cloud the edge needs a Google sign-in session (/auth); locally it names the operator itself.

export class ApiError extends Error {
  // `reasons`: every reason a configuration save was refused, when the service listed them.
  constructor(public status: number, public detail: string, public reasons: string[] = []) {
    super(detail);
  }
}

// Sent on window when the edge answers that nobody is signed in: a session that expired while a page was open.
// The app answers it with the sign-in screen, and the page the operator was on is where they come back to.
export const SIGNED_OUT = "mh:signed-out";

// How the console tries again. A test sets baseMs to 0.
export const retries = { attempts: 4, baseMs: 400, capMs: 4000 };
// The edge and its host answer these when a service could not: a restart, a timeout, a dropped connection.
const READ_AGAIN = new Set([408, 425, 429, 500, 502, 503, 504]);
// Turned away unread, so even a change can go again.
const SEND_AGAIN = new Set([429, 503]);

function waitBefore(attempt: number, response?: Response): number {
  const asked = Number(response?.headers.get("Retry-After"));
  if (retries.baseMs === 0) return 0;
  if (Number.isFinite(asked) && asked > 0) return Math.min(asked * 1000, retries.capMs);
  const ceiling = Math.min(retries.capMs, retries.baseMs * 2 ** (attempt - 1));
  return ceiling / 2 + Math.random() * (ceiling / 2);
}

// A transient failure is tried again with backoff before the page hears of it. A read is repeated after any of
// them; a change only when it cannot have been made. Every retry is logged to the browser's console.
async function send(path: string, init: RequestInit): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const reads = method === "GET" || method === "HEAD";
  for (let attempt = 1; ; attempt++) {
    let response: Response | undefined;
    let failure: unknown;
    try {
      response = await fetch(path, init);
      if (response.ok || !(reads ? READ_AGAIN : SEND_AGAIN).has(response.status)) return response;
    } catch (err) {
      failure = err;
      // The network failed, and a change may have arrived before it did.
      if (!reads) throw err;
    }
    if (attempt >= retries.attempts) {
      if (response) return response;
      throw failure;
    }
    const wait = waitBefore(attempt, response);
    console.warn(`${method} ${path.split("?")[0]}: ${response ? response.status : String(failure)}; try ${attempt} of ${retries.attempts} failed, trying again in ${Math.round(wait)}ms`);
    await new Promise((done) => setTimeout(done, wait));
  }
}

export async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await send(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await response.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    // A host's own error page is not JSON: the status below says what happened.
    if (response.ok) throw new ApiError(response.status, "The answer could not be read. Reload to try again.");
  }
  if (!response.ok) {
    const reasons: string[] = Array.isArray(body?.detail?.reasons) ? body.detail.reasons : [];
    const detail = body && typeof body.detail === "string" ? body.detail : reasons.join("; ") || JSON.stringify(body?.detail ?? body);
    // A sign-in that failed is the sign-in screen's to say, not a sign-out.
    if (response.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event(SIGNED_OUT));
    throw new ApiError(response.status, detail || response.statusText, reasons);
  }
  return body as T;
}

export function query(params: Record<string, string | number | undefined | null>): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return pairs.length ? "?" + new URLSearchParams(pairs.map(([k, v]) => [k, String(v)])).toString() : "";
}
