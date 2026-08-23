# -*- coding: utf-8 -*-
"""Kodi plugin entry point for Otaku Prime."""

import sys

import xbmcgui
import xbmcplugin


def main() -> None:
    handle = int(sys.argv[1])
    item = xbmcgui.ListItem(label="Otaku Prime development foundation")
    item.setProperty("IsPlayable", "false")
    xbmcplugin.addDirectoryItem(handle, "", item, isFolder=False)
    xbmcplugin.endOfDirectory(handle)


if __name__ == "__main__":
    main()
