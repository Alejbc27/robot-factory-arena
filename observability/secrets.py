"""Isolated secret scan for export-bound evidence.

Fixed deterministic byte patterns; any hit fails the artifact closed.
Mirrors the sealed evidence-loader secret policy without importing it.
"""

SECRET_PATTERNS = (
    b"sk-",
    b"api_key",
    b"apikey",
    b"authorization",
    b"bearer",
    b"password",
    b"secret_token",
)


def find_secret(raw):
    """Return the first matching secret pattern in raw bytes, or None."""
    lowered = raw.lower()
    for pattern in SECRET_PATTERNS:
        if pattern in lowered:
            return pattern.decode("ascii")
    return None
