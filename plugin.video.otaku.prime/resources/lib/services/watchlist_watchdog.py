# -*- coding: utf-8 -*-
"""Single-authority watchlist manager and synchronization watchdog."""
from __future__ import annotations

import datetime
import json
import threading
import time

from resources.lib.database.watchlist_items import (
    STATUSES,
    SUPPORTED_WATCHLIST_PROVIDERS,
    WatchlistItemStore,
)
from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)
PROVIDER_TIE_PRIORITY = {"anilist": 4, "mal": 3, "kitsu": 2, "simkl": 1}
WATCHLIST_ADDED = "WATCHLIST_ADDED"
WATCHLIST_UPDATED = "WATCHLIST_UPDATED"
WATCHLIST_REMOVED = "WATCHLIST_REMOVED"
EVENT_COMPARE_FIELDS = (
    "status",
    "progress",
    "anilist_id",
    "mal_id",
    "kitsu_id",
    "simkl_id",
    "english_name",
    "romaji_name",
    "native_name",
    "episode_count",
    "media_format",
    "release_date",
)


def timestamp_epoch(value):
    """Normalize provider Unix/ISO timestamps to UTC epoch seconds."""
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 100000000000:
            number /= 1000.0
        return max(0, int(number))
    text = str(value).strip()
    if not text:
        return 0
    try:
        number = float(text)
        if number > 100000000000:
            number /= 1000.0
        return max(0, int(number))
    except ValueError:
        pass
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return max(0, int(parsed.timestamp()))
    except ValueError:
        return 0


