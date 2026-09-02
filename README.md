# One-Time Text Bridge

A small, privacy-first web app for moving **non-sensitive** text, a photo, or
one file between two phones. Create a link, share it once, and it self-destructs.

> **This is not a chat app, a file-sharing service, or a way to bypass an
> employer's security policy.** It is meant for short personal text only.
>
> **Do not use this service for work-confidential data, passwords, MFA
> codes, financial data, personal data, or anything restricted by your
> organisation.**

## How it works

1. `/` — landing page with "Create one-time text" and "Open received text".
2. `/create` — write up to 2,000 characters or select one file/photo up to
  10 MiB, pick an expiry (5/10/30 minutes, default 10), confirm the content
  is not sensitive, and submit.
3. The server generates a cryptographically secure random 256-bit token
   (`secrets.token_urlsafe`). Only the **SHA-256 hash** of the token is
   stored in SQLite — the raw token is never written to disk or logs.
4. You get a one-time link `/r/<raw-token>` and a QR code for it.
5. `/r/<raw-token>` shows a reveal or download button. Pressing it atomically
  marks the share as consumed, then displays the text or downloads the file
  exactly once.
   After that, or after expiry, the link shows a generic "unavailable" page.
6. Expired and consumed messages are deleted automatically (on startup, on a
   recurring interval, and via a CLI command).

### Why "Reveal text" instead of consuming on first load?

Many things fetch a URL without human intent: chat-app link unfurlers,
antivirus/security URL scanners, and browser prefetching. If the first GET
request consumed the message, a real recipient could find their link
already burned before they ever saw it. Instead, `GET /r/<token>` only
validates the token (without mutating anything) and renders a page with a
"Reveal text" button. The **POST** to `/r/<token>/reveal` performs a single
atomic `UPDATE ... WHERE consumed = false` and only returns the text if that
update affected exactly one row. This preserves "viewable exactly once"
while being robust against non-human first requests. See
`app/services/messages.py` for the implementation and this reasoning in
code.

## The link is the secret

There are no user accounts. **The long random one-time URL (and the QR code
that encodes it) is the entire access credential.** Anyone who has the link
or scans the QR code can read the message once. Treat the link like a
password:

- Share it only through a channel you trust, with only the intended recipient.
- Don't post it anywhere public, and don't leave it in shared clipboard
  history, chat logs, or screenshots.
- If you're worried it was intercepted, that's exactly why links expire
  quickly and can only be opened once.

## Optional hardening (not required for local development)

For higher-trust deployments you can add either:

- **A shared passphrase**: `SHARED_PASSPHRASE` is read into
  `app/config.py`'s `Settings.shared_passphrase` but is not wired into the
  request flow. To use it, add a passphrase field to the create/receive
  forms and check it (with a constant-time comparison) before creating or
  revealing a message.
- **An identity-aware proxy** in front of Caddy, such as
  [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
  or [Tailscale](https://tailscale.com/) (e.g. exposing the app only on
  your tailnet). This restricts *who can reach the app at all*, independent
  of the one-time-link mechanism.

Neither is required to run the app locally or in a low-stakes deployment.

## Project structure

```
app/
  main.py                # FastAPI app, routes, middleware
  config.py               # Environment-based settings
  database.py              # SQLAlchemy engine/session setup
  models.py                 # Message ORM model
  security.py                # Token gen/hash, CSRF, rate limiting, headers
  cleanup.py                  # Cleanup task + CLI entrypoint
  services/messages.py         # Message create/reveal/delete/cleanup logic
  templates/                    # Jinja2 templates
  static/css, static/js           # Vanilla CSS/JS, no frameworks
tests/                              # pytest suite
requirements.txt
Dockerfile
docker-compose.yml
Caddyfile
.env.example
web.config                 # IIS reverse-proxy configuration
.github/workflows/release.yml # CI and Windows release package
```

## Local setup (Python virtual environment)

Requires Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # edit if you want, defaults work locally
mkdir -p data

uvicorn app.main:app --reload --port 8000
```

Visit http://localhost:8000.

Run the cleanup CLI manually at any time:

```bash
python -m app.cleanup
```

Run the tests:

```bash
pytest
```

## Docker Compose (local)

```bash
cp .env.example .env   # set ENVIRONMENT=development for local use
docker compose up --build
```

This starts the app behind Caddy. For pure local testing without HTTPS you
can instead run just the `app` service and hit it directly on port 8000:

```bash
docker compose run --rm --service-ports app
```

## Windows IIS deployment (no Docker)

Each pushed `v*` tag runs the GitHub Actions **Build Windows release** workflow.
It tests the application and attaches `one-time-text-bridge-windows.zip` to the
corresponding [GitHub Release](../../releases). Download and extract that ZIP on
the Windows server, for example to `C:\inetpub\one-time-text-bridge`.

1. Install Python 3.12, IIS, the IIS **URL Rewrite** module, and **Application
   Request Routing (ARR)**. In ARR's Server Proxy Settings, enable proxying.
2. In the extracted directory, create and activate a virtual environment, then
   install the pinned dependencies:
   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and set the production values:
   ```ini
   ENVIRONMENT=production
   SECRET_KEY=<generate with: py -c "import secrets; print(secrets.token_urlsafe(48))">
   ALLOWED_HOSTS=example.com
   BASE_URL=https://example.com
   TRUSTED_PROXY_IPS=127.0.0.1
   ```
   Keep `.env` and the `data` directory out of source control and back up
   `data\app.db` as appropriate.
4. Start the application on the local loopback interface, using a Windows
   service manager such as NSSM or an equivalent service wrapper:
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
   Set the service working directory to the extracted release directory so the
   SQLite database is stored under `data`.
5. In IIS Manager, create a site whose physical path is the extracted release
   directory. Bind the site to `https` with its certificate and hostname.
   The included `web.config` proxies requests to the loopback Uvicorn process
   and sends the forwarded HTTPS headers the application requires.

Do not expose port 8000 through Windows Firewall; only IIS should be publicly
reachable. The app must stay as a single process when using its built-in
in-memory rate limiter and SQLite database.

## Production deployment (Docker Compose behind Caddy)

1. Point a DNS record at your server and edit `Caddyfile`, replacing
   `example.com` with your real domain. Caddy will automatically obtain and
   renew a Let's Encrypt certificate.
2. Create a real `.env` (never commit it) based on `.env.example` with, at
   minimum:
   ```
   ENVIRONMENT=production
   SECRET_KEY=<output of: python -c "import secrets; print(secrets.token_urlsafe(48))">
   ALLOWED_HOSTS=example.com
   BASE_URL=https://example.com
   TRUSTED_PROXY_IPS=172.28.0.20   # Caddy's static IP in docker-compose.yml
   ```
   The app **refuses to start** in production if `SECRET_KEY`,
   `ALLOWED_HOSTS`, or a valid `https://` `BASE_URL` are missing (see
   `Settings.validate_for_startup` in `app/config.py`).
