# Outlook Mail Reader (`/outlook`)

Read-only Outlook mailbox access over Microsoft Graph, with OAuth 2.0 Authorization Code Flow.
Built so an agent can read mail through REST and **cannot** modify anything: every Graph call is a `GET`
(list folders, list messages, get message). There is no send, delete, move or update code path.

---

## 1. Azure app registration

1. **Microsoft Entra admin center → App registrations → New registration.**
   - Supported account types: *Accounts in this organizational directory only* (single tenant) is the safest choice;
     `common` / `organizations` also work for multi-tenant scenarios.
2. **Authentication → Add a platform → Web**, redirect URI:

   ```
   http://localhost:5001/outlook/oauth/callback
   ```

   The port must match `port` in `config.json` (default `5001`). The URL is visible and editable in the module UI
   (`REDIRECT_URI`) and must be identical character-for-character to the one registered in Azure.
3. **Certificates & secrets → New client secret.** Copy the *value* (not the secret ID).
4. **API permissions → Microsoft Graph → Delegated permissions:**

   | Permission | Why |
   |---|---|
   | `Mail.Read` | Read messages in the signed-in mailbox |
   | `User.Read` | Resolve the signed-in identity (`/me`) |
   | `Mail.Read.Shared` | **Only if** you read other mailboxes (shared mailboxes, `team@...`) |

   `offline_access` is added automatically by MSAL, which is what produces the refresh token.
   Click **Grant admin consent** if your tenant requires it (typical with `Mail.Read.Shared`).

Nothing is written to a tracked file: the client secret, tokens and aliases are stored in
`instance/outlook_mail.json` (already git-ignored, created with `0600`).

---

## 2. First login

1. Open `http://localhost:5001/outlook/`.
2. Expand **OAuth settings** and fill in `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID`, `REDIRECT_URI`, `SCOPES`.
3. **SAVE_OAUTH_SETTINGS**.
4. **LOGIN_WITH_MICROSOFT** → a dedicated Microsoft window opens → sign in and consent.
   The window closes itself and the module flips to `AUTHENTICATED: YES` on its own.
   If the browser blocks the popup, the login continues in the same tab (`OPEN_IN_SAME_TAB` does it explicitly).

Tokens are cached and refreshed silently. A `401 auth_required` on any endpoint means the refresh token is
gone or expired: log in again.

> Changing `client_id`, `tenant_id` or the secret invalidates the cached tokens on purpose,
> so a stale identity can never be reused.

### Why the popup does not use postMessage

Microsoft sends `Cross-Origin-Opener-Policy` on the sign-in pages, which severs every link between the popup
and the page that opened it: `window.closed`, `window.location` and `postMessage` all stop working. The module
therefore opens the window synchronously inside the click (so popup blockers allow it) and detects completion
by polling the server, where the callback records the outcome under a `generation` id created by the UI.
This is also why the popup can be closed at any moment without breaking the flow: the token exchange has
already happened server-side.

---

## 3. REST API for agents

Base: `/outlook/api`. Self-describing contract: `GET /outlook/api/query`.

### Access control

By default the API is open (it is a local tool). If you set an `API_TOKEN` in the module settings, external
callers must send it; the browser UI keeps working through its signed session cookie.

```bash
curl -H "X-API-Token: $TOKEN" http://localhost:5001/outlook/api/status
curl -H "Authorization: Bearer $TOKEN" http://localhost:5001/outlook/api/status
```

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Connection/login state (never returns the secret) |
| GET | `/api/me` | Signed-in identity |
| GET | `/api/folders` | Recursive folder list, with `path` usable as a filter value |
| GET | `/api/messages` | List messages |
| GET | `/api/messages/<id>` | Full message, body and headers |
| POST | `/api/logout` | Drop the cached tokens |

### `/api/messages` parameters

| Parameter | Meaning |
|---|---|
| `mailbox` | `me`/empty = signed-in user; otherwise a UPN, user id, or a configured alias |
| `folders` | Comma-separated folder paths, names or ids — **multiple folders are federated server-side** |
| `folder` | Single-folder shorthand |
| `top` | 1–1000 per folder (default 25) |
| `skip` | Offset (not allowed with `search`) |
| `filter` | Raw OData `$filter`, AND-ed with the convenience filters |
| `from` | `from/emailAddress/address eq '...'` |
| `subject` | `contains(subject,'...')` |
| `since` / `until` | `receivedDateTime` bounds, ISO 8601 |
| `unread` | `true` → `isRead eq false` |
| `has_attachments` | `true` → `hasAttachments eq true` |
| `search` | Free text or KQL. **Cannot be combined with `$filter`** |
| `search_from` / `search_subject` / `search_body` | KQL equivalents, for when you need search + sender/subject |
| `kql` | Raw KQL appended to the search expression |
| `orderby` | Server-side sort; default `receivedDateTime desc` when no filter is used |
| `select` | Comma-separated Graph properties |
| `body_type` | `text` or `html` (applies to `body`/`uniqueBody`) |
| `count` | `true` adds `@odata.count` (ignored with search) |
| `next_link` | Opaque link from a previous response, to fetch the next page |

