"""Operator commands: `invoice-auditor <command> --help`."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from .config import Settings, get_settings
from .db import Database
from .enums import ApiScope, UserRole
from .models import ApiKey, Organization, TaxRateLimit, User
from .security import generate_api_key

MIME_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


class CommandError(Exception):
    pass


def alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", "invoice_auditor:migrations")
    if database_url:
        config.attributes["database_url"] = database_url
    return config


def _upper_code(length: int):
    def parse(value: str) -> str:
        code = value.strip().upper()
        if len(code) != length or not code.isalpha():
            raise argparse.ArgumentTypeError(f"expected a {length}-letter code")
        return code

    return parse


def _percent(value: str) -> Decimal:
    try:
        rate = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("expected a number such as 5 or 7.5") from None
    if not 0 <= rate <= 100:
        raise argparse.ArgumentTypeError("expected a percentage between 0 and 100")
    return rate


async def _create_org(settings: Settings, args: argparse.Namespace) -> None:
    db = Database(settings)
    try:
        async with db.session() as session:
            organization = Organization(
                name=args.name,
                legal_name=args.legal_name,
                industry=args.industry,
                country_code=args.country,
                currency_code=args.currency,
            )
            session.add(organization)
            await session.commit()
            print(f"organization_id={organization.id}")
    finally:
        await db.dispose()


async def _create_user(settings: Settings, args: argparse.Namespace) -> None:
    db = Database(settings)
    try:
        async with db.session() as session:
            if await session.get(Organization, args.org) is None:
                raise CommandError(f"organization {args.org} not found")
            user = User(organization_id=args.org, email=args.email.strip().lower(), full_name=args.name, role=args.role)
            session.add(user)
            await session.commit()
            print(f"user_id={user.id}")
    finally:
        await db.dispose()


async def _create_api_key(settings: Settings, args: argparse.Namespace) -> None:
    db = Database(settings)
    try:
        async with db.session() as session:
            if await session.get(Organization, args.org) is None:
                raise CommandError(f"organization {args.org} not found")
            key, prefix, key_hash = generate_api_key()
            api_key = ApiKey(
                organization_id=args.org,
                name=args.name,
                key_prefix=prefix,
                key_hash=key_hash,
                permissions={"scopes": sorted(args.scopes)},
                expires_at=datetime.now(UTC) + timedelta(days=args.expires_days) if args.expires_days else None,
            )
            session.add(api_key)
            await session.commit()
            print(f"api_key_id={api_key.id}")
            print(f"api_key={key}")
            print("Store this key now: only its hash is kept, it cannot be shown again.", file=sys.stderr)
    finally:
        await db.dispose()


async def _revoke_api_key(settings: Settings, args: argparse.Namespace) -> None:
    db = Database(settings)
    try:
        async with db.session() as session:
            api_key = await session.get(ApiKey, args.key_id)
            if api_key is None:
                raise CommandError(f"api key {args.key_id} not found")
            if api_key.revoked_at is None:
                api_key.revoked_at = datetime.now(UTC)
                await session.commit()
            print(f"revoked_at={api_key.revoked_at.isoformat()}")
    finally:
        await db.dispose()


async def _set_tax_limit(settings: Settings, args: argparse.Namespace) -> None:
    db = Database(settings)
    try:
        async with db.session() as session:
            limit = await session.get(TaxRateLimit, args.country)
            if limit is None:
                limit = TaxRateLimit(country_code=args.country, max_rate_percent=args.max_rate)
                session.add(limit)
            limit.max_rate_percent = args.max_rate
            limit.note = args.note
            await session.commit()
            print(f"{args.country} max tax rate = {args.max_rate}%")
    finally:
        await db.dispose()


async def _list_tax_limits(settings: Settings, _: argparse.Namespace) -> None:
    db = Database(settings)
    try:
        async with db.session() as session:
            for limit in await session.scalars(select(TaxRateLimit).order_by(TaxRateLimit.country_code)):
                print(f"{limit.country_code}  {limit.max_rate_percent.normalize():f}%  {limit.note or ''}".rstrip())
    finally:
        await db.dispose()


async def _extract(settings: Settings, args: argparse.Namespace) -> None:
    from .ingestion.gemini import ExtractionError, GeminiInvoiceExtractor, build_process_request

    if settings.gemini_api_key is None:
        raise CommandError("GEMINI_API_KEY is not set")
    path: Path = args.file
    mime_type = MIME_TYPES.get(path.suffix.lower())
    if mime_type is None:
        raise CommandError(f"unsupported file type {path.suffix!r}; expected one of {', '.join(MIME_TYPES)}")
    document = path.read_bytes()
    try:
        extraction = await GeminiInvoiceExtractor.from_settings(settings).extract(document, mime_type)
    except (ExtractionError, ValueError) as exc:
        raise CommandError(str(exc)) from exc
    request = build_process_request(
        document, filename=path.name, mime_type=mime_type, extraction=extraction, storage_url=args.storage_url
    )
    print(json.dumps(request.model_dump(mode="json", exclude_none=True), indent=2))


def _migrate(settings: Settings, args: argparse.Namespace) -> None:
    command.upgrade(alembic_config(settings.migration_database_url), args.revision)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="invoice-auditor", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    migrate = commands.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("--revision", default="head")
    migrate.set_defaults(handler=_migrate, is_async=False)

    org = commands.add_parser("create-org", help="create a tenant organization")
    org.add_argument("--name", required=True)
    org.add_argument("--legal-name")
    org.add_argument("--industry")
    org.add_argument("--country", type=_upper_code(2), help="ISO 3166-1 alpha-2, e.g. AE")
    org.add_argument("--currency", type=_upper_code(3), default="USD", help="ISO 4217, e.g. AED")
    org.set_defaults(handler=_create_org, is_async=True)

    user = commands.add_parser("create-user", help="add a user to an organization")
    user.add_argument("--org", type=UUID, required=True)
    user.add_argument("--email", required=True)
    user.add_argument("--name")
    user.add_argument("--role", choices=[role.value for role in UserRole], default=UserRole.MEMBER.value)
    user.set_defaults(handler=_create_user, is_async=True)

    key = commands.add_parser("create-api-key", help="issue an API key (printed once)")
    key.add_argument("--org", type=UUID, required=True)
    key.add_argument("--name", required=True)
    key.add_argument(
        "--scopes",
        nargs="+",
        choices=[scope.value for scope in ApiScope],
        default=[scope.value for scope in ApiScope],
    )
    key.add_argument("--expires-days", type=int, help="omit for a key that does not expire")
    key.set_defaults(handler=_create_api_key, is_async=True)

    revoke = commands.add_parser("revoke-api-key", help="revoke an API key")
    revoke.add_argument("--key-id", type=UUID, required=True)
    revoke.set_defaults(handler=_revoke_api_key, is_async=True)

    tax = commands.add_parser("set-tax-limit", help="set the highest normal tax rate for a country")
    tax.add_argument("--country", type=_upper_code(2), required=True)
    tax.add_argument("--max-rate", type=_percent, required=True, help="percent, e.g. 5 for 5%%")
    tax.add_argument("--note")
    tax.set_defaults(handler=_set_tax_limit, is_async=True)

    tax_list = commands.add_parser("list-tax-limits", help="show configured country tax limits")
    tax_list.set_defaults(handler=_list_tax_limits, is_async=True)

    extract = commands.add_parser(
        "extract", help="run Gemini on a document and print the JSON body for /api/v1/invoices/process"
    )
    extract.add_argument("file", type=Path)
    extract.add_argument("--storage-url")
    extract.set_defaults(handler=_extract, is_async=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        if args.is_async:
            asyncio.run(args.handler(settings, args))
        else:
            args.handler(settings, args)
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
