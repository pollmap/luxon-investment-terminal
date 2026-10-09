// Static assets stay at the edge; public data goes to the existing API host.
// This avoids a second WAN hop and does not spend the Worker's dynamic quota.
export const EDGE_ORIGIN = "https://korea-stocks-edge.lch68-workers.workers.dev";
export const PUBLIC_ORIGIN = "https://stocks.62-171-141-206.sslip.io";
const PREFIX = "/api/market/v1/";
const readPosts = new Set([PREFIX + "screener", PREFIX + "screener/export", PREFIX + "analysis/scenario"]);

function currentOrigin() {
  return typeof location === "undefined" ? PUBLIC_ORIGIN : location.origin;
}

export function marketUrl(path: string, origin = currentOrigin()) {
  const url = new URL(path, origin);
  if (!path.startsWith(PREFIX) || path.split("?")[0].includes("\\") || url.origin !== origin ||
      !url.pathname.startsWith(PREFIX) ||
      url.pathname.slice(PREFIX.length).split("/").some(part => ["internal", "ops", "account", "accounts", "auth"].includes(part))) {
    throw new Error("지원하지 않는 조회 주소입니다.");
  }
  return origin === EDGE_ORIGIN ? PUBLIC_ORIGIN + url.pathname + url.search : path;
}

export function streamUrl(origin = currentOrigin()) {
  const url = new URL(PREFIX + "stream", origin === EDGE_ORIGIN ? PUBLIC_ORIGIN : origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function marketFetch(path: string, options: RequestInit = {}) {
  const target = marketUrl(path);
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD"].includes(method) && !(method === "POST" && readPosts.has(path.split("?")[0]))) {
    throw new Error("지원하지 않는 조회 방식입니다.");
  }
  const headers = new Headers(options.headers);
  if (["authorization", "cookie", "x-api-key", "proxy-authorization"].some(key => headers.has(key)) || options.credentials === "include") {
    throw new Error("공개 조회에는 인증정보를 보내지 않습니다.");
  }
  return fetch(target, {...options, headers, credentials: "omit"});
}
