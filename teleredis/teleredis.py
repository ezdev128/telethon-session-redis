
from telethon.sessions.memory import MemorySession, _SentFileType
from telethon.crypto import AuthKey
from telethon import utils
from telethon.tl.types import (
    InputPhoto, InputDocument, PeerUser, PeerChat, PeerChannel
)
import logging
import json
import base64
import time
import redis
import pickle
from enum import Enum


class PackFunction(Enum):
    JSON = 0
    PICKLE = 1


class UnpackFunction(Enum):
    JSON = 0
    PICKLE = 1


DEFAULT_TS_STR_FORMAT = "%F %T"
DEFAULT_HIVE_PREFIX = "telethon:client"
DEFAULT_PACK_FUNC = PackFunction.JSON
DEFAULT_UNPACK_FUNC = UnpackFunction.JSON


__log__ = logging.getLogger(__name__)


class RedisSession(MemorySession):

    session_name = None
    redis_connection = None
    hive_prefix = None
    sess_prefix = None
    use_indents = True
    add_timestamps = False
    ts_format = None
    pack_func = None
    unpack_func = None

    def __init__(self, session_name=None, redis_connection=None, hive_prefix=None,
                 use_indents=False, add_timestamps=False, ts_format=None,
                 pack_func=None, unpack_func=None):
        if not isinstance(session_name, (str, bytes)):
            raise TypeError("Session name must be a string or bytes.")

        if not redis_connection or not isinstance(redis_connection, redis.StrictRedis):
            raise TypeError('The given redis_connection must be a Redis instance.')

        super().__init__()

        self.session_name = session_name if isinstance(session_name, str) else session_name.decode()
        self.redis_connection = redis_connection
        self.hive_prefix = hive_prefix or DEFAULT_HIVE_PREFIX
        self.use_indents = use_indents
        self.add_timestamps = add_timestamps
        self.ts_format = ts_format or DEFAULT_TS_STR_FORMAT
        self.pack_func = pack_func or DEFAULT_PACK_FUNC
        self.unpack_func = unpack_func or DEFAULT_UNPACK_FUNC
        self.sess_prefix = f"{self.hive_prefix}:{self.session_name}"
        self.save_entities = True
        self.feed_session()

    def _pack(self, o, **kwargs):
        if self.pack_func == PackFunction.JSON:
            if self.use_indents:
                kwargs["indent"] = 2
        return json.dumps(o, **kwargs) if self.pack_func == PackFunction.JSON else pickle.dumps(o, **kwargs)

    def _unpack(self, o, **kwargs):
        if self.unpack_func == UnpackFunction.JSON and isinstance(o, bytes):
            o = o.decode()
        return json.loads(o, **kwargs) if self.unpack_func == UnpackFunction.JSON else pickle.loads(o, **kwargs)

    def feed_session(self):
        dc_id = self.redis_connection.get(f"{self.sess_prefix}:active_dc")
        if dc_id is None:
            self._auth_key = AuthKey(data=bytes())
            return

        dc_id = int(dc_id)
        key = f"{self.sess_prefix}:sessions:{dc_id}"
        s = self.redis_connection.get(key)
        if s is None:
            raise ValueError(
                f"Active DC {dc_id} has no session record; repair the active_dc pointer "
                "with scripts/migrate_active_dc.py before starting."
            )

        s = self._unpack(s)
        if s["dc_id"] != dc_id:
            raise ValueError("Active DC does not match the saved session record.")
        self._dc_id = dc_id
        self._server_address = s["server_address"]
        self._port = s["port"]
        self._auth_key = AuthKey(data=base64.standard_b64decode(s["auth_key"]))

    def _update_sessions(self):
        """
        Stores session into redis.
        """
        auth_key = self._auth_key.key if self._auth_key else bytes()
        if not self._dc_id:
            return

        s = {
            "dc_id": self._dc_id,
            "server_address": self._server_address,
            "port": self._port,
            "auth_key": base64.standard_b64encode(auth_key).decode(),
        }

        if self.add_timestamps:
            s.update({
                "ts_ts": time.time(),
                "ts_str": time.strftime(DEFAULT_TS_STR_FORMAT, time.localtime()),
            })

        key = f"{self.sess_prefix}:sessions:{self._dc_id}"
        try:
            self.redis_connection.mset({
                key: self._pack(s),
                f"{self.sess_prefix}:active_dc": self._dc_id,
            })
        except Exception as ex:
            __log__.exception(ex.args)

    def set_dc(self, dc_id, server_address, port):
        """
        Sets the information of the data center address and port that
        the library should connect to, as well as the data center ID,
        which is currently unused.
        """
        super().set_dc(dc_id, server_address, port)
        self._update_sessions()

        auth_key = bytes()

        if not self._dc_id:
            self._auth_key = AuthKey(data=auth_key)
            return

        key = f"{self.sess_prefix}:sessions:{self._dc_id}"
        s = self.redis_connection.get(key)
        if s:
            s = self._unpack(s)
            auth_key = base64.standard_b64decode(s["auth_key"])
        self._auth_key = AuthKey(data=auth_key)

    @MemorySession.auth_key.setter
    def auth_key(self, value):
        """
        Sets the ``AuthKey`` to be used for the saved data center.
        """
        self._auth_key = value
        self._update_sessions()

    def list_sessions(self):
        """
        Lists saved data-center IDs. Maintenance only; scans incrementally.
        """
        prefix = f"{self.sess_prefix}:sessions:"
        pattern = "".join("\\" + c if c in "\\*?[]" else c for c in prefix) + "*"
        sessions = self.redis_connection.scan_iter(match=pattern, count=1000)
        return [
            (key.decode() if isinstance(key, bytes) else key)[len(prefix):]
            for key in sessions
        ]

    def process_entities(self, tlo):
        """
        Processes the input ``TLObject`` or ``list`` and saves
        whatever information is relevant (e.g., ID or access hash).
        """

        if not self.save_entities:
            return

        rows = self._entities_to_rows(tlo)
        if not rows or len(rows) == 0 or len(rows[0]) == 0:
            return

        try:
            rows = rows[0]
            key = f"{self.sess_prefix}:entities:{rows[0]}"
            s = {
                "id": rows[0],
                "hash": rows[1],
                "username": rows[2],
                "phone": rows[3],
                "name": rows[4],
            }

            if self.add_timestamps:
                s.update({
                    "ts_ts": time.time(),
                    "ts_str": time.strftime(DEFAULT_TS_STR_FORMAT, time.localtime()),
                })

            self.redis_connection.set(key, self._pack(s))
        except Exception as ex:
            __log__.exception(ex.args)

    def _get_entities(self, strip_prefix=False):
        """
        Returns list of entities. if strip_prefix is False - returns redis keys,
        else returns list of id's
        """
        key_pattern = f"{self.hive_prefix}:{self.session_name}:entities:"
        try:
            entities = self.redis_connection.keys(key_pattern+"*")
            if not strip_prefix:
                return entities
            return [s.decode().replace(key_pattern, "") for s in entities]
        except Exception as ex:
            __log__.exception(ex.args)
            return []

    def get_entity_rows_by_phone(self, phone):
        try:
            for key in self._get_entities():
                entity = self._unpack(self.redis_connection.get(key))
                if "phone" in entity and entity["phone"] == phone:
                    return entity["id"], entity["hash"]
        except Exception as ex:
            __log__.exception(ex.args)
        return None

    def get_entity_rows_by_username(self, username):
        try:
            for key in self._get_entities():
                entity = self._unpack(self.redis_connection.get(key))
                if "username" in entity and entity["username"] == username:
                    return entity["id"], entity["hash"]
        except Exception as ex:
            __log__.exception(ex.args)
        return None

    def get_entity_rows_by_name(self, name):
        try:
            for key in self._get_entities():
                entity = self._unpack(self.redis_connection.get(key))
                if "name" in entity and entity["name"] == name:
                    return entity["id"], entity["hash"]
        except Exception as ex:
            __log__.exception(ex.args)

        return None

    def get_entity_rows_by_id(self, entity_id, exact=True):
        if exact:
            key = f"{self.sess_prefix}:entities:{entity_id}"
            s = self.redis_connection.get(key)
            if not s:
                return None
            try:
                s = self._unpack(s)
                return entity_id, s["hash"]
            except Exception as ex:
                __log__.exception(ex.args)
                return None
        else:
            ids = (
                utils.get_peer_id(PeerUser(entity_id)),
                utils.get_peer_id(PeerChat(entity_id)),
                utils.get_peer_id(PeerChannel(entity_id))
            )

            try:
                keys = [f"{self.sess_prefix}:entities:{peer_id}" for peer_id in ids]
                for value in self.redis_connection.mget(keys):
                    if value is None:
                        continue
                    entity = self._unpack(value)
                    if "id" in entity and entity["id"] in ids:
                        return entity["id"], entity["hash"]
            except Exception as ex:
                __log__.exception(ex.args)

    def get_file(self, md5_digest, file_size, cls):
        key = f"{self.sess_prefix}:sent_files:{md5_digest}"
        s = self.redis_connection.get(key)
        if s:
            try:
                s = self._unpack(s)
                return md5_digest, file_size \
                    if s["file_size"] == file_size and s["type"] == _SentFileType.from_type(cls).value \
                    else None
            except Exception as ex:
                __log__.exception(ex.args)
                return None

    def cache_file(self, md5_digest, file_size, instance):
        if not isinstance(instance, (InputDocument, InputPhoto)):
            raise TypeError(f'Cannot cache {type(instance)} instance')

        key = f"{self.sess_prefix}:sent_files:{md5_digest}"
        s = {
            "md5_digest": md5_digest,
            "file_size": file_size,
            "type": _SentFileType.from_type(type(instance)).value,
            "id": instance.id,
            "hash": instance.access_hash,
        }

        if self.add_timestamps:
            s.update({
                "ts_ts": time.time(),
                "ts_str": time.strftime(DEFAULT_TS_STR_FORMAT, time.localtime()),
            })

        try:
            self.redis_connection.set(key, self._pack(s))
        except Exception as ex:
            __log__.exception(ex.args)
