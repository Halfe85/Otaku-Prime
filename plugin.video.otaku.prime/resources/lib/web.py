# -*- coding: utf-8 -*-
"""Small built-in HTTP server for the Otaku Prime web interface."""

from __future__ import annotations

from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from resources.lib.auth import AuthService
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.database.app_logs import AppLogStore
from resources.lib.endpoints.auth_service import AuthenticatorAPI, AuthenticatorAPIError
from resources.lib.logging_config import get_logger
LOGGER=get_logger(__name__)
from resources.lib.ui import (
    read_static_asset,
    render_anilist_auth,
    render_home,
    render_kitsu_auth,
    render_login,
    render_mal_auth,
    render_new_password,
    render_simkl_auth,
)

MAX_FORM_BYTES = 16 * 1024


def create_server(host: str, port: int, user_store, app_log_store=None,
                  on_watchlist_changed=None) -> ThreadingHTTPServer:
    auth = AuthService(user_store)
    authenticator_api = AuthenticatorAPI()
    watchlist_accounts = WatchlistAccountStore(user_store.db_path)
    watchlist_accounts.initialize()
    watchlist_items = WatchlistItemStore(user_store.db_path)
    watchlist_items.initialize()
    app_log_store = app_log_store or AppLogStore(user_store.db_path)
    app_log_store.initialize()
    simkl_flows = {}
    simkl_flows_lock = threading.Lock()

    class PrimeRequestHandler(BaseHTTPRequestHandler):
        server_version = "OtakuPrime/0.1"

        def log_message(self, format, *args):
            return

        def log_error(self, format, *args):
            LOGGER.error("HTTP server: "+format,*args)

        def _cookie_value(self, name: str) -> str:
            raw_cookie = self.headers.get("Cookie", "")
            jar = cookies.SimpleCookie()
            try:
                jar.load(raw_cookie)
            except cookies.CookieError:
                return ""
            morsel = jar.get(name)
            return morsel.value if morsel else ""

        def _current_user(self):
            return auth.current_user(self._cookie_value("otaku_prime_session"))

        def _send_html(self, status: int, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(payload)

        def _send_bytes(self, status: int, content_type: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def _send_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            self._send_bytes(status, "application/json; charset=utf-8", payload)

        def _redirect(self, location: str, cookie_header: Optional[str] = None) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.end_headers()

        def _external_redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

        def _read_body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_FORM_BYTES:
                return b""
            return self.rfile.read(length)

        def _read_form(self) -> dict:
            raw = self._read_body().decode("utf-8", errors="replace")
            return {key: values[0] for key, values in parse_qs(raw).items() if values}

        def _read_api_payload(self) -> dict:
            raw = self._read_body()
            if not raw:
                return {}
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type == "application/json":
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    LOGGER.warning("Rejected malformed API request on %s: %s",self.path,exc)
                    return {}
                return payload if isinstance(payload, dict) else {}
            form = raw.decode("utf-8", errors="replace")
            return {key: values[0] for key, values in parse_qs(form).items() if values}

        def _send_auth_api_error(self, exc: AuthenticatorAPIError) -> None:
            level=LOGGER.error if int(exc.status)>=500 else LOGGER.warning
            level("Authentication API error %s on %s: %s",exc.code,self.path,exc.message)
            self._send_json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})

        def _require_user(self):
            user = self._current_user()
            if not user:
                self._redirect("/")
                return None
            return user

        def _watchlist_changed(self, provider):
            if on_watchlist_changed:
                threading.Thread(
                    target=on_watchlist_changed,
                    name="OtakuPrime{}Changed".format(provider.title()),
                    daemon=True,
                ).start()

        def _anilist_page(self, user: dict, message: str = "") -> None:
            account = watchlist_accounts.get(user["id"], "anilist")
            try:
                authorize_url = authenticator_api.authorization_url("anilist")
            except AuthenticatorAPIError as exc:
                LOGGER.warning("AniList authorization page unavailable: %s",exc.message)
                authorize_url = "#"
                if not message:
                    message = exc.message
            self._send_html(
                200,
                render_anilist_auth(
                    authorize_url=authorize_url,
                    connected_account=account,
                    message=message,
                ),
            )

        def _mal_page(self, user: dict, message: str = "") -> None:
            account = watchlist_accounts.get(user["id"], "mal")
            try:
                authorize_url = authenticator_api.authorization_url("mal")
            except AuthenticatorAPIError as exc:
                LOGGER.warning("MAL authorization page unavailable: %s",exc.message)
                authorize_url = "#"
                if not message:
                    message = exc.message
            self._send_html(
                200,
                render_mal_auth(
                    authorize_url=authorize_url,
                    connected_account=account,
                    message=message,
                ),
            )

        def _kitsu_page(self, user: dict, message: str = "") -> None:
            account = watchlist_accounts.get(user["id"], "kitsu")
            self._send_html(
                200,
                render_kitsu_auth(
                    connected_account=account,
                    message=message,
                ),
            )

        def _simkl_page(self, user: dict, message: str = "") -> None:
            account = watchlist_accounts.get(user["id"], "simkl")
            with simkl_flows_lock:
                pending = simkl_flows.get(user["id"])
                pending = pending.copy() if pending else None
            self._send_html(
                200,
                render_simkl_auth(
                    connected_account=account,
                    pending=pending,
                    message=message,
                ),
            )

        def _home_page(self, user: dict, message: str = "", active_tab: str = "watchlist") -> str:
            accounts = {
                "anilist": watchlist_accounts.get(user["id"], "anilist"),
                "kitsu": watchlist_accounts.get(user["id"], "kitsu"),
                "mal": watchlist_accounts.get(user["id"], "mal"),
                "simkl": watchlist_accounts.get(user["id"], "simkl"),
            }
            return render_home(
                user,
                message=message,
                active_tab=active_tab,
                watchlist_accounts=accounts,
            )

        def do_GET(self):
            request_url = urlsplit(self.path)
            path = request_url.path

            if path.startswith("/ui/"):
                asset = read_static_asset(path[len("/ui/"):])
                if asset is None:
                    self._send_html(404, "<h1>404</h1>")
                    return
                content_type, payload = asset
                self._send_bytes(200, content_type, payload)
                return

            if path == "/health":
                self._send_bytes(200, "text/plain; charset=utf-8", b"ok")
                return

            if path == "/api/auth/providers":
                self._send_json(200, authenticator_api.list_providers())
                return

            if path == "/api/logs":
                if not self._current_user():
                    self._send_json(401,{"ok":False,"message":"Sign in again."}); return
                query=parse_qs(request_url.query)
                try: after_id=int((query.get("after") or [0])[0])
                except (TypeError,ValueError): after_id=0
                entries=app_log_store.list(after_id=after_id)
                self._send_json(200,{"ok":True,"entries":entries}); return

            if path == "/api/watchlist/items":
                if not self._current_user():
                    self._send_json(401,{"ok":False,"message":"Sign in again."}); return
                self._send_json(200,{"ok":True,"entries":watchlist_items.list_all()})
                return

            if path == "/api/auth/anilist/info":
                try:
                    info = authenticator_api.provider_info("anilist")
                except AuthenticatorAPIError as exc:
                    self._send_auth_api_error(exc)
                    return
                self._send_json(200, {"ok": True, "provider": info})
                return

            if path == "/api/auth/mal/info":
                try:
                    info = authenticator_api.provider_info("mal")
                except AuthenticatorAPIError as exc:
                    self._send_auth_api_error(exc)
                    return
                self._send_json(200, {"ok": True, "provider": info})
                return

            if path == "/api/auth/kitsu/info":
                try:
                    info = authenticator_api.provider_info("kitsu")
                except AuthenticatorAPIError as exc:
                    self._send_auth_api_error(exc)
                    return
                self._send_json(200, {"ok": True, "provider": info})
                return

            if path == "/api/auth/simkl/info":
                try:
                    info = authenticator_api.provider_info("simkl")
                except AuthenticatorAPIError as exc:
                    self._send_auth_api_error(exc)
                    return
                self._send_json(200, {"ok": True, "provider": info})
                return

            if path == "/api/auth/anilist":
                try:
                    target = authenticator_api.authorization_url("anilist")
                except AuthenticatorAPIError as exc:
                    self._send_auth_api_error(exc)
                    return
                self._external_redirect(target)
                return

            if path == "/api/auth/mal":
                try:
                    target = authenticator_api.authorization_url("mal")
                except AuthenticatorAPIError as exc:
                    self._send_auth_api_error(exc)
                    return
                self._external_redirect(target)
                return

            if path == "/watchlist/anilist":
                user = self._require_user()
                if user:
                    self._anilist_page(user)
                return

            if path == "/watchlist/mal":
                user = self._require_user()
                if user:
                    self._mal_page(user)
                return

            if path == "/watchlist/kitsu":
                user = self._require_user()
                if user:
                    self._kitsu_page(user)
                return

            if path == "/watchlist/simkl":
                user = self._require_user()
                if user:
                    self._simkl_page(user)
                return

            if path == "/watchlist/simkl/status":
                user = self._current_user()
                if not user:
                    self._send_json(401, {"status": "error", "message": "Sign in again."})
                    return
                now = time.time()
                with simkl_flows_lock:
                    flow = simkl_flows.get(user["id"])
                    if flow is None:
                        self._send_json(404, {"status": "error", "message": "No Simkl authorization is active."})
                        return
                    if flow["expires_at"] <= now:
                        simkl_flows.pop(user["id"], None)
                        self._send_json(200, {"status": "expired", "message": "The Simkl code expired. Generate a new one."})
                        return
                    if flow["next_poll_at"] > now:
                        retry_after = max(1, int(flow["next_poll_at"] - now) + 1)
                        self._send_json(200, {"status": "pending", "retry_after": retry_after})
                        return
                    flow["next_poll_at"] = now + flow["interval"]
                    user_code = flow["user_code"]
                    interval = flow["interval"]
                try:
                    result = authenticator_api.poll_simkl(user_code)
                except AuthenticatorAPIError as exc:
                    self._send_json(exc.status, {"status": "error", "message": exc.message})
                    return
                if result is None:
                    self._send_json(200, {"status": "pending", "retry_after": interval})
                    return
                viewer = result["user"]
                watchlist_accounts.save(
                    user_id=user["id"],
                    provider="simkl",
                    external_user_id=str(viewer["id"]),
                    external_username=viewer["username"],
                    access_token=result["access_token"],
                )
                with simkl_flows_lock:
                    simkl_flows.pop(user["id"], None)
                self._watchlist_changed("simkl")
                self._send_json(200, {"status": "connected", "username": viewer["username"]})
                return

            if path == "/logout":
                token = self._cookie_value("otaku_prime_session")
                auth.logout(token)
                self._redirect(
                    "/",
                    "otaku_prime_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
                )
                return

            if path != "/":
                self._send_html(404, "<h1>404</h1>")
                return

            user = self._current_user()
            if user:
                if user.get("must_change_password"):
                    self._send_html(200, render_new_password(user))
                else:
                    self._send_html(200, self._home_page(user))
            else:
                self._send_html(200, render_login())

        def do_POST(self):
            path = self.path.split("?", 1)[0]

            if path == "/api/auth/anilist/verify":
                user = self._current_user()
                if not user:
                    self._send_json(
                        401,
                        {
                            "ok": False,
                            "error": "authentication_required",
                            "message": "Sign in to Otaku Prime before validating a watchlist token.",
                        },
                    )
                    return
                payload = self._read_api_payload()
                try:
                    result = authenticator_api.verify_token("anilist", str(payload.get("token", "")))
                except AuthenticatorAPIError as exc:
                    self._send_auth_api_error(exc)
                    return
                self._send_json(200, result)
                return

            if path == "/watchlist/anilist/connect":
                user = self._require_user()
                if not user:
                    return
                form = self._read_form()
                token = form.get("token", "").strip()
                try:
                    result = authenticator_api.verify_token("anilist", token)
                except AuthenticatorAPIError as exc:
                    self._anilist_page(user, exc.message)
                    return
                viewer = result["user"]
                watchlist_accounts.save(
                    user_id=user["id"],
                    provider="anilist",
                    external_user_id=str(viewer["id"]),
                    external_username=viewer["username"],
                    access_token=token,
                )
                LOGGER.info("AniList account connected through admin UI: %s",viewer["username"])
                self._watchlist_changed("anilist")
                self._redirect("/watchlist/anilist")
                return

            if path == "/watchlist/anilist/disconnect":
                user = self._require_user()
                if not user:
                    return
                watchlist_accounts.delete(user["id"], "anilist")
                self._watchlist_changed("anilist")
                self._redirect("/watchlist/anilist")
                return

            if path == "/watchlist/mal/connect":
                user = self._require_user()
                if not user:
                    return
                form = self._read_form()
                try:
                    result = authenticator_api.connect_mal(form.get("callback_url", ""))
                except AuthenticatorAPIError as exc:
                    self._mal_page(user, exc.message)
                    return
                viewer = result["user"]
                watchlist_accounts.save(
                    user_id=user["id"],
                    provider="mal",
                    external_user_id=str(viewer["id"]),
                    external_username=viewer["username"],
                    access_token=result["access_token"],
                    refresh_token=result["refresh_token"],
                    token_expires_at=result["expires_at"],
                )
                self._watchlist_changed("mal")
                self._redirect("/watchlist/mal")
                return

            if path == "/watchlist/mal/disconnect":
                user = self._require_user()
                if not user:
                    return
                watchlist_accounts.delete(user["id"], "mal")
                self._watchlist_changed("mal")
                self._redirect("/watchlist/mal")
                return

            if path == "/watchlist/kitsu/connect":
                user = self._require_user()
                if not user:
                    return
                form = self._read_form()
                try:
                    result = authenticator_api.connect_kitsu(
                        form.get("username", ""),
                        form.get("password", ""),
                    )
                except AuthenticatorAPIError as exc:
                    self._kitsu_page(user, exc.message)
                    return
                viewer = result["user"]
                watchlist_accounts.save(
                    user_id=user["id"],
                    provider="kitsu",
                    external_user_id=str(viewer["id"]),
                    external_username=viewer["username"],
                    access_token=result["access_token"],
                    refresh_token=result["refresh_token"],
                    token_expires_at=result["expires_at"],
                )
                self._watchlist_changed("kitsu")
                self._redirect("/watchlist/kitsu")
                return

            if path == "/watchlist/kitsu/disconnect":
                user = self._require_user()
                if not user:
                    return
                watchlist_accounts.delete(user["id"], "kitsu")
                self._watchlist_changed("kitsu")
                self._redirect("/watchlist/kitsu")
                return

            if path == "/watchlist/simkl/start":
                user = self._require_user()
                if not user:
                    return
                try:
                    device = authenticator_api.start_simkl()
                except AuthenticatorAPIError as exc:
                    self._simkl_page(user, exc.message)
                    return
                now = time.time()
                with simkl_flows_lock:
                    simkl_flows[user["id"]] = {
                        "user_code": device["user_code"],
                        "verification_url": device["verification_url"],
                        "expires_at": now + device["expires_in"],
                        "interval": device["interval"],
                        "next_poll_at": now + device["interval"],
                    }
                self._redirect("/watchlist/simkl")
                return

            if path == "/watchlist/simkl/disconnect":
                user = self._require_user()
                if not user:
                    return
                watchlist_accounts.delete(user["id"], "simkl")
                with simkl_flows_lock:
                    simkl_flows.pop(user["id"], None)
                self._watchlist_changed("simkl")
                self._redirect("/watchlist/simkl")
                return

            if path == "/login":
                form = self._read_form()
                token = auth.login(form.get("username", ""), form.get("password", ""))
                if token is None:
                    self._send_html(401, render_login("Invalid username or password."))
                    return
                self._redirect(
                    "/",
                    f"otaku_prime_session={token}; Path=/; HttpOnly; SameSite=Strict",
                )
                return

            if path == "/password":
                form = self._read_form()
                user = self._current_user()
                if not user:
                    self._redirect("/")
                    return
                token = self._cookie_value("otaku_prime_session")
                new_password = form.get("new_password", "")
                if new_password != form.get("confirm_password", new_password):
                    self._send_html(400, render_new_password(user, "The new passwords do not match."))
                    return
                result = auth.change_password(token, form.get("current_password", ""), new_password)
                if result == "incorrect_password":
                    page = (
                        render_new_password(user, "Current password is incorrect.")
                        if user.get("must_change_password")
                        else self._home_page(user, "Current password is incorrect.", "accounts")
                    )
                    self._send_html(403, page)
                    return
                if result == "password_too_short":
                    page = (
                        render_new_password(user, "New password must be at least 8 characters.")
                        if user.get("must_change_password")
                        else self._home_page(user, "New password must be at least 8 characters.", "accounts")
                    )
                    self._send_html(400, page)
                    return
                if result == "not_authenticated":
                    self._redirect("/")
                    return
                self._redirect(
                    "/",
                    "otaku_prime_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
                )
                return

            self._send_html(404, "<h1>404</h1>")

    return ThreadingHTTPServer((host, port), PrimeRequestHandler)
