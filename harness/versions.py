"""Harness and artifact schema versions (design §5, §7).

The core version is release-supplied metadata, never imported from the core
package; unknown values must fail closed before any launch.
"""

HARNESS_VERSION = "0.1.0"
CONFIG_SCHEMA_VERSION = 1
CONTEXT_SCHEMA_VERSION = 1
LOG_SCHEMA_VERSION = 1
CHECKPOINT_SCHEMA_VERSION = 1
LAUNCH_SCHEMA_VERSION = 1
DISCOVERIES_SCHEMA_VERSION = 1
KNOWN_CORE_VERSIONS = frozenset({"0"})


def is_known_core_version(version):
    return version in KNOWN_CORE_VERSIONS
