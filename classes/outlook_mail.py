"""Read-only Microsoft Graph client for Outlook mailboxes.

Implements OAuth 2.0 Authorization Code Flow (confidential client) and only
issues HTTP GET requests against Microsoft Graph, so the module can never
modify a mailbox.
"""

import json
import os
import re
import tempfile
import time
from urllib.parse import quote, urlparse

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_HOST = "graph.microsoft.com"
AUTHORITY_TEMPLATE = "https://login.microsoftonline.com/{tenant}"
DEFAULT_TENANT = "common"
DEFAULT_SCOPES = ["Mail.Read", "User.Read", "offline_access"]

# MSAL adds these itself and raises ValueError if they are passed explicitly.
RESERVED_SCOPES = {"openid", "profile", "offline_access"}

DEFAULT_MESSAGE_SELECT = (
    "id,conversationId,internetMessageId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,sentDateTime,isRead,isDraft,hasAttachments,importance,"
    "bodyPreview,webLink,categories,parentFolderId"
)

DEFAULT_MESSAGE_DETAIL_SELECT = (
    "id,conversationId,internetMessageId,subject,from,toRecipients,ccRecipients,"
    "bccRecipients,replyTo,receivedDateTime,sentDateTime,isRead,isDraft,"
    "hasAttachments,importance,webLink,categories,parentFolderId,bodyPreview,"
    "body,uniqueBody,internetMessageHeaders"
)

WELL_KNOWN_FOLDERS = {
    "inbox",
    "drafts",
    "sentitems",
    "deleteditems",
    "archive",
    "junkemail",
    "outbox",
    "conversationhistory",
    "msgfolderroot",
    "searchfolders",
    "root",
    "recoverableitemsdeletions",
}

FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_+=\-]{30,}$")
MAX_FOLDERS = 1000
FOLDER_CACHE_TTL = 60


class OutlookMailError(Exception):
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


def escape_odata(value):
    return str(value).replace("'", "''")


def escape_kql(value):
    return str(value).replace('"', "").strip()


def parse_aliases(raw):
    """Accept either a mapping or an ``alias=mailbox, ...`` string."""
    if isinstance(raw, dict):
        pairs = raw.items()
    else:
        pairs = []
        for chunk in str(raw).split(","):
            for separator in ("=", ":"):
                if separator in chunk:
                    pairs.append(tuple(chunk.split(separator, 1)))
                    break
    aliases = {}
    for key, value in pairs:
        alias = str(key).strip()
        target = str(value).strip()
        if alias and target:
            aliases[alias] = target
    return aliases


def default_store_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "instance", "outlook_mail.json")


def default_redirect_uri():
    port = 5000
    try:
        from utils import load_config

        port = int(load_config().get("port", 5000))
    except Exception:
        pass
    return f"http://localhost:{port}/outlook/oauth/callback"


