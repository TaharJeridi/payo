import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.merchants.keys import generate_api_key, hash_key, hashes_match, parse_prefix
from app.merchants.models import Merchant, MerchantApiKey

# Compared against when the prefix is unknown, so both branches do the same work.
_DUMMY_HASH = hash_key("pf_000000000000_" + "0" * 43)


@dataclass(frozen=True, slots=True)
class IssuedKey:
    key_id: uuid.UUID
    merchant_id: uuid.UUID
    plaintext: str


class MerchantService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_merchant(self, name: str) -> Merchant:
        name = name.strip()
        if not name:
            raise ValueError("Merchant name must not be empty")
        merchant = Merchant(name=name)
        self._session.add(merchant)
        self._session.flush()
        return merchant

    def issue_key(self, merchant_id: uuid.UUID, label: str | None = None) -> IssuedKey:
        if self._session.get(Merchant, merchant_id) is None:
            raise ValueError(f"Merchant {merchant_id} not found")
        generated = generate_api_key()
        key = MerchantApiKey(merchant_id=merchant_id, prefix=generated.prefix, key_hash=generated.key_hash, label=label)
        self._session.add(key)
        self._session.flush()
        return IssuedKey(key_id=key.id, merchant_id=merchant_id, plaintext=generated.plaintext)

    def revoke_key(self, key_id: uuid.UUID) -> bool:
        key = self._session.get(MerchantApiKey, key_id, with_for_update=True)
        if key is None:
            raise ValueError(f"API key {key_id} not found")
        if key.revoked_at is not None:
            return False
        key.revoked_at = datetime.now(UTC)
        return True

    def list_merchants(self) -> Sequence[Merchant]:
        return self._session.execute(select(Merchant).order_by(Merchant.created_at)).scalars().all()

    def list_keys(self, merchant_id: uuid.UUID) -> Sequence[MerchantApiKey]:
        return (
            self._session.execute(
                select(MerchantApiKey)
                .where(MerchantApiKey.merchant_id == merchant_id)
                .order_by(MerchantApiKey.created_at)
            )
            .scalars()
            .all()
        )

    def authenticate(self, plaintext: str) -> Merchant | None:
        """Return the merchant owning an active key, or None. Never says *why* it failed."""
        prefix = parse_prefix(plaintext)
        row = None
        if prefix is not None:
            row = self._session.execute(
                select(MerchantApiKey, Merchant)
                .join(Merchant, Merchant.id == MerchantApiKey.merchant_id)
                .where(MerchantApiKey.prefix == prefix)
            ).first()
        stored_hash = row[0].key_hash if row is not None else _DUMMY_HASH
        valid = hashes_match(plaintext, stored_hash)
        if row is None or not valid or row[0].revoked_at is not None:
            return None
        merchant: Merchant = row[1]
        return merchant
