import { API, type Detail } from "./types";
import {marketFetch} from "./public-transport";

/** One validator per mounted detail; no persistent/shared cache of market data. */
export function detailLoader(code: string, signal: AbortSignal, view = "summary") {
  let etag = "";
  return async (): Promise<Detail | null> => {
    const response = await marketFetch(`${API}/securities/${code}?view=${view}`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
      cache: "no-store",
      headers: etag ? { "If-None-Match": etag } : {},
    });
    if (response.status === 304 && etag) return null;
    if (!response.ok) throw new Error("종목 정보를 불러오지 못했습니다.");
    const data: Detail = await response.json();
    if (data.security?.code !== code) throw new Error("다른 종목의 응답입니다.");
    // Do not accept the validator until the body has been received and parsed.
    etag = response.headers.get("ETag") || "";
    return data;
  };
}
