"""Unit C compose/documentation contract tests (tasks 1-3, 6).

Structural checks over ``observability/docker-compose.yml`` (no YAML library in
the stdlib-only test suite): exact image pins from the official v3.114.0
deployment sources, no floating/major-only tags, only UI port 3000 published,
the exact official service set (no exporter/daemon/watcher service), env-only
credentials with non-empty guards, named downstream volumes, and readiness
checks. ``docker compose config`` validates the file when Docker is available.
Documentation checks pin the official source URLs, the research date, and the
operational contract — including the official credential coupling (S3
event-upload credentials equal the MinIO root credentials; no separate
MinIO user), honest secret-visibility wording (never a docker-inspect
secrecy claim), worker readiness limits (running, never healthy), and the
smoke procedure's partial-error/log/migration/destructive-teardown checks;
the README must point at the dedicated document.
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "observability" / "docker-compose.yml"
DOCS = REPO_ROOT / "docs" / "observability.md"
README = REPO_ROOT / "README.md"

EXPECTED_IMAGES = frozenset(
    {
        "docker.io/langfuse/langfuse:3.114.0",
        "docker.io/langfuse/langfuse-worker:3.114.0",
        "docker.io/clickhouse/clickhouse-server:24.3.18.7",
        "docker.io/minio/minio:RELEASE.2025-09-07T16-13-09Z",
        "docker.io/redis:7.2.4",
        "docker.io/postgres:17.5",
    }
)
EXPECTED_SERVICES = frozenset(
    {"langfuse-web", "langfuse-worker", "clickhouse", "minio", "redis", "postgres"}
)
SECRET_VARS = (
    "NEXTAUTH_SECRET",
    "SALT",
    "ENCRYPTION_KEY",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "CLICKHOUSE_PASSWORD",
    "REDIS_AUTH",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
)
NAMED_VOLUMES = (
    "langfuse_postgres_data",
    "langfuse_clickhouse_data",
    "langfuse_clickhouse_logs",
    "langfuse_minio_data",
    "langfuse_redis_data",
)
OFFICIAL_PLACEHOLDERS = ("miniosecret", "mysalt", "mysecret")
RELEASE_URL = "https://github.com/langfuse/langfuse/releases/tag/v3.114.0"
COMPOSE_SOURCE_URL = (
    "https://raw.githubusercontent.com/langfuse/langfuse/v3.114.0/docker-compose.yml"
)
SELFHOSTING_URL = "https://langfuse.com/docs/deployment/self-hosting"
RESEARCH_DATE = "2026-09-08"

# Test-only values so `docker compose config` can resolve the :? guards.
_CONFIG_ENV = {
    **os.environ,
    "NEXTAUTH_SECRET": "config-check-only",
    "SALT": "config-check-only",
    "ENCRYPTION_KEY": "00" * 32,
    "POSTGRES_USER": "config-check-only",
    "POSTGRES_PASSWORD": "config-check-only",
    "CLICKHOUSE_PASSWORD": "config-check-only",
    "REDIS_AUTH": "config-check-only",
    "MINIO_ROOT_USER": "config-check-only",
    "MINIO_ROOT_PASSWORD": "config-check-only",
}


def _compose_text():
    return COMPOSE.read_text(encoding="utf-8")


def _image_values(text):
    images = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("image:"):
            images.append(stripped.removeprefix("image:").strip())
    return images


def _docs_section(title):
    """Body of one ``## <title>`` documentation section (up to the next heading)."""
    return (
        DOCS.read_text(encoding="utf-8").split(f"## {title}", 1)[1].split("\n## ", 1)[0]
    )


def _service_names(text):
    """Top-level keys under ``services:`` (two-space indented ``name:``)."""
    names, in_services = set(), False
    for line in text.splitlines():
        if line.startswith("services:"):
            in_services = True
            continue
        if in_services and line and not line.startswith(" "):
            break
        if in_services and line.startswith("  ") and not line.startswith("   "):
            name = line.strip().rstrip(":")
            if name:
                names.add(name)
    return names


class TestComposePins(unittest.TestCase):
    def test_exactly_the_six_documented_exact_pins(self):
        self.assertEqual(frozenset(_image_values(_compose_text())), EXPECTED_IMAGES)

    def test_no_floating_major_only_or_untagged_images(self):
        for image in _image_values(_compose_text()):
            with self.subTest(image=image):
                self.assertIn(":", image)
                tag = image.rsplit(":", 1)[1]
                self.assertNotEqual(tag, "latest")
                self.assertNotRegex(tag, r"^\d+$")

    def test_official_placeholder_secrets_absent(self):
        text = _compose_text().lower()
        for placeholder in OFFICIAL_PLACEHOLDERS:
            self.assertNotIn(placeholder, text)


