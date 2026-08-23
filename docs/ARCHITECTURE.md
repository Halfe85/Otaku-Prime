# Otaku Prime Architecture

## Core ownership

### Kodi owns

- Anime titles present in the user's library
- Episodes present in the user's library
- Watched state (`playcount`)
- Resume position (`resume.position`, `resume.total`)
- Last played time
- Native Continue Watching / library history inputs

### Otaku Prime owns

- Watchlist account integration and synchronization
- Metadata acquisition and ID mapping
- Generated NFO/STRM library files
- Provider/source discovery
- Debrid integration
- Playback resolution
- Optional external tracker synchronization
- Background maintenance and web configuration service

## Planned flow

```text
Watchlist provider
      |
      v
Prime importer
      |
      v
NFO + STRM library files
      |
      v
Kodi native video library
      |
      | Play
      v
plugin://plugin.video.otaku.prime/play/...
      |
      v
Prime playback resolver
      |
      v
Kodi Player
      |
      v
Kodi watched/resume/lastplayed
      |
      `---- optional sync ----> AniList / MAL / Kitsu / Simkl
```

## Upstream Otaku policy

`main` is the reference/muse. `Otaku-Prime` is the product codebase.

When a useful Otaku function is needed:

1. Understand the behavior and dependencies.
2. Decide whether to reimplement or port it.
3. Move only the smallest useful unit into Prime's architecture.
4. Retain GPL/copyright notices for copied or adapted upstream code.
5. Add tests or a clearly defined integration boundary before bringing in the next subsystem.

This keeps source resolution, debrid, metadata, and other proven Otaku concepts available without inheriting the entire upstream architecture.
