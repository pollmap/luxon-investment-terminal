// Shared by the edge redirect and the static Nginx compatibility page.
const pages = { screener: 'screener', portfolio: 'watchlist' };
const analysis = {
  historical: 'valuation', graph: 'valuation', performance: 'valuation',
  forecasting: 'valuation', forecast: 'valuation', financials: 'financials',
  consensus: 'estimates', peers: 'peers',
};

export function legacyDestination(input) {
  const url = input instanceof URL ? new URL(input) : new URL(input, 'https://local.invalid');
  const path = url.pathname.replace(/\/$/, '') || '/';
  const match = path.match(/^\/company\/([^/]+)(?:\/([^/]+))?$/);
  if (!match && !['/', '/terminal', '/screener', '/portfolio', '/system'].includes(path)) return null;
  const params = new URLSearchParams(url.search);
  let routeCode, routeView;
  try { routeCode = match && decodeURIComponent(match[1]); routeView = match?.[2] && decodeURIComponent(match[2]); }
  catch { return null; }
  const oldTab = (routeView || params.get('tab') || '').toLowerCase();
  const ticker = routeCode || params.get('ticker');
  const code = ticker?.match(/^([A-Z0-9]{6})(?:\.(?:KS|KQ))?$/i)?.[1].toUpperCase();
  if (match && !code) return null;
  if (code && !params.has('code')) params.set('code', code);
  params.delete('ticker');
  const page = pages[path.slice(1)] || pages[oldTab];
  if (page && !params.has('page')) params.set('page', page);
  if (analysis[oldTab]) {
    params.set('tab', oldTab === 'financials' ? '재무·지표' : '기업·주주');
    if (!params.has('analysis')) params.set('analysis', analysis[oldTab]);
  } else if (oldTab === 'summary' || oldTab === 'snapshot') params.set('tab', '요약');
  else if (['screener', 'portfolio', 'system', 'data audit'].includes(oldTab)) params.delete('tab');
  if (match && !params.has('tab')) {
    params.set('tab', '기업·주주');
    params.set('analysis', 'valuation');
  }
  return '/stocks' + (params.size ? '?' + params.toString() : '') + url.hash;
}
