// Only the explicit offline page is cached. Financial/API responses remain network-only.
const CACHE='luxon-market-offline-v2';
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.add('/stocks/offline.html')));self.skipWaiting();});
self.addEventListener('activate',event=>{event.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('luxon-market-offline-')&&k!==CACHE).map(k=>caches.delete(k))))]));});
self.addEventListener('fetch',event=>{if(event.request.mode==='navigate'&&new URL(event.request.url).pathname.startsWith('/stocks'))event.respondWith(fetch(event.request).catch(()=>caches.match('/stocks/offline.html')));});
