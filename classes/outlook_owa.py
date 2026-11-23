"""Alternative, unsupported mail reader that automates Outlook Web with Playwright.

This backend exists for tenants where the Graph app cannot get admin consent.
It does not register an app and does not touch OAuth tokens: it drives a real
browser profile that the user has already signed into, so cookies, anti-CSRF
tokens and device binding are all genuine.

Consequences, stated plainly:
  * it depends on Outlook Web internals that Microsoft can change at any time;
  * Conditional Access policies such as "require compliant device" block it,
    because a scripted Chromium is not a managed device;
  * it only reads; every code path here is a listing or a text extraction.

The Graph backend in ``classes/outlook_mail.py`` remains the supported path.
"""

import html as html_module
import json
import os
import re
import threading
import time

OWA_HOME = "https://outlook.office.com/mail/"
LOGIN_HOST = "login.microsoftonline.com"
# OWA now serves from outlook.cloud.microsoft (it redirects from outlook.office.com).
OWA_HOSTS = ("outlook.office.com", "outlook.cloud.microsoft", "outlook.office365.com")

# The sign-in page also contains role="main", so mail readiness must be detected
# with a mail-only signal: an actual message row or the folder pane.
MAIL_READY_SELECTORS = (
    '[role="listbox"]',
    '[data-convid]',
    '[data-folder-name]',
    '[aria-label][id*="folder" i]',
)
LOGIN_SELECTORS = (
    'input[type="email"]',
    '#i0116',
    '#loginHeader',
    'input[name="loginfmt"]',
)
DEFAULT_PROFILE_DIRNAME = "owa_profile"
OPERATION_TIMEOUT = 60_000
NAV_TIMEOUT = 45_000

DISTINGUISHED_FOLDERS = {
    "inbox": "inbox",
    "sent items": "sentitems",
    "sentitems": "sentitems",
    "drafts": "drafts",
    "deleted items": "deleteditems",
    "deleteditems": "deleteditems",
    "archive": "archive",
    "junk email": "junkemail",
    "junkemail": "junkemail",
}

class OwaError(Exception):
    def __init__(self, message, status=500, code=None, details=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details

    def to_dict(self):
        payload = {"error": self.message}
        if self.code:
            payload["code"] = self.code
        if self.details is not None:
            payload["details"] = self.details
        return payload


def default_profile_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "instance", DEFAULT_PROFILE_DIRNAME)


def _html_to_text(value):
    """Strip tags so OTP extraction works on a plain string.

    Agents read an OTP out of the body; leaving HTML in place means the code is
    buried in markup. Text is therefore offered as an extra field while the
    original HTML is preserved for callers that want it.
    """
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def profile_signed_in(profile_dir=None):
    """True when the profile holds cookies, i.e. a sign-in actually happened.

    The directory itself is created on every launch, so its existence says
    nothing about the session state.
    """
    path = profile_dir or default_profile_dir()
    candidates = (
        os.path.join(path, "Default", "Cookies"),
        os.path.join(path, "Default", "Network", "Cookies"),
    )
    for candidate in candidates:
        try:
            if os.path.getsize(candidate) > 0:
                return True
        except OSError:
            continue
    return False


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OwaError(
            "Playwright non installato. Esegui: pip install -r requirements.txt "
            "e poi: python -m playwright install chromium",
            500,
            "missing_dependency",
        ) from exc
    return sync_playwright


