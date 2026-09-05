# Simkl-only mediation phase 1

Status: active implementation contract for the first mediator rebuild.

The Alpha11 mediator is preserved on branch
`backup/alpha11-mediator-before-simkl-rebuild`.

## Live mediation path

```text
Watchlist item
  -> exact/usable Simkl identity
  -> Simkl target metadata
  -> explicit TVDB coordinates returned by Simkl
  -> validated direct season-owner edge, or target's TVDB crossmap
  -> explicit TVDB SxxEyy coordinates
  -> coverage validation
  -> Prime catalogue owner normalized to exact TVDB series ID
  -> catalogue write
  -> structural evidence write
  -> Prime Physical
  -> Kodi projection/scan path
```

AniList, MAL, and Kitsu IDs remain useful watchlist identity evidence and
metadata returned by Simkl, but the live mediator does not instantiate their
native clients or fall back to their placement engines. Watchlist import/auth
is a separate existing subsystem and is unchanged by this mediation boundary.
If the Simkl path fails, the item fails/defer with the Simkl reason.

## Ownership rules

For TV-series placement in phase 1:

- Simkl is the only mediation provider.
- TVDB series ID is the Prime catalogue owner key.
- Every source episode must have an explicit TVDB season and episode number.
- PREQUEL/SEQUEL traversal does not select the owner or calculate a season.
- A direct `season N` edge can select an owner only if the candidate maps N
  and its mapped seasons precede the target's explicit episode seasons.
- Tracker root IDs are kept only with verified root evidence. Otherwise they
  stay on the watchlist/season/episode, not the franchise owner.
- Different TVDB series IDs remain different Prime TV owners.
- Standalone Simkl movies without a TVDB series owner remain Movies.
- Missing TVDB ownership or coordinates is an error, never a guessed placement.

## Referenced specials

The old identity layer may discover a Simkl parent/special locator. The strict
mediator does not trust a title/date-only locator. Before accepting a referenced
special it re-checks the actual Simkl episode row and requires an exact AniList,
MAL, or Kitsu ID match with the watchlist item.

Without exact external-ID evidence, the special is rejected with an explicit
reason.

## Trace format

Every live item uses the watchlist local ID as its trace key:

```text
MEDIATOR[abcdef] seq=001 stage=SERVICE event=MEDIATION_BEGIN ...
MEDIATOR[abcdef] seq=002 stage=START event=ITEM_RECEIVED ...
MEDIATOR[abcdef] seq=003 stage=SIMKL_IDENTITY event=REQUEST ...
MEDIATOR[abcdef] seq=004 stage=SIMKL event=PLACEMENT_DISCOVERED ...
MEDIATOR[abcdef] seq=005 stage=COVERAGE event=CHECKED ...
MEDIATOR[abcdef] seq=006 stage=TVDB_STRUCTURE event=VALIDATED ...
MEDIATOR[abcdef] seq=007 stage=PLAN event=READY_FOR_CATALOGUE ...
MEDIATOR[abcdef] seq=008 stage=CATALOGUE event=WRITE_BEGIN ...
MEDIATOR[abcdef] seq=009 stage=CATALOGUE event=WRITE_COMPLETE ...
MEDIATOR[abcdef] seq=010 stage=TVDB_STRUCTURE event=EVIDENCE_COMMITTED ...
MEDIATOR[abcdef] seq=011 stage=PHYSICAL event=TV_SERIES_PROJECTED ...
MEDIATOR[abcdef] seq=012 stage=END event=COMPLETE ...
```

The exact sequence can contain additional events for multi-season items,
movies, deferred metadata, timestamp scheduling, or failures.

The evidence payload records mediation-relevant facts including:

- watchlist local ID and all known tracker IDs;
- Simkl target ID set, title, media type, year and status;
- the structural owner and the source used to resolve it;
- whether relation traversal participated in ownership/season numbering;
- every source-episode -> TVDB SxxEyy mapping;
- coverage counts;
- Prime series/movie and season IDs after persistence;
- physical handoff completion;
- exact failure/defer reason.

Credential-like fields are automatically redacted by the trace serializer.

### Persistent diagnostic archive

All centrally emitted INFO/WARNING/ERROR records also go to
`special://profile/addon_data/plugin.video.otaku.prime/logs/prime.log`
(beside `users.sqlite` in the active addon's profile). The current file rotates
at 10 MiB and retains five backups, `prime.log.1` through `prime.log.5`.
This is bounded retention, not a permanent unlimited history. Copy these files
when preserving a reproduction. The file handler stays active when the service
detaches SQLite during shutdown. Archive creation failures are reported to the
remaining Kodi/UI sinks without stopping the addon.

The UI still retains 1,000 entries and caps each message at 4,000 characters.
Use the diagnostic files for complete episode mappings and older trace entries.
File records include timestamps and worker names; Simkl requests inherit the
active watchlist trace ID. No API credentials, HTTP headers, or authenticated
request URLs are logged by the new request instrumentation.

Additional evidence includes request status, timing, throttling, cache hits,
original episode row types before normalization, accepted/rejected crossmap
and direct-owner candidates with reasons, special-ID checks, catalogue episode
IDs and their source/stored coordinates, and physical handoff start/completion.
Repeated full staff biographies are omitted from placement summaries; credit
counts and structural data are retained. Simkl errors are not silently replaced
by another provider's placement.

## Failure policy

Hard Simkl/TVDB mediation errors are logged as `ERROR` and propagate to the
existing watchdog, which stores the item as unresolved with the exception text.
Metadata that is valid but not yet published remains `DEFERRED`; it is not
replaced by another provider and no episode coordinate is invented.

## Deployment note

The catalogue is derived data. Before evaluating the phase-1 results against a
catalogue previously generated by Alpha11, perform a controlled structural
catalogue/physical-library rebuild so old placements do not masquerade as new
Simkl-only decisions. A dedicated phase-1 catalogue revision bump is still the
preferred automatic migration mechanism before release.