### Graph constraints handled for you

- `$filter` + `$search` in one request is rejected by Graph. The module returns `400 invalid_query` with an explanation.
- `$filter` + `$orderby` triggers `InefficientFilter` unless the sort properties lead the filter. The implicit
  `receivedDateTime desc` sort is therefore skipped whenever a filter is present; pass `orderby` explicitly if you need it.
- `$skip` is not combined with `$search`.
- `next_link` is validated to be `https://graph.microsoft.com` before being followed, so it cannot be used to
  redirect the OAuth token to another host.
- 401 → `auth_required`, 429 → `throttled` with `Retry-After`, other 4xx/5xx are passed through with the Graph error code.

---

## 4. Examples


### Don't waste tokens version (owa)
```bash
curl -s -G http://localhost:5001/outlook/api/messages \
  --data-urlencode "backend=owa" \
  --data-urlencode "folders=Folder" \
  --data-urlencode "top=5" \
  --data-urlencode "body_type=text" \
  --data-urlencode "backend=owa"
```

### Several folders of the signed-in mailbox

```bash
curl -s -G http://localhost:5001/outlook/api/messages \
  --data-urlencode "folders=Inbox,Sent Items,Projects/Client" \
  --data-urlencode "top=50"
```

### A shared mailbox through an alias

```bash
# alias "team" -> team@contoso.com, configured once in the module settings
curl -s -G http://localhost:5001/outlook/api/messages \
  --data-urlencode "mailbox=team" \
  --data-urlencode "folders=Inbox,Archive" \
  --data-urlencode "since=2026-01-01T00:00:00Z"
```

### Sender + date filter

```bash
curl -s -G http://localhost:5001/outlook/api/messages \
  --data-urlencode "from=mario@example.com" \
  --data-urlencode "since=2026-01-01T00:00:00Z" \
  --data-urlencode "unread=true"
```

### Full-text search (KQL)

```bash
curl -s -G http://localhost:5001/outlook/api/messages \
  --data-urlencode 'search=from:"mario@example.com" subject:"fattura"'
```

### Discover folders before querying

```bash
curl -s "http://localhost:5001/outlook/api/folders?mailbox=team" | jq -r '.folders[].path'
```

### Full message

```bash
curl -s "http://localhost:5001/outlook/api/messages/AAMkAD...?mailbox=team&body_type=text"
```

### Pagination

`/api/messages` returns `next_link`, plus `next_links` when several folders were queried in one call.
Pass it back verbatim:

```bash
curl -s -G http://localhost:5001/outlook/api/messages \
  --data-urlencode "next_link=https://graph.microsoft.com/v1.0/me/messages?\$skiptoken=..."
```

---

## 5. Aliases

Aliases let an agent ask for a mailbox by a short name instead of a UPN. Configure them in the UI as
`team=team@contoso.com, support=support@contoso.com`.

- Aliases are resolved before the request is built, so `/users/team@contoso.com/...` is what Graph sees.
- If `team@...` is only an **SMTP alias of the signed-in mailbox**, no extra permission is needed: `mailbox=me` already covers it.
- If `team@...` is a **separate mailbox** (shared mailbox or another user), delegated `Mail.Read.Shared` is required.

---

## 6. Notes and limitations

- **Delegated** permissions only: the module reads what the signed-in user can read. For unattended,
  no-user access you would need the application `Mail.Read` permission and a separate client-credentials flow.
- Attachments are only reported via `hasAttachments`; attachment download is intentionally not implemented.
- The folder list is cached for 60 seconds per mailbox.
- Optional: `ROUTE_GRAPH_THROUGH_GLOBAL_PROXY` in the module settings routes Graph calls through the global proxy from `config.json`.

---

## 7. Fallback backend: Outlook Web automation (`backend=owa`)

> **Unsupported. Use only when the tenant refuses admin consent for your Graph app.**

If the sign-in page says *"L'approvazione dell'amministratore è necessaria"* and you cannot obtain
consent (no admin, no approval), this backend reads mail by driving **Outlook Web** in a real browser
profile instead of calling Graph. No app registration, no consent screen.

How it works: Playwright opens a persistent Chromium profile that you sign into **once**; the profile
keeps the SSO cookies, so later runs reuse the session. Messages are read from OWA's internal
`FindItem` service, with a DOM-scraping fallback when that service changes.