def epoch_iso(epoch):
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(
        int(epoch), tz=datetime.timezone.utc
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def changed_fields(previous, current):
    previous = previous or {}
    current = current or {}
    return [name for name in EVENT_COMPARE_FIELDS if previous.get(name) != current.get(name)]


class WatchlistWatchdogStore(WatchlistItemStore):
    """Persistence used only by the watchlist manager and its provider workers."""

    def initialize(self):
        super().initialize()
        with self._connection() as db:
            item_columns = {row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")}
            for column, declaration in (
                ("master_updated_at", "TEXT"),
                ("master_updated_epoch", "INTEGER NOT NULL DEFAULT 0"),
                ("master_updated_source", "TEXT"),
                ("watchdog_checked_epoch", "INTEGER NOT NULL DEFAULT 0"),
                ("watchdog_last_reconciled_at", "TEXT"),
            ):
                if column not in item_columns:
                    db.execute(
                        "ALTER TABLE watchlist_items ADD COLUMN {} {}".format(column, declaration)
                    )
            provider_columns = {
                row[1] for row in db.execute("PRAGMA table_info(watchlist_provider_entries)")
            }
            if "provider_updated_epoch" not in provider_columns:
                db.execute(
                    "ALTER TABLE watchlist_provider_entries ADD COLUMN "
                    "provider_updated_epoch INTEGER NOT NULL DEFAULT 0"
                )
            db.execute("""CREATE TABLE IF NOT EXISTS watchlist_watchdog_state(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              last_boot_sync_at TEXT,
              last_remote_sync_at TEXT,
              last_local_change_at TEXT,
              remote_interval_seconds INTEGER NOT NULL DEFAULT 3600,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            db.execute("""INSERT OR IGNORE INTO watchlist_watchdog_state(
              singleton,remote_interval_seconds) VALUES(1,3600)""")
            rows = db.execute(
                "SELECT provider,provider_item_id,provider_updated_at,provider_updated_epoch "
                "FROM watchlist_provider_entries"
            ).fetchall()
            for row in rows:
                if int(row["provider_updated_epoch"] or 0) > 0:
                    continue
                epoch = timestamp_epoch(row["provider_updated_at"])
                if epoch:
                    db.execute(
                        "UPDATE watchlist_provider_entries SET provider_updated_epoch=? "
                        "WHERE provider=? AND provider_item_id=?",
                        (epoch, row["provider"], row["provider_item_id"]),
                    )

    def _upsert_snapshot_row(self, db, provider, entry):
        local_id = super()._upsert_snapshot_row(db, provider, entry)
        updated_at = entry.get("provider_updated_at")
        db.execute(
            "UPDATE watchlist_provider_entries SET provider_updated_epoch=? "
            "WHERE provider=? AND provider_item_id=?",
            (timestamp_epoch(updated_at), provider, str(entry["provider_item_id"])),
        )
        return local_id

    def finalize_merge(self):
        """Initialize Prime state from the newest provider update, never max progress."""
        initialized = conflicts = 0
        with self._connection() as db:
            items = db.execute(
                "SELECT local_id,master_initialized,status,progress FROM watchlist_items"
            ).fetchall()
            for item in items:
                states = db.execute("""SELECT provider,status,progress,provider_updated_at,
                  provider_updated_epoch FROM watchlist_provider_entries WHERE local_id=?""",
                  (item["local_id"],),
                ).fetchall()
                conflict = len({(row["status"], int(row["progress"])) for row in states}) > 1
                if not item["master_initialized"] and states:
                    chosen = max(
                        states,
                        key=lambda row: (
                            int(row["provider_updated_epoch"] or 0),
                            PROVIDER_TIE_PRIORITY.get(row["provider"], 0),
                            int(row["progress"]),
                        ),
                    )
                    db.execute("""UPDATE watchlist_items SET status=?,progress=?,
                      master_initialized=1,has_conflict=?,master_updated_at=?,
                      master_updated_epoch=?,master_updated_source=?,updated_at=CURRENT_TIMESTAMP
                      WHERE local_id=?""", (
                        chosen["status"], int(chosen["progress"]), int(conflict),
                        chosen["provider_updated_at"], int(chosen["provider_updated_epoch"] or 0),
                        chosen["provider"], item["local_id"],
                    ))
                    initialized += 1
                else:
                    master = (item["status"], int(item["progress"]))
                    conflict = any(
                        (row["status"], int(row["progress"])) != master for row in states
                    )
                    db.execute(
                        "UPDATE watchlist_items SET has_conflict=? WHERE local_id=?",
                        (int(conflict), item["local_id"]),
                    )
                conflicts += int(conflict)
        return {"initialized": initialized, "conflicts": conflicts, "items": len(items)}

    def set_master_state(self, local_id, status, progress):
        raise RuntimeError(
            "Watchlist state is managed by WatchlistWatchdogService.update_item()"
        )

    def write_master_state(self, local_id, status, progress, source="prime",
                           updated_at=None, updated_epoch=None):
        """Internal write primitive. Call through WatchlistWatchdogService."""
        if status not in STATUSES:
            raise ValueError("unsupported watchlist status")
        epoch = int(updated_epoch or time.time())
        timestamp = updated_at or epoch_iso(epoch)
        with self._connection() as db:
            previous_row = db.execute(
                "SELECT * FROM watchlist_items WHERE local_id=?", (local_id,)
            ).fetchone()
            if not previous_row:
                raise KeyError("watchlist item not found")
            db.execute("""UPDATE watchlist_items SET status=?,progress=?,
              master_initialized=1,master_updated_at=?,master_updated_epoch=?,
              master_updated_source=?,updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (status, max(0, int(progress)), timestamp, epoch, str(source), local_id))
            if source == "prime":
                db.execute("""UPDATE watchlist_watchdog_state SET last_local_change_at=?,
                  updated_at=CURRENT_TIMESTAMP WHERE singleton=1""", (timestamp,))
            current_row = db.execute(
                "SELECT * FROM watchlist_items WHERE local_id=?", (local_id,)
            ).fetchone()
            return dict(previous_row), dict(current_row)

    def item(self, local_id):
        with self._connection() as db:
            row = db.execute("SELECT * FROM watchlist_items WHERE local_id=?", (local_id,)).fetchone()
            return dict(row) if row else None

    def provider_entries(self, local_id):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM watchlist_provider_entries
              WHERE local_id=? ORDER BY provider""", (local_id,))]

    def provider_entry(self, local_id, provider):
        with self._connection() as db:
            row = db.execute("""SELECT * FROM watchlist_provider_entries
              WHERE local_id=? AND provider=?""", (local_id, provider)).fetchone()
            return dict(row) if row else None

    def dirty_master_items(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM watchlist_items
              WHERE master_initialized=1 AND master_updated_epoch>watchdog_checked_epoch
              ORDER BY master_updated_epoch,local_id""")]

    def mark_watchdog_checked(self, local_id, epoch):
        with self._connection() as db:
            db.execute("""UPDATE watchlist_items SET watchdog_checked_epoch=?,
              watchdog_last_reconciled_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (int(epoch or 0), local_id))

    def mark_provider_synced(self, provider, item, result):
        provider_id = item.get(provider + "_id")
        if provider_id in (None, ""):
            return
        updated_at = result.get("updated_at") or epoch_iso(int(time.time()))
        epoch = timestamp_epoch(updated_at) or int(time.time())
        raw = {"watchdog_optimistic": True}
        if result.get("provider_entry_id"):
            raw["library_entry"] = {"id": str(result["provider_entry_id"])}
        with self._connection() as db:
            existing = db.execute("""SELECT raw_json FROM watchlist_provider_entries
              WHERE provider=? AND provider_item_id=?""",
              (provider, str(provider_id)),
            ).fetchone()
            raw_json = existing["raw_json"] if existing else json.dumps(raw, separators=(",", ":"))
            db.execute("""INSERT INTO watchlist_provider_entries(
              provider,provider_item_id,local_id,provider_status,status,progress,
              episode_count,media_format,release_date,is_adult,provider_updated_at,
              provider_updated_epoch,raw_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(provider,provider_item_id) DO UPDATE SET
                local_id=excluded.local_id,provider_status=excluded.provider_status,
                status=excluded.status,progress=excluded.progress,
                provider_updated_at=excluded.provider_updated_at,
                provider_updated_epoch=excluded.provider_updated_epoch,
                raw_json=CASE WHEN watchlist_provider_entries.raw_json='{}'
                              THEN excluded.raw_json ELSE watchlist_provider_entries.raw_json END,
                fetched_at=CURRENT_TIMESTAMP""", (
                provider, str(provider_id), item["local_id"], item["status"], item["status"],
                int(item.get("progress") or 0), item.get("episode_count"), item.get("media_format"),
                item.get("release_date"), int(bool(item.get("is_adult"))), str(updated_at), epoch,
                raw_json,
            ))

    def record_watchdog_sync(self, boot=False, interval_seconds=3600):
        now = epoch_iso(int(time.time()))
        with self._connection() as db:
            if boot:
                db.execute("""UPDATE watchlist_watchdog_state SET last_boot_sync_at=?,
                  last_remote_sync_at=?,remote_interval_seconds=?,updated_at=CURRENT_TIMESTAMP
                  WHERE singleton=1""", (now, now, int(interval_seconds)))
            else:
                db.execute("""UPDATE watchlist_watchdog_state SET last_remote_sync_at=?,
                  remote_interval_seconds=?,updated_at=CURRENT_TIMESTAMP WHERE singleton=1""",
                  (now, int(interval_seconds)))


class WatchlistWatchdogService:
    """Single public manager for Prime and provider watchlist state."""

    def __init__(self, importers, store, provider_writer, identity_enricher=None,
                 mediator=None, remote_interval_seconds=3600, local_poll_seconds=1.0,
                 error_handler=None):
        self.importers = list(importers)
        self.store = store
        self.provider_writer = provider_writer
        self.identity_enricher = identity_enricher
        self.mediator = mediator
        self.remote_interval_seconds = max(300, int(remote_interval_seconds))
        self.local_poll_seconds = max(0.25, float(local_poll_seconds))
        self.error_handler = error_handler or (lambda exc: None)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._remote_requested = threading.Event()
        self._run_lock = threading.Lock()
        self._thread = None
        self._last_remote_monotonic = 0.0
        self._retry_after = {}
        self._subscribers = []
        self._subscriber_lock = threading.Lock()
        self._connected_account_signature = None
        self._last_account_check_monotonic = 0.0

    # Public manager API -------------------------------------------------
    def list_items(self):
        return self.store.list_all()

    def get_item(self, local_id):
        return self.store.item(str(local_id))

    def subscribe(self, callback):
        """Subscribe to canonical ADDED/UPDATED/REMOVED events."""
        if not callable(callback):
            raise TypeError("watchlist subscriber must be callable")
        with self._subscriber_lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
        return callback

    def unsubscribe(self, callback):
        with self._subscriber_lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def update_item(self, local_id, status=None, progress=None, source="prime"):
        """Only supported program entry point for changing canonical watchlist state."""
        local_id = str(local_id)
        current = self.store.item(local_id)
        if not current:
            raise KeyError("watchlist item not found")
        next_status = status if status is not None else current.get("status")
        next_progress = int(progress if progress is not None else current.get("progress") or 0)
        if next_status not in STATUSES:
            raise ValueError("unsupported watchlist status")
        if current.get("status") == next_status and int(current.get("progress") or 0) == next_progress:
            return {"changed": False, "item": current}
        previous, updated = self.store.write_master_state(
            local_id, next_status, next_progress, source="prime"
        )
        self.local_changed()
        self._emit(
            WATCHLIST_UPDATED,
            updated,
            source=source,
            previous=previous,
            fields=changed_fields(previous, updated),
        )
        return {"changed": True, "item": updated}

    def request_remote_sync(self, *args, **kwargs):
        """Wake immediately after provider connect/disconnect or explicit refresh."""
        reason = args[0] if args else kwargs.get("provider") or kwargs.get("reason") or "explicit"
        LOGGER.info("Watchlist remote sync requested: reason=%s", reason)
        self._remote_requested.set()
        self._wake.set()
        return {"scheduled": True}

    # Lifecycle ----------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return {"scheduled": False, "busy": True}
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(
            target=self._run, name="OtakuPrimeWatchlistWatchdog", daemon=True
        )
        self._thread.start()
        return {"scheduled": True, "busy": False}

    def stop(self, timeout=35):
        self._stop.set()
        self._wake.set()
        # Retire the mediator before waiting for provider/identity workers.  An
        # enrichment progress callback may otherwise restart it during shutdown.
        if self.mediator:
            requester = getattr(self.mediator, "request_stop", None)
            if requester:
                requester()
        if self.identity_enricher:
            self.identity_enricher.stop(timeout=timeout)
        if self._thread:
            self._thread.join(timeout=timeout)
        if self.mediator:
            self.mediator.stop(timeout=timeout)

    def local_changed(self, local_id=None):
        """Wake after a Prime write or identity-enrichment pass."""
        self._wake.set()
        return {"scheduled": True}

    def identity_complete(self):
        self.local_changed()
        if self.mediator:
            self.mediator.start()

    # Event output -------------------------------------------------------
    def _emit(self, event_type, item, source, previous=None, fields=None):
        item_copy = dict(item) if item else None
        event = {
            "type": str(event_type),
            "local_id": (item_copy or previous or {}).get("local_id"),
            "source": str(source or "watchdog"),
            "item": item_copy,
            "previous": dict(previous) if previous else None,
            "changed_fields": list(fields or []),
            "emitted_at": epoch_iso(int(time.time())),
        }
        with self._subscriber_lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                LOGGER.exception("Watchlist event subscriber failed for %s", event_type)
        return event

    def _emit_remote_diff(self, before, after):
        before_ids = set(before)
        after_ids = set(after)
        for local_id in sorted(after_ids - before_ids):
            item = after[local_id]
            self._emit(
                WATCHLIST_ADDED,
                item,
                source=item.get("master_updated_source") or "remote",
                fields=list(EVENT_COMPARE_FIELDS),
            )
        for local_id in sorted(before_ids & after_ids):
            previous = before[local_id]
            item = after[local_id]
            fields = changed_fields(previous, item)
            if fields:
                self._emit(
                    WATCHLIST_UPDATED,
                    item,
                    source=item.get("master_updated_source") or "remote",
                    previous=previous,
                    fields=fields,
                )
        for local_id in sorted(before_ids - after_ids):
            previous = before[local_id]
            self._emit(
                WATCHLIST_REMOVED,
                None,
                source="remote",
                previous=previous,
                fields=list(EVENT_COMPARE_FIELDS),
            )

    # Worker -------------------------------------------------------------
    def _account_signature(self):
        """Non-secret account revision used to notice connects made by another service."""
        signature = []
        seen = set()
        for importer in self.importers:
            provider = getattr(importer, "provider", "anilist")
            if provider in seen:
                continue
            seen.add(provider)
            accounts = getattr(importer, "accounts", None)
            getter = getattr(accounts, "get", None)
            user_id = getattr(importer, "user_id", 1)
            account = getter(user_id, provider) if getter else None
            signature.append((provider, (account or {}).get("updated_at")))
        return tuple(sorted(signature))

    def _detect_account_change(self):
        now = time.monotonic()
        if now - self._last_account_check_monotonic < 5.0:
            return False
        self._last_account_check_monotonic = now
        signature = self._account_signature()
        if self._connected_account_signature is None:
            self._connected_account_signature = signature
            return False
        if signature == self._connected_account_signature:
            return False
        self._connected_account_signature = signature
        LOGGER.info("Watchlist provider account connection changed; scheduling remote sync")
        self._remote_requested.set()
        return True

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
                    boot = False
                self._process_local_changes()
                self._wake.wait(self.local_poll_seconds)
                self._wake.clear()
            except Exception as exc:
                LOGGER.exception("Watchlist watchdog cycle failed; retrying without stopping the service")
                self.error_handler(exc)
                self._wake.wait(min(5.0, max(1.0, self.local_poll_seconds)))
                self._wake.clear()

    def _refresh_remote(self, boot=False):
        if not self._run_lock.acquire(blocking=False):
            return {"busy": True}
        try:
            before_rows = {row["local_id"]: row for row in self.store.list_all()}
            results = []
            for importer in self.importers:
                if self._stop.is_set():
                    break
                try:
                    if boot and getattr(importer, "provider", None) == "simkl":
                        clear = getattr(importer, "_clear_state", None)
                        if clear:
                            clear()
                    LOGGER.info(
                        "Watchdog refreshing remote provider %s",
                        getattr(importer, "provider", importer.__class__.__name__),
                    )
                    result = importer.sync()
                    results.append(result)
                    LOGGER.info(
                        "Watchdog provider refresh complete: provider=%s connected=%s imported=%s mode=%s",
                        getattr(importer, "provider", importer.__class__.__name__),
                        result.get("connected"),
                        result.get("imported", result.get("watchlist_rows", 0)),
                        result.get("mode") or "full",
                    )
                except Exception as exc:
                    LOGGER.exception(
                        "Watchdog remote provider refresh failed: provider=%s",
                        getattr(importer, "provider", importer.__class__.__name__),
                    )
                    self.error_handler(exc)
                    results.append({"error": str(exc)})
            merge = self.store.finalize_merge()
            self._reconcile_remote_rows(self.store.list_all())
            final_rows = {row["local_id"]: row for row in self.store.list_all()}
            new_ids = sorted(set(final_rows) - set(before_rows))
            self._emit_remote_diff(before_rows, final_rows)
            self.store.record_watchdog_sync(
                boot=boot, interval_seconds=self.remote_interval_seconds
            )
            self._last_remote_monotonic = time.monotonic()
            LOGGER.info(
                "Watchlist watchdog remote sync complete: items=%s new=%s conflicts=%s",
                len(final_rows), len(new_ids), merge.get("conflicts", 0),
            )
            if new_ids:
                LOGGER.info("Watchlist watchdog discovered %s new Prime items", len(new_ids))
            if self.identity_enricher:
                self.identity_enricher.start()
            elif new_ids and self.mediator:
                self.mediator.start()
            return {"providers": results, "merge": merge, "new": new_ids}
        finally:
            self._run_lock.release()

    def _reconcile_remote_rows(self, items):
        for item in items:
            local_id = item["local_id"]
            providers = self.store.provider_entries(local_id)
            if not providers:
                continue
            newest = max(
                providers,
                key=lambda row: (
                    int(row.get("provider_updated_epoch") or 0),
                    PROVIDER_TIE_PRIORITY.get(row.get("provider"), 0),
                ),
            )
            provider_epoch = int(newest.get("provider_updated_epoch") or 0)
            master_epoch = int(item.get("master_updated_epoch") or 0)
            old_state = (item.get("status"), int(item.get("progress") or 0))
            if provider_epoch > master_epoch:
                _, item = self.store.write_master_state(
                    local_id,
                    newest["status"],
                    int(newest["progress"]),
                    source=newest["provider"],
                    updated_at=newest.get("provider_updated_at"),
                    updated_epoch=provider_epoch,
                )
                new_state = (item.get("status"), int(item.get("progress") or 0))
                if new_state != old_state:
                    LOGGER.info(
                        "Watchdog accepted newer %s state for %s: %s %s",
                        newest["provider"], local_id, item["status"], item["progress"],
                    )
            self._sync_master_to_providers(item)

    def _process_local_changes(self):
        now = time.time()
        for item in self.store.dirty_master_items():
            local_id = item["local_id"]
            if self._retry_after.get(local_id, 0) > now:
                continue
            self._sync_master_to_providers(item)

    def _sync_master_to_providers(self, item):
        if not item or not item.get("master_initialized"):
            return
        master_epoch = int(item.get("master_updated_epoch") or 0)
        all_ok = True
        for provider in SUPPORTED_WATCHLIST_PROVIDERS:
            provider_id = item.get(provider + "_id")
            if provider_id in (None, ""):
                continue
            entry = self.store.provider_entry(item["local_id"], provider)
            if entry and (
                entry["status"] == item["status"]
                and int(entry["progress"]) == int(item.get("progress") or 0)
            ):
                continue
            try:
                result = self.provider_writer.push(provider, item, entry)
                if result.get("skipped"):
                    continue
                self.store.mark_provider_synced(provider, item, result)
                LOGGER.info(
                    "Watchdog mirrored Prime item %s to %s",
                    item["local_id"], provider,
                )
            except Exception as exc:
                all_ok = False
                LOGGER.exception(
                    "Watchdog failed to mirror Prime item %s to %s",
                    item["local_id"], provider,
                )
                self.error_handler(exc)
        if all_ok:
            self.store.mark_watchdog_checked(item["local_id"], master_epoch)
            self._retry_after.pop(item["local_id"], None)
        else:
            self._retry_after[item["local_id"]] = time.time() + 60
