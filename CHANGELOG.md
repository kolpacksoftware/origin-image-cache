# Changelog

All notable changes to this project are documented here. This project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.2.0

### Changed

- The writable index is `index.sqlite` plus `objects/`, replacing `manifest.json`.

### Added

- Optional read-only sqlite indexes (`--read-only`, `ImageCache(read_only=...)`). A hit from one has `where` of `read-only` and `X-Cache: INDEX`. The `http` and `https` form of a URL are both tried. Files must stay inside the configured root.
- `--max-bytes` on `serve` and `get` (default 25 MB).
- `--fetch` JSON with `userAgent` and per-host overrides (`ImageCache(user_agent=..., host_agents=...)`). No hostnames in the package. Without `--fetch` the agent stays `origin-image-cache/<version>`.

## 0.1.0

### Added

- Initial package layout: library, `origin-image-cache` command, and tests.
- Local index lookup for an origin image URL, with fetch-and-store on a miss.
- `serve` and `get` commands. `GET /v1/image?url=` returns the bytes and an `X-Cache` header.
- `serve` binds to localhost by default and fetches the URL supplied by the client.
