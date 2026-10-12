# browser-profiles

Persistent Chromium profiles, each behind its own proxy, that you open from any
browser. One profile = one directory on disk (cookies, logins, extensions
survive restarts) + one proxy + one time zone. A small token-protected API
creates, starts, stops and inspects them; a plain page at `/` does the same by
hand.

```
you ──HTTPS──▶ gateway (nginx :8080)
                 ├── /            API + page            (api container)
                 ├── /profiles/*  JSON API, bearer token
                 └── /b/<name>/   the profile's screen  (bp-<name> container)

bp-<name>        lscr.io/linuxserver/chromium — Chromium on a virtual display,
                 streamed to the browser (Selkies), home dir = <data>/<name>/config
bp-<name>-proxy  the forwarder: gogost/gost sharing bp-<name>'s network namespace,
                 Chromium → 127.0.0.1:18080 → your proxy (with credentials)
```

Only the gateway publishes a port; the profile containers are reachable just
through it. Chromium itself never sees the proxy password: it uses the local
unauthenticated forwarder, which adds the credentials upstream. Host names are
resolved by the proxy (HTTP CONNECT / SOCKS5), so DNS does not leak.

## Run

Needs Docker with Compose. The API (a Python/FastAPI service) talks to the
Docker socket to create the per-profile containers next to itself.

```bash
cp .env.example .env      # set API_TOKEN, HOST_DATA_DIR, PUBLIC_URL
mkdir -p "$(grep ^HOST_DATA_DIR .env | cut -d= -f2)"
docker compose up -d --build
curl -s localhost:8080/healthz
```

`.env`:

| Variable | Meaning |
|---|---|
| `API_TOKEN` | Bearer token for `/profiles*`. |
| `HOST_DATA_DIR` | Directory on the Docker host with one sub-directory per profile. The API asks Docker to bind-mount `$HOST_DATA_DIR/<name>/config` into each browser container, and Docker resolves that path on the host, so it must be a host path, not a path inside the API container. |
| `PUBLIC_URL` | How you reach the gateway; used to build `ui_url` in responses. |
| `LISTEN` | Gateway bind, default `127.0.0.1:8080`. |
| `BROWSER_UI_USER` / `BROWSER_UI_PASSWORD` | Optional HTTP basic auth on the profile screens (`/b/<name>/`) only; `/profiles*` always uses the bearer token. |
| `EGRESS_URL` | What the egress check fetches through the proxy; default `https://ipinfo.io/json`. Set it in the `api` service environment. |
| `IDLE_STOP_MINUTES` | Stop a running profile once no one has had its screen open for this long (default 15; 0 = never). A viewer holds a WebSocket to the profile's container, so "open screen" = an established connection on its screen port, checked every 30 s. Cookies stay; `start` brings it back. |

The profile screens have no authentication beyond that optional basic auth.
Expose the gateway only through something that authenticates (Cloudflare
Access, an SSO proxy, a VPN) or keep it on localhost.

Images are pulled on first use; the first `start` takes a minute.

## API

All `/profiles` routes require `Authorization: Bearer $API_TOKEN`. OpenAPI
(Swagger) docs at `/docs`.

```bash
# create and start a profile behind an authenticated HTTP proxy
curl -s -X POST localhost:8080/profiles -H "Authorization: Bearer $API_TOKEN" \
  -H 'content-type: application/json' -d '{
    "name": "ie-1",
    "proxy": {"scheme": "http", "host": "proxy.example.com", "port": 10001,
              "username": "user", "password": "pass"},
    "timezone": "Europe/Dublin",
    "start_url": "https://example.com"
  }'
```

```json
{
  "name": "ie-1",
  "proxy": "http://user:***@proxy.example.com:10001",
  "timezone": "Europe/Dublin",
  "start_url": "https://example.com",
  "status": "running",
  "ui_url": "http://localhost:8080/b/ie-1/",
  "data_dir": "/srv/browser-profiles/ie-1",
  "created_at": "2026-09-04T12:00:00Z"
}
```

| Route | What it does |
|---|---|
| `GET /profiles` | Every profile with its status (`running`, `stopped`, `absent` = no containers), redacted proxy, directory and screen URL. |
| `POST /profiles` | Create; `"start": false` to only write it down. Names: `[a-z0-9][a-z0-9-]{0,30}`. `proxy` may be omitted for a direct connection; `scheme` is `http` or `socks5`. |
| `GET /profiles/{name}` | One profile. |
| `PATCH /profiles/{name}` | Change `proxy`, `timezone`, `start_url`, `logins` (`"clear_proxy": true` removes the proxy). A running profile has its two containers removed and created again so the change applies; the directory, and so cookies and logins, stays. Reload an open screen afterwards. A PATCH that changes nothing touches nothing, so a caller can send its whole wanted state every time. |
| `POST /profiles/{name}/start` | Create the two containers if they are not running and return once the screen answers (up to 90 s), so `ui_url` can be opened straight away. Meanwhile the screen URL shows a page that retries every 3 seconds. Also the fix when the screen loads but pages don't: the forwarder shares the browser container's network namespace, and if Docker restarted only the browser that link is gone; `start` recreates both. |
| `POST /profiles/{name}/stop` | Stop the containers, keep everything on disk. |
| `DELETE /profiles/{name}` | Remove the containers and forget the profile. `?purge=true` also deletes the directory; without it the Chromium home stays and a new profile of the same name reuses it. |
| `POST /profiles/{name}/egress` | Dial the profile's proxy from the API and return what `EGRESS_URL` sees (exit IP, city, country); works while stopped. |

### Saved logins

A profile may carry `logins`, each `{"url", "username", "password",
"totp_secret"}` (`totp_secret` optional: the base32 key an authenticator app
is set up with). The profile's Chromium then runs a built-in extension on each
login's site (its host without `www.`, subdomains included):

- with one login for the site, an empty sign-in form is filled as soon as it
  appears — never submitted, never overwritten once filled;
- a panel pinned to the right edge lists the logins: click the username to
  fill the form, the password to copy it, the six-digit code (refreshed every
  30 s) to type it into the code field, or copy it when there is none. The 🔑
  header folds the panel into a tab.

Responses show each login's `url`, `username` and `totp` (whether it has a
key), never the password or the key. Chromium reads the extension at launch,
so a change of logins recreates a running profile, as any PATCH does.

Open `ui_url` in a browser to use the profile. The screen page offers the
clipboard, file upload/download (`/b/<name>/files`) and a resolution that
follows the window.

## Layout on disk

```
$HOST_DATA_DIR/
└── ie-1/
    ├── profile.json   # name, proxy, logins (plaintext credentials), timezone, start_url
    ├── config/        # Chromium home: cookies, history, extensions, downloads
    └── extension/     # the saved-logins extension, rewritten at every start
```

Back up or move a profile by copying its directory. Credentials are stored in
plaintext and are visible in `docker inspect` of the forwarder container, so
treat the host as trusted.

## Development

```bash
uv sync
uv run ruff format && uv run ruff check && uv run pyright && uv run pytest
uv run uvicorn app.main:app --reload    # needs DATA_DIR, API_TOKEN, a local Docker
```
