"""Deterministic fake provider script for Unit 3 R2 harness acceptance.

Never executed by harness tests (they inject a fake process runner); it names
the provider invocation contract that a fake acceptance run would use and is
the only provider-artifact fixture source for this unit. Kept outside harness
evidence logs by design (§7): scripts and credentials are not harness evidence.
"""

PROVIDER = "fake"
MODEL = "fake-v1"
RESPONSE = "ok"
