import base64
import contextlib
import io
import json
import os
import pickle
import unittest
from unittest.mock import patch

import fakeredis

from scripts.migrate_active_dc import build_plan, main


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.redis = fakeredis.FakeStrictRedis()

    def store(self, account, dc, auth=b"k" * 256, fmt="json", hive_prefix="telethon:client", **overrides):
        record = {
            "dc_id": dc,
            "server_address": "149.154.167.51",
            "port": 443,
            "auth_key": base64.b64encode(auth).decode("ascii"),
        }
        record.update(overrides)
        value = json.dumps(record) if fmt == "json" else pickle.dumps(record)
        key = f"{hive_prefix}:{account}:sessions:{dc}"
        self.redis.set(key, value)
        return key

    def test_existing_valid_pointer_is_preserved(self):
        self.store("account", 2, b"a" * 256)
        self.store("account", 4, b"b" * 256)
        self.redis.set("telethon:client:account:active_dc", "4")

        plan, errors = build_plan(self.redis)

        self.assertEqual(plan, {b"telethon:client:account:active_dc": 4})
        self.assertEqual(errors, [])
        chosen_plan, chosen_errors = build_plan(self.redis, dc_map={"account": 2})
        self.assertEqual(chosen_plan, {b"telethon:client:account:active_dc": 2})
        self.assertEqual(chosen_errors, [])

    def test_unique_authenticated_record_ignores_empty_placeholder(self):
        self.store("account", 2, auth=b"")
        self.store("account", 4, auth=b"b" * 256)

        plan, errors = build_plan(self.redis)

        self.assertEqual(plan, {b"telethon:client:account:active_dc": 4})
        self.assertEqual(errors, [])

    def test_multiple_authenticated_records_require_explicit_map(self):
        self.store("account", 2)
        self.store("account", 4)

        plan, errors = build_plan(self.redis)

        self.assertEqual(plan, {})
        self.assertTrue(any("multiple authenticated DCs" in error for error in errors))
        self.assertEqual(build_plan(self.redis, dc_map={"account": 4}),
                         ({b"telethon:client:account:active_dc": 4}, []))

    def test_invalid_map_targets_and_unknown_accounts_are_errors(self):
        self.store("account", 2)

        plan, errors = build_plan(self.redis, dc_map={"account": 9, "missing": 2})

        self.assertEqual(plan, {})
        self.assertTrue(any("not an authenticated session record" in error for error in errors))
        self.assertTrue(any("unknown session account 'missing'" in error for error in errors))
        plan, errors = build_plan(self.redis, dc_map={"account": -1})
        self.assertEqual(plan, {})
        self.assertTrue(any("positive integer" in error for error in errors))

    def test_malformed_authentication_data_is_reported(self):
        self.store("account", 2, auth=b"short")

        plan, errors = build_plan(self.redis)

        self.assertEqual(plan, {})
        self.assertTrue(any("256 bytes" in error for error in errors))
        key = "telethon:client:account:sessions:3"
        self.redis.set(key, json.dumps({"dc_id": 3, "server_address": "x", "port": 443, "auth_key": "%%%"}))
        plan, errors = build_plan(self.redis)
        self.assertEqual(plan, {})
        self.assertTrue(any("valid base64" in error for error in errors))

    def test_noncanonical_dc_key_cannot_create_a_dangling_pointer(self):
        key = self.store("account", 4)
        self.redis.rename(key, key[:-1] + "04")
        plan, errors = build_plan(self.redis)
        self.assertEqual(plan, {})
        self.assertTrue(errors)

    def test_literal_prefix_and_rightmost_session_separator(self):
        self.store("acct*:with:sessions:segment", 7, hive_prefix="telethon:client*")
        self.store("acct-other:with:sessions:segment", 8, hive_prefix="telethon:clientX")

        plan, errors = build_plan(self.redis, hive_prefix="telethon:client*")

        self.assertEqual(plan, {b"telethon:client*:acct*:with:sessions:segment:active_dc": 7})
        self.assertEqual(errors, [])

    def test_pickle_requires_explicit_format_and_is_supported(self):
        self.store("account", 2, fmt="pickle")

        default_plan, default_errors = build_plan(self.redis)
        pickle_plan, pickle_errors = build_plan(self.redis, pack_format="pickle")

        self.assertEqual(default_plan, {})
        self.assertTrue(default_errors)
        self.assertEqual(pickle_plan, {b"telethon:client:account:active_dc": 2})
        self.assertEqual(pickle_errors, [])

    def test_planning_does_not_write(self):
        self.store("account", 2)

        plan, errors = build_plan(self.redis)

        self.assertEqual(errors, [])
        self.assertEqual(self.redis.get("telethon:client:account:active_dc"), None)
        self.assertEqual(self.redis.get("telethon:client:account:sessions:2"), json.dumps({
            "dc_id": 2,
            "server_address": "149.154.167.51",
            "port": 443,
            "auth_key": base64.b64encode(b"k" * 256).decode("ascii"),
        }).encode())


class MigrationCLITests(unittest.TestCase):
    def test_preview_preserves_auth_and_apply_changes_only_the_pointer(self):
        connection = fakeredis.FakeStrictRedis()
        record_key = "telethon:client:account:sessions:4"
        record = json.dumps({
            "dc_id": 4,
            "server_address": "dc4.example",
            "port": 443,
            "auth_key": base64.b64encode(b"k" * 256).decode(),
        }).encode()
        connection.set(record_key, record)
        pointer_key = "telethon:client:account:active_dc"
        connection.set(pointer_key, 2)
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost/0"}), \
                patch("scripts.migrate_active_dc.redis.Redis.from_url", return_value=connection), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 0)
            self.assertEqual(connection.get(pointer_key), b"2")
            self.assertEqual(connection.get(record_key), record)
            self.assertEqual(main(["--apply"]), 0)
        self.assertEqual(connection.get(pointer_key), b"4")
        self.assertEqual(connection.get(record_key), record)


if __name__ == "__main__":
    unittest.main()
