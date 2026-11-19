"""API key format and hashing.

Format: ``pf_<prefix>_<secret>``
- ``prefix``: 12 hex chars, public, stored in clear to look the key up.
- ``secret``: 32 random bytes (256 bit), url-safe base64.

Only ``sha256(full_key)`` is stored. A fast hash is appropriate here (unlike passwords)
because the key is high-entropy random data: there is no dictionary to brute-force.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

KEY_PREFIX = "pf"
_KEY_RE = re.compile(r"^pf_([0-9a-f]{12})_([A-Za-z0-9_-]{43})$")


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    plaintext: str
    prefix: str
    key_hash: str


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key() -> GeneratedKey:
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    plaintext = f"{KEY_PREFIX}_{prefix}_{secret}"
    return GeneratedKey(plaintext=plaintext, prefix=prefix, key_hash=hash_key(plaintext))


def parse_prefix(plaintext: str) -> str | None:
    match = _KEY_RE.match(plaintext)
    return match.group(1) if match else None


def hashes_match(plaintext: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_key(plaintext), stored_hash)