```bash
# one-time: install the browser
python -m playwright install chromium

# one-time: sign in, then the profile is reusable
curl -s -X POST http://localhost:5001/outlook/api/owa/bootstrap

# read with the fallback backend
curl -s -G http://localhost:5001/outlook/api/messages \
  --data-urlencode "backend=owa" \
  --data-urlencode "top=5"
```

In the UI: section **1. AUTHENTICATION** has two tabs — `GRAPH / OAUTH_2.0` and
`OWA_FALLBACK`. Clicking the fallback tab runs `BOOTSTRAP_OWA_LOGIN` and also routes the queries
of sections 2 and 3 with `backend=owa`. The chosen tab is remembered across reloads, and the
cURL examples in section 4 follow it.

| Endpoint | Purpose |
|---|---|
| `GET /api/owa/status` | Health check (does not launch a browser) |
| `POST /api/owa/bootstrap` | Starts the one-time sign-in in the background (returns immediately) |
| `GET /api/owa/bootstrap/status` | `running` / `authenticated` / `profile_signed_in` for that sign-in |
| `GET /api/owa/diagnostics` | Shows what the scraper currently sees (sample rows, selector hits) |

With `backend=owa`, `GET /api/folders` returns the real folder tree (custom folders included),
each entry marked `source: mailbox` (real folder from the mailbox) or `source: well_known`.

Parameter support is a subset of the Graph backend: `folders`, `top`, `from`, `subject`, `search`,
`since`, `until`, `body_type`. `mailbox`, `select`, `filter`, `orderby`, `next_link` and `count`
are Graph-only and ignored here.

### Folders (including custom ones)

`folders` accepts the well-known names **and any custom folder**, resolved against the live mailbox
tree through the Outlook REST v2.0 API:

```bash
# discover everything, including nested custom folders
curl -s "http://localhost:5001/outlook/api/folders?backend=owa"

# read a nested custom folder by path (case-insensitive)
curl -s -G http://localhost:5001/outlook/api/messages \
  --data-urlencode "backend=owa" \
  --data-urlencode "folders=Inbox/OTP/Portale" \
  --data-urlencode "top=5"

# mix well-known and custom folders in one request
curl -s -G http://localhost:5001/outlook/api/messages \
  --data-urlencode "backend=owa" \
  --data-urlencode "folders=inbox,Inbox/OTP,Progetti"
```

Resolution order: well-known name → exact path → display name. An unknown folder returns
`404 folder_not_found` with `details.available_folders`. In the UI, `LOAD_FOLDERS` renders every
folder as a chip; clicking a chip appends its path to the `FOLDERS` field.

Useful if you forward portal mail into a subfolder: point `folders` at that folder and the OTP
search never scans the whole inbox.

### Sign-in detection

Mail readiness is detected with *mail-only* markers (`[role="listbox"]`, `[data-convid]`,
`[data-folder-name]`). The Microsoft sign-in page also contains `role="main"`, so treating that as
success closed the window while the user was still typing credentials. The bootstrap now keeps the
window open for the whole budget (default 600 s) and returns only when the mailbox is actually usable.

Note that OWA now serves from `outlook.cloud.microsoft`, redirecting from `outlook.office.com`; both
hosts are accepted.

### How it reads mail (important)

Modern OWA no longer exposes the old `X-OWA-CANARY` cookie, so the internal `/owa/service.svc`
route answers `401`. Instead, OWA keeps its own MSAL tokens in `localStorage`, including an access
token whose audience is `https://outlook.office.com` with mail-read scope. This backend reuses that
token and calls the **Outlook REST v2.0 API** (`/api/v2.0/me/...`) from inside the page, which is
what OWA itself does.

Two consequences:

- the token audience is `outlook.office.com`, **not** `graph.microsoft.com`, so it cannot be sent to
  Graph (Graph answers `401 InvalidAuthenticationToken / Invalid audience`);
- `get_message` returns the real body plus a **`body_text`** field with the markup stripped, so an
  agent can regex an OTP without parsing HTML.

### Why it is unsupported

- It depends on Outlook Web internals (its MSAL storage layout and the v2.0 API) that Microsoft can
  change without notice. `GET /api/owa/diagnostics` is the tool for adapting to that.
- **Conditional Access "Require compliant device"** blocks it: a scripted Chromium is not a managed
  device, and nothing in this module can change that.
- Conditional Access **sign-in frequency** decides how often the profile needs a new interactive login.
- The profile directory (`instance/owa_profile`) holds live session cookies: treat it as a credential.
- Every code path is read-only, but this is not an authorised access path: the Terms of Use angle is
  yours to evaluate.

Use the Graph backend (sections 1–6) whenever consent is obtainable; it is the supported path, it
gives you a real refresh token and it survives UI changes.
