"""Tests for the fat-model detector."""

from __future__ import annotations

import textwrap

from django_arch_check.detectors.fat_models import detect
from tests.conftest import ProjectBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _model(class_name: str, base: str, methods: list[str], extra: str = "") -> str:
    """Build a minimal models.py source string."""
    lines = [
        "from django.db import models",
        "",
        f"class {class_name}({base}):",
        "    name = models.CharField(max_length=100)",
    ]
    for m in methods:
        lines.append(f"    def {m}(self): pass")
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Severity thresholds (default threshold=10)
# ---------------------------------------------------------------------------

def test_critical_model_thirty_or_more_methods(proj: ProjectBuilder) -> None:
    """Model with >= 30 non-dunder methods → critical (threshold=15, critical=15*2=30)."""
    methods = [f"method_{i}" for i in range(30)]
    proj.write("app/models.py", _model("BigModel", "models.Model", methods))
    findings = detect(proj.path)
    assert len(findings) == 1
    f = findings[0]
    assert f.class_name == "BigModel"
    assert f.method_count == 30
    assert f.severity == "critical"


def test_warning_model_between_threshold_and_critical(proj: ProjectBuilder) -> None:
    """Model with 15-29 methods → warning (new default threshold is 15)."""
    methods = [f"do_{i}" for i in range(17)]
    proj.write("app/models.py", _model("MediumModel", "models.Model", methods))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].method_count == 17


def test_clean_model_under_threshold(proj: ProjectBuilder) -> None:
    """Model with < 10 non-dunder methods → no finding."""
    methods = [f"helper_{i}" for i in range(5)]
    proj.write("app/models.py", _model("TinyModel", "models.Model", methods))
    assert detect(proj.path) == []


def test_dunder_methods_not_counted(proj: ProjectBuilder) -> None:
    """__str__, __repr__, __init__ etc. must not be counted."""
    dunders = ["__str__", "__repr__", "__eq__", "__hash__", "__init__",
               "__lt__", "__gt__", "__le__", "__ge__", "__ne__",
               "__contains__", "__len__"]  # 12 dunders
    regular = ["get_name", "save_clean"]  # 2 regular — well under threshold
    all_methods = dunders + regular
    proj.write("app/models.py", _model("DunderModel", "models.Model", all_methods))
    assert detect(proj.path) == []


def test_async_methods_are_counted(proj: ProjectBuilder) -> None:
    """async def methods count toward the threshold."""
    sync_methods  = [f"sync_m{i}" for i in range(5)]
    async_methods = [f"    async def async_m{i}(self): pass" for i in range(15)]
    source = textwrap.dedent("""\
        from django.db import models

        class AsyncModel(models.Model):
            pass
    """)
    source += "\n".join(f"    def sync_m{i}(self): pass" for i in range(5))
    source += "\n" + "\n".join(async_methods)
    proj.write("app/models.py", source)
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].method_count >= 15  # at minimum the async ones


def test_non_model_class_ignored(proj: ProjectBuilder) -> None:
    """A class not inheriting from *Model must not be flagged."""
    methods = [f"fn_{i}" for i in range(25)]
    proj.write("app/utils.py", _model("BigHelper", "object", methods))
    assert detect(proj.path) == []


def test_attribute_style_base_class(proj: ProjectBuilder) -> None:
    """``models.Model`` attribute-access form must be detected."""
    methods = [f"m_{i}" for i in range(20)]
    proj.write("app/models.py", _model("AttrModel", "models.Model", methods))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert findings[0].class_name == "AttrModel"


def test_custom_base_containing_model_word(proj: ProjectBuilder) -> None:
    """Classes like ``AbstractModel`` or ``TimeStampedModel`` are also matched."""
    methods = [f"op_{i}" for i in range(20)]
    proj.write("app/models.py", _model("UserProfile", "AbstractModel", methods))
    findings = detect(proj.path)
    assert len(findings) == 1


def test_custom_threshold(proj: ProjectBuilder) -> None:
    """A custom threshold of 5 should flag a 6-method model as warning."""
    methods = [f"fn_{i}" for i in range(6)]
    proj.write("app/models.py", _model("SmallModel", "models.Model", methods))
    # Default threshold=10 → clean
    assert detect(proj.path, threshold=10) == []
    # Custom threshold=5 → warning (6 >= 5 but < 10)
    findings = detect(proj.path, threshold=5)
    assert len(findings) == 1
    assert findings[0].severity == "warning"


def test_relative_file_path_in_finding(proj: ProjectBuilder) -> None:
    """file_path must be relative to the project root, not absolute."""
    methods = [f"m_{i}" for i in range(20)]
    proj.write("myapp/models.py", _model("BigModel", "models.Model", methods))
    findings = detect(proj.path)
    assert len(findings) == 1
    assert not findings[0].file_path.startswith("/")
    assert "myapp" in findings[0].file_path


def test_multiple_models_in_one_file(proj: ProjectBuilder) -> None:
    """Multiple fat models in the same file each produce a separate finding."""
    source = textwrap.dedent("""\
        from django.db import models

        class FatA(models.Model):
    """) + "\n".join(f"    def m{i}(self): pass" for i in range(20))
    source += textwrap.dedent("""

        class FatB(models.Model):
    """) + "\n".join(f"    def n{i}(self): pass" for i in range(15))
    proj.write("app/models.py", source)
    findings = detect(proj.path)
    names = {f.class_name for f in findings}
    assert names == {"FatA", "FatB"}


def test_skip_dirs_not_traversed(proj: ProjectBuilder) -> None:
    """Models inside .venv or __pycache__ must not be scanned."""
    methods = [f"m_{i}" for i in range(20)]
    proj.write(".venv/lib/myapp/models.py", _model("VenvModel", "models.Model", methods))
    proj.write("__pycache__/models.py",     _model("CacheModel", "models.Model", methods))
    assert detect(proj.path) == []


def test_syntax_error_file_skipped(proj: ProjectBuilder) -> None:
    """A file with a SyntaxError must be silently skipped."""
    proj.write("app/models.py", "this is not valid python )(][")
    assert detect(proj.path) == []
    