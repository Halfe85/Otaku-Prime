# -*- coding: utf-8 -*-
"""HTML presentation for the Otaku Prime management interface."""

from __future__ import annotations

import html


SETTINGS_CATEGORIES = (
    ("general", "General", "Language, interface, metadata, artwork, widgets, and downloads."),
    ("providers", "Providers", "Torrent, embed, streaming, and local-file providers."),
    ("scraping", "Scraping", "Source discovery, timeouts, cloud inspection, and fallback rules."),
    ("playback", "Playback", "Play style, audio, subtitles, skip intro, and playing next."),
    ("sort-filter", "Sort & Filter", "Resolution, codecs, source types, and result ordering."),
    ("accounts", "Accounts", "Prime administrator and future debrid service connections."),
    ("watchlist", "Watchlist", "AniList, MyAnimeList, Kitsu, and Simkl connections."),
    ("menu", "Menu Customization", "Choose which destinations appear in Kodi menus."),
    ("context", "Context Customization", "Configure actions shown in Kodi context menus."),
    ("maintenance", "Maintenance", "Cache, database, diagnostics, import, and export tools."),
)


BASE_STYLE = """
:root {
  color-scheme: dark;
  --background: #090b12;
  --surface: #121724;
  --surface-raised: #191f30;
  --border: #2b3348;
  --text: #f5f7ff;
  --muted: #9ba6bd;
  --accent: #8b7cff;
  --accent-strong: #aa9fff;
  --danger: #ff7e8e;
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body {
  background: radial-gradient(circle at top right, #1c1940 0, var(--background) 38%);
  color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
body::before, body::after {
  content: "";
  position: fixed;
  width: 360px;
  height: 360px;
  border-radius: 50%;
  filter: blur(100px);
  opacity: .16;
  pointer-events: none;
}
body::before { top: -150px; right: -90px; background: #8b7cff; }
body::after { bottom: -180px; left: -100px; background: #3d7cff; }
button, input, select { font: inherit; }
a { color: var(--accent-strong); }
.main-container {
  width: 100%;
  height: 100dvh;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  overflow: hidden;
}
.topbar, .bottombar {
  flex: none;
  background: rgba(14, 18, 29, .96);
  backdrop-filter: blur(14px);
  z-index: 2;
}
.topbar { border-bottom: 1px solid var(--border); }
.brand-row {
  min-height: 66px;
  padding: 12px clamp(18px, 4vw, 52px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--accent), #5546d7);
  font-weight: 800;
}
.brand h1 { margin: 0; font-size: 18px; }
.brand p { margin: 0; color: var(--muted); font-size: 12px; }
.tabs {
  padding: 0 clamp(18px, 4vw, 52px);
  display: flex;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: thin;
}
.tab {
  padding: 11px 14px;
  border: 0;
  border-bottom: 3px solid transparent;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  white-space: nowrap;
}
.tab:hover, .tab:focus-visible { color: var(--text); }
.tab.active { color: var(--text); border-color: var(--accent); }
.main-content {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 30px clamp(18px, 4vw, 52px) 52px;
}
.content-inner { width: min(980px, 100%); margin: 0 auto; }
.panel[hidden] { display: none; }
.panel-header { margin-bottom: 24px; }
.panel-header h2 { margin: 0 0 5px; font-size: clamp(24px, 4vw, 34px); }
.panel-header p, .muted { color: var(--muted); }
.card {
  margin: 0 0 16px;
  padding: 20px;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: linear-gradient(145deg, rgba(25,31,48,.95), rgba(17,22,35,.95));
}
.card h3 { margin: 0 0 6px; font-size: 17px; }
.badge {
  display: inline-block;
  margin-bottom: 13px;
  padding: 3px 8px;
  border: 1px solid #544b9c;
  border-radius: 999px;
  color: #c6beff;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
}
.field-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
label { display: grid; gap: 6px; color: #d9deec; }
input, select {
  width: 100%;
  min-height: 42px;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: #0d111c;
  color: var(--text);
}
input:disabled, select:disabled { opacity: .55; cursor: not-allowed; }
.button, button.button {
  display: inline-flex;
  min-height: 40px;
  align-items: center;
  justify-content: center;
  padding: 8px 14px;
  border: 1px solid #695bda;
  border-radius: 9px;
  background: #5f51d1;
  color: white;
  cursor: pointer;
  text-decoration: none;
}
.button.secondary { border-color: var(--border); background: transparent; }
.notice { padding: 11px 13px; border-radius: 9px; background: #27213e; color: #ded8ff; }
.warning { border-left: 3px solid var(--danger); }
.bottombar {
  min-height: 45px;
  padding: 10px clamp(18px, 4vw, 52px);
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.auth-screen {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 20px;
}
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 10;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(4, 6, 12, .58);
  backdrop-filter: blur(10px);
}
.auth-modal {
  width: min(440px, 100%);
  margin: 0;
  padding: 28px;
  border: 1px solid rgba(139, 124, 255, .34);
  border-radius: 20px;
  background: linear-gradient(155deg, rgba(28, 34, 52, .98), rgba(13, 17, 28, .98));
  box-shadow: 0 28px 90px rgba(0, 0, 0, .55), 0 0 0 1px rgba(255,255,255,.025) inset;
}
.auth-modal .brand { margin-bottom: 24px; }
.auth-modal h2 { margin: 0 0 7px; font-size: 23px; }
.auth-modal > p { margin-top: 0; }
.auth-modal form { display: grid; gap: 15px; }
.auth-modal .button { width: 100%; margin-top: 2px; }
.security-note {
  display: flex;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--muted);
  font-size: 12px;
}
@media (max-width: 620px) {
  .brand-row { min-height: 58px; }
  .brand p, .user-name { display: none; }
  .main-content { padding-top: 22px; }
  .bottombar span:last-child { display: none; }
}
"""


