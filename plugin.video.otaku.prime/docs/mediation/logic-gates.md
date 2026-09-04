# Mediation logic gates

Every changed canonical watchlist item passes through these gates. A provider
priority decides where evidence is requested first; it does not allow a
high-priority provider to bypass a gate.

Each gate returns exactly one outcome:

- `PASS`: continue with validated evidence.
- `DEFER`: information is incomplete or not published yet; retry later.
- `ISOLATE`: do not merge; create a new independent franchise candidate.
- `QUARANTINE`: evidence conflicts; preserve the watchlist row but do not
  change the catalogue or physical library.

## Gate 0: snapshot integrity

**Input:** responses from every connected watchlist provider.

**PASS when:** every successful response is parsed and committed as a complete
provider snapshot.

**Rules:**

- One provider failure must not erase that provider's last successful snapshot.
- An empty successful watchlist and a failed request are different outcomes.
- Merge begins only after all connected providers have returned success,
  failure, or cancellation for this run.
- Cancellation writes no partial provider snapshot.

## Gate 1: canonical item identity

**Input:** raw provider entries and cross-provider ID results.

**PASS when:** all non-null provider IDs agree on one exact media item.

**Rules:**

- Validate returned IDs against the ID used for the request.
- Enforce uniqueness of `(provider, provider_id)` across canonical watchlist
  items.
- Merge rows only through exact cross-provider IDs.
- Title, year, format, episode count, and release date may reject a candidate;
  they may not independently approve one.
- Missing provider IDs are valid and remain null.
- Conflicting exact IDs return `QUARANTINE`.

**Output:** an immutable identity set:

```text
prime_watchlist_id
anilist_id?
mal_id?
kitsu_id?
simkl_id?
identity_evidence[]
```

## Gate 2: relationship classification

**Input:** provider relationship edges for validated identities.

Every edge must be typed before traversal:

| Edge class | Examples | Permitted use |
|---|---|---|
| Sequence | `PREQUEL`, `SEQUEL` | Ordering within an already validated franchise |
| Ownership candidate | `PARENT`, `PARENT_STORY` | Candidate only; requires independent structural confirmation |
| Non-owning association | `OTHER`, `ALTERNATIVE`, `ALTERNATIVE_SETTING`, `SPIN_OFF` | Display/audit only |
| Source/adaptation | `SOURCE`, `ADAPTATION` | Never joins anime catalogue franchises |
| Unknown | Unrecognized provider value | Audit only; no traversal |

**Rules:**

- Relation direction must be consistent with release chronology when dates are
  known.
- A later title cannot become the root through a purported backward edge.
- A sequence edge cannot cross two different validated TVDB series IDs.
- No title-similarity threshold can turn an edge into ownership.
- Unknown or non-owning edges are terminal for ownership traversal.

## Gate 3: franchise ownership

**Input:** canonical identity plus typed relationship evidence.

Resolve an owner using deterministic evidence in this order:

1. An existing Prime series matching an exact validated root provider ID.
2. One TVDB series ID independently cross-mapped from the target identity.
3. An explicit parent edge confirmed by a second independent provider and not
   contradicted by structural IDs.
4. Otherwise return `ISOLATE` and create a new franchise candidate for the
   target itself.

**QUARANTINE when:**

- exact IDs resolve to more than one existing Prime series;
- non-null TVDB series IDs disagree;
- provider roots point to different canonical entities;
- a proposed merge would replace an existing franchise's root identity.

The selected owner is immutable within the placement plan.

## Gate 4: provider-ID closure

**Input:** selected franchise owner and every validated identity source.

**PASS when:** the franchise identity contains every known ID for the root
entity and none belong to the target season/special by mistake.

Target `tv_series` identity fields:

```text
root_anilist_id
root_mal_id
root_kitsu_id
root_simkl_id
tvdb_id
```

The canonical watchlist row is a required source. A helper returning `None`
must not erase an already validated ID. Conflicting values return
`QUARANTINE`; missing values remain null and may be enriched later.

## Gate 5: library type and structure

**Input:** validated franchise owner plus provider episode structure.

**Rules:**

- A standalone movie with no validated series owner goes to Movies.
- A movie/OVA/ONA/OAD/special with a validated structural series owner is
  placed inside that TV-show, normally Season 0 when the structural source says
  so.
- A multipart source may map across multiple Kodi seasons, but every source
  episode must have exactly one destination coordinate.
- The structural TVDB ID must equal the franchise TVDB ID when both exist.
- Episode counts establish coverage, not ownership.
- Unknown episode metadata returns `DEFER` rather than inventing episodes.

**Output:** a placement plan containing no database-local IDs yet:

```text
library_type
franchise_identity
season_number
source_episode -> destination SxxEyy mappings
structural_evidence
```

## Gate 6: global collision validation

**Input:** all placement plans plus the existing catalogue.

**PASS when:** every destination coordinate has one compatible owner.

**Rules:**

- Validate collisions using `(franchise, season, episode)` across the complete
  batch, not processing order.
- The same watchlist item may update its own existing coordinates.
- Two watchlist items may share one season but may not claim the same episode
  unless exact provider evidence proves they represent the same item.
- Never shift an incoming special to the next free episode merely to avoid a
  collision.
- Never create duplicate Season 0 or duplicate numbered seasons for one
  franchise.
- Conflicting plans return `QUARANTINE` with both owners and all evidence.

## Gate 7: release and mature-content policy

**Input:** validated placement plan and user policy.

**Rules:**

- Catalogue structure may be retained for announced content.
- Physical STRM/NFO files are created only for released episodes or movies.
- Unknown future release data returns `DEFER` for physical publication.
- Mature items remain excluded from physical publication while mature content
  is disabled or required age verification is absent.
- Policy filtering does not destroy the canonical watchlist row.

## Gate 8: atomic catalogue commit

**Input:** one globally validated reconciliation plan.

**PASS when:** all rows, links, IDs, coordinates, and ownership records commit
in one transaction.

The transaction must include:

- franchise insert/update;
- complete provider-ID closure;
- season insert/update;
- `season_watchlist_links` ownership;
- episode insert/update and originating watchlist ID;
- structural evidence and decision audit rows;
- mediator status update.

A crash, stop, or constraint error rolls back the complete plan. No physical
handoff occurs after rollback.

## Gate 9: physical projection and Kodi scan

**Input:** committed changed Prime series/movie IDs.

**Rules:**

- Prime Physical queries the database itself; the mediator passes only opaque
  Prime IDs.
- Project to a temporary/staging directory and atomically publish the finished
  directory where possible.
- Never delete a valid directory before its replacement is ready.
- Scan Kodi only after the directory exists and its NFO/STRM set validates.
- Failed Kodi import does not roll back the Prime catalogue, but records a
  retryable physical/scan error.

## Forbidden shortcuts

- No per-title special cases.
- No fuzzy-title database merges.
- No `OTHER` or `alternative_setting` ownership traversal.
- No first-provider-wins when exact identities conflict.
- No partial catalogue write followed by later validation.
- No silent coordinate renumbering.
- No provider `None` overwriting a validated ID.