def _plain_address(value):
    if not value:
        return None
    text = str(value)
    match = re.search(r"<([^>]+)>", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
    return match.group(0) if match else text.strip()


def _display_name(value):
    """'Portal <noreply@x>' -> 'Portal'; keeps plain values untouched."""
    if not value:
        return None
    text = str(value).strip()
    match = re.match(r"^(.*?)\s*<[^>]+>\s*$", text)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return None if ("@" in text and "<" not in text) else text


def _normalize_message(item):
    """Present scraped/service rows in the same shape the Graph backend returns."""
    sender = item.get("from")
    address = None
    name = None
    if isinstance(sender, dict):
        address = sender.get("address") or sender.get("EmailAddress")
        name = sender.get("name")
    elif sender:
        address = _plain_address(sender)
        name = _display_name(sender)
    return {
        "id": item.get("id"),
        "subject": item.get("subject"),
        "from": {"emailAddress": {"address": address, "name": name or address}},
        "receivedDateTime": item.get("receivedDateTime"),
        "bodyPreview": item.get("preview") or item.get("bodyPreview"),
        "isRead": not bool(item.get("unread")),
        "hasAttachments": bool(item.get("hasAttachments")),
        "webLink": item.get("webLink"),
    }


class OwaReader:
    """Drives a persistent Outlook Web profile. One browser per operation."""

    def __init__(self, profile_dir=None, headless=True, timeout=OPERATION_TIMEOUT):
        self.profile_dir = profile_dir or default_profile_dir()
        self.headless = headless
        self.timeout = timeout
        # The profile directory cannot be opened by two browsers at once.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Browser plumbing
    # ------------------------------------------------------------------
    def _launch(self, playwright, headless):
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        try:
            context = playwright.chromium.launch_persistent_context(
                self.profile_dir,
                headless=headless,
                args=args,
                viewport={"width": 1440, "height": 900},
                locale="it-IT",
                timezone_id="Europe/Rome",
            )
        except Exception as exc:
            raise OwaError(
                f"Impossibile avviare il browser: {exc}. "
                "Verifica con: python -m playwright install chromium",
                500,
                "browser_launch_failed",
            ) from exc
        context.set_default_timeout(self.timeout)
        context.set_default_navigation_timeout(NAV_TIMEOUT)
        return context

    @staticmethod
    def _first_page(context):
        pages = context.pages
        return pages[0] if pages else context.new_page()

    @staticmethod
    def _on_login_page(page):
        url = page.url or ""
        if LOGIN_HOST in url or "login.live.com" in url:
            return True
        for selector in LOGIN_SELECTORS:
            try:
                if page.locator(selector).count():
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _mail_ready(page):
        """True only when the mail surface itself is present.

        The Microsoft sign-in page also has role="main", so that selector alone
        produces a false positive and closes the browser mid-login.
        """
        for selector in MAIL_READY_SELECTORS:
            try:
                if page.locator(selector).count():
                    return True
            except Exception:
                continue
        return False

    def _open_mail(self, page):
        """Load OWA and report whether the mailbox is actually usable."""
        page.goto(OWA_HOME, wait_until="domcontentloaded")
        # OWA bounces through outlook.cloud.microsoft before the login redirect,
        # so give the navigation time to settle, then poll for a mail-only marker.
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                page.wait_for_timeout(1000)
            except Exception:
                # A navigation in flight destroys the execution context; retry.
                continue
            if self._on_login_page(page):
                return False
            if self._mail_ready(page):
                return True
        return False

    def _run(self, action, headless=None):
        """Open the profile, run ``action(page)`` and always close the browser."""
        headless = self.headless if headless is None else headless
        sync_playwright = _import_playwright()
        with self._lock:
            os.makedirs(self.profile_dir, exist_ok=True)
            with sync_playwright() as playwright:
                context = self._launch(playwright, headless)
                try:
                    page = self._first_page(context)
                    return action(page)
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Public surface (mirrors the Graph client where it can)
    # ------------------------------------------------------------------
    def status(self):
        def action(page):
            signed_in = self._open_mail(page)
            return {
                "backend": "owa",
                "profile_dir": self.profile_dir,
                "headless": self.headless,
                "authenticated": signed_in,
                "url": page.url,
            }

        try:
            return self._run(action)
        except OwaError:
            raise
        except Exception as exc:
            raise OwaError(str(exc), 502, "owa_status_failed") from exc

    def bootstrap(self, timeout_seconds=300):
        """Open a visible browser so the user can sign in once.

        Stays open until the mail surface is reachable. It must NOT stop as soon
        as the URL leaves login.microsoftonline.com: the sign-in page itself has
        role="main", and treating that as success closed the window mid-login.
        """
        deadline = time.time() + max(60, int(timeout_seconds))

        def action(page):
            page.goto(OWA_HOME, wait_until="domcontentloaded")
            while time.time() < deadline:
                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    # Navigation in progress destroyed the context; keep waiting.
                    continue
                if self._mail_ready(page):
                    return {"authenticated": True, "url": page.url}
            return {"authenticated": False, "url": page.url}

        try:
            result = self._run(action, headless=False)
        except OwaError:
            raise
        except Exception as exc:
            raise OwaError(str(exc), 502, "owa_bootstrap_failed") from exc

        if not result.get("authenticated"):
            raise OwaError(
                "Login non rilevato entro il tempo previsto. Ripeti il bootstrap e "
                "completa l'accesso nella finestra che si apre.",
                401,
                "owa_login_required",
                result,
            )
        return result

    def _outlook_token(self, page):
        """Lift the Outlook API token that OWA keeps in MSAL storage.

        Modern OWA authenticates to https://outlook.office.com with MSAL and
        stores the access token in localStorage. That token has an
        outlook.office.com audience (not graph.microsoft.com), so it is used
        against the Outlook REST v2.0 endpoint, which is what OWA itself calls.
        The old X-OWA-CANARY cookie no longer exists, which is why the internal
        /owa/service.svc route returns 401.
        """
        script = r"""
        () => {
            const keys = Object.keys(localStorage).filter(k => /accesstoken/i.test(k));
            for (const key of keys) {
                let entry;
                try { entry = JSON.parse(localStorage.getItem(key)); } catch (e) { continue; }
                if (!entry || !entry.secret) continue;
                const target = entry.target || '';
                if (!/outlook\.office\.com/i.test(target)) continue;
                // Prefer a token that already carries mail read permission.
                if (!/mail\.read/i.test(target)) continue;
                const exp = entry.expiresOn ? Number(entry.expiresOn) : null;
                return {
                    token: entry.secret,
                    target: target,
                    expiresOn: exp,
                    expired: exp ? (exp * 1000) < Date.now() : false,
                };
            }
            return null;
        }
        """
        try:
            found = page.evaluate(script)
        except Exception:
            found = None
        if not found or not found.get("token"):
            raise OwaError(
                "Nessun token API Outlook trovato nella sessione di Outlook Web. "
                "Esegui di nuovo il bootstrap (BOOTSTRAP_OWA_LOGIN) e verifica di "
                "essere arrivato alla casella.",
                401,
                "owa_token_missing",
            )
        return found["token"]

    def _api_get(self, page, token, path, params=None):
        """GET on Outlook REST v2.0 from inside the OWA page.

        Running the fetch in the page keeps cookies, CORS and any conditional
        access context identical to OWA's own requests.
        """
        script = """
        async ({token, path, params}) => {
            const query = new URLSearchParams(params || {});
            const url = 'https://outlook.office.com/api/v2.0' + path
                + (query.toString() ? '?' + query.toString() : '');
            const response = await fetch(url, {
                headers: { Authorization: 'Bearer ' + token, Accept: 'application/json' },
                credentials: 'omit',
            });
            const text = await response.text();
            return { status: response.status, body: text.slice(0, 900000) };
        }
        """
        try:
            raw = page.evaluate(script, {"token": token, "path": path, "params": params or {}})
        except Exception as exc:
            raise OwaError(f"Chiamata Outlook API fallita: {exc}", 502, "owa_api_failed") from exc

        if not raw:
            raise OwaError("Nessuna risposta dall'API Outlook.", 502, "owa_api_failed")
        if raw.get("status") == 401:
            raise OwaError(
                "Token API Outlook scaduto o rifiutato. Esegui di nuovo il bootstrap.",
                401,
                "owa_token_expired",
            )
        if raw.get("status") != 200:
            detail = (raw.get("body") or "")[:300]
            raise OwaError(
                f"L'API Outlook ha risposto HTTP {raw.get('status')}: {detail}",
                502,
                "owa_api_error",
            )
        try:
            return json.loads(raw.get("body") or "{}")
        except ValueError as exc:
            raise OwaError("Risposta API Outlook non valida.", 502, "owa_api_bad_json") from exc

    def _api_folders(self, page, token):
        """Recursive folder tree as path -> {id, name, path}."""
        folders = {}
        queue = [("", "/me/MailFolders", {"$top": "100"})]

        while queue:
            prefix, path, params = queue.pop(0)
            data = self._api_get(page, token, path, params)
            for item in data.get("value") or []:
                name = item.get("DisplayName")
                folder_id = item.get("Id")
                if not name or not folder_id:
                    continue
                full = f"{prefix}{name}"
                folders[full.lower()] = {"id": folder_id, "name": name, "path": full}
                if item.get("ChildFolderCount"):
                    queue.append((f"{full}/", f"/me/MailFolders/{folder_id}/childfolders", {"$top": "100"}))
            next_link = data.get("@odata.nextLink") or data.get("odata.nextLink")
            if next_link and "skiptoken" in next_link.lower():
                token_param = next_link.split("$skiptoken=")[-1].split("&")[0]
                queue.append((prefix, path, {"$skiptoken": token_param}))

        return folders

    def list_folders(self, mailbox=None, include_hidden=False):
        """Folder list from the mailbox itself, custom folders included."""

        def action(page):
            if not self._open_mail(page):
                raise OwaError(
                    "Sessione Outlook Web non attiva. Esegui prima il bootstrap del profilo.",
                    401,
                    "owa_login_required",
                )
            token = self._outlook_token(page)
            found = self._api_folders(page, token)
            rows = [
                {
                    "id": info["id"],
                    "displayName": info["name"],
                    "path": info["path"],
                    "childFolderCount": 0,
                    "source": "mailbox",
                }
                for info in sorted(found.values(), key=lambda f: f["path"].lower())
            ]
            rows.extend(
                {"id": key, "displayName": key, "path": key, "childFolderCount": 0, "source": "well_known"}
                for key in ("inbox", "sentitems", "drafts", "deleteditems", "archive", "junkemail")
                if not any(r["path"].lower() == key for r in rows)
            )
            return {"backend": "owa", "mailbox": "me", "count": len(rows), "folders": rows}

        try:
            return self._run(action)
        except OwaError:
            raise
        except Exception as exc:
            raise OwaError(str(exc), 502, "owa_folders_failed") from exc

    def _resolve_folder_ids(self, page, token, names):
        """Map requested folder names/paths to Outlook folder ids.

        Well-known names map to the REST v2.0 well-known ids; anything else is
        matched against the live folder tree by path, then by display name.
        """
        resolved = []
        unknown = []
        for name in names:
            key = DISTINGUISHED_FOLDERS.get(str(name).strip().lower())
            if key:
                resolved.append({"requested": name, "id": key, "kind": "distinguished"})
            else:
                unknown.append(name)

        if not unknown:
            return resolved

        tree = self._api_folders(page, token)

        for name in unknown:
            wanted = str(name).strip().lower()
            match = tree.get(wanted)
            if not match:
                by_name = [info for info in tree.values()
                           if info["name"].lower() == wanted]
                match = by_name[0] if by_name else None
            if not match:
                available = [info["path"] for info in tree.values()][:60]
                raise OwaError(
                    f"Cartella '{name}' non trovata nella casella.",
                    404,
                    "folder_not_found",
                    {"available_folders": available},
                )
            resolved.append({"requested": name, "id": match["id"], "kind": "custom",
                             "path": match["path"]})
        return resolved

    @staticmethod
    def _filter(messages, from_contains, subject_contains, search, since, until):
        """Client-side filtering, since the OWA backend cannot always express
        these as server-side query parameters."""
        def keep(message):
            sender = ((message.get("from") or {}).get("emailAddress") or {}).get("address") or ""
            subject = message.get("subject") or ""
            preview = message.get("bodyPreview") or ""
            when = message.get("receivedDateTime") or ""

            if from_contains and str(from_contains).lower() not in sender.lower():
                return False
            if subject_contains and str(subject_contains).lower() not in subject.lower():
                return False
            if search:
                needle = str(search).lower()
                haystack = f"{subject} {preview} {sender}".lower()
                if needle not in haystack:
                    return False
            if since and when and when < since:
                return False
            if until and when and when > until:
                return False
            return True

        return [m for m in messages if keep(m)]

    @staticmethod
    def _normalize_api_message(item):
        """Outlook v2.0 Message -> the same shape the Graph backend returns."""
        sender = item.get("From") or {}
        mailbox = sender.get("EmailAddress") or {}
        return {
            "id": item.get("Id"),
            "subject": item.get("Subject"),
            "from": {
                "emailAddress": {
                    "address": mailbox.get("Address"),
                    "name": mailbox.get("Name") or mailbox.get("Address"),
                }
            },
            "receivedDateTime": item.get("ReceivedDateTime") or item.get("DateTimeReceived"),
            "bodyPreview": item.get("BodyPreview"),
            "isRead": bool(item.get("IsRead")),
            "hasAttachments": bool(item.get("HasAttachments")),
            "webLink": item.get("WebLink"),
        }

    def _api_messages(self, page, token, folder_id, top, kind):
        """List messages of one folder through the Outlook v2.0 API."""
        if kind == "distinguished":
            path = f"/me/MailFolders/{folder_id}/messages"
        else:
            path = f"/me/MailFolders/{folder_id}/messages"
        params = {
            "$top": str(max(1, min(int(top), 200))),
            "$select": "Id,Subject,From,ReceivedDateTime,BodyPreview,IsRead,HasAttachments,WebLink",
            "$orderby": "ReceivedDateTime desc",
        }
        data = self._api_get(page, token, path, params)
        return data.get("value") or []

    def list_messages(self, folders=None, top=25, from_contains=None, subject_contains=None,
                      search=None, since=None, until=None, body_type="text", **_ignored):
        """List messages from one or more folders of the signed-in mailbox."""
        wanted = [f for f in (folders or ["inbox"]) if f]

        def action(page):
            if not self._open_mail(page):
                raise OwaError(
                    "Sessione Outlook Web non attiva. Esegui prima il bootstrap del profilo.",
                    401,
                    "owa_login_required",
                )

            token = self._outlook_token(page)
            resolved = self._resolve_folder_ids(page, token, wanted)

            collected = []
            for folder in resolved:
                collected.extend(
                    self._api_messages(page, token, folder["id"], top, folder["kind"])
                )

            messages = [self._normalize_api_message(row) for row in collected]
            messages = self._filter(messages, from_contains, subject_contains, search, since, until)
            messages.sort(key=lambda m: m.get("receivedDateTime") or "", reverse=True)
            return {
                "backend": "owa",
                "mailbox": "me",
                "folders": [
                    {"requested": f["requested"], "folder_id": f["id"], "kind": f["kind"]}
                    for f in resolved
                ],
                "count": len(messages[:top]),
                "messages": messages[:top],
                "next_link": None,
                "next_links": [],
                "source": "outlook-api",
            }

        try:
            return self._run(action)
        except OwaError:
            raise
        except Exception as exc:
            raise OwaError(str(exc), 502, "owa_read_failed") from exc

    def get_message(self, message_id, body_type="text", **_ignored):
        """Fetch one message through the Outlook API.

        The API returns the real body, which is far more reliable than reading
        the reading pane, and it is the part an OTP extraction depends on.
        """

        def action(page):
            if not self._open_mail(page):
                raise OwaError(
                    "Sessione Outlook Web non attiva. Esegui prima il bootstrap del profilo.",
                    401,
                    "owa_login_required",
                )

            token = self._outlook_token(page)
            data = self._api_get(
                page,
                token,
                f"/me/messages/{message_id}",
                {"$select": "Id,Subject,From,ReceivedDateTime,IsRead,HasAttachments,"
                            "Body,BodyPreview,ToRecipients"},
            )

            body = data.get("Body") or {}
            content = body.get("Content") or data.get("BodyPreview") or ""
            content_type = (body.get("ContentType") or "text").lower()
            sender = ((data.get("From") or {}).get("EmailAddress") or {})
            text = _html_to_text(content) if content_type == "html" else content
            return {
                "backend": "owa",
                "id": data.get("Id") or message_id,
                "subject": data.get("Subject"),
                "from": {"emailAddress": {"address": sender.get("Address"),
                                          "name": sender.get("Name") or sender.get("Address")}},
                "receivedDateTime": data.get("ReceivedDateTime"),
                "body": {"contentType": content_type, "content": content},
                # Convenience for agents: the same body with markup removed.
                "body_text": text,
                "bodyPreview": data.get("BodyPreview"),
            }

        try:
            return self._run(action)
        except OwaError:
            raise
        except Exception as exc:
            raise OwaError(str(exc), 502, "owa_read_failed") from exc

    def diagnostics(self):
        """Surface the token/API state, to debug when OWA changes."""
        def action(page):
            signed_in = self._open_mail(page)
            if not signed_in:
                return {"authenticated": False, "url": page.url}

            info = {"authenticated": True, "url": page.url, "title": page.title()}
            try:
                token = self._outlook_token(page)
                info["token_found"] = True
                info["token_length"] = len(token)
                folders = self._api_folders(page, token)
                info["folder_count"] = len(folders)
                info["folders"] = [f["path"] for f in
                                   sorted(folders.values(), key=lambda x: x["path"].lower())][:25]
                try:
                    me = self._api_get(page, token, "/me", {"$select": "DisplayName,EmailAddress"})
                    info["account"] = me.get("DisplayName") or me.get("EmailAddress")
                except OwaError as exc:
                    info["account_error"] = exc.message
            except OwaError as exc:
                info["token_found"] = False
                info["error"] = exc.message
                info["code"] = exc.code
            return info

        try:
            return self._run(action)
        except OwaError:
            raise
        except Exception as exc:
            raise OwaError(str(exc), 502, "owa_diagnostics_failed") from exc
