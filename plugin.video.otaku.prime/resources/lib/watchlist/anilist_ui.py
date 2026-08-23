# -*- coding: utf-8 -*-
"""Small HTML view for direct AniList PIN authentication."""

from __future__ import annotations

import html
from typing import Optional


def render_anilist_auth(*, authorize_url: str, connected_account: Optional[dict], message: str = "") -> str:
    notice = f'<p class="notice">{html.escape(message)}</p>' if message else ""
    if connected_account:
        account_html = f"""
        <div class="status ok">Connected as <strong>{html.escape(connected_account['external_username'])}</strong></div>
        <form method="post" action="/watchlist/anilist/disconnect">
          <button type="submit" class="button secondary">Disconnect AniList</button>
        </form>
        """
    else:
        account_html = """
        <div class="status">Not connected</div>
        <div class="auth-grid">
          <div class="qr-card"><img src="/watchlist/anilist/qr.svg" alt="AniList authorization QR code"></div>
          <div>
            <p>1. Scan the QR code or open AniList.</p>
            <p>2. Sign in and approve Otaku Prime.</p>
            <p>3. AniList will show your access token on its official PIN page.</p>
            <p>4. Paste that token below.</p>
            <p><a class="button" href="{authorize_url}" target="_blank" rel="noopener noreferrer">Open AniList Authorization</a></p>
          </div>
        </div>
        <form method="post" action="/watchlist/anilist/connect">
          <label>Access token
            <textarea name="token" rows="4" autocomplete="off" spellcheck="false" required></textarea>
          </label>
          <button type="submit" class="button">Connect AniList</button>
        </form>
        """.format(authorize_url=html.escape(authorize_url, quote=True))

    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AniList - Otaku Prime</title>
<style>
:root {{ color-scheme: dark; --bg:#090b12; --card:#161b29; --border:#2b3348; --text:#f5f7ff; --muted:#9ba6bd; --accent:#6858e8; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; min-height:100vh; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,sans-serif; }}
main {{ width:min(860px,calc(100% - 32px)); margin:40px auto; }}
.card {{ padding:24px; border:1px solid var(--border); border-radius:16px; background:var(--card); }}
h1 {{ margin-top:0; }}
.muted {{ color:var(--muted); }}
.auth-grid {{ display:grid; grid-template-columns:220px 1fr; gap:24px; align-items:center; margin:22px 0; }}
.qr-card {{ background:white; padding:12px; border-radius:12px; }}
.qr-card img {{ display:block; width:100%; height:auto; }}
.button {{ display:inline-block; border:1px solid #7869f0; border-radius:9px; padding:10px 14px; background:var(--accent); color:white; text-decoration:none; cursor:pointer; }}
.button.secondary {{ background:transparent; border-color:var(--border); }}
label {{ display:grid; gap:8px; }}
textarea {{ width:100%; padding:10px; border:1px solid var(--border); border-radius:9px; background:#0d111c; color:var(--text); margin-bottom:12px; }}
.notice,.status {{ margin:12px 0; padding:10px 12px; border-radius:9px; background:#27213e; }}
.status.ok {{ background:#173527; }}
.back {{ margin-top:18px; }}
@media(max-width:650px) {{ .auth-grid {{ grid-template-columns:1fr; }} .qr-card {{ width:min(260px,100%); margin:auto; }} }}
</style>
</head>
<body>
<main>
<section class="card">
<h1>AniList</h1>
<p class="muted">Direct AniList authentication using the OAuth implicit grant and AniList's official PIN page. Otaku Prime never uses or distributes a client secret.</p>
{notice}
{account_html}
<p class="back"><a href="/#watchlist">← Back to Watchlist settings</a></p>
</section>
</main>
</body>
</html>""".format(notice=notice, account_html=account_html)