def _document(title: str, body: str, script: str = "") -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>{style}</style>
</head>
<body>{body}<script>{script}</script></body>
</html>""".format(title=html.escape(title), style=BASE_STYLE, body=body, script=script)


def render_login(message: str = "") -> str:
    notice = '<p class="notice warning">{}</p>'.format(html.escape(message)) if message else ""
    body = """
<main class="auth-screen">
  <div class="modal-backdrop">
    <section class="auth-modal" id="login-modal" role="dialog" aria-modal="true" aria-labelledby="login-title">
      <div class="brand"><span class="brand-mark">OP</span><div><h1>Otaku Prime</h1><p>Local management</p></div></div>
      <h2 id="login-title">Welcome back</h2>
      <p class="muted">Sign in to manage this Otaku Prime installation.</p>
      {notice}
      <form method="post" action="/login">
        <label>Username<input name="username" autocomplete="username" required autofocus></label>
        <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
        <button class="button" type="submit">Sign in</button>
      </form>
    </section>
  </div>
</main>""".format(notice=notice)
    return _document("Sign in - Otaku Prime", body)


def render_new_password(user: dict, message: str = "") -> str:
    notice = '<p class="notice warning">{}</p>'.format(html.escape(message)) if message else ""
    body = """
<main class="auth-screen">
  <div class="modal-backdrop">
    <section class="auth-modal" id="new-password-modal" role="dialog" aria-modal="true" aria-labelledby="password-title">
      <div class="brand"><span class="brand-mark">OP</span><div><h1>Otaku Prime</h1><p>Account security</p></div></div>
      <span class="badge">Required</span>
      <h2 id="password-title">Create a new password</h2>
      <p class="muted">The bootstrap password for <strong>{username}</strong> must be replaced before settings can be opened.</p>
      {notice}
      <form method="post" action="/password">
        <label>Current password<input name="current_password" type="password" autocomplete="current-password" required autofocus></label>
        <label>New password<input name="new_password" type="password" autocomplete="new-password" minlength="8" required></label>
        <label>Confirm new password<input name="confirm_password" type="password" autocomplete="new-password" minlength="8" required></label>
        <div class="security-note"><span>●</span><span>Use at least 8 characters. The password is stored only as a salted hash.</span></div>
        <button class="button" type="submit">Save new password</button>
      </form>
    </section>
  </div>
</main>""".format(username=html.escape(user["username"]), notice=notice)
    return _document("New password - Otaku Prime", body)


def _preview_card(category_id: str) -> str:
    previews = {
        "general": ("Interface defaults", "Title language", "English", "Preferred artwork", "Kodi default"),
        "providers": ("Provider selection", "Torrent providers", "Not configured", "Streaming providers", "Not configured"),
        "scraping": ("Source discovery", "Scraping timeout", "30 seconds", "Try next source", "Enabled"),
        "playback": ("Playback defaults", "Audio language", "Japanese", "Subtitle language", "English"),
        "sort-filter": ("Result filtering", "Maximum resolution", "1080p", "Source ordering", "Quality first"),
        "watchlist": ("Tracker connections", "AniList", "Not connected", "MyAnimeList", "Not connected"),
        "menu": ("Kodi menu visibility", "Watchlist menu", "Planned", "Search menu", "Planned"),
        "context": ("Kodi context actions", "Rescrape", "Planned", "Recommendations", "Planned"),
        "maintenance": ("Maintenance tools", "Clear cache", "Unavailable", "Export settings", "Unavailable"),
    }
    title, label_one, value_one, label_two, value_two = previews[category_id]
    return """
