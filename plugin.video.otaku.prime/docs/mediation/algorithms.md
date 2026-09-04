# Global mediation algorithms

These algorithms operate on the complete changed watchlist set. Provider
adapters collect facts; they do not make catalogue ownership decisions.

## Batch reconciliation

```text
reconcile_all(provider_snapshots, existing_catalogue):
    canonical_items = merge_exact_provider_identities(provider_snapshots)
    evidence = collect_identity_relation_and_structure_evidence(canonical_items)
    graph = build_typed_identity_graph(evidence)

    plans = []
    decisions = []

    for item in stable_prime_id_order(canonical_items):
        identity = validate_identity_set(item, graph)
        if identity.conflict:
            decisions += quarantine(item, IDENTITY_CONFLICT, identity.evidence)
            continue

        owner = resolve_franchise_owner(identity, graph, existing_catalogue)
        if owner.conflict:
            decisions += quarantine(item, FRANCHISE_CONFLICT, owner.evidence)
            continue

        closed_owner = close_provider_ids(owner, identity, graph)
        if closed_owner.conflict:
            decisions += quarantine(item, PROVIDER_ID_CONFLICT, closed_owner.evidence)
            continue

        structure = resolve_structure(item, closed_owner, evidence)
        if structure.pending:
            decisions += defer(item, STRUCTURE_PENDING, structure.evidence)
            continue
        if structure.conflict:
            decisions += quarantine(item, STRUCTURE_CONFLICT, structure.evidence)
            continue

        plans += make_placement_plan(item, closed_owner, structure)

    valid_plans, collision_decisions = validate_global_coordinates(
        plans, existing_catalogue
    )
    decisions += collision_decisions

    committed_ids = commit_valid_plans_atomically(valid_plans, decisions)
    publish_released_policy_allowed_ids(committed_ids)
```

Stable iteration order is useful for reproducible logs, but it must not affect
the result because no catalogue mutation occurs until global validation ends.

## Exact identity graph

Represent each provider identity as a node:

```text
(anilist, 269)
(mal, 269)
(kitsu, 244)
(simkl, 41066)
(tvdb, 74796)
```

Only validated cross-ID responses create identity edges. The graph algorithm
uses union-find/disjoint sets to create canonical media-item components.

Before joining two components:

1. Reject if both already contain different IDs for the same provider.
2. Reject if returned identity does not equal the requested identity.
3. Reject if strong metadata contradicts media type or release identity.
4. Record the provider response and validation result as evidence.
5. Join only after all checks pass.

Titles never create graph edges. They are used only to search for candidates
that must subsequently return exact IDs.

## Typed relation graph

Relationship edges are stored separately from identity edges:

```text
relation_edge = {
    source_identity,
    destination_identity,
    provider,
    provider_relation_type,
    normalized_class,
    direction,
    release_dates,
    accepted_for_ordering,
    accepted_for_ownership
}
```

The normalizer uses a closed allowlist. New provider relation values default to
`UNKNOWN_NON_OWNING` until deliberately classified.

Franchise-root traversal may follow a sequence edge only when both nodes have
already passed the same-franchise ownership gate. This prevents a relation
edge from proving the condition required to traverse itself.

## Franchise-owner resolution

```text
resolve_franchise_owner(item):
    exact_existing = catalogue rows matching any validated root ID
    if exact_existing contains multiple Prime IDs:
        return conflict
    if exact_existing contains one compatible Prime ID:
        return that owner

    structural_ids = independently validated structural series IDs
    if structural_ids disagree:
        return conflict
    if one structural ID exists:
        return existing owner for that exact ID, or new owner candidate

    confirmed_parent = parent edge confirmed by another provider
    if confirmed_parent exists and has no structural contradiction:
        return confirmed parent owner

    return isolated owner based on the target identity
```

There is no fuzzy title fallback. An isolated franchise can be joined later
only by an explicit repair plan after stronger identity evidence appears.

## Provider-ID closure

Provider-ID closure fills the root entity from every validated source without
confusing target-season IDs with franchise-root IDs.

```text
for provider in anilist, mal, kitsu, simkl, tvdb:
    candidates = validated IDs describing selected root
    if candidates has more than one distinct non-null value:
        conflict
    if existing root value is non-null and differs from candidate:
        conflict
    result[provider] = existing value or candidate or null
```

The originating canonical watchlist row participates only when the watchlist
item itself is the selected root. When the item is Season 2 or a special, its
IDs belong on the season/episode and cannot be copied to the franchise root.

## Structural mapping

For each source unit in the watchlist item, build one mapping:

```text
(watchlist_local_id, source_episode_number)
    -> (franchise_candidate, season_number, episode_number)
```

Accept explicit provider coordinates before inferred ordering. An inferred
coordinate requires agreement between independent sources and must pass:

- franchise-owner compatibility;
- release chronology;
- complete source coverage;
- no duplicate destination;
- no overlap with another watchlist item unless exact identity proves it is
  the same source unit.

Unreleased items with a known structure may produce a catalogue-only plan.
Items with neither published episodes nor safe coordinates remain deferred.

## Global coordinate validation

Create an in-memory index from the existing catalogue and every proposed plan:

```text
destination[(franchise_identity, season_number, episode_number)]
    = (watchlist_local_id, source_episode_number, provider_identity_set)
```

On duplicate destination:

- accept an idempotent update from the same watchlist/source episode;
- accept two rows only when exact identities prove they are the same media
  unit, then consolidate the ownership link;
- otherwise quarantine both proposed claims and leave the existing valid row
  unchanged.

Do not choose a winner by provider priority and do not move either claim to a
free coordinate.

## Reconciliation diff

Before committing, calculate a declarative diff:

```text
franchises_to_insert
franchises_to_enrich
seasons_to_insert_or_update
season_links_to_insert
episodes_to_insert_or_update
derived_rows_to_remove
physical_ids_to_rebuild
```

Rows are removed only when Prime owns them and the complete canonical input no
longer produces them. Local Kodi media and unrelated catalogue rows are never
deleted by this algorithm.

