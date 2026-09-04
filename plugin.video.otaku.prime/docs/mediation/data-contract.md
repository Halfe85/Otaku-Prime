# Mediation data and audit contract

This document describes the target database responsibilities needed by the
logic-gate pipeline. It is a design contract, not a statement that all columns
and tables already exist.

## Canonical watchlist

`watchlist_items` remains the Prime-owned union of connected providers.

Required identity fields:

```text
local_id
anilist_id
mal_id
kitsu_id
simkl_id
```

The row owns user state such as status and progress. It does not own Kodi
season numbering. Raw provider payloads remain in `watchlist_provider_entries`
so every merge can be audited or recomputed.

## TV-show franchise

`tv_series` must store the validated root identity, not only the provider used
by the successful mediator path.

Required identity fields:

```text
local_id             six hexadecimal characters
root_anilist_id       nullable, unique when non-null
root_mal_id           nullable, unique when non-null
root_kitsu_id         nullable, unique when non-null
root_simkl_id         nullable, unique when non-null
tvdb_id               nullable, unique when non-null
```

All non-null values must describe the same franchise root. `source_provider`
records provenance; it must not control which identity columns are preserved.

An update may fill a null identity. It may not replace a different non-null
identity without an explicit audited repair operation.

## Seasons and episodes

`seasons` describes a Kodi season coordinate inside one Prime TV-show.
`season_watchlist_links` is the authoritative many-to-many connection between
a Kodi season and the watchlist items contributing episodes to it.

`episodes.watchlist_local_id` identifies the exact originating watchlist item.
Specials must also retain their provider IDs directly on the episode because
one Season 0 can contain several independent watchlist items.

Hierarchical Prime IDs retain the existing contract:

```text
series  = xxxxxx
season  = xxxxxxyyyyyy
episode = xxxxxxyyyyyyzzzzzz
```

The prefixes prove database containment but do not prove provider identity.

## Structural evidence

`season_structural_sources` records where the season/episode coordinates came
from. It must not silently substitute for franchise identity.

For each placement it should retain:

```text
watchlist_local_id
structural_provider
structural_series_id
structural_season_number
source_episode_number
destination_episode_number
evidence_payload_or_hash
```

If `structural_series_id` is a TVDB ID and `tv_series.tvdb_id` is non-null, the
two values must match before commit.

## Decision audit

Add an auditable decision record rather than relying only on log text. A
minimal target table is:

```sql
CREATE TABLE mediation_decisions(
  watchlist_local_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  state TEXT NOT NULL,
  gate TEXT NOT NULL,
  franchise_local_id TEXT,
  evidence_json TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Suggested states:

```text
IDENTITY_PENDING
IDENTITY_CONFLICT
RELATION_READY
FRANCHISE_ISOLATED
FRANCHISE_READY
STRUCTURE_PENDING
PLACEMENT_READY
CATALOGUED
PHYSICAL_PENDING
PUBLISHED
QUARANTINED
```

This makes the answer to “why was this episode placed here?” available without
reconstructing hours of application logs.

## Transaction boundary

Evidence collection does not mutate catalogue ownership. The database write
starts only after the whole proposed placement has passed every validation
gate.

Within one transaction:

1. Lock/re-read all existing provider identities used by the plan.
2. Re-check uniqueness and coordinate collisions.
3. Insert or enrich the franchise.
4. Insert/update seasons and watchlist links.
5. Insert/update episodes and their source ownership.
6. Store structural evidence and the final decision.
7. Mark the watchlist item catalogued.
8. Commit.

On any error, roll back all eight operations.

## Reconciliation and repair

Because this is an alpha build, the first implementation of this contract
should rebuild the mediated catalogue and physical library rather than migrate
known-corrupted ownership.

The canonical watchlist and provider snapshots are the source input. Rebuild
must preserve authentication, connected accounts, user status/progress, and
preferences while replacing derived catalogue, mediation-decision, artwork
mapping, and physical-library state.

Before deletion, the rebuild command must produce a dry-run report containing:

- canonical watchlist item count;
- identity conflicts;
- proposed isolated franchises;
- proposed franchise merges;
- structural conflicts;
- coordinate collisions;
- unreleased/mature items excluded from physical publication;
- directories that would be replaced.

