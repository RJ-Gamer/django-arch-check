"""Tests for the secret leakage detector."""

from __future__ import annotations

import textwrap

import pytest

from django_arch_check.detectors.secret_leakage import detect
from tests.conftest import ProjectBuilder


# ---------------------------------------------------------------------------
# Hardcoded secrets
# ---------------------------------------------------------------------------


class TestHardcodedSecrets:
    @pytest.mark.parametrize(
        "var_name",
        [
            "SECRET_KEY",
            "API_KEY",
            "APIKEY",
            "AUTH_TOKEN",
            "ACCESS_TOKEN",
            "PRIVATE_KEY",
            "PASSWORD",
            "PASSWD",
            "PWD",
            "CREDENTIALS",
            "CLIENT_SECRET",
            "DB_PASS",
            "DATABASE_PASSWORD",
            "SMTP_PASS",
            "JWT_SECRET",
            "ENCRYPTION_KEY",
            "SIGNING_KEY",
            "WEBHOOK_SECRET",
            "STRIPE_KEY",
            "TWILIO_TOKEN",
            "SENDGRID_KEY",
            "AWS_SECRET",
        ],
    )
    def test_secret_variable_names_flagged(
        self, proj: ProjectBuilder, var_name: str
    ) -> None:
        proj.write("app/settings.py", f'{var_name} = "real-secret-value-abc123"\n')
        findings = detect(proj.path)
        assert len(findings) == 1
        assert findings[0].kind == "hardcoded_secret"
        assert findings[0].detail == var_name
        assert findings[0].severity == "critical"

    def test_hardcoded_secret_line_number_accurate(self, proj: ProjectBuilder) -> None:
        source = textwrap.dedent("""\
            # config
            DEBUG = False
            SECRET_KEY = "my-super-secret-key"
            ALLOWED_HOSTS = ["*"]
        """)
        proj.write("app/settings.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert len(findings) == 1
        assert findings[0].line_number == 3

    def test_empty_string_not_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("app/settings.py", 'SECRET_KEY = ""\n')
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_placeholder_not_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("app/settings.py", 'SECRET_KEY = "<your-secret-key-here>"\n')
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_env_var_reference_not_flagged(self, proj: ProjectBuilder) -> None:
        proj.write(
            "app/settings.py",
            'import os\nSECRET_KEY = os.environ.get("SECRET_KEY")\n',
        )
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_non_string_value_not_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("app/settings.py", "SECRET_KEY = None\n")
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_annotated_assignment_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("app/config.py", 'API_KEY: str = "sk-live-abc123"\n')
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert len(findings) == 1
        assert findings[0].detail == "API_KEY"

    def test_multiple_secrets_in_same_file(self, proj: ProjectBuilder) -> None:
        source = textwrap.dedent("""\
            SECRET_KEY = "abc123"
            API_KEY = "sk-live-xyz"
            DEBUG = False
        """)
        proj.write("app/settings.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert len(findings) == 2
        details = {f.detail for f in findings}
        assert details == {"SECRET_KEY", "API_KEY"}

    def test_file_path_is_relative(self, proj: ProjectBuilder) -> None:
        proj.write("myapp/settings.py", 'SECRET_KEY = "abc123"\n')
        findings = detect(proj.path)
        assert len(findings) >= 1
        assert not findings[0].file_path.startswith("/")
        assert "myapp" in findings[0].file_path


# ---------------------------------------------------------------------------
# DEBUG = True
# ---------------------------------------------------------------------------


class TestDebugTrue:
    def test_debug_true_in_settings_is_critical(self, proj: ProjectBuilder) -> None:
        proj.write("app/settings.py", "DEBUG = True\n")
        findings = [f for f in detect(proj.path) if f.kind == "debug_true"]
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert findings[0].detail == "DEBUG = True"

    def test_debug_true_in_non_settings_is_warning(self, proj: ProjectBuilder) -> None:
        proj.write("app/views.py", "DEBUG = True\n")
        findings = [f for f in detect(proj.path) if f.kind == "debug_true"]
        assert len(findings) == 1
        assert findings[0].severity == "warning"

    def test_debug_false_not_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("app/settings.py", "DEBUG = False\n")
        findings = [f for f in detect(proj.path) if f.kind == "debug_true"]
        assert findings == []

    def test_debug_true_line_number_accurate(self, proj: ProjectBuilder) -> None:
        source = textwrap.dedent("""\
            ALLOWED_HOSTS = ["*"]
            DATABASES = {}
            DEBUG = True
        """)
        proj.write("app/settings.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "debug_true"]
        assert len(findings) == 1
        assert findings[0].line_number == 3

    @pytest.mark.parametrize(
        "filename",
        ["settings.py", "settings_dev.py", "settings_local.py", "dev_settings.py"],
    )
    def test_settings_filename_variants_are_critical(
        self, proj: ProjectBuilder, filename: str
    ) -> None:
        proj.write(f"app/{filename}", "DEBUG = True\n")
        findings = [f for f in detect(proj.path) if f.kind == "debug_true"]
        assert len(findings) == 1
        assert findings[0].severity == "critical"


# ---------------------------------------------------------------------------
# Logged secrets
# ---------------------------------------------------------------------------


class TestLoggedSecrets:
    @pytest.mark.parametrize(
        "log_call",
        [
            "logger.debug(password)",
            "logger.info(api_key)",
            "logger.warning(secret_key)",
            "logger.error(auth_token)",
            "logger.critical(private_key)",
            "logger.exception(credentials)",
            "print(password)",
            "print(api_key)",
        ],
    )
    def test_logging_calls_with_secret_vars_flagged(
        self, proj: ProjectBuilder, log_call: str
    ) -> None:
        source = textwrap.dedent(f"""\
            import logging
            logger = logging.getLogger(__name__)
            def fn():
                {log_call}
        """)
        proj.write("app/views.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "logged_secret"]
        assert len(findings) == 1
        assert findings[0].severity == "warning"

    def test_logged_secret_line_number_accurate(self, proj: ProjectBuilder) -> None:
        source = textwrap.dedent("""\
            import logging
            logger = logging.getLogger(__name__)
            def send():
                user = get_user()
                logger.info(api_key)
        """)
        proj.write("app/views.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "logged_secret"]
        assert len(findings) == 1
        assert findings[0].line_number == 5

    def test_logged_secret_detail_is_variable_name(
        self, proj: ProjectBuilder
    ) -> None:
        proj.write(
            "app/views.py",
            "import logging\nlogger = logging.getLogger(__name__)\ndef f():\n    logger.info(api_key)\n",
        )
        findings = [f for f in detect(proj.path) if f.kind == "logged_secret"]
        assert findings[0].detail == "api_key"

    def test_fstring_with_secret_var_flagged(self, proj: ProjectBuilder) -> None:
        source = textwrap.dedent("""\
            import logging
            logger = logging.getLogger(__name__)
            def fn():
                logger.info(f"key={api_key}")
        """)
        proj.write("app/views.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "logged_secret"]
        assert len(findings) == 1

    def test_attribute_access_secret_flagged(self, proj: ProjectBuilder) -> None:
        source = textwrap.dedent("""\
            import logging
            logger = logging.getLogger(__name__)
            def fn():
                logger.info(config.api_key)
        """)
        proj.write("app/views.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "logged_secret"]
        assert len(findings) == 1
        assert findings[0].detail == "api_key"

    def test_logging_non_secret_var_not_flagged(self, proj: ProjectBuilder) -> None:
        source = textwrap.dedent("""\
            import logging
            logger = logging.getLogger(__name__)
            def fn():
                logger.info(user_email)
                logger.debug(request_id)
                print(response_body)
        """)
        proj.write("app/views.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "logged_secret"]
        assert findings == []

    def test_one_finding_per_log_call_site(self, proj: ProjectBuilder) -> None:
        """A single log call referencing a secret produces exactly one finding."""
        source = textwrap.dedent("""\
            import logging
            logger = logging.getLogger(__name__)
            def fn():
                logger.info(api_key)
                logger.info(password)
        """)
        proj.write("app/views.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "logged_secret"]
        assert len(findings) == 2

    def test_print_secret_flagged(self, proj: ProjectBuilder) -> None:
        proj.write("app/debug_utils.py", "def dump():\n    print(secret_key)\n")
        findings = [f for f in detect(proj.path) if f.kind == "logged_secret"]
        assert len(findings) == 1
        assert findings[0].detail == "secret_key"


# ---------------------------------------------------------------------------
# Skip dirs and non-py files
# ---------------------------------------------------------------------------


class TestSkipBehaviour:
    def test_venv_not_scanned(self, proj: ProjectBuilder) -> None:
        proj.write(".venv/lib/site.py", 'SECRET_KEY = "abc123"\nDEBUG = True\n')
        assert detect(proj.path) == []

    def test_node_modules_not_scanned(self, proj: ProjectBuilder) -> None:
        proj.write("node_modules/pkg/index.py", 'API_KEY = "abc123"\n')
        assert detect(proj.path) == []

    def test_non_py_file_not_scanned(self, proj: ProjectBuilder) -> None:
        proj.write("app/config.env", 'SECRET_KEY="abc123"\n')
        assert detect(proj.path) == []

    def test_ignore_path_respected(self, proj: ProjectBuilder) -> None:
        proj.write("legacy/settings.py", 'SECRET_KEY = "abc123"\n')
        proj.write("live/settings.py", 'SECRET_KEY = "xyz789"\n')
        findings = detect(proj.path, ignore_paths=("legacy/",))
        paths = {f.file_path for f in findings}
        assert not any("legacy" in p for p in paths)
        assert any("live" in p for p in paths)

    def test_syntax_error_file_skipped_gracefully(
        self, proj: ProjectBuilder
    ) -> None:
        proj.write("app/broken.py", "def (:\n    SECRET_KEY = 'abc'\n")
        # Should not raise; broken file is silently skipped
        findings = detect(proj.path)
        assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# Combined / integration
# ---------------------------------------------------------------------------


class TestCombined:
    def test_all_three_kinds_detected_in_one_file(
        self, proj: ProjectBuilder
    ) -> None:
        source = textwrap.dedent("""\
            import logging
            logger = logging.getLogger(__name__)
            SECRET_KEY = "super-secret-abc123"
            DEBUG = True
            def send():
                logger.info(api_key)
        """)
        proj.write("app/settings.py", source)
        findings = detect(proj.path)
        kinds = {f.kind for f in findings}
        assert kinds == {"hardcoded_secret", "debug_true", "logged_secret"}

    def test_clean_file_produces_no_findings(self, proj: ProjectBuilder) -> None:
        source = textwrap.dedent("""\
            import os
            SECRET_KEY = os.environ["SECRET_KEY"]
            DEBUG = False
            def send():
                logger.info(user_id)
        """)
        proj.write("app/settings.py", source)
        assert detect(proj.path) == []

    def test_findings_across_multiple_files(self, proj: ProjectBuilder) -> None:
        proj.write("app/settings.py", 'SECRET_KEY = "abc123"\nDEBUG = True\n')
        proj.write(
            "app/views.py",
            "import logging\nlogger = logging.getLogger(__name__)\ndef f():\n    logger.info(password)\n",
        )
        findings = detect(proj.path)
        files = {f.file_path for f in findings}
        assert len(files) == 2


# ---------------------------------------------------------------------------
# Value heuristics (false-positive suppression)
# ---------------------------------------------------------------------------


class TestValueHeuristics:
    def test_sentence_with_spaces_not_flagged(self, proj: ProjectBuilder) -> None:
        """PASSWORD_ERROR = 'Username or password is incorrect' has 4 spaces."""
        proj.write(
            "app/messages.py",
            'PASSWORD_ERROR = "Username or password is incorrect"\n',
        )
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_sentence_ending_with_period_not_flagged(self, proj: ProjectBuilder) -> None:
        proj.write(
            "app/messages.py",
            'PASSWORD_HELP = "Your password must be at least 8 characters."\n',
        )
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_sentence_ending_with_exclamation_not_flagged(self, proj: ProjectBuilder) -> None:
        proj.write(
            "app/messages.py",
            'PASSWORD_SUCCESS = "Password changed successfully!"\n',
        )
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_message_word_triggers_suppression(self, proj: ProjectBuilder) -> None:
        """Contains 'invalid' -> suppressed by message-word heuristic."""
        proj.write("app/errors.py", 'PASSWORD_INVALID = "invalid"\n')
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_long_string_not_flagged(self, proj: ProjectBuilder) -> None:
        long_msg = "x" * 101
        proj.write("app/messages.py", f'SECRET_HELP = "{long_msg}"\n')
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_short_token_without_spaces_still_flagged(self, proj: ProjectBuilder) -> None:
        """A short random-looking value with no spaces must still be flagged."""
        proj.write("app/settings.py", 'SECRET_KEY = "xK9mP2qR"\n')
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert len(findings) == 1

    def test_two_word_value_still_flagged(self, proj: ProjectBuilder) -> None:
        """Two words (1 space) is below the 3-space threshold -> still flagged."""
        proj.write("app/settings.py", 'API_KEY = "live secret"\n')
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert len(findings) == 1

    def test_required_word_suppresses(self, proj: ProjectBuilder) -> None:
        proj.write("app/validators.py", 'PASSWORD_REQUIRED = "required"\n')
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_incorrect_word_suppresses(self, proj: ProjectBuilder) -> None:
        proj.write("app/validators.py", 'PASSWORD_MSG = "incorrect"\n')
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []


# ---------------------------------------------------------------------------
# Inline suppress comment
# ---------------------------------------------------------------------------


class TestInlineSuppressComment:
    def test_ignore_comment_suppresses_hardcoded_secret(self, proj: ProjectBuilder) -> None:
        proj.write(
            "app/constants.py",
            'PASSWORD_MIN_MSG = "too short"  # django-arch-check: ignore\n',
        )
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_ignore_comment_suppresses_debug_true(self, proj: ProjectBuilder) -> None:
        proj.write(
            "app/settings_test.py",
            "DEBUG = True  # django-arch-check: ignore\n",
        )
        findings = [f for f in detect(proj.path) if f.kind == "debug_true"]
        assert findings == []

    def test_ignore_comment_only_suppresses_its_own_line(self, proj: ProjectBuilder) -> None:
        """The comment on line 1 must not suppress the finding on line 2."""
        source = (
            'PASSWORD_RESET_MSG = "Reset link sent."  # django-arch-check: ignore\n'
            'SECRET_KEY = "real-secret-abc123"\n'
        )
        proj.write("app/settings.py", source)
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert len(findings) == 1
        assert findings[0].line_number == 2

    def test_ignore_comment_on_real_secret_suppresses_it(self, proj: ProjectBuilder) -> None:
        """Developer can suppress a genuine finding they know is safe."""
        proj.write(
            "app/settings.py",
            'SECRET_KEY = "real-abc123"  # django-arch-check: ignore\n',
        )
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []

    def test_without_ignore_comment_sentence_suppressed_by_heuristic(
        self, proj: ProjectBuilder
    ) -> None:
        """Heuristic alone suppresses without needing the comment."""
        proj.write(
            "app/messages.py",
            'PASSWORD_ERROR = "Username or password is incorrect"\n',
        )
        findings = [f for f in detect(proj.path) if f.kind == "hardcoded_secret"]
        assert findings == []
