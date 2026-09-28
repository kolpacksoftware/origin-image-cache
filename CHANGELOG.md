# Changelog

All notable changes to this project are documented here. This project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0

### Added

- Initial package layout: library, `origin-image-cache` command, and tests.
- Local index lookup for an origin image URL, with fetch-and-store on a miss.
- `serve` and `get` commands. `GET /v1/image?url=` returns the bytes and an `X-Cache` header.
- `serve` binds to localhost by default and fetches the URL supplied by the client.
