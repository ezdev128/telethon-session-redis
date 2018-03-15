Telethon Redis session
===========================

A `Telethon`_ session storage implementation backed by Redis.

.. _Telethon: https://github.com/LonamiWebs/Telethon

Usage
-----
This session implementation can store multiple Sessions in the same key hive.



Installing
----------

    .. code-block:: sh

        pip3 install teleredis


Quick start
-----------
    .. code-block:: python

        from telethon import TelegramClient
        from teleredis import RedisSession
        import redis

        # These example values won't work. You must get your own api_id and
        # api_hash from https://my.telegram.org, under API Development.
        api_id = 12345
        api_hash = '0123456789abcdef0123456789abcdef'

        redis_connector = redis.Redis(host='localhost', port=6379, db=0, decode_responses=False)
        session = RedisSession('session_name', redis_connector)
        client = TelegramClient(session, api_id, api_hash)
        client.start()

