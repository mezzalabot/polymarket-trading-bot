#!/usr/bin/env python3
"""Smoke test for Polymarket CLOB executor prerequisites.

What this script checks:
1. Environment variables load correctly.
2. py-clob-client can be imported.
3. Read-only connectivity to the CLOB works.
4. Auth client can be constructed.
5. API credentials can be derived or reused.

What this script does NOT do:
- It does not place any order.
- It does not cancel any order.
- It does not modify balances or allowances.
"""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _mask(value: str | None, keep: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return value
    return f"{value[:keep]}...{value[-keep:]}"


def _get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    return value or default


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()

    results: list[CheckResult] = []

    host = _get_env("HOST", "https://clob.polymarket.com")
    chain_id_raw = _get_env("CHAIN_ID", "137")
    private_key = _get_env("PRIVATE_KEY")
    signature_type_raw = _get_env("POLY_SIGNATURE_TYPE", "2")
    funder = _get_env("POLY_FUNDER_ADDRESS")
    signer = _get_env("POLY_SIGNER_ADDRESS")
    api_key = _get_env("CLOB_API_KEY")
    api_secret = _get_env("CLOB_SECRET")
    api_passphrase = _get_env("CLOB_PASSPHRASE")

    try:
        chain_id = int(chain_id_raw or "137")
        signature_type = int(signature_type_raw or "2")
        results.append(CheckResult("env_parse", True, f"HOST={host}, CHAIN_ID={chain_id}, SIGNATURE_TYPE={signature_type}"))
    except Exception as exc:
        results.append(CheckResult("env_parse", False, f"Invalid CHAIN_ID or POLY_SIGNATURE_TYPE: {exc}"))
        _print_results(results)
        return 1

    try:
        from py_clob_client.client import ClobClient
        results.append(CheckResult("import_py_clob_client", True, "py_clob_client imported successfully"))
    except Exception as exc:
        results.append(CheckResult("import_py_clob_client", False, f"Import failed: {exc}"))
        _print_results(results)
        return 1

    # Read-only connectivity test
    try:
        readonly_client = ClobClient(host)
        readonly_ok = readonly_client.get_ok()
        results.append(CheckResult("readonly_connectivity", True, f"CLOB get_ok() returned: {readonly_ok}"))
    except Exception as exc:
        results.append(CheckResult("readonly_connectivity", False, f"Failed to reach CLOB: {exc}"))

    if not private_key:
        results.append(CheckResult("auth_prereq", False, "PRIVATE_KEY is empty"))
        _print_results(results)
        return 1
    if not funder:
        results.append(CheckResult("auth_prereq", False, "POLY_FUNDER_ADDRESS is empty"))
        _print_results(results)
        return 1

    results.append(
        CheckResult(
            "auth_prereq",
            True,
            f"PRIVATE_KEY={_mask(private_key)}, FUNDER={_mask(funder)}, SIGNER={_mask(signer)}",
        )
    )

    # Auth client construction + creds
    try:
        auth_client = ClobClient(
            host,
            key=private_key,
            chain_id=chain_id,
            signature_type=signature_type,
            funder=funder,
        )
        results.append(CheckResult("auth_client_construct", True, "Authenticated client constructed"))
    except Exception as exc:
        results.append(CheckResult("auth_client_construct", False, f"Failed to construct auth client: {exc}"))
        _print_results(results)
        return 1

    try:
        if api_key and api_secret and api_passphrase:
            # Reuse provided creds when present.
            auth_client.set_api_creds(
                {
                    "key": api_key,
                    "secret": api_secret,
                    "passphrase": api_passphrase,
                }
            )
            results.append(CheckResult("api_creds", True, "Existing L2 credentials loaded from env"))
        else:
            creds = auth_client.create_or_derive_api_creds()
            try:
                auth_client.set_api_creds(creds)
            except Exception:
                # Some versions accept the object directly only in later calls.
                pass
            key_preview = getattr(creds, "api_key", None) or getattr(creds, "key", None) or "derived"
            results.append(CheckResult("api_creds", True, f"L2 credentials derived successfully: {_mask(str(key_preview))}"))
    except Exception as exc:
        results.append(CheckResult("api_creds", False, f"Failed to derive/load L2 credentials: {exc}"))
        _print_results(results)
        return 1

    # Optional lightweight authenticated endpoint if available.
    try:
        if hasattr(auth_client, "get_api_keys"):
            auth_client.get_api_keys()
            results.append(CheckResult("auth_endpoint", True, "Authenticated endpoint call succeeded"))
        else:
            results.append(CheckResult("auth_endpoint", True, "Skipped: client has no get_api_keys()"))
    except Exception as exc:
        results.append(CheckResult("auth_endpoint", False, f"Authenticated endpoint failed: {exc}"))

    _print_results(results)

    failed = any(not r.ok for r in results)
    if failed:
        print("\nRESULT: SMOKE TEST FAILED")
        print("Do not switch to LIVE yet.")
        return 1

    print("\nRESULT: SMOKE TEST PASSED")
    print("Safe next step: keep dry-run on, then test executor initialization inside the bot.")
    return 0


def _print_results(results: list[CheckResult]) -> None:
    print("=" * 72)
    print("Polymarket Executor Smoke Test")
    print("=" * 72)
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        raise SystemExit(130)
    except Exception:
        print("\nUnhandled exception during smoke test:")
        traceback.print_exc()
        raise SystemExit(1)
