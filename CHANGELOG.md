# Changelog

All notable changes to `django-arch-check` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.9.0] - 2026-06-08

### Added

- Added `--watch` mode to the `analyze` command. Running `django-arch-check analyze --watch ./` monitors the project tree for `.py` file changes and automatically re-runs the full analysis on every save, with no manual re-invocation needed.
- Added `django_arch_check/watcher.py` — a self-contained file watcher that works out of the box with zero extra dependencies using pure-Python mtime polling, and automatically upgrades to lower-latency event-based watching when `watchdog` is installed.
- Added `[watch]` optional install extra: `pip install django-arch-check[watch]` installs `watchdog>=4.0` for lower-latency file change detection.
- Added diff output between watch runs: after the first full analysis, subsequent runs show only `✔ N finding(s) resolved` and `✖ N new finding(s)` with per-finding detail, keeping the terminal readable during active development.
- Added score line (`Score: 82/100  B · Good`) and timestamp (`[14:32:01]`) to each watch iteration header so developers can track health trend at a glance.
- `--watch` is blocked with a clear error when combined with `--format html`, `json`, or `sarif` since those formats are not meaningful in a live loop.

### Fixed

- Fixed `_has_critical_findings` in `cli.py` which previously only checked five detectors (`fat_models`, `god_apps`, `circular_imports`, `missing_service_layer`, `celery_tasks`). It now covers all nine detectors, so `direct_sql`, `n_plus_one`, `migration_safety`, and `n1_serializer_risk` critical findings correctly trigger a non-zero exit code.

### Tests

- Added `tests/test_watcher.py` with 9 tests covering `_snapshot`, `_diff`, polling callback firing, no-fire-without-change, skip-dirs behaviour, and a hang-safe stop condition.
- Added 11 watch-mode tests to `tests/test_cli.py` covering format rejection, `--help` visibility, delegation to `_run_watch`, threshold forwarding, `_finding_key` stability, `_all_finding_keys` counting, `_has_critical_findings` completeness, and `_print_watch_diff` for no-change / new / resolved scenarios.
- Test count: 245 passing.

## [v0.8.1] - 2026-06-08

### Fixed

- Fixed health score returning `F` (31/100) when scanning the tool's own repository. The old formula applied a density penalty factor of `8` with a cap of `65`, which meant even pure warning-only findings (e.g. 27 `direct_sql` hits in test fixtures) could max out the penalty and produce a misleading grade.
- Fixed `Health Grade` summary card always rendering in red regardless of score. The card was hardcoded to `g-cr`; it now uses `g-ok` (green) for scores ≥ 75, `g-wa` (yellow) for 60–74, and `g-cr` (red) below 60.
- Fixed a path-relativity bug where scanning a subdirectory scored lower than scanning the full project with more findings. Smaller `file_count` produced a smaller log denominator, inflating density for the same capped weight. A `_MIN_FILE_COUNT = 30` floor ensures consistent scores regardless of which directory is passed.

### Changed

- Reworked the scoring formula to be severity-aware. Critical and error findings now count `2×` in the density numerator while warnings count `1×`, so architecture-breaking issues (circular imports, unsafe Celery tasks) cause meaningfully steeper penalties than code-smell warnings.
- Reduced `_DENSITY_FACTOR` from `8` to `4` and `_DENSITY_PENALTY_CAP` from `65` to `45` so warning-only projects can no longer reach `F` territory through density alone.
- Added `_DETECTOR_FINDING_CAP` — per-detector ceiling on findings counted toward the score. Noisy detectors (`direct_sql` capped at 8, `n_plus_one` at 8, `migration_safety` at 10, `n1_serializer_risk` at 8, `fat_models` at 6) stop contributing past their cap, preventing a single high-count detector from dominating the result.
- Added `_score_card_class` helper and wired it into the HTML report so the Health Grade summary card color always matches the actual score band.

### Tests

- Updated `test_more_findings_scores_lower_than_fewer` to stay within the `fat_models` cap so the assertion is meaningful.
- Updated `test_low_score_html_shows_critical_label` (replaces `test_zero_score_html`) to reflect that the new formula no longer produces scores of exactly 0 for small critical finding sets.
- Updated `test_score_is_size_aware` docstring and assertion to reflect that the floor fix intentionally equalises scores across path sizes.
- Added `test_min_file_count_floor_equalises_small_paths` — verifies `_count_python_files` returns `_MIN_FILE_COUNT` for directories with fewer than 30 Python files.
- Added `test_criticals_double_weighted_in_density` — verifies that equal raw weight with critical severity produces a lower score than the equivalent warning weight.
- Added `test_grade_card_class_green_for_good_score` — covers all three branches of `_score_card_class`.
- Added `test_grade_card_class_appears_in_html` — verifies `g-ok` renders in the HTML for a perfect score.
- Test count: 226 passing.

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
