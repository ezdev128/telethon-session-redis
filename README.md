# Telethon Redis session

A Redis-backed session implementation for [Telethon](https://github.com/LonamiWebs/Telethon).

Multiple accounts can share a Redis database; each account uses a distinct session name.


## Setup

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

The minimum Telethon version is **1.31.0**. Earlier releases import `imghdr`,
which was [removed in Python 3.13](https://docs.python.org/3.13/library/imghdr.html).
Version 1.31.0 provides the required session APIs without that dependency.

```sh
uv sync --locked
```

This installs this checkout and its development dependencies into `.venv`.
After updating the checkout, run the same command. After deliberately changing
dependencies, regenerate `uv.lock` with `uv lock`.

To install this package from PyPI in another uv-managed service:

```sh
uv add telethon-redis
```

## Usage

Provide `REDIS_URL`, `TELEGRAM_API_ID`, and `TELEGRAM_API_HASH` through your
environment or secret manager. Use a stable, unique session name for each account.

```python
import os

import redis
from telethon import TelegramClient
from teleredis import RedisSession

connection = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=False)
session = RedisSession("account_name", connection)
client = TelegramClient(
    session,
    int(os.environ["TELEGRAM_API_ID"]),
    os.environ["TELEGRAM_API_HASH"],
)
client.start()
```

Save the example as `bot.py` and run it with `uv run --locked python bot.py`.

## Storage and startup

Under the default `telethon:client` hive:

| Key | Purpose |
| --- | --- |
| `telethon:client:<session>:active_dc` | Active data-center ID |
| `telethon:client:<session>:sessions:<dc_id>` | Connection metadata and authentication key |

Session loading uses two direct `GET` commands. Writes atomically update the
session record and active-DC pointer with `MSET`. Telethon's startup self-ID
lookups use one `MGET` per lookup for the possible user, chat, and channel entity
keys instead of scanning all entities. When IDs overlap, lookup prefers user,
then chat, then channel. Exact-ID lookups use one `GET`.

**Existing accounts must be migrated before deploying this version.** Startup
never discovers old records or repairs pointers automatically:

- A missing pointer means a new, unauthenticated session.
- A pointer whose record is missing raises an error instead of selecting another DC.
- A record whose DC disagrees with its pointer also raises an error.

Phone, username, and name lookups still use `KEYS`; they are not part of ordinary
saved-session startup. The public `list_sessions()` maintenance method uses
incremental `SCAN` but is never called while loading a session.

## Migrate existing sessions

Run from the repository root after `uv sync --locked`.

1. **Back up Redis and verify your restore procedure.** Stop every client writing
   this session hive, including supervisors, workers, and login services. Keep
   them stopped through migration and deployment. Do not mix old and new writers.

2. Export `REDIS_URL` for the correct host and database, preferably through your
   secret manager. For a local instance without authentication:

   ```sh
   export REDIS_URL='redis://localhost:6379/0'
   ```

3. Preview the migration. Without `--apply`, nothing is written:

   ```sh
   uv run --locked python scripts/migrate_active_dc.py
   ```

   The command traverses the database once with incremental `SCAN`, fetching only
   matching session records, never entity values. It preserves a valid active
   pointer or selects the sole authenticated DC, ignoring empty-auth placeholders.
   Any validation or selection error prevents all writes.

4. If an account has multiple authenticated DC records, determine its intended
   active DC from your account configuration or known working deployment.
   Create `active-dcs.json` with explicit choices:

   ```json
   {
     "account_name": 4,
     "another_account_name": 5
   }
   ```

   Use the exact session names passed to `RedisSession`, including the leading
   `+` for phone-number session names. Explicit choices override existing
   pointers, so review the preview:

   ```sh
   uv run --locked python scripts/migrate_active_dc.py --dc-map active-dcs.json
   ```

5. Apply the reviewed plan:

   ```sh
   uv run --locked python scripts/migrate_active_dc.py --apply
   # If explicit choices were required:
   uv run --locked python scripts/migrate_active_dc.py --dc-map active-dcs.json --apply
   ```

   Only active-DC pointers are written, in one atomic `MSET`. Authentication keys,
   session records, entities, and cached files are not rewritten or deleted.
   **Keep the per-DC records:** the runtime still uses them.

6. Rerun the preview to check the pointers, deploy the patched package to every
   client, then start the clients. Ordinary startup performs no keyspace traversal.
   If a selected record is later deleted, stop its writers and repair the pointer
   explicitly with this procedure.

For a custom hive, pass `--hive-prefix` on every migration invocation.
For pickle-serialized sessions, explicitly pass `--format pickle`.
**Only unpickle trusted Redis data: pickle can execute arbitrary code.**
The default format is JSON.

## Development

```sh
uv run --locked python -m unittest discover -s tests -v
uv build
```

Package metadata and dependencies live in `pyproject.toml`; `uv.lock` records the
resolved environment. The package version comes from `teleredis/__init__.py`.

## Credits

Based on [telethon-session-redis](https://github.com/ezdev128/telethon-session-redis)
by [Konstantin M. (@ezdev128)](https://github.com/ezdev128).