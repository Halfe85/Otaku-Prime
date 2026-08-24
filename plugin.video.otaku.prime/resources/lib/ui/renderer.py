# -*- coding: utf-8 -*-
"""Render the file-backed Otaku Prime web interface."""

from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple


HTML_ROOT = Path(__file__).resolve().parent / "html"
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


@lru_cache(maxsize=32)
def _template(relative_path: str) -> str:
    return (HTML_ROOT / relative_path).read_text(encoding="utf-8")


def _fill(template: str, **values: str) -> str:
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", value)
    return template


def _document(title: str, content: str, body_class: str, component: str) -> str:
    base = "/ui/components/{0}/{0}".format(component)
    return _fill(
        _template("index.html"),
        TITLE=html.escape(title),
        BODY_CLASS=html.escape(body_class),
        CONTENT=content,
        COMPONENT_STYLES='<link rel="stylesheet" href="{}.css">'.format(base),
        COMPONENT_SCRIPTS='<script src="{}.js" defer></script>'.format(base),
    )


def render_login(message: str = "") -> str:
    notice = '<p class="notice warning">{}</p>'.format(html.escape(message)) if message else ""
    content = _fill(_template("components/login-modal/login-modal.html"), NOTICE=notice)
    return _document("Sign in - Otaku Prime", content, "auth-page", "login-modal")


def render_new_password(user: dict, message: str = "") -> str:
    notice = '<p class="notice warning">{}</p>'.format(html.escape(message)) if message else ""
    content = _fill(_template("components/new-password-modal/new-password-modal.html"), USERNAME=html.escape(user["username"]), NOTICE=notice)
    return _document("New password - Otaku Prime", content, "auth-page", "new-password-modal")


def render_anilist_auth(*, authorize_url: str, connected_account: Optional[dict], message: str = "") -> str:
    notice = '<p class="notice">{}</p>'.format(html.escape(message)) if message else ""
    if connected_account:
        account = _fill(
            _template("components/anilist-auth/connected.html"),
            EXTERNAL_USERNAME=html.escape(connected_account["external_username"]),
        )
    else:
        account = _fill(
            _template("components/anilist-auth/connect.html"),
            AUTHORIZE_URL=html.escape(authorize_url, quote=True),
        )
    content = _fill(_template("components/anilist-auth/anilist-auth.html"), NOTICE=notice, ACCOUNT_CONTENT=account)
    return _document("AniList - Otaku Prime", content, "anilist-page", "anilist-auth")


def _preview_card(category_id: str) -> str:
    previews = {
        "general": ("Interface defaults", "Title language", "English", "Preferred artwork", "Kodi default"),
        "providers": ("Provider selection", "Torrent providers", "Not configured", "Streaming providers", "Not configured"),
        "scraping": ("Source discovery", "Scraping timeout", "30 seconds", "Try next source", "Enabled"),
        "playback": ("Playback defaults", "Audio language", "Japanese", "Subtitle language", "English"),
        "sort-filter": ("Result filtering", "Maximum resolution", "1080p", "Source ordering", "Quality first"),
        "menu": ("Kodi menu visibility", "Watchlist menu", "Planned", "Search menu", "Planned"),
        "context": ("Kodi context actions", "Rescrape", "Planned", "Recommendations", "Planned"),
        "maintenance": ("Maintenance tools", "Clear cache", "Unavailable", "Export settings", "Unavailable"),
    }
    title, label_one, value_one, label_two, value_two = previews[category_id]
    return _fill(_template("components/main-container/preview-card.html"), CARD_TITLE=html.escape(title), LABEL_ONE=html.escape(label_one), VALUE_ONE=html.escape(value_one), LABEL_TWO=html.escape(label_two), VALUE_TWO=html.escape(value_two))


def _accounts_content(user: dict, message: str) -> str:
    notice = '<p class="notice">{}</p>'.format(html.escape(message)) if message else ""
    return _fill(_template("components/main-container/accounts.html"), USERNAME=html.escape(user["username"]), ROLE=html.escape(user["role"]), NOTICE=notice)


def _watchlist_content() -> str:
    return _template("components/main-container/watchlist.html")


def render_home(user: dict, message: str = "", active_tab: str = "general") -> str:
    if active_tab not in {item[0] for item in SETTINGS_CATEGORIES}:
        active_tab = "general"
    tabs, panels = [], []
    for category_id, label, description in SETTINGS_CATEGORIES:
        selected = category_id == active_tab
        tabs.append('<button class="tab{}" type="button" role="tab" aria-selected="{}" aria-controls="panel-{}" data-tab="{}">{}</button>'.format(" active" if selected else "", "true" if selected else "false", category_id, category_id, html.escape(label)))
        if category_id == "accounts":
            panel_content = _accounts_content(user, message)
        elif category_id == "watchlist":
            panel_content = _watchlist_content()
        else:
            panel_content = _preview_card(category_id)
        panels.append('<section class="panel" id="panel-{}" role="tabpanel"{}><header class="panel-header"><h2>{}</h2><p>{}</p></header>{}</section>'.format(category_id, "" if selected else " hidden", html.escape(label), html.escape(description), panel_content))
    content = _fill(_template("components/main-container/main-container.html"), USERNAME=html.escape(user["username"]), TABS="".join(tabs), PANELS="".join(panels))
    return _document("Settings - Otaku Prime", content, "settings-page", "main-container")


def read_static_asset(relative_path: str) -> Optional[Tuple[str, bytes]]:
    """Return an allow-listed CSS or JavaScript file below the UI root."""
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or path.suffix not in (".css", ".js"):
        return None
    candidate = (HTML_ROOT / Path(*path.parts)).resolve()
    try:
        candidate.relative_to(HTML_ROOT.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    content_type = "text/css; charset=utf-8" if path.suffix == ".css" else "text/javascript; charset=utf-8"
    return content_type, candidate.read_bytes()
