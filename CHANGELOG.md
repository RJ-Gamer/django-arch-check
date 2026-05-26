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



## [v0.6.0] - 2026-05-26

### Added

- Added `migration_safety` detector — scans all `migrations/` directories and
  flags operations that carry deployment or data-safety risk: `RemoveField`,
  `RenameField`, `AddField` without a default on a NOT NULL column,
  `RunPython` without `atomic = False`, and `RunSQL`. Every finding is an
  advisory with a message explaining the risk and a safer alternative.
  Suppress a known-safe finding with `# django-arch-check: ignore` on the
  operation line.

### Fixed

- `missing_service_layer` detector no longer flags DRF and Django CBV override
  methods (`get_queryset`, `perform_create`, `get_context_data`, `form_valid`,
  and others) where ORM calls are expected and correct. These were false
  positives that degraded trust in the detector on real DRF projects.