
const CACHE = "football-pwa-v08-fixtures";

const SHELL = [
    "/",
    "/manifest.json",
    "/icon-192.png",
    "/icon-512.png"
];

self.addEventListener("install", event => {
    event.waitUntil(
        caches.open(CACHE).then(
            cache => cache.addAll(SHELL)
        )
    );

    self.skipWaiting();
});

self.addEventListener("activate", event => {
    event.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.filter(key => key !== CACHE)
                .map(key => caches.delete(key))
        ))
    );

    self.clients.claim();
});

self.addEventListener("fetch", event => {
    const request = event.request;
    const url = new URL(request.url);

    if (
        request.method !== "GET" ||
        url.origin !== self.location.origin ||
        url.pathname.startsWith("/api/")
    ) return;

    if (request.mode === "navigate") {
        event.respondWith(
            fetch(request).catch(
                () => caches.match("/")
            )
        );
    } else if (SHELL.includes(url.pathname)) {
        event.respondWith(
            caches.match(request).then(
                saved => saved || fetch(request)
            )
        );
    }
});
