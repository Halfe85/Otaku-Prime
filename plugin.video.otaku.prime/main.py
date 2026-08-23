# -*- coding: utf-8 -*-
"""Main Kodi plugin entry point for Otaku Prime."""

from __future__ import annotations

import sys

import xbmcgui
import xbmcplugin


def main() -> None:
    """Start the Otaku Prime plugin."""
    handle = int(sys.argv[1])

    item = xbmcgui.ListItem(label="Otaku Prime development foundation")
    item.setProperty("IsPlayable", "false")

    xbmcplugin.addDirectoryItem(
        handle=handle,
        url="",
        listitem=item,
        isFolder=False,
    )
    xbmcplugin.endOfDirectory(handle)


if __name__ == "__main__":
    main()
