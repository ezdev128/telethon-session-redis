#!/usr/bin/env python3
"""Explicitly migrate legacy session records to active-DC pointers.

Pickle records can execute code while being loaded. Use ``--format pickle`` only
with trusted Redis data. Stop all application writers before applying a plan.
"""

import argparse
import base64
import binascii
import json
import os
import pickle
import re
import sys

import redis


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise ValueError("expected text")


def _redis_pattern_literal(text):
    """Escape Redis glob metacharacters in a literal prefix."""
    return re.sub(r"([\\*?\[\]])", r"\\\1", text)


def _decode_record(raw, pack_format):
    try:
        if pack_format == "json":
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        return pickle.loads(raw)
    except Exception:
        # Decoder exceptions may quote the raw value, which can contain auth data.
        raise ValueError(f"record is not valid {pack_format}")


def _positive_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str) and value.isdecimal():
        try:
            number = int(value)
        except ValueError:
            return None
        return number if number > 0 else None
    return None


def _validate_record(raw, key_dc, pack_format):
    record = _decode_record(raw, pack_format)
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    dc_value = record.get("dc_id")
    dc_id = dc_value if isinstance(dc_value, int) and not isinstance(dc_value, bool) else None
    if dc_id is None or dc_id <= 0 or dc_id != key_dc:
        raise ValueError("dc_id does not match the session key")
    address = record.get("server_address")
    if not isinstance(address, str) or not address.strip():
        raise ValueError("server_address must be nonempty")
    port = record.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer from 1 to 65535")
    auth_encoded = record.get("auth_key")
    if not isinstance(auth_encoded, str):
        raise ValueError("auth_key must be base64 text")
    try:
        auth_key = base64.b64decode(auth_encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("auth_key is not valid base64")
    if auth_key and len(auth_key) != 256:
        raise ValueError("auth_key must decode to 256 bytes (or be empty)")
    return bool(auth_key)


def build_plan(connection, hive_prefix="telethon:client", pack_format="json", dc_map=None):
    """Return (pointer-key -> DC plan, errors) without writing to Redis."""
    plan = {}
    errors = []
    if pack_format not in ("json", "pickle"):
        return plan, ["format must be 'json' or 'pickle'"]
    try:
        prefix = _text(hive_prefix)
    except (UnicodeDecodeError, ValueError):
        return plan, ["hive prefix must be text"]
    if not prefix:
        return plan, ["hive prefix must not be empty"]
    if dc_map is None:
        dc_map = {}
    if not hasattr(dc_map, "items"):
        return plan, ["DC map must be an object mapping session names to positive integer DCs"]

    mappings = {}
    for account, value in dc_map.items():
        if not isinstance(account, str) or not account:
            errors.append("DC map session names must be nonempty strings")
            continue
        target = value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
        if target is None:
            errors.append(f"DC map target for {account!r} must be a positive integer")
        else:
            mappings[account] = target

    # Scan only session keys; escaping prevents prefix glob syntax from widening the match.
    pattern = _redis_pattern_literal(prefix) + ":*:sessions:*"
    matcher = re.compile(r"^" + re.escape(prefix) + r":(.*):sessions:([^:]+)$")
    records = {}
    key_type = None
    try:
        cursor = 0
        while True:
            cursor, keys = connection.scan(cursor=cursor, match=pattern, count=1000)
            for key in keys:
                key_type = type(key)
                try:
                    key_text = _text(key)
                    match = matcher.match(key_text)
                    if not match:
                        continue
                    account, dc_text = match.groups()
                    dc_id = _positive_int(dc_text)
                    if dc_id is None or dc_text != str(dc_id):
                        errors.append(f"Malformed session key {key_text!r}: DC suffix must be a canonical positive integer")
                        continue
                    raw = connection.get(key)
                    if raw is None:
                        errors.append(f"Session record {key_text!r} disappeared during planning")
                        continue
                    try:
                        authenticated = _validate_record(raw, dc_id, pack_format)
                    except ValueError as exc:
                        # Avoid including record contents or exception payloads in diagnostics.
                        errors.append(f"Malformed session record {key_text!r}: {str(exc) or 'invalid data'}")
                        continue
                    records.setdefault(account, {})[dc_id] = authenticated
                except (UnicodeDecodeError, ValueError) as exc:
                    errors.append(f"Could not parse a matching session key: {str(exc)}")
            if not cursor:
                break
    except redis.RedisError:
        return {}, errors + ["Redis error while scanning session keys"]

    for account in mappings:
        if account not in records:
            errors.append(f"DC map names unknown session account {account!r}")

    pointer_type = key_type or bytes
    for account, candidates in records.items():
        pointer_text = f"{prefix}:{account}:active_dc"
        pointer_key = pointer_text.encode("utf-8") if pointer_type is bytes else pointer_text
        try:
            current = connection.get(pointer_key)
        except redis.RedisError:
            errors.append(f"Redis error while reading active-DC pointer for {account!r}")
            continue
        current_dc = _positive_int(current) if current is not None else None
        authenticated = {dc: valid for dc, valid in candidates.items() if valid}
        if account in mappings:
            selected = mappings[account]
            if not authenticated.get(selected, False):
                errors.append(f"DC map target {selected} for {account!r} is not an authenticated session record")
                continue
        elif current_dc is not None and authenticated.get(current_dc, False):
            selected = current_dc
        elif len(authenticated) == 1:
            selected = next(iter(authenticated))
        elif len(authenticated) > 1:
            errors.append(f"Session account {account!r} has multiple authenticated DCs; provide an explicit DC map entry")
            continue
        else:
            # Empty-auth placeholders are not migration candidates.
            continue
        plan[pointer_key] = selected

    return ({} if errors else plan), errors


def _load_dc_map(filename):
    with open(filename, "r", encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError("DC map JSON must be an object")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Plan or explicitly migrate legacy active-DC pointers. Stop ALL Redis session writers before applying.")
    parser.add_argument("--apply", action="store_true", help="write the complete plan using one atomic MSET (default: preview without writes)")
    parser.add_argument("--hive-prefix", default="telethon:client", help="literal Redis hive prefix")
    parser.add_argument("--format", choices=("json", "pickle"), default="json", help="session record format (pickle loads trusted data only)")
    parser.add_argument("--dc-map", metavar="FILE", help="JSON object mapping session names to positive DC IDs")
    args = parser.parse_args(argv)

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        print("error: REDIS_URL environment variable is required", file=sys.stderr)
        return 2
    if args.format == "pickle":
        print("Warning: pickle can execute code; use trusted Redis data only.", file=sys.stderr)
    try:
        dc_map = _load_dc_map(args.dc_map) if args.dc_map else None
    except (OSError, ValueError):
        print("error: could not read the DC map", file=sys.stderr)
        return 2
    try:
        connection = redis.Redis.from_url(redis_url)
        plan, errors = build_plan(connection, args.hive_prefix, args.format, dc_map)
    except (ValueError, redis.RedisError):
        print("error: invalid REDIS_URL or Redis connection failure", file=sys.stderr)
        return 2

    for key, dc_id in plan.items():
        display_key = _text(key)
        print(f"{display_key} -> {dc_id}")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1
    if args.apply and plan:
        try:
            connection.mset(plan)
        except redis.RedisError:
            print("error: Redis failed to apply the active-DC plan", file=sys.stderr)
            return 2
        print(f"Applied {len(plan)} active-DC pointer(s).")
    elif not args.apply:
        print(f"Dry run: {len(plan)} active-DC pointer(s) selected; no writes made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
