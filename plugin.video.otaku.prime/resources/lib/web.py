# -*- coding: utf-8 -*-
"""Small built-in HTTP server for the Otaku Prime web interface."""

from __future__ import annotations

from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs

from resources.lib.auth import AuthService
from resources.lib.ui import render_home, render_login, render_new_password

MAX_FORM_BYTES = 16 * 1024


def create_server(host: str, port: int, user_store) -> ThreadingHTTPServer:
    auth = AuthService(user_store)

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

        def _redirect(self, location: str, cookie_header: Optional[str] = None) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.end_headers()

        def _read_form(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_FORM_BYTES:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            return {key: values[0] for key, values in parse_qs(raw).items() if values}

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                payload = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
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
            form = self._read_form()

            if path == "/login":
                token = auth.login(
                    form.get("username", ""),
                    form.get("password", ""),
                )
                if token is None:
                    self._send_html(401, render_login("Invalid username or password."))
                    return

                self._redirect(
                    "/",
                    f"otaku_prime_session={token}; Path=/; HttpOnly; SameSite=Strict",
                )
                return

            if path == "/password":
                user = self._current_user()
                if not user:
                    self._redirect("/")
                    return

                token = self._cookie_value("otaku_prime_session")
                new_password = form.get("new_password", "")
                if new_password != form.get("confirm_password", new_password):
                    page = render_new_password(user, "The new passwords do not match.")
                    self._send_html(400, page)
                    return
                result = auth.change_password(
                    token,
                    form.get("current_password", ""),
                    new_password,
                )
                if result == "incorrect_password":
                    if user.get("must_change_password"):
                        page = render_new_password(user, "Current password is incorrect.")
                    else:
                        page = render_home(user, "Current password is incorrect.", "accounts")
                    self._send_html(403, page)
                    return
                if result == "password_too_short":
                    if user.get("must_change_password"):
                        page = render_new_password(user, "New password must be at least 8 characters.")
                    else:
                        page = render_home(user, "New password must be at least 8 characters.", "accounts")
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
