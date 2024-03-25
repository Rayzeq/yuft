from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from functools import partial
from typing import (
    TYPE_CHECKING,
    Awaitable,
    Callable,
    ClassVar,
    Generic,
    TypeVar,
)

from utils import ChannelMention, Mention, TaskPool, Timestamp

if TYPE_CHECKING:
    import discord


T = TypeVar("T")


@dataclass
class Database(Generic[T]):
    channel: discord.abc.Messageable
    t2str: Callable[[T], str]
    str2t: Callable[[str], Awaitable[T | None]]
    reminders: list[Reminder[T]] = field(default_factory=list)
    task_pool: TaskPool = field(default_factory=TaskPool)

    async def run(self: Database[T], callback: Callable[[Reminder[T]], Awaitable[None]]) -> None:
        await self.fetch_all()
        print("Reminders are loaded")

        await self.mainloop(callback)

    async def mainloop(self: Database[T], callback: Callable[[Reminder[T]], Awaitable[None]]) -> None:
        while True:
            for reminder in self.reminders[:]:
                if reminder.remind_date.timestamp - time.time() < 5 * 60:
                    self.task_pool.run_after(
                        reminder.remind_date.timestamp - time.time(),
                        partial(self.process, callback, reminder),
                    )
                    self.reminders.remove(reminder)

            await asyncio.sleep(5 * 60)

    async def process(
        self: Database[T],
        callback: Callable[[Reminder[T]], Awaitable[None]],
        reminder: Reminder[T],
    ) -> None:
        await callback(reminder)
        await reminder.delete()

    async def create(
        self: Database[T],
        event_date: Timestamp,
        remind_date: Timestamp,
        user: Mention | discord.User | discord.Member,
        fallback_channel: ChannelMention | discord.TextChannel,
        source: T,
    ) -> Reminder[T]:
        reminder = Reminder(
            event_date,
            remind_date,
            Mention(user),
            ChannelMention(fallback_channel),
            source,
            None,  # type: ignore [arg-type] # we set it later
            self,
        )

        reminder.message = await self.channel.send(content=reminder.serialize(self.t2str))
        self.reminders.append(reminder)
        return reminder

    async def fetch_all(
        self: Database[T],
        *,
        limit: int | None = None,
    ) -> list[Reminder[T]]:
        reminders = [
            x
            for x in [
                await Reminder.from_message(msg, self.str2t, self) async for msg in self.channel.history(limit=limit)
            ]
            if x is not None
        ]
        self.reminders = reminders
        return reminders

    async def fetch(self: Database[T], id: int) -> Reminder[T] | None:
        async for msg in self.channel.history():
            if str(msg.id).endswith(str(id)):
                break
        else:
            return None

        return await Reminder.from_message(msg, self.str2t, self)


@dataclass
class Reminder(Generic[T]):
    DELIMITER: ClassVar[str] = "\\\\!"

    event_date: Timestamp
    remind_date: Timestamp
    user: Mention
    fallback_channel: ChannelMention
    source: T

    message: discord.Message
    database: Database[T]

    def serialize(self: Reminder[T], transformer: Callable[[T], str]) -> str:
        return self.DELIMITER.join(
            (
                str(self.event_date),
                str(self.remind_date),
                str(self.user),
                str(self.fallback_channel),
                transformer(self.source),
            ),
        )

    @classmethod
    async def from_message(
        cls: type[Reminder[T]],
        message: discord.Message,
        converter: Callable[[str], Awaitable[T | None]],
        database: Database[T],
    ) -> Reminder[T] | None:
        (
            event_date,
            remind_date,
            user,
            fallback_channel,
            source,
        ) = message.content.split(cls.DELIMITER)

        source_ = await converter(source)

        self = cls(
            Timestamp(event_date),
            Timestamp(remind_date),
            Mention(user),
            ChannelMention(fallback_channel),
            source_,  # type: ignore [arg-type] # the object is not returned if conversion failed
            message,
            database,
        )

        if source_ is None:
            await self.delete()
            return None

        return self

    async def delete(self: Reminder[T]) -> None:
        # the reminder might not be in the database cache if it was removed because it's about to be send
        if self in self.database.reminders:
            self.database.reminders.remove(self)
        await self.message.delete()

    def __eq__(self: Reminder[T], o: object) -> bool:
        if isinstance(o, Reminder):
            return self.message.id == o.message.id
        else:
            return NotImplemented
