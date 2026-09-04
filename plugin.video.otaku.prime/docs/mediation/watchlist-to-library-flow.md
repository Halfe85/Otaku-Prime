# Watchlist-to-library flow diagram

```mermaid
flowchart TD
    START([Watchlist synchronization starts]) --> FETCH[Fetch every connected provider snapshot]
    FETCH --> G0{Gate 0<br/>Snapshot complete?}
    G0 -- Request failed --> KEEP[Keep last successful provider snapshot<br/>record retryable failure]
    G0 -- Cancelled --> STOP([Stop without partial writes])
    G0 -- Yes --> MERGE[Build one canonical Prime watchlist]

    KEEP --> MERGE
    MERGE --> IDENTITIES[Enrich exact AniList / MAL / Kitsu / Simkl IDs<br/>for all changed items]
    IDENTITIES --> G1{Gate 1<br/>Exact identities agree?}
    G1 -- Conflict --> Q1[Quarantine identity conflict]
    G1 -- Missing IDs --> ALLOW[Keep null IDs; continue with known evidence]
    G1 -- Yes --> RELATIONS
    ALLOW --> RELATIONS[Fetch and type relationship evidence]

    RELATIONS --> G2{Gate 2<br/>Edge permits traversal?}
    G2 -- OTHER / ALTERNATIVE / SPIN-OFF / unknown --> AUDIT[Record non-owning association only]
    G2 -- PREQUEL / SEQUEL --> ORDER[Use only for ordering inside a validated franchise]
    G2 -- Confirmed parent candidate --> OWNER
    AUDIT --> OWNER[Resolve franchise owner globally]
    ORDER --> OWNER

    OWNER --> G3{Gate 3<br/>One compatible owner?}
    G3 -- No trusted owner --> ISOLATE[Create isolated franchise candidate]
    G3 -- Multiple or conflicting owners --> Q2[Quarantine franchise conflict]
    G3 -- One owner --> IDCLOSE[Close provider IDs on franchise root]
    ISOLATE --> IDCLOSE

    IDCLOSE --> G4{Gate 4<br/>All non-null IDs compatible?}
    G4 -- Conflict --> Q3[Quarantine provider-ID conflict]
    G4 -- Yes / missing allowed --> STRUCTURE[Resolve library type, TVDB owner,<br/>season and episode coordinates]

    STRUCTURE --> G5{Gate 5<br/>Structure complete and compatible?}
    G5 -- Metadata not published --> DEFER[Defer and retry later]
    G5 -- TVDB/franchise mismatch --> Q4[Quarantine structural conflict]
    G5 -- Yes --> PLAN[Add placement to global batch plan]

    PLAN --> G6{Gate 6<br/>Global coordinates collision-free?}
    G6 -- Conflict --> Q5[Quarantine both claims<br/>never silently renumber]
    G6 -- Yes --> G7{Gate 7<br/>Released and policy allows publication?}
    G7 -- Not released --> CATALOG_ONLY[Keep validated catalogue structure<br/>without physical files]
    G7 -- Mature content blocked --> POLICY[Keep watchlist row; exclude physical publication]
    G7 -- Yes --> COMMIT
    CATALOG_ONLY --> COMMIT[Commit validated reconciliation plan]
    POLICY --> COMMIT

    COMMIT --> G8{Gate 8<br/>Atomic transaction succeeds?}
    G8 -- No --> ROLLBACK[Roll back complete plan]
    G8 -- Yes --> HANDOFF[Hand changed opaque Prime IDs to Prime Physical]

    HANDOFF --> G9{Gate 9<br/>Physical projection validates?}
    G9 -- No --> RETRY[Record retryable physical error<br/>keep catalogue intact]
    G9 -- Yes --> KODI[Request scoped Kodi library scan]
    KODI --> DONE([Reconciliation complete])

    Q1 --> REVIEW([Visible admin review queue])
    Q2 --> REVIEW
    Q3 --> REVIEW
    Q4 --> REVIEW
    Q5 --> REVIEW
    DEFER --> RETRY_LATER([Watchdog retries after metadata changes])
    ROLLBACK --> RETRY_LATER
    RETRY --> RETRY_LATER
```

The diagram describes the target pipeline. The current Alpha11 mediator still
writes catalogue rows during individual item processing and therefore does not
yet provide this global validation boundary.

