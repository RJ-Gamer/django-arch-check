"""Secret leakage detector.

Scans Python source files for three categories of secret exposure risk:

1. Hardcoded secrets -- string literals assigned to names that suggest
   credentials (SECRET_KEY, API_KEY, PASSWORD, TOKEN, etc.).

2. Logged secrets -- logging calls (logger.info, print, etc.) whose
   arguments reference variable names that suggest sensitive data.

3. DEBUG = True -- Django's DEBUG flag left enabled, which causes full
   tracebacks and settings values to be exposed in HTTP error responses.

False-positive suppression
--------------------------
Two layers prevent noise:

a) Value heuristics -- a string is skipped when it looks like a human-readable
   message rather than a credential:
   - 3 or more spaces  ("Username or password is incorrect")
   - ends with sentence punctuation  (".", "!", "?", "...")
   - longer than 100 characters
   - contains common message/validation words (invalid, incorrect, required, ...)

b) Inline suppress comment -- add ``# django-arch-check: ignore`` on the
   assignment line to unconditionally skip it:

       PASSWORD_RESET_MSG = "Check your email."  # django-arch-check: ignore

Severity:
    - critical: hardcoded secret or DEBUG = True in settings files
    - warning:  logged secret reference or DEBUG = True in non-settings files
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from typing import Literal

from django_arch_check.detectors import filter_dirnames, should_ignore_file

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretLeakageFinding:
    """A single secret-leakage finding."""

    file_path: str
    line_number: int
    kind: str          # "hardcoded_secret" | "logged_secret" | "debug_true"
    detail: str        # human-readable description, e.g. variable name / pattern
    severity: Literal["critical", "warning"]


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn", ".tox", ".venv", "venv", "env", ".env",
        "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache",
        ".pytest_cache", "htmlcov", "dist", "build", ".eggs",
    }
)

# Variable-name fragments that suggest a secret value.
_SECRET_NAME_RE = re.compile(
    r"(secret|api[_\-]?key|apikey|auth[_\-]?token|access[_\-]?token|"
    r"private[_\-]?key|password|passwd|pwd|credentials?|client[_\-]?secret|"
    r"db[_\-]?pass|database[_\-]?password|smtp[_\-]?pass|jwt[_\-]?secret|"
    r"encryption[_\-]?key|signing[_\-]?key|webhook[_\-]?secret|"
    r"stripe[_\-]?key|twilio[_\-]?token|sendgrid[_\-]?key|aws[_\-]?secret)",
    re.IGNORECASE,
)

# Logging / output call names that could expose values.
_LOG_CALL_NAMES: frozenset[str] = frozenset(
    {
        "print",
        "debug", "info", "warning", "error", "critical", "exception",
        "log", "msg", "write",
    }
)

# Settings file name patterns.
_SETTINGS_FILE_RE = re.compile(r"settings.*\.py$", re.IGNORECASE)

# Words that strongly suggest a human-readable message rather than a credential.
# Deliberately narrow: only words that are unambiguously message/validation copy
# and would never appear in a real token or key.
_MESSAGE_WORDS_RE = re.compile(
    r"\b(invalid|incorrect|required|must be|cannot|already exists|"
    r"does not|please|provide|missing|forbidden|"
    r"unauthorized|confirm|characters?|digits?|letters?|username)",
    re.IGNORECASE,
)

# Sentence-ending punctuation that signals a human-readable string.
_SENTENCE_ENDINGS: tuple[str, ...] = (".", "!", "?", "\u2026", "...")

# Maximum length a real credential is expected to be.
_MAX_CREDENTIAL_LENGTH = 100

# Minimum spaces in a string before it is treated as a sentence.
_MIN_SPACES_FOR_SENTENCE = 3


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_non_empty_string(node: ast.expr) -> bool:
    """Return True if *node* is a non-empty, non-placeholder string literal."""
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    val = node.value.strip()
    if not val or val.startswith("<") or val in {"...", "CHANGE_ME", "TODO", "FIXME"}:
        return False
    if val.startswith("$") or val.startswith("%"):
        return False
    return True


def _looks_like_message(value: str) -> bool:
    """Return True when *value* looks like a human-readable message, not a credential.

    Any one of the following is sufficient to suppress the finding:
    - 3 or more spaces  -> likely a sentence
    - ends with sentence punctuation  -> likely a sentence
    - longer than _MAX_CREDENTIAL_LENGTH chars  -> too long to be a token
    - contains common message/validation words  -> UI copy, not a secret
    """
    if len(value) > _MAX_CREDENTIAL_LENGTH:
        return True
    if value.count(" ") >= _MIN_SPACES_FOR_SENTENCE:
        return True
    if value.rstrip().endswith(_SENTENCE_ENDINGS):
        return True
    if _MESSAGE_WORDS_RE.search(value):
        return True
    return False


def _has_ignore_comment(source_lines: list[str], lineno: int) -> bool:
    """Return True when the source line carries a suppress comment.

    *lineno* is 1-based (as reported by the AST).
    """
    idx = lineno - 1
    if 0 <= idx < len(source_lines):
        return "# django-arch-check: ignore" in source_lines[idx]
    return False


def _assignment_targets_name(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """Return all simple target names from an assignment node."""
    names: list[str] = []
    if isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name):
            names.append(node.target.id)
    else:
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Attribute):
                names.append(target.attr)
    return names


def _call_func_name(call: ast.Call) -> str:
    """Return the bare function/method name of a Call node."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _node_references_secret(node: ast.expr) -> str | None:
    """Return the secret-like name if *node* references one, else None."""
    if isinstance(node, ast.Name) and _SECRET_NAME_RE.search(node.id):
        return node.id
    if isinstance(node, ast.Attribute) and _SECRET_NAME_RE.search(node.attr):
        return node.attr
    if isinstance(node, ast.JoinedStr):
        for part in ast.walk(node):
            if isinstance(part, ast.Name) and _SECRET_NAME_RE.search(part.id):
                return part.id
            if isinstance(part, ast.Attribute) and _SECRET_NAME_RE.search(part.attr):
                return part.attr
    return None