3. Start the stack:
   ```bash
   docker compose up -d --build
   ```
4. The `app` container is not published on the host; only Caddy (ports
   80/443) is. Caddy terminates TLS and forwards `X-Forwarded-Proto` /
   `X-Forwarded-For` / `X-Forwarded-Host` to the app. The app only trusts
   those headers when the direct peer is `TRUSTED_PROXY_IPS` (Caddy's
   pinned IP in the `internal` docker network), and redirects any
   non-HTTPS request to HTTPS when `ENVIRONMENT=production`.
5. The SQLite database lives in the `app-data` named volume, mounted at
   `/app/data` inside the container, so it survives container restarts/rebuilds.
6. Cleanup of expired/consumed messages runs automatically inside the app
   process (on startup and every `CLEANUP_INTERVAL_SECONDS`). You can also
   run it as a one-off:
   ```bash
   docker compose exec app python -m app.cleanup
   ```

## Security features

- HTTPS enforced (redirect) in production; TLS terminated by Caddy.
- Trusted proxy headers: `X-Forwarded-*` only honoured from a configured
  proxy IP.
- Security headers on every response: `Cache-Control: no-store, max-age=0`,
  `Pragma: no-cache`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, a restrictive `Content-Security-Policy`,
  `Referrer-Policy: no-referrer`, and a locked-down `Permissions-Policy`.
- Only a SHA-256 hash of the one-time token is stored — never the raw token.
- Message text is never put into URLs, logs, exception messages, page
  titles, or the database query string; only generic errors are shown for
  invalid/expired/consumed links.
- CSRF protection (signed double-submit token) on all state-changing POSTs.
- Server-side input validation (message length 1–2000 chars, expiry in
  {5, 10, 30} minutes).
- All displayed text is HTML-escaped by Jinja2's autoescaping (XSS-safe).
- Simple in-memory per-IP rate limiting on create/receive endpoints. **This
  limiter is per-process and unsuitable for multi-instance production
  deployments** — replace it with a shared store (e.g. Redis) if you scale
  horizontally.
- Automatic deletion of expired/consumed rows (startup, interval, and CLI).

## Threat model

### What this app protects against

- **Casual link exposure / replay**: a receive link can only be used once;
  after it's viewed (or expires), the underlying row is deleted.
- **Server data at rest, long-term**: expired/consumed messages are
  automatically purged, minimizing the amount of text sitting in the
  database at any time.
- **Network eavesdropping in transit**: HTTPS is enforced in production.
- **XSS via message content**: all message text is escaped before display.
- **CSRF** on the create/reveal/delete forms.
- **Basic scraping/brute-force of tokens**: tokens are 256-bit random
  values, hashed at rest, and rate limiting slows naive guessing.
- **Casual log/analytics leakage**: no analytics, trackers, or third-party
  services are used; raw tokens and message text are never logged.

### What this app does **not** protect against

- **A compromised phone.** If the sender's or recipient's device is
  compromised (malware, shoulder-surfing, unlocked device), the text (or
  the link/QR code) can be captured regardless of anything this app does.
- **The recipient sharing the link or the text.** Once revealed, the
  recipient can copy, screenshot, forward, or retype the content anywhere.
  There is no way to enforce "one reader" once content leaves the app.
- **Screenshots or photos of the screen**, at either end.
- **Browser/device management or MDM policies.** This app does not detect
  or enforce corporate device-management or acceptable-use policies. It is
  the sender's/recipient's responsibility to follow their organisation's
  rules — this app is not a way around them.
- **An employer's (or any organisation's) security policy.** Sending
  anything work-confidential, regulated, or restricted through this tool is
  out of scope and against its intended use, regardless of technical
  controls.
- **A malicious or compromised server operator.** The self-hosting party
  can read the SQLite database directly (message text is stored as plain
  text, not client-side end-to-end encrypted). Only run this on
  infrastructure you trust.
- **Traffic analysis or metadata correlation** by network intermediaries
  (e.g. that two IPs used the service around the same time).
- **Multi-instance rate-limit bypass.** The built-in rate limiter is
  in-memory per process; it does not protect a horizontally scaled
  deployment without a shared backing store.

## License

No license specified; all rights reserved unless a `LICENSE` file is added.
