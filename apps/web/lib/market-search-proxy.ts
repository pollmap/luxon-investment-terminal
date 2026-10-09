import { gzipSync } from "node:zlib";

type SearchReply = {
  status: number; body: ArrayBuffer; compressed: ArrayBuffer; contentType: string;
};

/** Search-only snapshots live at most 250ms from the start of the source read. */
export function createSearchProxy({
  baseURL = () => process.env.MARKET_API_BASE_URL ?? "http://127.0.0.1:8210",
  fetcher = fetch,
  maxEntries = 256,
  maxAgeMs = 250,
  maxBytes = 4 * 1024 * 1024,
  clock = () => performance.now(),
}: {
  baseURL?: () => string;
  fetcher?: typeof fetch;
  maxEntries?: number;
  maxAgeMs?: number;
  maxBytes?: number;
  clock?: () => number;
} = {}) {
  const pending = new Map<string, Promise<SearchReply>>();
  const snapshots = new Map<string, { reply: SearchReply; expires: number; size: number }>();
  let snapshotBytes = 0;

  function forget(key: string) {
    const previous = snapshots.get(key);
    if (previous) { snapshotBytes -= previous.size; snapshots.delete(key); }
  }

  function remember(key: string, reply: SearchReply, started: number) {
    const expires = started + Math.max(0, Math.min(250, maxAgeMs));
    const size = reply.body.byteLength + reply.compressed.byteLength;
    if (reply.status !== 200 || clock() >= expires || size > maxBytes) return;
    forget(key);
    for (const [entryKey, entry] of snapshots) if (clock() >= entry.expires) forget(entryKey);
    while (snapshots.size && (snapshots.size >= maxEntries || snapshotBytes + size > maxBytes)) {
      forget(snapshots.keys().next().value!);
    }
    snapshots.set(key, { reply, expires, size });
    snapshotBytes += size;
  }

  async function read(url: URL): Promise<SearchReply> {
    const response = await fetcher(url, {
      method: "GET",
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json", "Accept-Encoding": "identity" },
      // Do not forward a single caller's cancellation to the other readers.
      signal: AbortSignal.timeout(5000),
    });
    const body = await response.arrayBuffer();
    if (body.byteLength > 512 * 1024) throw new Error("Oversized search response");
    return {
      status: response.status,
      body,
      compressed: Uint8Array.from(gzipSync(new Uint8Array(body), { level: 6 })).buffer,
      contentType: response.headers.get("content-type") ?? "application/json",
    };
  }

  return async function search(request: Request): Promise<Response> {
    try {
      const url = new URL(`${baseURL().replace(/\/+$/, "")}/api/market/v1/search`);
      url.search = new URL(request.url).search;
      if (url.search.length > 4096) return new Response(null, { status: 414 });
      const key = url.href;
      const cached = snapshots.get(key);
      let reply: SearchReply;
      if (cached && clock() < cached.expires) {
        reply = cached.reply;
        snapshots.delete(key);
        snapshots.set(key, cached); // Recency changes; the deadline never slides.
      } else {
        forget(key);
        let task = pending.get(key);
        if (!task) {
          const started = clock();
          task = read(url).then(value => { remember(key, value, started); return value; });
          if (pending.size < maxEntries) {
            pending.set(key, task);
            const clear = () => { if (pending.get(key) === task) pending.delete(key); };
            void task.then(clear, clear);
          }
        }
        reply = await task;
      }
      const gzip = (request.headers.get("accept-encoding") ?? "").split(",").some(part =>
        part.trim().split(";")[0].toLowerCase() === "gzip"
        && !/q=0(?:\.0*)?(?:;|$)/.test(part.replace(/\s/g, "")),
      );
      const body = gzip ? reply.compressed : reply.body;
      // Each reader gets its own response. Fetch has already decoded the body,
      // so upstream Content-Encoding/Content-Length must not be copied.
      return new Response(body.slice(0), {
        status: reply.status,
        headers: {
          "Content-Type": reply.contentType, "Cache-Control": "no-store",
          Vary: "Accept-Encoding", "Content-Length": String(body.byteLength),
          ...(gzip ? { "Content-Encoding": "gzip" } : {}),
        },
      });
    } catch {
      return Response.json({ detail: "종목 검색에 연결하지 못했습니다. 다시 시도해 주세요." }, { status: 503 });
    }
  };
}
