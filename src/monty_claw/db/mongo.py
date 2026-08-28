"""MongoDB chat repository (PyMongo async API)."""

from datetime import datetime, timezone

from pymongo import AsyncMongoClient

from monty_claw.db.base import ChatRecord


class MongoChatRepo:
    def __init__(self, client: AsyncMongoClient, db_name: str) -> None:
        self._chats = client[db_name]['chats']

    async def get(self, chat_key: str) -> ChatRecord | None:
        doc = await self._chats.find_one({'_id': chat_key})
        if doc is None:
            return None
        return ChatRecord(
            chat_key=chat_key,
            history=doc.get('history', []),
            session_blob_key=doc.get('session_blob_key'),
            last_update_id=doc.get('last_update_id'),
            transcript=doc.get('transcript', []),
        )

    async def save(self, record: ChatRecord) -> None:
        await self._chats.replace_one(
            {'_id': record.chat_key},
            {
                'history': record.history,
                'session_blob_key': record.session_blob_key,
                'last_update_id': record.last_update_id,
                'transcript': record.transcript,
                'updated_at': datetime.now(timezone.utc),
            },
            upsert=True,
        )

    async def delete(self, chat_key: str) -> None:
        await self._chats.delete_one({'_id': chat_key})


class MongoConfigRepo:
    """Runtime config overrides (edited from the web UI) as a single doc."""

    def __init__(self, client: AsyncMongoClient, db_name: str) -> None:
        self._config = client[db_name]['config']

    async def load(self) -> dict:
        doc = await self._config.find_one({'_id': 'runtime'})
        return (doc or {}).get('values', {})

    async def save(self, values: dict) -> None:
        await self._config.replace_one({'_id': 'runtime'}, {'values': values}, upsert=True)
