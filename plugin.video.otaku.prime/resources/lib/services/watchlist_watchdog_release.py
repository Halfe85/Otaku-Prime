# -*- coding: utf-8 -*-
"""Release-aware runtime for the single-authority watchlist watchdog."""
from __future__ import annotations

import time

from resources.lib.logging_config import get_logger
from resources.lib.services.watchlist_release import (
    WATCHLIST_RELEASE_UPDATED,
    WatchlistReleaseManager,
)
from resources.lib.services.watchlist_watchdog import WatchlistWatchdogService


LOGGER = get_logger(__name__)


class ReleaseAwareWatchlistWatchdogService(WatchlistWatchdogService):
    """Watchlist watchdog with persistent season/next-episode scheduling."""

    def __init__(self, *args, release_manager=None, release_poll_seconds=30.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.release_manager = release_manager or WatchlistReleaseManager(self.store)
        self.release_manager.initialize()
        self.release_poll_seconds = max(1.0, float(release_poll_seconds))
        self._last_release_monotonic = 0.0
        self._release_refresh_retry = {}

    def identity_progress(self,progress=None):
        """Wake the mediator whenever another ten-percent Watchdog batch is ready."""
        if self.mediator:
            self.mediator.start()
        self._last_release_monotonic = 0.0
        self._wake.set()
        if progress:
            LOGGER.info("Watchdog released mediator batch: %s/%s (%s%%)",
                        progress.get("processed"),progress.get("total"),progress.get("percent"))
        return progress or {"scheduled":True}

    def identity_complete(self):
        result = super().identity_complete()
        # The mediator runs asynchronously after identity completion. Resetting
        # this timer makes the watchdog pick up its catalogue writes promptly.
        self._last_release_monotonic = 0.0
        self._wake.set()
        return result

    def _refresh_released_items(self, now_epoch):
        due_getter = getattr(self.release_manager, "due_release_ids", None)
        refresher = getattr(self.mediator, "refresh_item", None) if self.mediator else None
        if not due_getter or not refresher:
            return 0
        candidates = set(due_getter(now_epoch=now_epoch))
        candidates.update(
            local_id for local_id, retry_at in self._release_refresh_retry.items()
            if int(retry_at) <= int(now_epoch)
        )
        refreshed = 0
        for local_id in sorted(candidates):
            try:
                result = refresher(local_id) or {}
                if result.get("busy"):
                    self._release_refresh_retry[local_id] = int(now_epoch) + 30
                    LOGGER.info(
                        "Mediator busy while refreshing released Prime item %s; retrying",
                        local_id,
                    )
                    continue
                if result.get("refreshed"):
                    refreshed += 1
                    self._release_refresh_retry.pop(local_id, None)
                else:
                    self._release_refresh_retry[local_id] = int(now_epoch) + 60
            except Exception as exc:
                self._release_refresh_retry[local_id] = int(now_epoch) + 60
                LOGGER.exception(
                    "Mediator release refresh failed for Prime item %s", local_id
                )
                self.error_handler(exc)
        return refreshed

    def _process_release_schedules(self, force=False):
        now_epoch = int(time.time())
        self._refresh_released_items(now_epoch)
        try:
            events = self.release_manager.refresh_due(
                now_epoch=now_epoch, force=force
            )
        except Exception as exc:
            LOGGER.exception("Watchlist release schedule refresh failed")
            self.error_handler(exc)
            return 0
        for event in events:
            previous = event["previous"]
            current = event["item"]
            released_episode = previous.get("next_episode_number")
            if (
                released_episode is not None
                and previous.get("next_episode_release_epoch")
                and int(previous["next_episode_release_epoch"]) <= now_epoch
                and released_episode != current.get("next_episode_number")
            ):
                LOGGER.info(
                    "Watchdog release rollover for %s: episode %s released; next=%s date=%s",
                    current["local_id"],
                    released_episode,
                    current.get("next_episode_number"),
                    current.get("next_episode_release_date"),
                )
            self._emit(
                WATCHLIST_RELEASE_UPDATED,
                current,
                source="release-watchdog",
                previous=previous,
                fields=event["changed_fields"],
            )
        self._last_release_monotonic = time.monotonic()
        return len(events)

    def _run(self):
        boot = True
        while not self._stop.is_set():
            try:
                self._detect_account_change()
                now = time.monotonic()
                remote_due = boot or (
                    self._remote_requested.is_set()
                    or now - self._last_remote_monotonic >= self.remote_interval_seconds
                )
                if remote_due:
                    self._remote_requested.clear()
                    self._refresh_remote(boot=boot)
                    self._process_release_schedules(force=boot)
                    boot = False
                self._process_local_changes()
                if now - self._last_release_monotonic >= self.release_poll_seconds:
                    self._process_release_schedules()
                wait_seconds = min(self.local_poll_seconds, self.release_poll_seconds)
                self._wake.wait(wait_seconds)
                self._wake.clear()
            except Exception as exc:
                LOGGER.exception("Watchlist watchdog cycle failed; retrying without stopping the service")
                self.error_handler(exc)
                self._wake.wait(min(5.0, max(1.0, self.local_poll_seconds)))
                self._wake.clear()
