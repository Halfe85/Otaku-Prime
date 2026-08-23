# Otaku Prime

Otaku Prime is a new Kodi anime addon built from a fresh codebase.

## Branch model

- `main` — upstream/reference snapshot used to study Otaku behavior and implementations.
- `Otaku-Prime` — the new Otaku Prime codebase.

Otaku Prime may reimplement or port selected concepts and functions from Otaku where useful, but code is moved deliberately rather than treating the upstream addon as the application base.

## Architecture direction

Kodi owns the user's media library and viewing state (watched, resume, last played). Otaku Prime owns metadata/provider integration, watchlist synchronization, source discovery, and playback resolution.

The first major milestone is watchlist -> Kodi native library import using stable `.strm` playback entries.

## License and upstream attribution

Otaku Prime is GPL-3.0 licensed. It is inspired by and may contain adapted GPL-licensed portions of Otaku. When upstream code is copied or adapted, its copyright/license notices must be retained as required by GPL-3.0.
