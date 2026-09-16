/**
 * Service Worker for INERTIA Investment Management PWA
 * Provides offline capability and asset caching
 */

const CACHE_NAME = 'inertia-app-v3';
const RUNTIME_CACHE = 'inertia-runtime-v3';

// App paths that must always hit network (never serve cached 404/error)
const NETWORK_ONLY_PATHS = /^\/unified-recommendations\/|^\/auth\/|^\/clients\/|^\/dashboard|^\/tools\//;

// Assets to cache on install
const STATIC_ASSETS = [
    '/',
    '/static/css/modern-theme.css',
    '/static/css/style.css',
    '/static/css/logo-theme.css',
    '/static/images/inertia-logo.png',
    '/static/images/inertia-logo.svg',
    '/static/js/main.js',
    '/static/manifest.json'
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
    console.log('[Service Worker] Installing...');
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('[Service Worker] Caching static assets');
                return cache.addAll(STATIC_ASSETS);
            })
            .then(() => {
                console.log('[Service Worker] Installation complete');
                return self.skipWaiting(); // Activate immediately
            })
            .catch((error) => {
                console.error('[Service Worker] Installation failed:', error);
            })
    );
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
    console.log('[Service Worker] Activating...');
    event.waitUntil(
        caches.keys()
            .then((cacheNames) => {
                return Promise.all(
                    cacheNames.map((cacheName) => {
                        if (cacheName !== CACHE_NAME && cacheName !== RUNTIME_CACHE) {
                            console.log('[Service Worker] Deleting old cache:', cacheName);
                            return caches.delete(cacheName);
                        }
                    })
                );
            })
            .then(() => {
                console.log('[Service Worker] Activation complete');
                return self.clients.claim(); // Take control of all pages
            })
    );
});

// Never cache error responses (404, 5xx) so app routes work after server fixes
function shouldCacheResponse(response) {
    return response && response.status === 200;
}

// Fetch event - serve from cache, fallback to network
self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);

    // Skip non-GET requests
    if (request.method !== 'GET') {
        return;
    }

    // Skip cross-origin requests
    if (url.origin !== location.origin) {
        return;
    }

    // App routes: always network, never serve cached response (avoids serving cached 404)
    if (request.mode === 'navigate' && NETWORK_ONLY_PATHS.test(url.pathname)) {
        event.respondWith(
            fetch(request).catch(() => caches.match('/') || caches.match(request))
        );
        return;
    }

    // Strategy: Cache First for static assets, Network First for HTML/API
    if (request.url.match(/\.(css|js|png|jpg|jpeg|gif|svg|woff|woff2|ttf|eot)$/)) {
        // Static assets: Cache First
        event.respondWith(
            caches.match(request)
                .then((cachedResponse) => {
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    return fetch(request)
                        .then((response) => {
                            if (shouldCacheResponse(response)) {
                                const responseToCache = response.clone();
                                caches.open(CACHE_NAME)
                                    .then((cache) => cache.put(request, responseToCache));
                            }
                            return response;
                        });
                })
        );
    } else if (request.url.match(/\.(html|json)$/) || url.pathname === '/') {
        // HTML/JSON: Network First, fallback to cache (never cache 404)
        event.respondWith(
            fetch(request)
                .then((response) => {
                    if (shouldCacheResponse(response)) {
                        const responseToCache = response.clone();
                        caches.open(RUNTIME_CACHE)
                            .then((cache) => cache.put(request, responseToCache));
                    }
                    return response;
                })
                .catch(() => {
                    return caches.match(request)
                        .then((cachedResponse) => {
                            if (cachedResponse) return cachedResponse;
                            return caches.match('/');
                        });
                })
        );
    } else {
        // Default: Network First, never cache 404
        event.respondWith(
            fetch(request)
                .then((response) => {
                    if (shouldCacheResponse(response)) {
                        const clone = response.clone();
                        caches.open(RUNTIME_CACHE).then((cache) => cache.put(request, clone));
                    }
                    return response;
                })
                .catch(() => caches.match(request))
        );
    }
});

// Message event - handle cache updates
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
    
    if (event.data && event.data.type === 'CACHE_UPDATE') {
        // Force update cache
        caches.delete(CACHE_NAME)
            .then(() => {
                return self.registration.update();
            });
    }
});
