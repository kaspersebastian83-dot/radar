// Radar service worker (v4.15.1).
// The app page and the data files: the network is tried first, so a new version wins when the connection is good,
// but only for a short while (PAGE_WAIT / DATA_WAIT). On a slow or flaky connection the last good copy is shown at once
// and the network answer still lands in the cache for the next start. Without a copy it simply waits for the network.
const C='radar-v2', PAGE_WAIT=1500, DATA_WAIT=4000;
self.addEventListener('install',e=>{ self.skipWaiting();
  e.waitUntil(caches.open(C).then(c=>c.addAll(['./','index.html','manifest.json','icon-192.png','icon.svg'])).catch(()=>{})); });
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=='GET'||u.origin!==location.origin) return;   // GitHub API, CDN: straight through
  const page=e.request.mode==='navigate'||/\/$|\.html$/.test(u.pathname);
  const key=page?u.origin+u.pathname.replace(/index\.html$/,''):u.origin+u.pathname;   // one cache entry per file, ?t=… ignored
  const net=fetch(e.request).then(r=>{ if(r.ok){ const cp=r.clone(); e.waitUntil(caches.open(C).then(c=>c.put(key,cp))); } return r; });
  e.respondWith(caches.match(key).then(old=>{
    if(!old) return net;
    const late=new Promise(ok=>setTimeout(()=>ok(old),page?PAGE_WAIT:DATA_WAIT));
    return Promise.race([net.catch(()=>old),late]);
  }));
});
