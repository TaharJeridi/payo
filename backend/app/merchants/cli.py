"""Merchant administration from the command line (no HTTP admin API on purpose).

    python -m app.merchants.cli create "Acme Srl"
    python -m app.merchants.cli issue-key <merchant_id> [--label ci]
    python -m app.merchants.cli revoke-key <key_id>
    python -m app.merchants.cli list

The plaintext API key is printed exactly once and is not recoverable afterwards.
"""

import argparse
import sys
import uuid
from collections.abc import Sequence

from app.db.session import get_session_factory
from app.merchants.service import MerchantService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.merchants.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create a merchant and its first API key")
    create.add_argument("name")
    issue = sub.add_parser("issue-key", help="issue an additional API key")
    issue.add_argument("merchant_id", type=uuid.UUID)
    issue.add_argument("--label")
    revoke = sub.add_parser("revoke-key", help="revoke an API key (cannot be undone)")
    revoke.add_argument("key_id", type=uuid.UUID)
    sub.add_parser("list", help="list merchants and their keys (never the secrets)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with get_session_factory()() as session, session.begin():
        service = MerchantService(session)
        try:
            if args.command == "create":
                merchant = service.create_merchant(args.name)
                issued = service.issue_key(merchant.id, label="initial")
                print(f"merchant_id={merchant.id}")
                print(f"key_id={issued.key_id}")
                print(f"api_key={issued.plaintext}")
                print("Store the api_key now: it is not saved anywhere in clear text.", file=sys.stderr)
            elif args.command == "issue-key":
                issued = service.issue_key(args.merchant_id, label=args.label)
                print(f"key_id={issued.key_id}")
                print(f"api_key={issued.plaintext}")
                print("Store the api_key now: it is not saved anywhere in clear text.", file=sys.stderr)
            elif args.command == "revoke-key":
                changed = service.revoke_key(args.key_id)
                print("revoked" if changed else "already revoked")
            else:
                for merchant in service.list_merchants():
                    print(f"{merchant.id}  {merchant.name}")
                    for key in service.list_keys(merchant.id):
                        state = "revoked" if key.revoked_at else "active"
                        print(f"    key {key.id}  pf_{key.prefix}_…  {state}  {key.label or ''}")
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
