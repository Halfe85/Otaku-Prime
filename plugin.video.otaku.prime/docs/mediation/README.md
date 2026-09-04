# Prime watchlist-to-library mediation

Status: architecture contract for the next mediator rebuild. The current
Alpha11 mediator does not yet satisfy every rule in this document.

Prime must use one deterministic pipeline for every watchlist item. Series
names such as Bleach, BURN THE WITCH, Bakemonogatari, or BanG Dream must never
appear in the implementation as placement exceptions.

The complete flow is shown in
[`watchlist-to-library-flow.md`](watchlist-to-library-flow.md). The individual
gate contracts are defined in [`logic-gates.md`](logic-gates.md), the global
algorithms in [`algorithms.md`](algorithms.md), and the required database
ownership model in [`data-contract.md`](data-contract.md).

## Fundamental separation

Prime handles three different questions. Evidence answering one question must
not silently answer another.

1. **Identity**: which AniList, MAL, Kitsu, Simkl, and TVDB IDs describe the
   same media item?
2. **Franchise ownership**: which Prime TV-show or standalone movie owns the
   item?
3. **Structure**: which season and episode coordinates represent the item in
   Kodi?

For example, an AniList `OTHER` relationship may explain why two works are
listed together by AniList. It supplies neither franchise ownership nor Kodi
coordinates. Likewise, a TVDB season/episode coordinate describes structure;
it must not rename or replace the Prime franchise identity.

## Non-negotiable invariants

- Every canonical watchlist item retains all known provider IDs.
- Every catalogue entity retains all provider IDs that were validated for that
  exact entity.
- Titles and years are lookup hints only. They never authorize a merge.
- `OTHER`, `ALTERNATIVE`, `ALTERNATIVE_SETTING`, `RECOMMENDATION`, shared
  staff, and shared characters never create franchise ownership.
- `PREQUEL` and `SEQUEL` establish ordering only after both endpoints have been
  accepted as members of the same franchise.
- A TVDB series ID is structural evidence. Different non-null TVDB series IDs
  may not be written into one Prime TV-show.
- Missing evidence produces an isolated new franchise or `DEFERRED`; conflicting
  evidence produces `QUARANTINED`. Prime must under-merge instead of corrupting
  an existing franchise.
- Season and episode coordinates are allocated globally before anything is
  committed. A collision is never silently renumbered.
- Catalogue changes are atomic and idempotent. A stopped or failed run leaves
  the last valid catalogue intact.
- Prime Physical runs only after a committed placement plan and receives only
  the stable Prime series or movie ID.

## Global processing model

Although each watchlist item receives an individual decision, Prime must not
immediately mutate the catalogue one item at a time. That makes the result
depend on processing order and lets an early incorrect root poison later items.

Each synchronization is instead divided into global phases:

1. Fetch provider snapshots without deleting the last successful snapshot.
2. Merge snapshots into one canonical watchlist.
3. Enrich and validate provider identities for all changed items.
4. Build a typed relationship graph for the complete changed set.
5. Resolve franchise candidates without writing catalogue rows.
6. Resolve structural owners and episode coordinates without writing rows.
7. Validate every proposed placement and collision as one plan.
8. Commit only valid plans in database transactions.
9. Project changed Prime IDs into the physical library.
10. Ask Kodi to scan only successfully projected directories.

This makes the same rules apply to television seasons, multipart seasons,
specials, OVAs, ONAs, OADs, related movies, and standalone movies.

## Required outcome for the Bleach/BURN THE WITCH regression

The rebuilt mediator must produce two independent franchises:

```text
Bleach
  root AniList: 269
  root MAL:     269
  root Kitsu:   244
  root Simkl:   41066
  TVDB:         74796

BURN THE WITCH
  BURN THE WITCH #0.8 is placed only inside this structure when validated
  by its structural provider.
```

AniList's `OTHER` association and MAL's `alternative_setting` association
between the works must be recorded, if useful for display, as non-owning
evidence. They must never connect the two catalogue franchises.

## Current-build gaps

The current build violates this contract in several known places:

- Simkl prequel traversal accepts title similarity as low as `0.30`.
- AniList explicitly promotes a special-to-`OTHER` bridge to TV ownership.
- MAL explicitly promotes `alternative_setting` to TV ownership.
- A Simkl structural TVDB owner can disagree with the selected franchise TVDB
  owner without rejecting the placement.
- `tv_series` cannot store MAL or Kitsu root IDs.
- AniList/MAL placement can discard Simkl and Kitsu IDs already stored on the
  canonical watchlist row.
- Catalogue writes occur while individual items are being processed instead of
  after global plan validation.

These are implementation defects, not accepted compatibility behavior.
