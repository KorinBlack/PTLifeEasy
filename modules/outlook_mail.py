"""Read-only Outlook mailbox REST API for PTLifeEasy agents.

Exposes Microsoft Graph mail reading behind a small REST surface, with OAuth
2.0 Authorization Code Flow. Every Graph call is a GET: the module cannot
write, send, delete or move anything.
"""

import hmac
import re
import secrets
import threading
import time

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from classes.outlook_mail import OutlookMailClient, OutlookMailError, escape_kql, escape_odata, parse_aliases
from classes.outlook_owa import OwaError, OwaReader, default_profile_dir, profile_signed_in

MODULE_NAME = "Outlook Mail Reader"
MODULE_DESCRIPTION = "Read-only OAuth 2.0 access to Outlook mailboxes via Microsoft Graph, with folder and alias filtering."
MODULE_PREFIX = "/outlook"

module_bp = Blueprint('outlook_mail', __name__, template_folder='../../templates', static_folder='../../static')

_client = None
_owa_reader = None

# Outcome of the last OAuth attempt. Microsoft's Cross-Origin-Opener-Policy
# severs the popup from its opener, so neither window.closed nor postMessage can
# be relied upon; the callback records the result here and the parent polls it.
_last_auth = {
    "generation": None,
    "success": None,
    "message": None,
    "account": None,
    "at": 0,
}
_auth_lock = threading.Lock()

# Background bootstrap runs so the HTTP request never holds the server thread
# during an interactive sign-in (which can wait minutes for MFA).
_bootstrap_state = {
    "running": False,
    "authenticated": None,
    "message": None,
    "started_at": None,
    "finished_at": None,
}
_bootstrap_lock = threading.Lock()


def get_client():
    global _client
    if _client is None:
        _client = OutlookMailClient()
    return _client


def get_owa_reader():
    """Alternative backend: drives a signed-in Outlook Web profile.

    keep_alive defaults to on: a warm browser drops a query from ~4.5s to
    ~150ms for ~600MB RSS. It can be disabled per call via ?keep_alive=false,
    which tears the browser down after the request.
    """
    global _owa_reader
    if _owa_reader is None:
        _owa_reader = OwaReader(headless=True, keep_alive=False)
    return _owa_reader


def _owa_reader_for_request():
    """Honour a per-request keep_alive toggle without restarting the app."""
    keep_alive = request.args.get('keep_alive')
    reader = get_owa_reader()
    if keep_alive is None:
        return reader
    wanted = _truthy(keep_alive)
    if wanted != reader.keep_alive:
        if not wanted:
            reader.close()
        reader.keep_alive = wanted
    return reader


def _requested_backend():
    """'graph' (default) or 'owa' when the tenant refuses admin consent."""
    value = (request.args.get('backend') or request.headers.get('X-Mail-Backend') or 'graph')
    value = value.strip().lower()
    return value if value in ('graph', 'owa') else 'graph'


def _record_auth(generation, success, message=None, account=None):
    with _auth_lock:
        _last_auth.update({
            "generation": generation,
            "success": success,
            "message": message,
            "account": account,
            "at": time.time(),
        })


def _auth_state():
    with _auth_lock:
        return dict(_last_auth)


@module_bp.errorhandler(OutlookMailError)
def handle_outlook_error(error):
    return jsonify(error.to_dict()), error.status


@module_bp.errorhandler(OwaError)
def handle_owa_error(error):
    return jsonify(error.to_dict()), error.status


@module_bp.before_request
def enforce_api_token():
    """Optional shared-secret guard for agent access.

    The local UI is trusted through a signed session cookie set when the
    dashboard is rendered, so a configured api_token only gates external
    callers hitting /outlook/api/* directly.
    """
    if not request.path.startswith(MODULE_PREFIX + '/api/'):
        return None
    expected = get_client().settings().get('api_token')
    if not expected:
        return None
    if session.get('outlook_ui'):
        return None

    provided = request.headers.get('X-API-Token')
    if not provided:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.lower().startswith('bearer '):
            provided = auth_header[7:].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        return jsonify({
            "error": "API token mancante o non valido: invia l'header X-API-Token.",
            "code": "unauthorized",
        }), 401
    return None


def _split_values(raw):
    if not raw:
        return []
    return [item.strip() for item in str(raw).split(',') if item.strip()]


