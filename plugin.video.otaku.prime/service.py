# -*- coding: utf-8 -*-
"""Background service entry point for Otaku Prime."""

import xbmc


class PrimeMonitor(xbmc.Monitor):
    pass


def main() -> None:
    xbmc.log("OTAKU PRIME: service started", xbmc.LOGINFO)
    PrimeMonitor().waitForAbort()
    xbmc.log("OTAKU PRIME: service stopped", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
