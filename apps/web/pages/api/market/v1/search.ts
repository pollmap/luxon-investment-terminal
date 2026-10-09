import type { NextApiRequest, NextApiResponse } from "next";
import { createSearchProxy } from "../../../../lib/market-search-proxy";

const search = createSearchProxy();

// This small JSON endpoint does not need the App Router rendering pipeline.
export const config = { api: { bodyParser: false, responseLimit: "512kb" } };

export default async function handler(request: NextApiRequest, response: NextApiResponse) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.setHeader("Allow", "GET, HEAD");
    response.status(405).end();
    return;
  }
  const started = performance.now();
  const query = (request.url ?? "").split("?").slice(1).join("?");
  const reply = await search(new Request(`http://localhost/api/market/v1/search?${query}`, {
    headers: { "accept-encoding": request.headers["accept-encoding"] ?? "" },
  }));
  response.statusCode = reply.status;
  reply.headers.forEach((value, name) => response.setHeader(name, value));
  response.setHeader("Server-Timing", `search;dur=${(performance.now() - started).toFixed(2)}`);
  response.end(request.method === "HEAD" ? undefined : Buffer.from(await reply.arrayBuffer()));
}
