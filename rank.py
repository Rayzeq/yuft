from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
)

from utils import Mention

if TYPE_CHECKING:
    import discord


@dataclass
class Database:
    channel: discord.abc.Messageable

    async def get(
        self: Database,
        owner: discord.User | discord.Member,
    ) -> Rank:
        rank = await self.fetch(owner.id)
        if rank:
            return rank

        rank_ = Rank(
            Mention(owner),
            0,
            0,
            None,  # type: ignore [arg-type] # we set it later
        )

        rank_.message = await self.channel.send(content=rank_.serialize())
        return rank_

    async def fetch_all(self: Database, *, limit: int | None = None) -> list[Rank]:
        return [x for x in [Rank.from_message(msg) async for msg in self.channel.history(limit=limit)] if x is not None]

    async def fetch(self: Database, user_id: int) -> Rank | None:
        async for msg in self.channel.history():
            rank = Rank.from_message(msg)
            if rank and rank.owner.id == user_id:
                return rank
        return None


@dataclass
class Rank:
    owner: Mention
    proposed: int
    participated: int

    message: discord.Message

    @classmethod
    def from_message(cls: type[Rank], message: discord.Message) -> Rank | None:
        (
            owner,
            proposed,
            participated,
        ) = message.content.split(" ")

        return cls(Mention(owner), int(proposed), int(participated), message)

    def serialize(self: Rank) -> str:
        return " ".join(
            (
                str(self.owner),
                str(self.proposed),
                str(self.participated),
            ),
        )

    async def save(self: Rank) -> None:
        await self.message.edit(content=self.serialize())
