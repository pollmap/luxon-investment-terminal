import assert from 'node:assert/strict';
import test from 'node:test';
import { gunzipSync } from 'node:zlib';
import { createSearchProxy } from '../../lib/market-search-proxy.ts';

const request = (q = '삼성', options) => new Request(`http://localhost/api/market/v1/search?q=${encodeURIComponent(q)}`, options);

test('shares 100 overlapping reads, next read checks the source again', async () => {
  let resolve, count = 0;
  const gate = new Promise(r => { resolve = r; });
  const proxy = createSearchProxy({ maxAgeMs: 0, baseURL: () => 'http://api:8210', fetcher: async () => {
    count++; await gate; return Response.json({ revision: count });
  } });
  const work = Array.from({ length: 100 }, () => proxy(request()));
  assert.equal(count, 1);
  resolve();
  const responses = await Promise.all(work);
  assert.deepEqual(await Promise.all(responses.map(r => r.json())), Array(100).fill({ revision: 1 }));
  assert.equal(responses[0].headers.get('cache-control'), 'no-store');
  assert.deepEqual(await (await proxy(request())).json(), { revision: 2 });
});

test('does not share different queries, cookies, credentials or cancellation', async () => {
  const calls = [];
  const controller = new AbortController();
  const proxy = createSearchProxy({ baseURL: () => 'http://api:8210', fetcher: async (url, options) => {
    calls.push({ url: url.href, options });
    return Response.json({ q: url.searchParams.get('q') });
  } });
  const a = proxy(request('005930', { signal: controller.signal, headers: { Cookie: 'private=fixture', Authorization: 'Bearer fixture' } }));
  controller.abort();
  const b = proxy(request('005935'));
  assert.deepEqual(await (await a).json(), { q: '005930' });
  assert.deepEqual(await (await b).json(), { q: '005935' });
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal(new URL(call.url).host, 'api:8210');
    assert.equal(call.options.headers.Cookie, undefined);
    assert.equal(call.options.headers.Authorization, undefined);
    assert.equal(call.options.signal.aborted, false);
  }
});

test('failed source calls are retryable and validation status stays unchanged', async () => {
  let count = 0;
  const proxy = createSearchProxy({ fetcher: async () => {
    if (++count === 1) throw new Error('fixture failure');
    return Response.json({ detail: 'Query too long' }, { status: 422 });
  } });
  assert.equal((await proxy(request())).status, 503);
  const retry = await proxy(request());
  assert.equal(retry.status, 422);
  assert.deepEqual(await retry.json(), { detail: 'Query too long' });
  assert.equal(count, 2);
});

test('does not forward compressed headers for the decoded fetch body', async () => {
  const proxy = createSearchProxy({ fetcher: async () => new Response('{"rows":[]}', {
    headers: { 'Content-Type': 'application/json', 'Content-Encoding': 'gzip', 'Content-Length': '999', 'Set-Cookie': 'private=fixture' },
  }) });
  const result = await proxy(request());
  assert.deepEqual(await result.json(), { rows: [] });
  for (const name of ['content-encoding', 'set-cookie']) assert.equal(result.headers.get(name), null);
  assert.equal(result.headers.get('content-length'), '11');
});

test('transfers compressed bytes only when gzip is accepted', async () => {
  const value = { rows: [{ code: '005930', name: 'fixture_non_production' }] };
  const proxy = createSearchProxy({ fetcher: async () => Response.json(value) });
  const encoded = await proxy(request('삼성', { headers: { 'Accept-Encoding': 'gzip, deflate' } }));
  assert.equal(encoded.headers.get('content-encoding'), 'gzip');
  assert.equal(encoded.headers.get('vary'), 'Accept-Encoding');
  const bytes = await encoded.arrayBuffer();
  assert.equal(Number(encoded.headers.get('content-length')), bytes.byteLength);
  assert.deepEqual(JSON.parse(gunzipSync(new Uint8Array(bytes))), value);
  const plain = await proxy(request('삼성', { headers: { 'Accept-Encoding': 'gzip;q=0' } }));
  assert.equal(plain.headers.get('content-encoding'), null);
  assert.deepEqual(await plain.json(), value);
});

test('250ms source deadline does not slide under repeated search hits', async () => {
  let time = 0, count = 0;
  const proxy = createSearchProxy({ clock: () => time, fetcher: async () => Response.json({ revision: ++count }) });
  assert.deepEqual(await (await proxy(request())).json(), { revision: 1 });
  for (time of [100, 200, 249]) assert.deepEqual(await (await proxy(request())).json(), { revision: 1 });
  time = 250;
  assert.deepEqual(await (await proxy(request())).json(), { revision: 2 });
  assert.equal(count, 2);
});

test('slow reads, oversize snapshots and LRU limits cannot retain an old result', async () => {
  let time = 0, count = 0;
  const slow = createSearchProxy({ clock: () => time, fetcher: async () => {
    time += 251; return Response.json({ revision: ++count });
  } });
  await slow(request()); await slow(request());
  assert.equal(count, 2);
  const tiny = createSearchProxy({ maxBytes: 1, fetcher: async () => Response.json({ revision: ++count }) });
  await tiny(request()); await tiny(request());
  assert.equal(count, 4);
  const bounded = createSearchProxy({ maxEntries: 1, clock: () => time,
    fetcher: async () => Response.json({ revision: ++count }) });
  await bounded(request('005930')); await bounded(request('005935')); await bounded(request('005930'));
  assert.equal(count, 7);
});
