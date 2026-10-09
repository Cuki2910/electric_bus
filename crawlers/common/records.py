"""Author-handle hashing helpers for the Facebook crawler."""

import hashlib
import os
import unicodedata

try:
    # Load FB_HANDLE_SALT from .env without overriding an exported value.
    #
    # The path is resolved explicitly from this file's location (repo root is
    # two levels up from crawlers/common/) rather than left to load_dotenv()'s
    # default stack-frame search, which finds the wrong location depending on
    # how Python was invoked (e.g. `python -c`, a different cwd) and fails
    # silently rather than raising.
    from dotenv import load_dotenv

    _ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
    load_dotenv(_ENV_PATH)
except ImportError:  # pragma: no cover - keeps stdlib-only unit tests importable
    pass


def salted_hash(value, salt):
    """``sha1(salt + normalized(value))`` — pseudonymizes author handles.

    The salt lives in an environment variable and is never committed, so the
    hashes cannot be reversed by dictionary attack from the repo alone. Same
    handle always maps to the same hash, which keeps repeat-commenter grouping
    and spam detection working.
    """
    if not salt:
        raise ValueError("salted_hash requires a non-empty salt")
    normalized = unicodedata.normalize("NFC", str(value or "")).strip().casefold()
    return hashlib.sha1((salt + normalized).encode("utf-8")).hexdigest()


def require_env_salt(env_name):
    """Read a required salt from the environment with an actionable error."""
    salt = os.environ.get(env_name, "").strip()
    if not salt:
        raise RuntimeError(
            f"{env_name} is not set. Add it to .env (gitignored) or export it; "
            "it salts the author hashes and must stay out of the repo."
        )
    return salt