def _truthy(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _int_or_none(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise OutlookMailError(f"Valore numerico non valido: {value}", 400, "invalid_parameter")


# ----------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------
@module_bp.route('/')
def index():
    session['outlook_ui'] = True
    client = get_client()
    return render_template(
        'outlook_reader/index.html',
        config_redirect_uri=client.settings()['redirect_uri'],
    )


# ----------------------------------------------------------------------
# Settings / status
# ----------------------------------------------------------------------
@module_bp.route('/api/settings', methods=['GET'])
def get_settings():
    client = get_client()
    return jsonify({
        "settings": client.public_settings(),
        "status": client.status(),
    })


@module_bp.route('/api/settings', methods=['POST'])
def update_settings():
    client = get_client()
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict()

    cleaned = {}
    for key in ('client_id', 'tenant_id', 'redirect_uri', 'client_secret'):
        if key in payload and payload[key] is not None:
            cleaned[key] = payload[key]

    if 'scopes' in payload:
        scopes = payload['scopes']
        if isinstance(scopes, str):
            scopes = [s.strip() for s in scopes.split(',') if s.strip()]
        cleaned['scopes'] = scopes

    if 'aliases' in payload:
        cleaned['aliases'] = parse_aliases(payload['aliases'])

    if 'use_proxy' in payload:
        cleaned['use_proxy'] = _truthy(payload['use_proxy'])

    if 'api_token' in payload:
        cleaned['api_token'] = str(payload['api_token'] or '').strip()

    client.save_settings(cleaned)
    return jsonify({
        "settings": client.public_settings(),
        "status": client.status(),
    })


@module_bp.route('/api/status', methods=['GET'])
def get_status():
    """Connection state, plus the last OAuth outcome when a generation is asked.

    ``generation`` is the raw state value from /api/oauth/start, so the poller
    only reacts to a result produced after its own attempt began.
    """
    client = get_client()
    status = client.status()
    generation = request.args.get('generation')
    if generation:
        state = _auth_state()
        status['auth_outcome'] = {
            "success": state["success"],
            "message": state["message"],
            "account": state["account"],
            "generation": state["generation"],
            "newer": state["generation"] != generation,
        }
    return jsonify(status)


# ----------------------------------------------------------------------
# Alternative backend: Outlook Web automation (no app registration)
# ----------------------------------------------------------------------
@module_bp.route('/api/owa/status', methods=['GET'])
def owa_status():
    """Cheap health check; does not launch a browser."""
    profile_dir = default_profile_dir()
    try:
        import playwright  # noqa: F401
        available = True
    except ImportError:
        available = False
    return jsonify({
        "backend": "owa",
        "playwright_installed": available,
        # The directory is created on every launch; only stored cookies mean
        # a sign-in actually happened.
        "profile_signed_in": profile_signed_in(profile_dir),
        "profile_dir": profile_dir,
        "support": "unsupported",
        "note": (
            "Automatizza Outlook Web con un profilo browser gia' autenticato. "
            "Non richiede app registration ne' consenso admin, ma dipende dalla UI "
            "di Outlook Web e viene bloccato da Conditional Access 'compliant device'."
        ),
    })


@module_bp.route('/api/owa/bootstrap', methods=['POST'])
def owa_bootstrap():
    """Start the one-time interactive sign-in in the background.

    Returns immediately so the UI stays responsive and the browser is never
    closed because an HTTP client gave up waiting. Poll /api/owa/bootstrap/status.
    """
    timeout = _int_or_none(request.args.get('timeout')) or 600
    with _bootstrap_lock:
        if _bootstrap_state["running"]:
            # A previous attempt may be stuck (e.g. left over from a crash or a
            # browser that never appeared). Let the caller force a clean retry.
            started = _bootstrap_state.get("started_at") or 0
            age = time.time() - started
            # Do not trust "running" forever: a dead worker thread must not lock
            # out new attempts. The bootstrap itself has a 600s default budget.
            if age < (timeout + 120) and not _truthy(request.args.get('force')):
                return jsonify({
                    "running": True,
                    "age_seconds": round(age),
                    "message": "Bootstrap gia' in corso: usa ?force=true per forzare.",
                })
        _bootstrap_state.update({
            "running": True,
            "authenticated": None,
            "message": "Finestra aperta: completa il login Microsoft.",
            "started_at": time.time(),
            "finished_at": None,
        })

    def worker():
        state = {"authenticated": False, "message": None}
        try:
            result = get_owa_reader().bootstrap(timeout_seconds=timeout)
            state["authenticated"] = bool(result.get("authenticated"))
            state["message"] = "Login completato."
        except OwaError as error:
            state["authenticated"] = False
            state["message"] = error.message
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as text
            state["authenticated"] = False
            state["message"] = str(exc)
        finally:
            with _bootstrap_lock:
                _bootstrap_state.update({
                    "running": False,
                    "authenticated": state["authenticated"],
                    "message": state["message"],
                    "finished_at": time.time(),
                })

    threading.Thread(target=worker, name="owa-bootstrap", daemon=True).start()
    return jsonify({"running": True, "message": "Finestra aperta: completa il login Microsoft."})


@module_bp.route('/api/owa/reset', methods=['POST'])
def owa_reset():
    """Release every browser holding the profile and clear a stuck bootstrap.

    Chromium allows only one browser per profile directory. If a previous run
    left one behind, the next bootstrap cannot open a window at all.
    """
    reader = get_owa_reader()
    was_running = _bootstrap_state.get("running")
    try:
        reader.close()
    except Exception:
        pass
    with _bootstrap_lock:
        _bootstrap_state.update({
            "running": False,
            "authenticated": None,
            "message": "Stato azzerato manualmente." if was_running else "Nessun bootstrap attivo.",
            "finished_at": time.time(),
        })
    return jsonify({
        "reset": True,
        "was_running": bool(was_running),
        "profile_signed_in": profile_signed_in(default_profile_dir()),
    })


@module_bp.route('/api/owa/bootstrap/status', methods=['GET'])
def owa_bootstrap_status():
    with _bootstrap_lock:
        payload = dict(_bootstrap_state)
    payload["profile_signed_in"] = profile_signed_in(default_profile_dir())
    return jsonify(payload)


@module_bp.route('/api/owa/diagnostics', methods=['GET'])
def owa_diagnostics():
    """Show what the scraper currently sees, to adapt selectors after UI changes."""
    return jsonify(get_owa_reader().diagnostics())


@module_bp.route('/api/owa/session', methods=['GET'])
def owa_session():
    """State of the keep-alive browser: warm or cold, and when it was used."""
    reader = get_owa_reader()
    warm = False
    try:
        warm = bool(reader._page is not None and not reader._page.is_closed())
    except Exception:
        warm = False
    return jsonify({
        "keep_alive": reader.keep_alive,
        "browser_running": warm,
        "profile_signed_in": profile_signed_in(),
        "last_used": reader._last_used or None,
    })


@module_bp.route('/api/owa/session', methods=['POST'])
def owa_session_control():
    """Warm up or shut down the keep-alive browser.

    ``{"action": "start"}`` opens it now so the first query is already fast;
    ``{"action": "stop"}`` releases the ~600MB it holds.
    """
    reader = get_owa_reader()
    payload = request.get_json(silent=True) or {}
    action = str(payload.get('action') or request.args.get('action') or 'start').strip().lower()

    if action == 'stop':
        reader.close()
        return jsonify({"keep_alive": reader.keep_alive, "browser_running": False})

    if action != 'start':
        raise OwaError("Azione non valida: usa 'start' oppure 'stop'.", 400, "invalid_action")

    reader.keep_alive = True
    if not reader._warm_up():
        raise OwaError(
            "Impossibile aprire la casella: esegui BOOTSTRAP_OWA_LOGIN.",
            401,
            "owa_login_required",
        )
    return jsonify({"keep_alive": True, "browser_running": True})


# ----------------------------------------------------------------------
# OAuth 2.0 Authorization Code Flow
# ----------------------------------------------------------------------
GENERATION_RE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')


def _resolve_generation():
    """Correlation id for one OAuth attempt.

    The UI generates it so the popup can be opened synchronously inside the
    click handler (popup blockers otherwise reject window.open), then polls
    /api/status?generation=<id> for the callback's outcome.
    """
    candidate = (request.args.get('generation') or '').strip()
    if candidate and GENERATION_RE.match(candidate):
        return candidate
    return secrets.token_urlsafe(24)


@module_bp.route('/api/oauth/start', methods=['GET'])
def oauth_start():
    """Expose the authorization URL for callers that cannot follow a redirect."""
    client = get_client()
    state = _resolve_generation()
    session['outlook_oauth_state'] = state
    _record_auth(state, None)
    try:
        url = client.login_url(state=state, prompt=request.args.get('prompt', 'select_account'))
    except OutlookMailError as error:
        _record_auth(state, False, error.message)
        raise
    return jsonify({"generation": state, "authorization_url": url})


@module_bp.route('/oauth/login', methods=['GET'])
def oauth_login():
    client = get_client()
    state = _resolve_generation()
    session['outlook_oauth_state'] = state
    _record_auth(state, None)
    try:
        url = client.login_url(state=state, prompt=request.args.get('prompt', 'select_account'))
    except OutlookMailError as error:
        _record_auth(state, False, error.message)
        return _callback_page(False, error.message)
    return redirect(url)


@module_bp.route('/oauth/callback', methods=['GET'])
def oauth_callback():
    client = get_client()

    if request.args.get('error'):
        message = request.args.get('error_description') or request.args.get('error')
        _record_auth(session.pop('outlook_oauth_state', None), False, message)
        return _callback_page(False, message)

    expected_state = session.pop('outlook_oauth_state', None)
    received_state = request.args.get('state')
    if not expected_state or not received_state or not hmac.compare_digest(expected_state, received_state):
        message = "State OAuth non valido o scaduto. Ripeti il login."
        _record_auth(expected_state, False, message)
        return _callback_page(False, message)

    try:
        account = client.complete_login(request.args.get('code'))
    except OutlookMailError as error:
        _record_auth(expected_state, False, error.message)
        return _callback_page(False, error.message)

    display = (account or {}).get('username') or (account or {}).get('name') or 'account'
    message = f"Autenticazione completata per {display}."
    _record_auth(expected_state, True, message, account)
    return _callback_page(True, message)


def _callback_page(success, message):
    return render_template(
        'outlook_reader/callback.html',
        success=success,
        message=message,
        module_url=url_for('outlook_mail.index'),
    )


@module_bp.route('/api/logout', methods=['POST'])
def logout():
    client = get_client()
    session.pop('outlook_oauth_state', None)
    return jsonify(client.logout())


# ----------------------------------------------------------------------
# Mail reading
# ----------------------------------------------------------------------
@module_bp.route('/api/me', methods=['GET'])
def get_me():
    return jsonify(get_client().get_me())


@module_bp.route('/api/folders', methods=['GET'])
def get_folders():
    if _requested_backend() == 'owa':
        return jsonify(_owa_reader_for_request().list_folders())

    client = get_client()
    folders = client.list_folders(
        mailbox=request.args.get('mailbox'),
        include_hidden=_truthy(request.args.get('include_hidden')),
    )
    return jsonify({
        "mailbox": client.resolve_mailbox(request.args.get('mailbox')),
        "count": len(folders),
        "folders": folders,
    })


@module_bp.route('/api/messages', methods=['GET'])
def get_messages():
    args = request.args

    raw_folders = args.get('folders') or args.get('folder')
    folders = _split_values(raw_folders)

    mailbox = args.get('mailbox') or args.get('alias')
    top = _int_or_none(args.get('top')) or 25
    skip = _int_or_none(args.get('skip'))

    if _requested_backend() == 'owa':
        return jsonify(_owa_reader_for_request().list_messages(
            folders=folders or None,
            top=top,
            from_contains=(args.get('from') or '').strip() or None,
            subject_contains=(args.get('subject') or '').strip() or None,
            search=(args.get('search') or '').strip() or None,
            since=(args.get('since') or '').strip() or None,
            until=(args.get('until') or '').strip() or None,
            body_type=args.get('body_type'),
        ))

    client = get_client()
    result = client.list_messages(
        mailbox=mailbox,
        folders=folders or None,
        top=top,
        skip=skip,
        select=args.get('select'),
        filter=_build_filter(args),
        search=_build_search(args),
        orderby=args.get('orderby'),
        next_link=args.get('next_link'),
        body_content_type=args.get('body_type'),
        include_count=_truthy(args.get('count')),
    )
    return jsonify(result)


@module_bp.route('/api/messages/<path:message_id>', methods=['GET'])
def get_message(message_id):
    if _requested_backend() == 'owa':
        return jsonify(_owa_reader_for_request().get_message(
            message_id,
            body_type=request.args.get('body_type', 'text'),
        ))

    client = get_client()
    message = client.get_message(
        mailbox=request.args.get('mailbox') or request.args.get('alias'),
        message_id=message_id,
        body_content_type=request.args.get('body_type', 'text'),
        select=request.args.get('select'),
    )
    return jsonify(message)


def _param(args, name):
    """Return a parameter only when it is present and not empty."""
    value = args.get(name)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _build_filter(args):
    """Compose a Graph $filter from convenience parameters plus a raw filter."""
    clauses = []
    raw_filter = (_param(args, 'filter') or '')
    if raw_filter:
        clauses.append(f"({raw_filter})")

    sender = _param(args, 'from')
    if sender:
        clauses.append(f"from/emailAddress/address eq '{escape_odata(sender)}'")

    subject = _param(args, 'subject')
    if subject:
        clauses.append(f"contains(subject,'{escape_odata(subject)}')")

    since = _param(args, 'since')
    if since:
        clauses.append(f"receivedDateTime ge {escape_odata(since)}")

    until = _param(args, 'until')
    if until:
        clauses.append(f"receivedDateTime le {escape_odata(until)}")

    unread = _param(args, 'unread')
    if unread is not None:
        clauses.append(f"isRead eq {'false' if _truthy(unread) else 'true'}")

    attachments = _param(args, 'has_attachments')
    if attachments is not None:
        clauses.append(f"hasAttachments eq {'true' if _truthy(attachments) else 'false'}")

    return ' and '.join(clauses) if clauses else None


def _build_search(args):
    """Build a KQL $search expression.

    Graph rejects $filter + $search together; the convenience parameters map to
    KQL so callers can still filter by sender/subject/date in one request.
    """
    parts = []
    kql = _param(args, 'kql')
    if kql:
        parts.append(kql)

    sender = _param(args, 'search_from')
    if sender:
        parts.append(f'from:"{escape_kql(sender)}"')

    subject = _param(args, 'search_subject')
    if subject:
        parts.append(f'subject:"{escape_kql(subject)}"')

    body = _param(args, 'search_body')
    if body:
        parts.append(f'body:"{escape_kql(body)}"')

    terms = _param(args, 'search')
    if terms:
        parts.append(terms)

    attachments = _param(args, 'search_attachments')
    if attachments is not None:
        parts.append('hasAttachments:true' if _truthy(attachments) else 'hasAttachments:false')

    return ' '.join(parts) if parts else None


@module_bp.route('/api/query', methods=['GET'])
def describe_query():
    """Self-describing endpoint so an agent can discover the API contract."""
    return jsonify({
        "module": MODULE_NAME,
        "base": MODULE_PREFIX + "/api",
        "read_only": True,
        "authentication": "Bearer OAuth token stored server-side; optional X-API-Token header for agent access.",
        "endpoints": {
            "GET /api/status": "Connection and login state.",
            "GET /api/settings": "Non-secret OAuth settings.",
            "GET /api/me": "Signed-in identity.",
            "GET /api/folders?mailbox=&include_hidden=":
                "List folders recursively; 'path' values are usable as the folders parameter.",
            "GET /api/messages": "List messages. See 'parameters'.",
            "GET /api/messages/<id>": "Full message including body and headers.",
            "GET /api/owa/status": "Health of the OWA fallback backend.",
            "POST /api/owa/bootstrap": "One-time interactive sign-in for the OWA fallback.",
            "GET /api/owa/diagnostics": "What the OWA scraper currently sees.",
        },
        "backends": {
            "graph": "Supported. OAuth 2.0 + Microsoft Graph. Full parameter set.",
            "owa": (
                "Unsupported fallback for tenants that refuse admin consent: drives Outlook Web "
                "in a persistent browser profile. Pass backend=owa or X-Mail-Backend: owa. "
                "Supports top, from, subject, search, since, until, body_type and folders; "
                "folders accepts well-known names, custom folder paths (e.g. 'Inbox/OTP/Portale') "
                "and display names, resolved via FindFolder. Ignores "
                "mailbox/select/filter/orderby/next_link/count."
            ),
        },
        "parameters": {
            "mailbox": "User id, UPN or configured alias. Omit or 'me' for the signed-in user.",
            "folders": "Comma-separated folder paths/names/ids, e.g. 'Inbox,Sent Items,Projects/Client'.",
            "folder": "Alias of 'folders' for a single folder.",
            "top": "1-1000 messages per folder (default 25).",
            "skip": "Offset; not allowed together with search.",
            "filter": "Raw OData $filter, AND-ed with the convenience filters.",
            "from": "Sender address equality filter.",
            "subject": "Subject contains filter.",
            "since": "receivedDateTime >= ISO 8601, e.g. 2026-01-01T00:00:00Z.",
            "until": "receivedDateTime <= ISO 8601.",
            "unread": "true/false isRead filter.",
            "has_attachments": "true/false hasAttachments filter.",
            "search": "Free-text or KQL. Cannot be combined with $filter.",
            "search_from": "KQL from:\"...\" (use instead of $filter when searching).",
            "search_subject": "KQL subject:\"...\".",
            "search_body": "KQL body:\"...\".",
            "kql": "Raw KQL appended to the search expression.",
            "orderby": "Server-side sort, default receivedDateTime desc.",
            "select": "Comma-separated Graph properties.",
            "body_type": "text or html for body/uniqueBody.",
            "count": "true adds @odata.count (ignored with search).",
            "next_link": "Opaque @odata.nextLink returned by a previous page.",
            "backend": "graph (default) or owa. Also accepted as the X-Mail-Backend header.",
        },
        "graph_limits": [
            "$filter and $search cannot be combined.",
            "When using $orderby with $filter, orderby properties must lead the filter.",
            "Reading another user's mailbox requires delegated Mail.Read.Shared consent.",
        ],
    })
