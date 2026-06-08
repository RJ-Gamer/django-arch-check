# django-arch-check

![PyPI](https://img.shields.io/badge/PYPI-django--arch--check-4f8ef7?style=for-the-badge&logo=pypi&logoColor=white)
![Version](https://img.shields.io/badge/VERSION-0.9.0-4f8ef7?style=for-the-badge)
![Python](https://img.shields.io/badge/PYTHON-3.11%2B-4f8ef7?style=for-the-badge&logo=python&logoColor=white)
![License](https://img.shields.io/badge/LICENSE-MIT-yellow?style=for-the-badge)
![Status](https://img.shields.io/badge/STATUS-ACTIVE-brightgreen?style=for-the-badge)
![Detectors](https://img.shields.io/badge/DETECTORS-9-orange?style=for-the-badge)
![Tests](https://img.shields.io/badge/TESTS-245%20PASSING-brightgreen?style=for-the-badge&logo=pytest&logoColor=white)
![PRs](https://img.shields.io/badge/PRS-WELCOME-blueviolet?style=for-the-badge&logo=github)
[![Sponsor](https://img.shields.io/badge/SPONSOR-%E2%9D%A4-ea4aaa?style=for-the-badge&logo=github-sponsors)](https://github.com/sponsors/RJ-Gamer)

A command-line architectural health checker for Django projects.

It scans source code statically and flags structural issues before they become entrenched technical debt:

- Fat models
- God apps
- Circular imports
- Missing service layer boundaries
- Celery tasks without retry
- Direct SQL usage
- N+1 query risks
- N+1 serializer risks
- Migration safety risks

```text
Analyzing: /home/user/myproject

── Fat Models ──────────────────────────────
  [CRITICAL]  core/models.py → UserProfile (34 methods)

  Found 1 fat model(s).

── God Apps ────────────────────────────────
  [WARNING]   core/ owns 41% of total project code (860 / 2,102 lines)

  Found 1 god app(s).

── Circular Imports ────────────────────────
  No circular imports found.

── Missing Service Layer ────────────────────
  [WARNING]   orders/views.py → create_order() makes direct ORM calls

  Found 1 missing service layer issue(s).

── Celery Tasks Without Retry ──────────────
  [CRITICAL]  payments/tasks.py → send_invoice_email() — high-stakes task, no retry configured

  Found 1 Celery task(s) without retry.
```

---

## Why

Django projects often drift toward the same architectural problems:

- Models absorb business logic until they become hard to reason about
- One app quietly turns into the center of the codebase
- View functions talk directly to the ORM and blur boundaries
- Celery tasks lose work because retries were never configured
- Circular imports pile up as module boundaries erode

These issues are easy to normalize and hard to see in code review. `django-arch-check` makes them visible early, in local development and in CI.

---

## Installation

```bash
pip install django-arch-check
```

Requirements:

- Python 3.11+
- No Django runtime setup required

The tool is static-only: it reads source files, parses ASTs, and never imports your project code.

---

## Quick Start

```bash
# Analyze a project and print findings to the terminal
django-arch-check analyze /path/to/project

# Generate a self-contained HTML report
django-arch-check analyze --format html /path/to/project

# Emit machine-readable JSON for scripts and dashboards
django-arch-check analyze --format json /path/to/project > results.json

# Emit SARIF for GitHub code scanning, VS Code, and CI dashboards
django-arch-check analyze --format sarif /path/to/project > results.sarif

# Tune thresholds
django-arch-check analyze \
  --fat-model-threshold 20 \
  --god-app-threshold 40 \
  /path/to/project
```

---

## Pre-commit Integration

`django-arch-check` ships a ready-made `.pre-commit-hooks.yaml`, so teams can add it to an existing pre-commit setup with a single hook entry:

```yaml
repos:
  - repo: https://github.com/RJ-Gamer/django-arch-check
    rev: v0.9.0
    hooks:
      - id: django-arch-check
```

The bundled hook runs `django-arch-check analyze .` from the repository root and disables filename passing, which makes it work correctly for a whole-project architecture scan.

You can still pass your own CLI options from `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/RJ-Gamer/django-arch-check
    rev: v0.9.0
    hooks:
      - id: django-arch-check
        args: [--ignore-path, legacy/]
```

---

## Ignore Detectors

Use `--ignore` to skip one or more detectors entirely.

```bash
django-arch-check analyze --ignore fat_models /path/to/project
django-arch-check analyze --ignore fat_models --ignore god_apps /path/to/project
```

Valid detector names:

- `fat_models`
- `god_apps`
- `circular_imports`
- `missing_service_layer`
- `celery_tasks`
- `direct_sql`
- `n_plus_one`
- `migration_safety`
- `n1_serializer_risk`

If an invalid detector name is passed, the CLI exits with a clear error:

```text
Error: Unknown detector 'fat_modelz'. Valid detectors are: fat_models, god_apps, circular_imports, missing_service_layer, celery_tasks, direct_sql, n_plus_one, migration_safety, n1_serializer_risk
```

In HTML reports, skipped detectors are shown as:

```text
⊘ Skipped (--ignore flag)
```

---

## Ignore Paths

Use `--ignore-path` to skip files whose relative path contains a given substring.

```bash
django-arch-check analyze --ignore-path legacy/ /path/to/project
django-arch-check analyze --ignore-path legacy/ --ignore-path archive/ /path/to/project
```

This is applied across all detectors. If a file path contains the ignored string, that file is not analyzed.

Examples:

- `--ignore-path legacy/` skips files under paths like `legacy/models.py`
- `--ignore-path archive/` skips files under paths like `apps/orders/archive/tasks.py`

Path ignores are substring-based, not glob-based.

---

## CLI Options

Current `django-arch-check analyze --help` output:

```text
Usage: main analyze [OPTIONS] PROJECT_PATH

  Analyze a Django project at PROJECT_PATH for architectural issues.

Options:
  --fat-model-threshold N  Flag models with >= N non-dunder methods.  [default:
                           15]
  --god-app-threshold PCT  Flag apps owning >= PCT% of total project LOC.
                           [default: 30]
  --ignore DETECTOR        Ignore a detector by name. Repeatable.
  --ignore-path PATH       Skip files whose path contains PATH. Repeatable.
  --format [text|html|json|sarif]
                           Output format: text/html/json/sarif. HTML writes
                           arch-report.html; the others use stdout.  [default:
                           text]
  --watch                  Re-run analysis automatically on every .py file
                           change. Text format only.
  --help                   Show this message and exit.
```

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | No critical findings |
| `1` | At least one critical finding, or a CLI usage error |

This makes the tool suitable for CI gating.

---

## Watch Mode

`--watch` turns `django-arch-check` into a live feedback loop. It runs a full analysis immediately, then re-runs automatically every time a `.py` file is saved.

```bash
# Start watching the current directory
django-arch-check analyze --watch ./

# Watch with custom thresholds and ignored detectors
django-arch-check analyze --watch --fat-model-threshold 20 --ignore direct_sql ./
```

Each run prints a timestamp, health score, and a diff showing only what changed:

```text
django-arch-check v0.9.0 — watch mode
  Watching: /home/user/myproject
  Press Ctrl+C to stop.

──────────────────────────────────────────────────
[14:32:01] Analyzing: /home/user/myproject
  Score: 91/100  A · Excellent
  [full output on first run]

──────────────────────────────────────────────────
[14:32:44] Analyzing: /home/user/myproject
  Score: 87/100  B · Good
  Changed: views.py
  ✖  1 new finding(s):
    [WARNING]   [Missing Service Layer] orders/views.py

──────────────────────────────────────────────────
[14:33:10] Analyzing: /home/user/myproject
  Score: 91/100  A · Excellent
  Changed: views.py
  ✔  1 finding(s) resolved.
```

Press `Ctrl+C` to stop.

### Lower-latency watching

By default the watcher polls for file changes every second. Install `watchdog` to switch to event-based detection with sub-100ms latency:

```bash
pip install django-arch-check[watch]
django-arch-check analyze --watch ./
```

`--watch` only works with `--format text`. Using it with `html`, `json`, or `sarif` exits with a clear error.

---

## Detectors

### Fat Models

Flags Django model classes with too many non-dunder methods.

- Warning: `method_count >= threshold`
- Critical: `method_count >= threshold * 2`
- Default threshold: `15`
- Default critical boundary: `30`

Notes:

- Counts `def` and `async def`
- Ignores dunder methods like `__str__`
- Scans Python files across the project

### God Apps

Flags Django apps that own too much of the total project LOC.

- Warning: `percentage >= threshold`
- Critical: `percentage >= threshold + 20`
- Default threshold: `30%`
- Default critical boundary: `50%`

Notes:

- LOC excludes blank lines and standalone comment lines
- Requires at least 2 Django apps before it reports findings
- Uses `models.py` or `apps.py` to identify app directories

### Circular Imports

Flags cycles in the intra-project import graph.

- Critical: any detected cycle

Notes:

- Only top-level imports are analyzed
- Function-level imports are intentionally ignored
- Reports both short and multi-node cycles

### Missing Service Layer

Flags views that contain too many direct ORM calls and likely need service-layer extraction.

- Warning: 2 or more direct `Model.objects.*` calls in a single view function or method
- Critical: 4 or more direct `Model.objects.*` calls in a single view function or method

Notes:

- Scans `views.py` files only
- Supports function-based views and class-based view methods
- Uses ORM call count, not raw line count

### Celery Tasks Without Retry

Flags Celery tasks that lack retry configuration.

- Critical: task name contains `payment`, `email`, `invoice`, or `notification`, and has no retry config
- Warning: any other task with no retry config

Retry config is considered present when the decorator includes any of:

- `max_retries`
- `autoretry_for`
- `retry_backoff`

Notes:

- Detects both `@shared_task` and `@app.task`
- Skips migration files

### Direct SQL

Flags raw SQL patterns that bypass Django's ORM.

Detected patterns:

- `cursor.execute(`
- `connection.cursor()`
- `.raw(`
- `.extra(select=`

Severity:

- Warning only

Notes:

- Migration files are excluded

### N+1 Query Risks

Flags likely N+1 query patterns inside loops and list comprehensions.

- Warning: ORM call inside a loop or list comprehension, with no `select_related` or `prefetch_related` found in the same function scope

Notes:

- Scans `views.py` and `serializers.py`
- Looks for `X.objects.method(...)` patterns inside loops
- Heuristic by design; false positives and false negatives are possible


### Migration Safety

Flags migration operations that carry deployment or data-safety risk.

- Warning: `RemoveField` — field removal is irreversible
- Warning: `RenameField` — breaks code referencing the old name during rolling deploys
- Warning: `AddField` with a NOT NULL column and no `default` — fails on non-empty tables
- Warning: `RunPython` without `atomic = False` on the Migration class — long-running data migrations hold locks
- Warning: `RunSQL` — raw SQL bypasses Django's ORM safety layer

Every finding includes an advisory message explaining the risk and a safer alternative approach. To suppress a known-safe finding, add `# django-arch-check: ignore` on the operation line:

```python
(
    migrations.RemoveField(  # django-arch-check: ignore
        model_name="order",
        name="legacy_status",
    ),
)
```

Notes:

- Only scans files inside `migrations/` directories
- Skips `__init__.py`
- Does not block or fail — advises only

### N+1 Serializer Risk

Flags DRF serializer and viewset patterns that commonly trigger per-object ORM access.

- Error: ORM call inside a `SerializerMethodField` getter
- Error: nested serializer field with no matching `prefetch_related(...)` or `select_related(...)` in the paired viewset
- Error: serializer `source=` targeting a model `@property` that performs ORM work
- Warning: bare `queryset = Model.objects.all()` on a viewset paired with a relational serializer

Notes:

- Uses static AST analysis only; it does not import Django or DRF
- Includes code snippets in HTML, JSON, and SARIF output
- Treats detector-level `error` findings as critical in aggregate report badges and score summaries

---

## HTML Report

```bash
django-arch-check analyze --format html /path/to/project
```

![Sample HTML report](assets/full-report.png)

The generated `arch-report.html` is self-contained and works offline.

It includes:

- A weighted, size-aware health score from `0` to `100`
- A letter grade from `A` to `F` with a plain-language label
- Summary counts for critical and warning findings
- A per-detector score breakdown table
- One section per detector
- Accordion-style code snippets for findings that include source context
- Sticky severity filters for critical-only and warning-only views
- Skipped detector notes when `--ignore` is used
- Dark and light theme toggle with `localStorage` persistence

When you generate HTML from the CLI, it also prints the score, grade, and label in the terminal before showing the saved report path.

### Health Score

The score is weighted by detector risk and normalized by project size. Critical findings count double in the density calculation so architecture-breaking issues hurt meaningfully more than warning-level code smells. A per-detector finding cap prevents a single noisy detector from dominating the result.

Formula:

```text
critical_weight    = sum of weights for critical/error findings only
warning_weight     = sum of weights for warning findings only
normalized_density = (critical_weight * 2 + warning_weight) / ln(max(file_count, 30) + 1)
density_penalty    = min(45, round(normalized_density * 4))
absolute_penalty   = min(10, round((critical_weight + warning_weight) * 0.05))
score              = max(0, 100 - density_penalty - absolute_penalty)
```

Per-detector finding caps (findings beyond the cap do not contribute to the score):

- `direct_sql` = `8`
- `n_plus_one` = `8`
- `n1_serializer_risk` = `8`
- `migration_safety` = `10`
- `fat_models` = `6`

Default detector weights:

- `circular_imports` critical = `10`
- `celery_tasks` critical = `8`, warning = `3`
- `migration_safety` warning = `6`
- `missing_service_layer` critical = `4`, warning = `2`
- `n_plus_one` warning = `3`
- `direct_sql` warning = `2`
- `god_apps` critical = `3`, warning = `1.5`
- `fat_models` critical = `2`, warning = `1`
- `n1_serializer_risk` error = `3`, warning = `1.5`

Grades:

- `A` (`90-100`) = Excellent
- `B` (`75-89`) = Good
- `C` (`60-74`) = Needs Work
- `D` (`40-59`) = Poor
- `F` (`0-39`) = Critical

---

## JSON and SARIF Output

For automation, use machine-readable output modes:

```bash
django-arch-check analyze --format json ./ > results.json
django-arch-check analyze --format sarif ./ > results.sarif
```

### JSON

JSON output is designed for scripting, custom dashboards, and third-party integrations.

It includes:

- Tool metadata and version
- Project path and generation timestamp
- Health score and severity summary
- Per-detector findings, skip state, and normalized messages
- Code snippet payloads for detectors that provide source context

The JSON summary uses the same project-aware health score calculation as the HTML report.

### SARIF

SARIF output follows SARIF `2.1.0`, the standard format consumed by:

- GitHub Advanced Security / code scanning
- VS Code's Problems panel
- CI systems and security dashboards that ingest SARIF

Each result includes the detector rule id, severity level, message, and source location when one is available.

Both JSON and SARIF are emitted as ASCII-safe JSON so shell redirection works reliably on Windows cp1252 terminals.

---

## CI Integration

### GitHub Actions

```yaml
- name: Check Django architecture
  run: |
    pip install django-arch-check
    django-arch-check analyze ./
```

If you want to ignore legacy areas while still gating the rest of the codebase:

```yaml
- name: Check Django architecture
  run: |
    pip install django-arch-check
    django-arch-check analyze --ignore-path legacy/ ./
```

To generate an HTML report artifact:

```yaml
- name: Django architecture report
  run: |
    pip install django-arch-check
    django-arch-check analyze --format html ./

- name: Upload report
  uses: actions/upload-artifact@v4
  with:
    name: arch-report
    path: arch-report.html
```

### GitHub Code Scanning (SARIF)

```yaml
- name: Generate SARIF report
  run: |
    pip install django-arch-check
    django-arch-check analyze --format sarif ./ > results.sarif

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: results.sarif
```

---

## How It Works

`django-arch-check` analyzes source code statically.

It:

- Walks the project tree
- Skips common non-source directories like `.venv`, `node_modules`, and caches
- Parses Python files with the standard-library `ast` module
- Runs each detector independently through a central analyzer

It does not:

- Import your Django project
- Require configured settings
- Hit the database
- Execute application code

That makes it safe to run in CI, pre-commit hooks, and partially broken repos.

---

## Limitations

- Circular import detection only covers top-level imports
- `--ignore-path` uses substring matching, not glob syntax
- Missing service layer detection only scans files literally named `views.py`
- N+1 detection is heuristic and only reasons within a single function scope
- Serializer N+1 detection is heuristic and relies on class-name and field-name matching across serializers, models, and viewsets
- God app analysis requires at least 2 detectable Django apps

---

## Development

```bash
git clone https://github.com/RJ-Gamer/django-arch-check.git
cd django-arch-check

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e ".[dev]"

pytest -q --basetemp .pytest-tmp

django-arch-check analyze /path/to/project
```

### Project Structure

```text
django_arch_check/
├── __init__.py
├── cli.py
├── analyzer.py
├── report.py
└── detectors/
    ├── __init__.py
    ├── fat_models.py
    ├── god_apps.py
    ├── circular_imports.py
    ├── missing_service_layer.py
    ├── celery_tasks.py
    ├── direct_sql.py
    ├── migration_safety.py
    ├── n1_serializer_risk.py
    └── n_plus_one.py

tests/
├── conftest.py
├── test_analyzer.py
├── test_fat_models.py
├── test_god_apps.py
├── test_circular_imports.py
├── test_missing_service_layer.py
├── test_celery_tasks.py
├── test_direct_sql.py
├── test_migration_safety.py
├── test_n1_serializer_risk.py
├── test_n_plus_one.py
├── test_report.py
├── test_watcher.py
└── test_cli.py
```

### Adding a New Detector

1. Create `django_arch_check/detectors/my_detector.py`
2. Add a `detect(...)` function that returns finding dataclasses
3. Add the detector to `analyzer.py` and `AnalysisResult`
4. Add text output in `cli.py`
5. Add HTML rendering support in `report.py`
6. Add tests

---

## Contributing

Issues and pull requests are welcome.

If you are proposing a larger detector or behavior change, opening an issue first is appreciated.

---

## Version

The current release version is `0.9.0`.

---

## License

MIT. See [LICENSE](LICENSE).
