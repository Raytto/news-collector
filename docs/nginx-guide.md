# AgentDuck frontend nginx guide

This guide publishes only the user‑facing frontend (Vite build in `frontend/`)
behind `https://us.pangruitao.com/agentduck/`. All externally reachable paths
live under `/agentduck` (including assets). Backend services remain bound to
`127.0.0.1` and are not reverse‑proxied.

## 1. Build the production bundle

Ensure the build emits assets and links under the `/agentduck/` prefix:

- Option A (one‑off build): `npm run build -- --base=/agentduck/`
- Option B (persistent): set `base: '/agentduck/'` in `frontend/vite.config.ts`
  under `defineConfig({ base: '/agentduck/', ... })`
  
If using React Router, also set `basename="/agentduck"` on `BrowserRouter` so
links resolve under the prefix.

```
cd /home/pp/mp/news-collector/frontend
npm install   # skip when node_modules is already up to date
npm run build
```

The build produces `frontend/dist/` with `index.html` and the hashed assets
under `frontend/dist/assets/`.

## 2. Stage the static files for nginx

Nginx runs as `www-data`, so move the build output to a world-readable path
outside `/home/pp` and keep a symlink for quick updates.

```
sudo mkdir -p /var/www/agentduck
sudo rsync -av --delete frontend/dist/ /var/www/agentduck/
# optional: keep a pointer in your workspace for convenience
ln -sfn /var/www/agentduck /home/pp/mp/news-collector/frontend/published
sudo chown -R www-data:www-data /var/www/agentduck
```

Each time you rebuild, re-run the `rsync` command.

## 3. Update `/etc/nginx/sites-available/ganghaofan`

Make a backup first.

```
sudo cp /etc/nginx/sites-available/ganghaofan \
  /etc/nginx/sites-available/ganghaofan.$(date +%Y%m%d_%H%M%S).bak
```

Add the new locations inside the TLS (`listen 443`) server block. Place the
snippet before any more specific locations so the SPA fallback is checked first.

```nginx
    # AgentDuck frontend (all public paths live under /agentduck)
    location = /agentduck {
        return 301 /agentduck/;
    }

    # Cache immutable assets aggressively
    location ^~ /agentduck/assets/ {
        alias /var/www/agentduck/assets/;
        try_files $uri $uri/ =404;
        access_log off;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    # HTML + SPA routes (no-store to avoid stale shell)
    location ^~ /agentduck/ {
        alias /var/www/agentduck/;
        index index.html;
        try_files $uri $uri/ /agentduck/index.html;
        add_header Cache-Control "no-store";
    }

    # Optional: health probe behind the prefix
    location = /agentduck/healthz {
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }
```

Note: This guide intentionally does not expose any backend (`/api`, etc.).
Keep backend services bound to `127.0.0.1` only. If the frontend issues API
requests, update it to use the same `/agentduck` prefix and add a dedicated
proxy later (e.g. `location ^~ /agentduck/api/ { ... }`).

If you are keeping a Vite dev server online for debugging, add a temporary
block before the static one that proxies to `http://127.0.0.1:5180`. Comment it
out in production to avoid leaking the dev tooling:

```nginx
    # location ^~ /agentduck/ {
    #     proxy_pass http://127.0.0.1:5180/;
    #     proxy_http_version 1.1;
    #     proxy_set_header Upgrade $http_upgrade;
    #     proxy_set_header Connection "upgrade";
    #     proxy_set_header Host $host;
    #     proxy_set_header X-Real-IP $remote_addr;
    #     proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    #     proxy_set_header X-Forwarded-Proto $scheme;
    #     proxy_read_timeout 3600;
    #     proxy_send_timeout 3600;
    # }
```

## 4. Validate and reload nginx

```
sudo nginx -t
sudo systemctl reload nginx
```

If `nginx -t` fails, revert to the backup and investigate the syntax error.

## 5. Smoke test

1. Open `https://us.pangruitao.com/agentduck/` in a private tab.
2. Load DevTools → Network to confirm `/agentduck/assets/...` responds with `200`
   and has the immutable cache headers.
3. Navigate the app: deep links like
   `https://us.pangruitao.com/agentduck/<route>` should render via the SPA
   fallback.
4. Confirm no backend endpoints are reachable externally (e.g. `/api` 404).

## Rollback

```
sudo cp /etc/nginx/sites-available/ganghaofan.YYYYMMDD_HHMMSS.bak \
  /etc/nginx/sites-available/ganghaofan
sudo systemctl reload nginx
```

After rolling back, remove `/var/www/agentduck` if it is no longer needed.
