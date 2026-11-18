import { afterEach, expect, test, vi } from "vitest";
import { ApiError, call, retries } from "./client";

afterEach(() => vi.unstubAllGlobals());

const reply = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });

function service(...steps: (number | Error)[]) {
  const seen: string[] = [];
  vi.stubGlobal("fetch", async (path: string) => {
    const step = steps[Math.min(seen.length, steps.length - 1)];
    seen.push(path);
    if (step instanceof Error) throw step;
    return reply(step, { detail: `status ${step}`, ok: step === 200 });
  });
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  return seen;
}

test.each([502, 503, 504, 429, 500])("a read that gets %i is tried again before the page hears of it", async (status) => {
  const seen = service(status, 200);
  expect(await call("/v1/labels")).toMatchObject({ ok: true });
  expect(seen).toHaveLength(2);
});

test("a read is tried again after the network fails, and reports the last answer when it stays down", async () => {
  const recovered = service(new TypeError("Failed to fetch"), 200);
  await call("/v1/goals");
  expect(recovered).toHaveLength(2);

  const down = service(502);
  await expect(call("/v1/goals")).rejects.toMatchObject({ status: 502, detail: "status 502" });
  expect(down).toHaveLength(retries.attempts);
});

test("a change is repeated only when it was turned away unread", async () => {
  const unread = service(503, 200);
  await call("/v1/goals", { method: "POST", body: "{}" });
  expect(unread).toHaveLength(2);

  const maybeMade = service(502, 200);
  await expect(call("/v1/goals", { method: "POST", body: "{}" })).rejects.toBeInstanceOf(ApiError);
  expect(maybeMade).toHaveLength(1);

  const dropped = service(new TypeError("Failed to fetch"), 200);
  await expect(call("/v1/goals", { method: "POST", body: "{}" })).rejects.toThrow("Failed to fetch");
  expect(dropped).toHaveLength(1);
});

test("a refusal is reported at once, and a host's error page still gives its status", async () => {
  const refused = service(422);
  await expect(call("/v1/goals")).rejects.toMatchObject({ status: 422 });
  expect(refused).toHaveLength(1);

  vi.stubGlobal("fetch", async () => new Response("<html>Bad gateway</html>", { status: 404 }));
  await expect(call("/v1/goals")).rejects.toMatchObject({ status: 404 });
});
