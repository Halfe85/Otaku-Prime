# -*- coding: utf-8 -*-
"""Small built-in HTTP server for the Otaku Prime web interface."""

from __future__ import annotations

import html
import secrets
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_FORM_BYTES = 16 * 1024


class SessionStore:
    def __init__(self) -> None:
        self._sessions = {}
        self._lock = threading.Lock()

    def create(self, user: dict) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = {
                "user": user,
                "expires": time.time() + SESSION_TTL_SECONDS,
            }
        return token

    def get(self, token: str):
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if session["expires"] <= time.time():
                self._sessions.pop(token, None)
                return None
            return session["user"]

    def delete(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)


def create_server(host: str, port: int, user_store) -> ThreadingHTTPServer:
    sessions = SessionStore()

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
            return sessions.get(self._cookie_value("otaku_prime_session"))

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

        def _redirect(self, location: str, cookie_header: str | None = None) -> None:
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

        def _login_page(self, message: str = "") -> str:
            notice = f"<p>{html.escape(message)}</p>" if message else ""
            return f"""<!doctype html>
<html>
<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Otaku Prime</title></head>
<body>
  <main>
    <h1>Otaku Prime</h1>
    {notice}
    <form method=\"post\" action=\"/login\">
      <label>Username <input name=\"username\" autocomplete=\"username\" required></label><br>
      <label>Password <input name=\"password\" type=\"password\" autocomplete=\"current-password\" required></label><br>
      <button type=\"submit\">Sign in</button>
    </form>
  </main>
</body>
</html>"""

        def _home_page(self, user: dict, message: str = "") -> str:
            warning = ""
            if user.get("must_change_password"):
                warning = "<p><strong>Default password is active. Change it below.</strong></p>"
            notice = f"<p>{html.escape(message)}</p>" if message else ""
            return f"""<!doctype html>
<html>
<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Otaku Prime</title></head>
<body>
  <main>
    <h1>Otaku Prime</h1>
    <p>Signed in as <strong>{html.escape(user['username'])}</strong> ({html.escape(user['role'])})</p>
    {warning}
    {notice}
    <form method=\"post\" action=\"/password\">
      <label>Current password <input name=\"current_password\" type=\"password\" required></label><br>
      <label>New password <input name=\"new_password\" type=\"password\" required></label><br>
      <button type=\"submit\">Change password</button>
    </form>
    <p><a href=\"/logout\">Sign out</a></p>
  </main>
</body>
</html>"""

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
                sessions.delete(token)
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
                self._send_html(200, self._home_page(user))
            else:
                self._send_html(200, self._login_page())

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            form = self._read_form()

            if path == "/login":
                user = user_store.authenticate(
                    form.get("username", ""),
                    form.get("password", ""),
                )
                if user is None:
                    self._send_html(401, self._login_page("Invalid username or password."))
                    return

                token = sessions.create(user)
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

                verified = user_store.authenticate(
                    user["username"],
                    form.get("current_password", ""),
                )
                new_password = form.get("new_password", "")
                if verified is None:
                    self._send_html(403, self._home_page(user, "Current password is incorrect."))
                    return
                if len(new_password) < 8:
                    self._send_html(400, self._home_page(user, "New password must be at least 8 characters."))
                    return

                user_store.update_password(user["id"], new_password)
                sessions.delete(self._cookie_value("otaku_prime_session"))
                self._redirect(
                    "/",
                    "otaku_prime_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
                )
                return

            self._send_html(404, "<h1>404</h1>")

    return ThreadingHTTPServer((host, port), PrimeRequestHandler)