class TestComposeBoundaries(unittest.TestCase):
    def test_exact_official_service_set_no_exporter_service(self):
        self.assertEqual(_service_names(_compose_text()), EXPECTED_SERVICES)

    def test_only_ui_port_3000_published(self):
        ports = [
            line.strip()
            for line in _compose_text().splitlines()
            if line.strip().startswith("- ") and ":3" in line
        ]
        published = [item for item in ports if item.lstrip("- ").count(":") == 1]
        self.assertEqual(published, ['- "3000:3000"'])

    def test_env_only_secrets_with_non_empty_guards(self):
        text = _compose_text()
        for var in SECRET_VARS:
            with self.subTest(var=var):
                self.assertIn(f"${{{var}:?", text)

    def test_named_downstream_volumes_declared(self):
        text = _compose_text()
        for volume in NAMED_VOLUMES:
            self.assertIn(volume, text)

    def test_readiness_dependencies_and_health_checks(self):
        text = _compose_text()
        self.assertIn("service_healthy", text)
        self.assertIn("/api/public/health", text)
        for dependency in ("postgres:", "clickhouse:", "redis:", "minio:"):
            self.assertIn(dependency, text)

    def test_redis_healthcheck_authenticates_without_host_interpolation(self):
        """C3: the requirepass-protected probe authenticates from env, and the
        file only ever claims interpolation control — never secrecy."""
        text = _compose_text()
        redis_block = text.split("\n  redis:\n", 1)[1].split("\n  postgres:\n", 1)[0]
        healthcheck = redis_block.split("healthcheck:", 1)[1]
        self.assertIn("redis-cli", healthcheck)
        self.assertIn("ping", healthcheck)
        # $$ defers expansion to the container shell so the healthcheck
        # command is not host-interpolated; the password still remains
        # ordinary container configuration, readable by any
        # Docker-privileged user (docker inspect/exec).
        self.assertIn('"$$REDIS_AUTH"', healthcheck)
        self.assertNotIn("${REDIS_AUTH", healthcheck)
        self.assertIn("REDIS_AUTH: ${REDIS_AUTH:?", redis_block)

    def test_compose_never_claims_docker_inspect_secrecy(self):
        """$$ is interpolation control, not secrecy: no inspect claim here."""
        self.assertNotIn("docker inspect", _compose_text())

    def test_worker_service_defines_no_healthcheck(self):
        """Readiness honesty: the worker is expected ``running``, never healthy."""
        worker = (
            _compose_text()
            .split("\n  langfuse-worker:\n", 1)[1]
            .split("\n  langfuse-web:\n", 1)[0]
        )
        self.assertNotIn("healthcheck", worker)

    def test_s3_upload_credentials_derive_from_minio_root(self):
        """Official v3.114.0 coupling: upload credentials are the MinIO root pair."""
        text = _compose_text()
        self.assertIn(
            "LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID:"
            " ${MINIO_ROOT_USER:?set MINIO_ROOT_USER}",
            text,
        )
        self.assertIn(
            "LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY:"
            " ${MINIO_ROOT_PASSWORD:?set MINIO_ROOT_PASSWORD}",
            text,
        )
        # No duplicate credential prerequisites: the dedicated variables
        # must not exist as env guards anywhere in the file.
        self.assertNotIn("${LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID:", text)
        self.assertNotIn("${LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY:", text)

    def test_docker_compose_config_validates(self):
        if shutil.which("docker") is None:  # pragma: no cover
            self.skipTest("docker not available")
        done = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE), "config", "--quiet"],
            capture_output=True,
            text=True,
            env=_CONFIG_ENV,
            timeout=120,
            check=False,
        )
        self.assertEqual(done.returncode, 0, f"compose config failed:\n{done.stderr}")


class TestDocumentationContract(unittest.TestCase):
    def test_docs_record_every_exact_pin_and_official_source(self):
        docs = DOCS.read_text(encoding="utf-8")
        for image in EXPECTED_IMAGES:
            self.assertIn(image.rsplit(":", 1)[1], docs)
        for url in (RELEASE_URL, COMPOSE_SOURCE_URL, SELFHOSTING_URL):
            self.assertIn(url, docs)
        self.assertIn(RESEARCH_DATE, docs)

    def test_docs_record_operations_env_and_teardown(self):
        docs = DOCS.read_text(encoding="utf-8")
        self.assertIn("docker compose -f observability/docker-compose.yml up -d", docs)
        self.assertIn("down -v", docs)
        for var in (
            "NEXTAUTH_SECRET",
            "SALT",
            "ENCRYPTION_KEY",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
        ):
            self.assertIn(var, docs)
        self.assertIn("optional", docs.lower())
        self.assertIn("smoke", docs.lower())
        self.assertIn("cannot alter", docs)

    def test_docs_state_credential_coupling_without_second_user(self):
        docs = DOCS.read_text(encoding="utf-8")
        # No duplicate S3 credential prerequisites in any list or example.
        self.assertNotIn("LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID", docs)
        self.assertNotIn("LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY", docs)
        # Official coupling, stated: upload credentials equal the MinIO
        # root pair; the stack provisions no separate user.
        self.assertIn("MINIO_ROOT_USER", docs)
        self.assertIn("no separate MinIO user", docs)

    def test_docs_state_secret_visibility_honestly(self):
        docs = DOCS.read_text(encoding="utf-8")
        self.assertIn("Docker-privileged", docs)
        self.assertIn("docker inspect", docs)

    def test_docs_readiness_honest_about_worker(self):
        readiness = _docs_section("Start, validate, readiness, UI")
        # Whitespace-normalized: the honest phrases must hold across the doc's
        # line wrapping (markdown bold splits ``no healthcheck`` across lines).
        flat = " ".join(readiness.split())
        self.assertNotIn("six services healthy", flat)
        self.assertIn("no healthcheck", flat)
        self.assertIn("`running`", readiness)

    def test_smoke_procedure_checks_operational_reality(self):
        smoke = _docs_section("Optional gated real-instance smoke")
        for needle in (
            "partial",
            "errors",
            "worker",
            "logs",
            "MinIO root",
            "migration",
            "running",
            "down -v",
            "irreversible",
        ):
            self.assertIn(needle, smoke)

    def test_readme_points_at_observability_doc(self):
        self.assertIn("docs/observability.md", README.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
