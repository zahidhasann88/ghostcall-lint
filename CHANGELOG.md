# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) ·
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- GitHub Action (`action.yml`) that checks a pull request's diff.
- `ghostcall check --staged` without `--diff` (compares with `HEAD`).
- GC000 in JSON, SARIF and GitHub output.
- Benchmark cases for pydantic, fastapi, wrong import paths and methods on the wrong class.
- CI on macOS and Windows; tests that run without the pinned environment.

### Changed

- Calls through decorators that may change the signature are skipped.
- Classes with a custom metaclass or `__new__` are skipped.
- Environment discovery no longer runs `.pth` files (`python -I -S`).
- `# ghostcall: ignore` at the end of a line applies to that line only.
- GC007 is reported even when the signature can't be trusted.

### Fixed

- Config and environment errors exiting with a traceback instead of codes 2/3.
- Windows paths in the test suite.
- A race condition with `--jobs`.

## [0.1.0] - 2026-10-16

First release, published as `ghostcall-lint`.

- Rules GC000–GC007: missing modules, names, attributes, unknown keywords,
  wrong argument counts, missing arguments, deprecated APIs.
- Static analysis of the target environment; runtime import only with `--allow-import`.
- Medium-confidence instance tracking behind `--include-medium`.
- Output as text, JSON, SARIF and GitHub annotations; diff and stdin modes.
- `explain`, `info`, `bench` and `cache` commands; `[tool.ghostcall]` config.
- pre-commit hooks, CI workflows and PyPI trusted publishing.

[Unreleased]: https://github.com/zahidhasann88/ghostcall-lint/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/zahidhasann88/ghostcall-lint/releases/tag/v0.1.0