# ---------------------------------------------------------------------------
# Per-file scanning
# ---------------------------------------------------------------------------


def _scan_file(full_path: str, rel_path: str) -> list[SecretLeakageFinding]:
    findings: list[SecretLeakageFinding] = []
    is_settings = bool(_SETTINGS_FILE_RE.search(os.path.basename(rel_path)))

    try:
        source = open(full_path, encoding="utf-8").read()
        tree = ast.parse(source, filename=full_path)
    except (OSError, UnicodeDecodeError, SyntaxError):
        return findings

    source_lines = source.splitlines()

    for node in ast.walk(tree):
        # -- 1. Hardcoded secrets & DEBUG = True ------------------------------
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None:
                continue
            # Inline suppress comment skips the entire assignment.
            if _has_ignore_comment(source_lines, node.lineno):
                continue
            for name in _assignment_targets_name(node):
                if name == "DEBUG":
                    if isinstance(value, ast.Constant) and value.value is True:
                        findings.append(SecretLeakageFinding(
                            file_path=rel_path,
                            line_number=node.lineno,
                            kind="debug_true",
                            detail="DEBUG = True",
                            severity="critical" if is_settings else "warning",
                        ))
                elif _SECRET_NAME_RE.search(name) and _is_non_empty_string(value):
                    # Value heuristic: skip strings that look like UI messages.
                    assert isinstance(value, ast.Constant)
                    if _looks_like_message(value.value):
                        continue
                    findings.append(SecretLeakageFinding(
                        file_path=rel_path,
                        line_number=node.lineno,
                        kind="hardcoded_secret",
                        detail=name,
                        severity="critical",
                    ))

        # -- 2. Logged secrets ------------------------------------------------
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if _call_func_name(call) in _LOG_CALL_NAMES:
                all_args: list[ast.expr] = list(call.args) + [
                    kw.value for kw in call.keywords
                ]
                for arg in all_args:
                    secret_name = _node_references_secret(arg)
                    if secret_name:
                        findings.append(SecretLeakageFinding(
                            file_path=rel_path,
                            line_number=node.lineno,
                            kind="logged_secret",
                            detail=secret_name,
                            severity="warning",
                        ))
                        break  # one finding per call site

    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(
    project_path: str,
    ignore_paths: tuple[str, ...] = (),
) -> list[SecretLeakageFinding]:
    """Walk *project_path* and return all secret-leakage findings."""
    findings: list[SecretLeakageFinding] = []

    for dirpath, dirnames, filenames in os.walk(project_path):
        filter_dirnames(project_path, dirpath, dirnames, _SKIP_DIRS, ignore_paths)

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, project_path)
            if should_ignore_file(rel_path, ignore_paths):
                continue

            findings.extend(_scan_file(full_path, rel_path))

    return findings
