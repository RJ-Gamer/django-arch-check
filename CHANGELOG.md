# Changelog

All notable changes to `django-arch-check` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.5.0] - 2026-05-19

### Added

- Added `.pre-commit-hooks.yaml` so teams can integrate `django-arch-check` with pre-commit in one hook entry.
- Added `--format json` output for scripting, dashboards, and third-party integrations.
- Added `--format sarif` output for GitHub code scanning, VS Code, and CI systems that consume SARIF.
- Added a dedicated serializer layer for machine-readable output formats.

### Changed

- Made machine-readable output emit clean stdout payloads without human-oriented banners.
- Made HTML output preserve CI-gating behavior by exiting non-zero when critical findings are present.

