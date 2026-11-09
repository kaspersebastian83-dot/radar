// Radar service worker: network first, so a new version always wins when online;
// the last good copy of the app and its data when offline (or GitHub Pages is slow).
const C='radar-v1';
self.addEventListener('install',e=>{ self.skipWaiting();
  e.waitUntil(caches.open(C).then(c=>c.addAll(['./','index.html','manifest.json','icon-192.png','icon.svg'])).catch(()=>{})); });
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=='GET'||u.origin!==location.origin) return;   // GitHub API, CDN: straight through
  const key=u.origin+u.pathname;                                      // prices.json?t=… → one cache entry
  e.respondWith(fetch(e.request).then(r=>{ if(r.ok){ const cp=r.clone(); caches.open(C).then(c=>c.put(key,cp)); } return r; })
    .catch(()=>caches.match(key).then(r=>r||caches.match(u.origin+u.pathname.replace(/[^/]*$/,'')))));
});
