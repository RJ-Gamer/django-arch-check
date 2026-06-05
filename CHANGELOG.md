# Changelog

All notable changes to `django-arch-check` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.8.0] - 2026-06-05

### Added

- Added `n1_serializer_risk`, a new detector focused on DRF serializer and viewset N+1 patterns. It flags ORM work inside `SerializerMethodField` getters, nested serializers without paired `prefetch_related`/`select_related`, serializer `source=` fields bound to ORM-backed model `@property` methods, and bare viewset querysets paired with relational serializers.
- Added code-snippet payloads for `n1_serializer_risk` findings, including line ranges and preserved source lines for HTML, JSON, and SARIF consumers.
- Added an accordion-style HTML report rendering path for findings that carry `code_snippet` context.
- Added detector, analyzer, report, CLI, and machine-output test coverage for the serializer-risk workflow.

### Changed

- Updated the detector registry, score model, HTML sections, JSON output, and SARIF rule set to include `n1_serializer_risk`.
- Report severity summaries now treat detector-level `error` findings as critical for aggregate counts and badges.

## [v0.7.1] - 2026-05-26

### Added

- Added dark/light theme toggle to the HTML report with `localStorage` persistence so the chosen theme survives page reloads.
- Added full CSS variable layer for theming — all colors, gradients, shadows, and grid lines resolve through CSS custom properties so both themes share a single stylesheet.

### Changed

- Refactored HTML report CSS to use CSS variables (`--body-grad-*`, `--grid-line`, `--scanline`, `--nav-bg`, `--hero-sub`, `--orbit-*`, `--card-shadow`) instead of hardcoded `oklch`/`rgba` literals, making the light theme override clean and maintainable.

---

## [v0.7.0] - 2026-05-26

### Added

- Added score grades and labels (`A`-`F`, `Excellent` to `Critical`) to HTML output and to the CLI summary shown after `--format html`.
- Added a score breakdown table to the HTML report so each detector shows finding count, weighted impact, and a low/medium/high impact label.
- Added report tests covering grade boundaries, score labels, footer formula text, and size-aware score behavior.

### Changed

- Replaced the old rate-based health score with a weighted, detector-risk-aware, size-normalized formula based on Python file count.
- Updated the HTML report to surface the new score model more clearly with grade presentation, revised footer copy, and richer summary cards.

### Fixed

- Fixed JSON output so `summary.health_score` uses the analyzed `project_path`, matching the project-aware score shown in the HTML report.

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

## [v0.5.0] - 2026-05-19

### Added

- Added `.pre-commit-hooks.yaml` so teams can integrate `django-arch-check` with pre-commit in one hook entry.
- Added `--format json` output for scripting, dashboards, and third-party integrations.
- Added `--format sarif` output for GitHub code scanning, VS Code, and CI systems that consume SARIF.
- Added a dedicated serializer layer for machine-readable output formats.

### Changed

- Made machine-readable output emit clean stdout payloads without human-oriented banners.
- Made HTML output preserve CI-gating behavior by exiting non-zero when critical findings are present.
