# django-arch-check

![PyPI](https://img.shields.io/badge/PYPI-django--arch--check-4f8ef7?style=for-the-badge&logo=pypi&logoColor=white)
![Version](https://img.shields.io/badge/VERSION-0.3.3-4f8ef7?style=for-the-badge)
![Python](https://img.shields.io/badge/PYTHON-3.11%2B-4f8ef7?style=for-the-badge&logo=python&logoColor=white)
![License](https://img.shields.io/badge/LICENSE-MIT-yellow?style=for-the-badge)
![Status](https://img.shields.io/badge/STATUS-ACTIVE-brightgreen?style=for-the-badge)
![Detectors](https://img.shields.io/badge/DETECTORS-7-orange?style=for-the-badge)
![Tests](https://img.shields.io/badge/TESTS-156%20PASSING-brightgreen?style=for-the-badge&logo=pytest&logoColor=white)
![PRs](https://img.shields.io/badge/PRS-WELCOME-blueviolet?style=for-the-badge&logo=github)
[![Sponsor](https://img.shields.io/badge/SPONSOR-%E2%9D%A4-ea4aaa?style=for-the-badge&logo=github-sponsors)](https://github.com/sponsors/RJ-Gamer)

A command-line tool that analyzes Django projects and detects architectural problems before they become technical debt.

```
Analyzing: /home/user/myproject

── Fat Models ──────────────────────────────
  [CRITICAL]  core/models.py → UserProfile (22 methods)
  [WARNING]   orders/models.py → Order (12 methods)

  Found 2 fat model(s).

── God Apps ────────────────────────────────
  [CRITICAL]  core/ owns 67% of total project code (1,840 / 2,740 lines)

  Found 1 god app(s).

── Circular Imports ────────────────────────
  [CRITICAL]  Circular import detected: orders.models → payments.models → orders.models

  Found 1 circular import(s).

── Celery Tasks Without Retry ──────────────
  [CRITICAL]  payments/tasks.py → charge_payment() — high-stakes task, no retry configured
  [WARNING]   reports/tasks.py → generate_report() — no retry configured

  Found 2 Celery task(s) without retry.
```

---

## Why

Django projects tend to develop the same structural problems over time, regardless of team size or experience. Models accumulate methods. One app ends up owning half the codebase. Celery tasks silently drop work because nobody added retry logic. Circular imports cause mysterious `ImportError` crashes in production.

These problems are hard to spot in code review and easy to miss until they cause pain. `django-arch-check` catches them automatically, so you can fix them early — or at least make them visible.

---

## Installation

```bash
pip install django-arch-check
```

Requires Python 3.11+. No Django installation required — the tool analyzes source code statically without importing it.

---

## Usage

```bash
# Analyze a project and print results to the terminal
django-arch-check analyze /path/to/your/django/project

# Generate a self-contained HTML report instead
django-arch-check analyze --format html /path/to/your/django/project
# Saves arch-report.html in the project root

# Adjust thresholds
django-arch-check analyze \
  --fat-model-threshold 15 \
  --god-app-threshold 40 \
  /path/to/your/django/project
```

### All options

```
Options:
  --fat-model-threshold N   Flag models with >= N non-dunder methods. [default: 10]
  --god-app-threshold PCT   Flag apps owning >= PCT% of total project LOC. [default: 30]
  --format [text|html]      Output format: text (stdout) or html (arch-report.html). [default: text]
  --help                    Show this message and exit.
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | No findings, or findings are warnings only |
| `1`  | At least one `CRITICAL` finding |

The non-zero exit on critical findings makes `django-arch-check` usable as a CI gate.

---

## Detectors

### Fat Models
A model class that accumulates too many methods is doing too much. It becomes hard to test, hard to understand, and a merge-conflict magnet.

- **Warning** — model has ≥ 10 non-dunder methods (configurable via `--fat-model-threshold`)
- **Critical** — model has ≥ 20 non-dunder methods

Dunder methods (`__str__`, `__repr__`, `__init__`, etc.) are excluded from the count.

### God Apps
A Django app that owns a disproportionate share of the total project code is a structural smell. It indicates that decomposition never happened and the app became a catch-all.

- **Warning** — app owns ≥ 30% of total project LOC (configurable via `--god-app-threshold`)
- **Critical** — app owns ≥ 50% of total project LOC

Only projects with 2 or more Django apps are evaluated. Single-app projects are skipped.

### Circular Imports
Circular imports between modules cause `ImportError` crashes and indicate that module boundaries are not respected. Any cycle in the import graph is a problem.

- **Critical** — any cycle detected (no threshold; cycles are never acceptable)

Detects both 2-node (`orders → payments → orders`) and multi-node chains. Only top-level imports are analysed — function-level imports used to break cycles are intentionally excluded.

### Missing Service Layer
Business logic belongs in a service layer, not in views. Views that call the ORM directly are harder to test and mix concerns that should be separate.

- **Warning** — view function makes direct `Model.objects.*` calls
- **Critical** — view function makes ORM calls **and** has more than 10 lines of body code

Only `views.py` files are scanned.

### Celery Tasks Without Retry
Tasks without retry configuration silently drop work on transient failures — network blips, database locks, third-party rate limits. High-stakes tasks (payment processing, email sending, invoicing) are particularly dangerous without retries.

Retry configuration is considered present when any of `max_retries`, `autoretry_for`, or `retry_backoff` appears on the decorator.

- **Critical** — task name contains `payment`, `email`, `invoice`, or `notification`, and has no retry config
- **Warning** — any other task with no retry config

### Direct SQL
Raw SQL queries bypass Django's ORM, are harder to migrate across databases, and can introduce SQL injection vulnerabilities when used carelessly.

Detected patterns:
- `cursor.execute(`
- `connection.cursor()`
- `.raw(`
- `.extra(select=`

- **Warning** — always (migration files are excluded)

### N+1 Query Risk
A for-loop or list comprehension that makes ORM calls on each iteration causes N+1 queries — one query to get the list, then one per item. On large datasets this can bring a server to its knees.

- **Warning** — ORM call found inside a loop, with no `select_related` or `prefetch_related` in the same function scope

Scans `views.py` and `serializers.py` files.

---

## HTML Report

```bash
django-arch-check analyze --format html /path/to/project
```

![Sample HTML report](assets/full-report.png)

Generates `arch-report.html` in the project root — a self-contained file with no external dependencies that works offline. It includes:

- **Health score** (0–100): starts at 100, deducts 15 per critical finding and 5 per warning
- One section per detector, color-coded by severity
- A summary of total critical and warning counts

The HTML file is suitable for sharing with a team or dropping into a wiki.

---

## CI Integration

Add `django-arch-check` to your CI pipeline to prevent new architectural problems from being merged:

### GitHub Actions

```yaml
- name: Check Django architecture
  run: |
    pip install django-arch-check
    django-arch-check analyze ./
```

The command exits with code `1` if any critical findings are detected, which will fail the CI step.

To generate a report as a build artifact:

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

---

## How it works

`django-arch-check` analyzes your project **statically** — it reads and parses source files using Python's built-in `ast` module. It never imports your code, runs your application, or connects to a database. This means:

- No Django settings configuration required
- No database connection required
- Safe to run in any environment, including CI
- Works on partial or broken projects

The only runtime dependency is [Click](https://click.palletsprojects.com/) for the CLI.

---

## Limitations

- **Circular import detection** only covers top-level imports. Function-level imports (a common workaround for circular dependencies) are intentionally excluded.
- **N+1 detection** is heuristic — it checks whether `select_related` or `prefetch_related` appears anywhere in the same function scope, not whether it was called on the correct queryset. False positives and false negatives are possible.
- **Missing service layer** only scans `views.py` files — not `api.py`, `endpoints.py`, or other view-like files with different names.
- **God app** requires at least 2 Django apps to produce findings.

---

## Development

```bash
# Clone the repo
git clone https://github.com/yourusername/django-arch-check.git
cd django-arch-check

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run the tests
pytest tests/ -v

# Run against a real project
django-arch-check analyze /path/to/a/django/project
```

### Project structure

```
django_arch_check/
├── __init__.py              # version
├── cli.py                   # Click CLI — all output lives here
├── analyzer.py              # orchestrates all detectors
├── report.py                # HTML report generation and health score
└── detectors/
    ├── fat_models.py
    ├── god_apps.py
    ├── circular_imports.py
    ├── missing_service_layer.py
    ├── celery_tasks.py
    ├── direct_sql.py
    └── n_plus_one.py

tests/
├── conftest.py              # shared ProjectBuilder fixture
├── test_fat_models.py
├── test_god_apps.py
├── test_circular_imports.py
├── test_missing_service_layer.py
├── test_celery_tasks.py
├── test_direct_sql.py
├── test_n_plus_one.py
├── test_report.py
└── test_cli.py
```

### Adding a new detector

1. Create `django_arch_check/detectors/my_detector.py` with a `detect(project_path: str) -> list[MyFinding]` function
2. Add a `MyFinding` dataclass with at least a `severity` field
3. Import and call it in `analyzer.py`, adding a field to `AnalysisResult`
4. Add a `_print_my_detector()` function in `cli.py` and call it from `analyze()`
5. Add tests in `tests/test_my_detector.py`

---

## Contributing

Bug reports, feature requests, and pull requests are welcome. Please open an issue before starting work on a large change.

---

## License

MIT — see [LICENSE](LICENSE) for details.
