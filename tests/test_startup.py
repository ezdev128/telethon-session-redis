import base64
import json
import unittest

import fakeredis
from telethon.crypto import AuthKey
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser

from teleredis import PackFunction, RedisSession, UnpackFunction
from scripts.migrate_active_dc import build_plan


class ScanGuardRedis(fakeredis.FakeStrictRedis):
    allow_scan = False

    def execute_command(self, *args, **kwargs):
        command = args[0].upper()
        if command == "KEYS" or (command == "SCAN" and not self.allow_scan):
            raise AssertionError(f"Unexpected keyspace traversal: {command}")
        return super().execute_command(*args, **kwargs)


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.redis = ScanGuardRedis()

    def store_session(self, dc_id, auth_key, account="account"):
        self.redis.set(f"telethon:client:{account}:sessions:{dc_id}", json.dumps({
            "dc_id": dc_id,
            "server_address": f"dc{dc_id}.example",
            "port": 443,
            "auth_key": base64.b64encode(auth_key).decode(),
        }))

    def test_explicit_migration_preserves_auth_for_scan_free_startup(self):
        auth_key = b"a" * 256
        self.store_session(4, auth_key)
        self.redis.allow_scan = True
        plan, errors = build_plan(self.redis)
        self.assertEqual(errors, [])
        self.redis.mset(plan)
        self.redis.allow_scan = False

        restored = RedisSession("account", self.redis)
        self.assertEqual(restored.dc_id, 4)
        self.assertEqual(restored.server_address, "dc4.example")
        self.assertEqual(restored.port, 443)
        self.assertEqual(restored.auth_key.key, auth_key)

    def test_deleted_active_record_fails_without_selecting_another_dc(self):
        self.store_session(2, b"a" * 256)
        self.store_session(4, b"b" * 256)
        self.redis.set("telethon:client:account:active_dc", 2)
        restored = RedisSession("account", self.redis)
        self.assertEqual(restored.dc_id, 2)
        self.assertEqual(restored.auth_key.key, b"a" * 256)

        # Discovery and pointer repair belong to the migration command, not startup.
        self.redis.delete("telethon:client:account:sessions:2")
        with self.assertRaisesRegex(ValueError, "no session record"):
            RedisSession("account", self.redis)
        self.assertEqual(self.redis.get("telethon:client:account:active_dc"), b"2")

    def test_session_writes_preserve_auth_and_active_dc_in_both_formats(self):
        for pack, unpack in ((PackFunction.JSON, UnpackFunction.JSON),
                             (PackFunction.PICKLE, UnpackFunction.PICKLE)):
            with self.subTest(pack=pack):
                redis = ScanGuardRedis()
                session = RedisSession("account", redis, pack_func=pack, unpack_func=unpack)
                session.set_dc(4, "dc4.example", 443)
                session.auth_key = AuthKey(b"k" * 256)
                restored = RedisSession("account", redis, pack_func=pack, unpack_func=unpack)
                self.assertEqual(restored.dc_id, 4)
                self.assertEqual(restored.auth_key.key, b"k" * 256)

    def test_self_id_and_nonexact_peer_lookups_without_traversal(self):
        session = RedisSession("account", self.redis)
        rows = ((0, 42), (42, 420), (-43, 0), (-1000000000044, 440))
        for entity_id, access_hash in rows:
            self.redis.set(f"telethon:client:account:entities:{entity_id}",
                           session._pack({"id": entity_id, "hash": access_hash}))

        # These are the two direct session lookups made by Telethon.connect().
        self_id = session.get_input_entity(0).access_hash
        self.assertEqual(self_id, 42)
        self_user = session.get_input_entity(self_id)
        self.assertIsInstance(self_user, InputPeerUser)
        self.assertEqual((self_user.user_id, self_user.access_hash), (42, 420))

        chat = session.get_input_entity(43)
        self.assertIsInstance(chat, InputPeerChat)
        self.assertEqual(chat.chat_id, 43)
        channel = session.get_input_entity(44)
        self.assertIsInstance(channel, InputPeerChannel)
        self.assertEqual((channel.channel_id, channel.access_hash), (44, 440))
        self.assertIsNone(session.get_entity_rows_by_id(999, exact=False))

    def test_missing_pointer_does_not_automatically_migrate_legacy_auth(self):
        self.store_session(4, b"a" * 256)
        session = RedisSession("account", self.redis)
        self.assertFalse(session.auth_key.key)
        self.assertIsNone(self.redis.get("telethon:client:account:active_dc"))

    def test_mismatched_active_record_is_not_loaded(self):
        self.store_session(4, b"a" * 256)
        self.redis.set("telethon:client:account:active_dc", 2)
        self.redis.rename("telethon:client:account:sessions:4",
                          "telethon:client:account:sessions:2")
        with self.assertRaisesRegex(ValueError, "does not match"):
            RedisSession("account", self.redis)


if __name__ == "__main__":
    unittest.main()
