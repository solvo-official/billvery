import hashlib
import secrets

API_KEY_PREFIX = "iak_"


def generate_api_key() -> tuple[str, str, str]:
    """Return (full key, display prefix, SHA-256 hex). Only the prefix and hash are stored.

    A fast hash is correct here: the key carries 256 bits of randomness, so it cannot be
    brute-forced, and a deterministic hash lets authentication use an index lookup.
    """
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:12], hash_api_key(key)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