class OutlookMailClient:
    """Single-user delegated mail reader.

    Tokens and OAuth settings live in ``instance/outlook_mail.json`` (gitignored)
    so the client secret is never written into a tracked file.
    """

    def __init__(self, store_path=None):
        self.store_path = store_path or default_store_path()
        self._cache = None
        self._folder_cache = {}
        self._data = self._load()

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.store_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _persist(self):
        cache = self._cache
        if cache is not None:
            self._data["token_cache"] = cache.serialize()
        directory = os.path.dirname(self.store_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, indent=2)
            os.replace(tmp, self.store_path)
            os.chmod(self.store_path, 0o600)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def settings(self):
        data = {
            "client_id": "",
            "client_secret": "",
            "tenant_id": DEFAULT_TENANT,
            "redirect_uri": default_redirect_uri(),
            "scopes": list(DEFAULT_SCOPES),
            "aliases": {},
            "use_proxy": False,
            "api_token": "",
            "token_cache": None,
            "account": None,
            "last_login": None,
        }
        data.update(self._data)
        scopes = [s for s in (data.get("scopes") or DEFAULT_SCOPES) if s]
        if "offline_access" not in scopes:
            scopes.append("offline_access")
        data["scopes"] = scopes
        if not data.get("redirect_uri"):
            data["redirect_uri"] = default_redirect_uri()
        if not isinstance(data.get("aliases"), dict):
            data["aliases"] = {}
        return data

    def public_settings(self):
        settings = self.settings()
        return {
            "client_id": settings["client_id"],
            "tenant_id": settings["tenant_id"],
            "redirect_uri": settings["redirect_uri"],
            "scopes": settings["scopes"],
            "aliases": settings["aliases"],
            "use_proxy": bool(settings.get("use_proxy")),
            "has_client_secret": bool(settings["client_secret"]),
            "has_api_token": bool(settings.get("api_token")),
        }

    def save_settings(self, payload):
        settings = self.settings()
        identity_changed = False

        for key in ("client_id", "tenant_id", "redirect_uri"):
            if key in payload and payload[key] is not None:
                value = str(payload[key]).strip()
                if settings[key] != value:
                    identity_changed = identity_changed or key in ("client_id", "tenant_id")
                    settings[key] = value

        secret = payload.get("client_secret")
        if secret is not None and str(secret).strip():
            if settings["client_secret"] != str(secret).strip():
                identity_changed = True
            settings["client_secret"] = str(secret).strip()

        if "scopes" in payload and payload["scopes"] is not None:
            requested = payload["scopes"]
            if isinstance(requested, str):
                requested = [s.strip() for s in requested.split(",") if s.strip()]
            scopes = [str(s).strip() for s in requested if str(s).strip()]
            if "offline_access" not in scopes:
                scopes.append("offline_access")
            # Consent is bound to the scope set: requesting more later requires
            # a fresh login, otherwise the cached token lacks the new scope.
            if set(scopes) != set(settings["scopes"]):
                identity_changed = True
            settings["scopes"] = scopes

        if "aliases" in payload and payload["aliases"] is not None:
            settings["aliases"] = parse_aliases(payload["aliases"])

        if "use_proxy" in payload:
            settings["use_proxy"] = bool(payload["use_proxy"])

        if "api_token" in payload and payload["api_token"] is not None:
            settings["api_token"] = str(payload["api_token"]).strip()

        if not settings["tenant_id"]:
            settings["tenant_id"] = DEFAULT_TENANT
        if not settings["redirect_uri"]:
            settings["redirect_uri"] = default_redirect_uri()

        if identity_changed:
            settings["token_cache"] = None
            settings["account"] = None
            self._cache = None

        self._data = settings
        self._persist()
        self._folder_cache.clear()
        return self.public_settings()

    # ------------------------------------------------------------------
    # MSAL helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _import_msal():
        try:
            import msal
        except ImportError as exc:
            raise OutlookMailError(
                "Libreria 'msal' non installata. Esegui: pip install -r requirements.txt",
                500,
                "missing_dependency",
            ) from exc
        return msal

    def _token_cache(self):
        if self._cache is None:
            msal = self._import_msal()
            cache = msal.SerializableTokenCache()
            serialized = self.settings().get("token_cache")
            if serialized:
                cache.deserialize(serialized)
            self._cache = cache
        return self._cache

    def _msal_app(self):
        msal = self._import_msal()
        settings = self.settings()
        if not settings["client_id"] or not settings["client_secret"]:
            raise OutlookMailError(
                "OAuth non configurato: imposta client_id e client_secret nelle impostazioni del modulo.",
                400,
                "not_configured",
            )
        return msal.ConfidentialClientApplication(
            settings["client_id"],
            authority=AUTHORITY_TEMPLATE.format(tenant=settings["tenant_id"] or DEFAULT_TENANT),
            client_credential=settings["client_secret"],
            token_cache=self._token_cache(),
        )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def _msal_scopes(self):
        """Scopes as MSAL wants them: reserved ones are added by MSAL itself."""
        return [scope for scope in self.settings()["scopes"] if scope not in RESERVED_SCOPES]

    def login_url(self, state=None, prompt="select_account"):
        settings = self.settings()
        app = self._msal_app()
        return app.get_authorization_request_url(
            self._msal_scopes(),
            state=state,
            redirect_uri=settings["redirect_uri"],
            prompt=prompt,
        )

    def complete_login(self, code):
        if not code:
            raise OutlookMailError("Parametro 'code' mancante nel callback OAuth.", 400, "invalid_callback")
        settings = self.settings()
        app = self._msal_app()
        result = app.acquire_token_by_authorization_code(
            code,
            scopes=self._msal_scopes(),
            redirect_uri=settings["redirect_uri"],
        )
        if not result or "error" in result:
            error = (result or {}).get("error", "authorization_failed")
            description = (result or {}).get("error_description") or "Scambio del codice OAuth fallito."
            raise OutlookMailError(description, 401, error)

        accounts = app.get_accounts() or []
        account = accounts[0] if accounts else None
        settings["account"] = account
        settings["last_login"] = int(time.time())
        self._data = settings
        self._persist()
        return self._account_summary()

    def logout(self):
        settings = self.settings()
        settings["token_cache"] = None
        settings["account"] = None
        self._cache = None
        self._folder_cache.clear()
        self._data = settings
        self._persist()
        return self._account_summary()

    def _account_summary(self):
        account = self.settings().get("account") or {}
        if not account:
            return None
        return {
            "username": account.get("username"),
            "name": account.get("name"),
            "home_account_id": account.get("home_account_id"),
            "environment": account.get("environment"),
        }

    def status(self):
        settings = self.settings()
        return {
            "configured": bool(settings["client_id"] and settings["client_secret"]),
            "authenticated": bool(settings.get("account")),
            "account": self._account_summary(),
            "tenant_id": settings["tenant_id"],
            "scopes": settings["scopes"],
            "redirect_uri": settings["redirect_uri"],
            "aliases": settings["aliases"],
            "use_proxy": bool(settings.get("use_proxy")),
            "last_login": settings.get("last_login"),
            "default_redirect_uri": default_redirect_uri(),
        }

    def _access_token(self):
        settings = self.settings()
        app = self._msal_app()
        scopes = self._msal_scopes()

        account = settings.get("account")
        if not account:
            accounts = app.get_accounts() or []
            account = accounts[0] if accounts else None

        result = app.acquire_token_silent(scopes, account=account) if account else None
        if not result:
            raise OutlookMailError(
                "Autenticazione Outlook richiesta: apri /outlook/oauth/login per autorizzare la lettura.",
                401,
                "auth_required",
            )
        if "access_token" not in result:
            raise OutlookMailError(
                result.get("error_description") or "Impossibile ottenere un access token valido.",
                401,
                result.get("error", "token_error"),
            )

        if settings.get("account") != account:
            settings["account"] = account
            self._data = settings
        self._persist()
        return result["access_token"]

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _session(self):
        import requests

        session = requests.Session()
        if self.settings().get("use_proxy"):
            try:
                from utils import load_config

                global_config = load_config()
            except Exception:
                global_config = {}
            if global_config.get("proxy_enabled"):
                proxies = {}
                if global_config.get("proxy_http"):
                    proxies["http"] = global_config["proxy_http"]
                if global_config.get("proxy_https"):
                    proxies["https"] = global_config["proxy_https"]
                if proxies:
                    session.proxies = proxies
        return session

    @staticmethod
    def _validate_graph_url(url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.netloc or "").lower() != GRAPH_HOST:
            raise OutlookMailError(
                "URL non consentito: sono ammesse solo chiamate a https://graph.microsoft.com.",
                400,
                "invalid_url",
            )

    def _graph_get(self, path, params=None, headers=None):
        url = path if path.startswith("http") else GRAPH_BASE + path
        self._validate_graph_url(url)

        token = self._access_token()
        request_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if headers:
            request_headers.update(headers)

        response = self._session().get(
            url,
            params=params,
            headers=request_headers,
            verify=True,
            timeout=60,
        )

        if response.status_code == 401:
            raise OutlookMailError(
                "Token Outlook non valido o scaduto. Rifai il login (/outlook/oauth/login).",
                401,
                "auth_required",
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "pochi")
            raise OutlookMailError(
                f"Rate limit Microsoft Graph raggiunto. Riprova tra {retry_after} secondi.",
                429,
                "throttled",
            )
        if response.status_code >= 400:
            code = "graph_error"
            message = f"Microsoft Graph ha risposto HTTP {response.status_code}."
            try:
                error = response.json().get("error", {})
                code = error.get("code") or code
                message = error.get("message") or message
            except ValueError:
                pass
            status = response.status_code if response.status_code < 500 else 502
            raise OutlookMailError(message, status, code)

        try:
            return response.json()
        except ValueError as exc:
            raise OutlookMailError("Risposta Graph non valida (JSON atteso).", 502, "invalid_response") from exc

    # ------------------------------------------------------------------
    # Mailbox / folder resolution
    # ------------------------------------------------------------------
    def resolve_mailbox(self, mailbox):
        value = (mailbox or "").strip()
        if not value or value.lower() in ("me", "self"):
            return "me"
        aliases = self.settings().get("aliases") or {}
        for alias, target in aliases.items():
            if alias.lower() == value.lower():
                return target
        return value

    @staticmethod
    def _mailbox_base(mailbox_value):
        if mailbox_value == "me":
            return "/me"
        return f"/users/{quote(mailbox_value, safe='@')}"

    def list_folders(self, mailbox=None, include_hidden=False):
        mailbox_value = self.resolve_mailbox(mailbox)
        cache_key = (mailbox_value, bool(include_hidden))
        cached = self._folder_cache.get(cache_key)
        if cached and time.time() - cached[0] < FOLDER_CACHE_TTL:
            return cached[1]

        base = self._mailbox_base(mailbox_value)
        hidden = "true" if include_hidden else "false"
        queue = [(f"{base}/mailFolders", {"$top": "250", "includeHiddenFolders": hidden}, "")]
        folders = []

        while queue and len(folders) < MAX_FOLDERS:
            path, params, prefix = queue.pop(0)
            url = path
            while url and len(folders) < MAX_FOLDERS:
                data = self._graph_get(url, params=params)
                params = None
                for item in data.get("value") or []:
                    name = item.get("displayName") or ""
                    folder_path = f"{prefix}{name}"
                    folders.append(
                        {
                            "id": item.get("id"),
                            "displayName": name,
                            "path": folder_path,
                            "parentFolderId": item.get("parentFolderId"),
                            "childFolderCount": item.get("childFolderCount", 0),
                            "totalItemCount": item.get("totalItemCount"),
                            "unreadItemCount": item.get("unreadItemCount"),
                        }
                    )
                    if item.get("childFolderCount"):
                        queue.append(
                            (
                                f"{base}/mailFolders/{item.get('id')}/childFolders",
                                {"$top": "250", "includeHiddenFolders": hidden},
                                f"{folder_path}/",
                            )
                        )
                url = data.get("@odata.nextLink")

        folders.sort(key=lambda folder: (folder["path"] or "").lower())
        self._folder_cache[cache_key] = (time.time(), folders)
        return folders

    def resolve_folder(self, mailbox, folder):
        value = (folder or "").strip()
        if not value or value.lower() in ("all", "*", "any"):
            return None

        lowered = value.lower()
        if lowered in WELL_KNOWN_FOLDERS:
            return lowered

        folders = self.list_folders(mailbox)
        for entry in folders:
            if (entry["path"] or "").lower() == lowered:
                return entry["id"]
        matches = [entry for entry in folders if (entry["displayName"] or "").lower() == lowered]
        if matches:
            return matches[0]["id"]

        # Only accept an opaque id after name resolution fails, so long folder
        # names are not mistaken for ids.
        if FOLDER_ID_RE.match(value):
            return value

        available = [entry["path"] for entry in folders][:100]
        raise OutlookMailError(
            f"Cartella '{folder}' non trovata nella mailbox richiesta.",
            404,
            "folder_not_found",
            {"available_folders": available},
        )

    # ------------------------------------------------------------------
    # Mail reading (read-only)
    # ------------------------------------------------------------------
    def get_me(self):
        return self._graph_get(
            "/me",
            params={"$select": "id,displayName,mail,userPrincipalName"},
        )

    def list_messages(
        self,
        mailbox=None,
        folders=None,
        top=25,
        skip=None,
        select=None,
        filter=None,
        search=None,
        orderby=None,
        next_link=None,
        body_content_type=None,
        include_count=False,
    ):
        if filter and search:
            raise OutlookMailError(
                "Microsoft Graph non consente di combinare $filter e $search nella stessa richiesta. "
                "Usa 'search' con sintassi KQL (es. 'from:team subject:fattura') oppure solo 'filter'.",
                400,
                "invalid_query",
            )

        if next_link:
            self._validate_graph_url(next_link)
            page = self._graph_get(next_link)
            return self._format_page(page, mailbox)

        mailbox_value = self.resolve_mailbox(mailbox)
        base = self._mailbox_base(mailbox_value)

        try:
            top_value = max(1, min(int(top or 25), 1000))
        except (TypeError, ValueError):
            top_value = 25

        selected = select or DEFAULT_MESSAGE_SELECT
        headers = {}
        if body_content_type in ("text", "html"):
            headers["Prefer"] = f'outlook.body-content-type="{body_content_type}"'
        if include_count and not search:
            headers["ConsistencyLevel"] = "eventual"

        requested_folders = folders if folders else [None]
        resolved = []
        messages = []
        next_links = []

        for raw_folder in requested_folders:
            folder_id = self.resolve_folder(mailbox_value, raw_folder)
            path = f"{base}/mailFolders/{folder_id}/messages" if folder_id else f"{base}/messages"
            params = {"$top": str(top_value), "$select": selected}
            if filter:
                params["$filter"] = filter
            if search:
                params["$search"] = search
            if orderby:
                params["$orderby"] = orderby
            elif not search and not filter:
                # Graph rejects $filter + $orderby unless orderby properties lead
                # the filter (InefficientFilter), so only sort implicit queries.
                params["$orderby"] = "receivedDateTime desc"
            if skip and not search:
                params["$skip"] = str(skip)
            if include_count and not search:
                params["$count"] = "true"

            page = self._graph_get(path, params=params, headers=headers)
            messages.extend(page.get("value") or [])
            next_link = page.get("@odata.nextLink")
            if next_link:
                next_links.append(next_link)
            resolved.append({
                "requested": raw_folder or "all",
                "folder_id": folder_id,
                "path": path,
                "count": len(page.get("value") or []),
                "next_link": next_link,
            })

        if len(resolved) > 1:
            messages.sort(key=lambda item: item.get("receivedDateTime") or "", reverse=True)
            messages = messages[:top_value]

        return {
            "mailbox": mailbox_value,
            "folders": resolved,
            "count": len(messages),
            "messages": messages,
            # A global next_link is only unambiguous for a single folder; with
            # several folders use the per-folder next_link entries above.
            "next_link": next_links[0] if len(next_links) == 1 and len(resolved) == 1 else None,
            "next_links": next_links,
        }

    def _format_page(self, page, mailbox):
        return {
            "mailbox": self.resolve_mailbox(mailbox),
            "folders": [],
            "count": len(page.get("value") or []),
            "messages": page.get("value") or [],
            "next_link": page.get("@odata.nextLink"),
            "next_links": [page["@odata.nextLink"]] if page.get("@odata.nextLink") else [],
        }

    def get_message(self, mailbox=None, message_id=None, body_content_type="text", select=None):
        if not message_id:
            raise OutlookMailError("message_id mancante.", 400, "invalid_request")
        mailbox_value = self.resolve_mailbox(mailbox)
        base = self._mailbox_base(mailbox_value)
        headers = {}
        if body_content_type in ("text", "html"):
            headers["Prefer"] = f'outlook.body-content-type="{body_content_type}"'
        return self._graph_get(
            f"{base}/messages/{quote(message_id, safe='')}",
            params={"$select": select or DEFAULT_MESSAGE_DETAIL_SELECT},
            headers=headers,
        )