<article class="card">
  <span class="badge">Alpha preview</span>
  <h3>{title}</h3>
  <p class="muted">These controls establish the layout only. Values are not saved yet.</p>
  <div class="field-grid">
    <label>{label_one}<input value="{value_one}" disabled></label>
    <label>{label_two}<input value="{value_two}" disabled></label>
  </div>
</article>""".format(
        title=html.escape(title),
        label_one=html.escape(label_one),
        value_one=html.escape(value_one),
        label_two=html.escape(label_two),
        value_two=html.escape(value_two),
    )


def _accounts_content(user: dict, message: str) -> str:
    notice = '<p class="notice">{}</p>'.format(html.escape(message)) if message else ""
    warning = ""
    if user.get("must_change_password"):
        warning = '<p class="notice warning">The bootstrap password is still active. Change it now.</p>'
    return """
<article class="card">
  <span class="badge">Active</span>
  <h3>Prime administrator</h3>
  <p class="muted">Signed in as <strong>{username}</strong> ({role}). Password changes are saved securely.</p>
  {warning}{notice}
  <form method="post" action="/password">
    <div class="field-grid">
      <label>Current password<input name="current_password" type="password" autocomplete="current-password" required></label>
      <label>New password<input name="new_password" type="password" autocomplete="new-password" minlength="8" required></label>
    </div>
    <p><button class="button" type="submit">Change password</button></p>
  </form>
</article>
<article class="card">
  <span class="badge">Alpha preview</span>
  <h3>Debrid accounts</h3>
  <p class="muted">Real-Debrid, AllDebrid, Premiumize, and TorBox connections will be added in a later milestone.</p>
</article>""".format(
        username=html.escape(user["username"]),
        role=html.escape(user["role"]),
        warning=warning,
        notice=notice,
    )


def render_home(user: dict, message: str = "", active_tab: str = "general") -> str:
    category_ids = {item[0] for item in SETTINGS_CATEGORIES}
    if active_tab not in category_ids:
        active_tab = "general"

    tabs = []
    panels = []
    for category_id, label, description in SETTINGS_CATEGORIES:
        selected = category_id == active_tab
        tabs.append(
            '<button class="tab{}" type="button" role="tab" aria-selected="{}" '
            'aria-controls="panel-{}" data-tab="{}">{}</button>'.format(
                " active" if selected else "",
                "true" if selected else "false",
                category_id,
                category_id,
                html.escape(label),
            )
        )
        content = _accounts_content(user, message) if category_id == "accounts" else _preview_card(category_id)
        panels.append(
            '<section class="panel" id="panel-{}" role="tabpanel"{}>'
            '<header class="panel-header"><h2>{}</h2><p>{}</p></header>{}</section>'.format(
                category_id,
                "" if selected else " hidden",
                html.escape(label),
                html.escape(description),
                content,
            )
        )

    body = """
<div class="main-container">
  <header class="topbar">
    <div class="brand-row">
      <div class="brand"><span class="brand-mark">OP</span><div><h1>Otaku Prime</h1><p>Management interface</p></div></div>
      <div><span class="user-name">{username} &nbsp;</span><a class="button secondary" href="/logout">Sign out</a></div>
    </div>
    <nav class="tabs" role="tablist" aria-label="Settings categories">{tabs}</nav>
  </header>
  <main class="main-content"><div class="content-inner">{panels}</div></main>
  <footer class="bottombar"><span>Otaku Prime 0.1.0 Alpha</span><span>Settings shell · only account security is active</span></footer>
</div>""".format(
        username=html.escape(user["username"]),
        tabs="".join(tabs),
        panels="".join(panels),
    )
    script = """
(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[data-tab]'));
  function selectTab(id) {
    tabs.forEach(function (tab) {
      var selected = tab.getAttribute('data-tab') === id;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', selected ? 'true' : 'false');
      document.getElementById('panel-' + tab.getAttribute('data-tab')).hidden = !selected;
    });
    if (history.replaceState) history.replaceState(null, '', '#' + id);
  }
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () { selectTab(tab.getAttribute('data-tab')); });
  });
  var requested = location.hash.slice(1);
  if (document.getElementById('panel-' + requested)) selectTab(requested);
}());
"""
    return _document("Settings - Otaku Prime", body, script)
