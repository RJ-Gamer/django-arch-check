# django-arch-check

A CLI tool that analyzes Django projects and detects architectural problems such as fat models, god apps, circular imports, missing service layers, and more.

## Installation

```bash
pip install django-arch-check
```

## Usage

```bash
django-arch-check analyze /path/to/your/django/project
```

## Development

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run the CLI
django-arch-check analyze /path/to/project
```

## License

MIT