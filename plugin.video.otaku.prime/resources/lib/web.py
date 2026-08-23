# -*- coding: utf-8 -*-
"""Small built-in HTTP server for the Otaku Prime web interface."""

from __future__ import annotations

from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from typing import Optional
from urllib.parse import parse_qs

import qrcode
import qrcode.image.svg

from resources.lib.auth import AuthService
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.endpoints.auth_service import AuthenticatorAPI, AuthenticatorAPIError
from resources.lib.ui import render_home, render_login, render_new_password
from resources.lib.watchlist.anilist_ui import render_anilist_auth

MAX_FORM_BYTES = 16 * 1024


def create_server(host: str, port: int, user_store) -> ThreadingHTTPServer:
    auth = AuthService(user_store)
    authenticator_api = AuthenticatorAPI()
    watchlist_accounts = WatchlistAccountStore(user_store.db_path)
    watchlist_accounts.initialize()

    class PrimeRequestHandler(BaseHTTPRequestHandler):
        server_version = "OtakuPrime/0.1"

        def log_message(self, format, *args):
            return

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
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return {}
                return payload if isinstance(payload, dict) else {}
            form = raw.decode("utf-8", errors="replace")
            return {key: values[0] for key, values in parse_qs(form).items() if values}

        def _send_auth_api_error(self, exc: AuthenticatorAPIError) -> None:
            self._send_json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})

        def _require_user(self):
            user = self._current_user()
            if not user:
                self._redirect("/")
                return None
            return user

        def _anilist_page(self, user: dict, message: str = "") -> None:
            account = watchlist_accounts.get(user["id"], "anilist")
            try:
                authorize_url = authenticator_api.authorization_url("anilist")
            except AuthenticatorAPIError as exc:
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

        def do_GET(self):
            path = self.path.split("?", 1)[0]

            if path == "/health":
                self._send_bytes(200, "text/plain; charset=utf-8", b"ok")
                return

            if path == "/api/auth/providers":
                self._send_json(200, authenticator_api.list_providers())
                return

            if path == "/api/auth/anilist/info":
                try:
                    info = authenticator_api.provider_info("anilist")
                except AuthenticatorAPIError as exc:
                    self._send_auth_api_error(exc)
                    return
                self._send_json(200, {"ok": True, "provider": info})
                return

            # Canonical direct AniList authorization redirect. No Armkai or client secret.
            if path == "/api/auth/anilist":
                try:
                    target = authenticator_api.authorization_url("anilist")
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

            if path == "/watchlist/anilist/qr.svg":
                user = self._require_user()
                if not user:
                    return
                try:
                    target = authenticator_api.authorization_url("anilist")
                except AuthenticatorAPIError as exc:
                    self._send_html(exc.status, f"<h1>AniList unavailable</h1><p>{exc.message}</p>")
                    return
                image = qrcode.make(target, image_factory=qrcode.image.svg.SvgPathImage)
                output = io.BytesIO()
                image.save(output)
                self._send_bytes(200, "image/svg+xml; charset=utf-8", output.getvalue())
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
                    self._send_html(200, render_home(user))
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
                self._redirect("/watchlist/anilist")
                return

            if path == "/watchlist/anilist/disconnect":
                user = self._require_user()
                if not user:
                    return
                watchlist_accounts.delete(user["id"], "anilist")
                self._redirect("/watchlist/anilist")
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
                        else render_home(user, "Current password is incorrect.", "accounts")
                    )
                    self._send_html(403, page)
                    return
                if result == "password_too_short":
                    page = (
                        render_new_password(user, "New password must be at least 8 characters.")
                        if user.get("must_change_password")
                        else render_home(user, "New password must be at least 8 characters.", "accounts")
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
