from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

import discord
from discord import app_commands

from utils import Mention, Timestamp

if TYPE_CHECKING:
    from datetime import datetime


class UnknownCarpool(Exception):
    pass


class Client(discord.Client, ABC):
    carpool_database: Database


@dataclass
class Database:
    channel: discord.abc.Messageable

    async def create(
        self: Database,
        owner: discord.User | discord.Member,
        date: datetime,
        departure_place: str,
        arrival_place: str,
        max_distance: str,
        duration: str,
        seats: int,
    ) -> Carpool:
        carpool = Carpool(
            Mention(owner),
            Timestamp(date),
            departure_place,
            arrival_place,
            max_distance,
            duration,
            seats,
            [],
            None,  # type: ignore [arg-type] # we set it later
        )

        carpool.message = await self.channel.send(content=carpool.serialize())
        return carpool

    async def fetch_all(self: Database, *, limit: int | None = None) -> list[Carpool]:
        return [
            x
            for x in [await Carpool.from_message(msg) async for msg in self.channel.history(limit=limit)]
            if x is not None
        ]

    async def fetch(self: Database, id: int) -> Carpool | None:
        async for msg in self.channel.history():
            if str(msg.id).endswith(str(id)):
                break
        else:
            return None

        return await Carpool.from_message(msg)


@dataclass
class Carpool:
    DELIMITER: ClassVar[str] = "\\\\!"

    owner: Mention
    date: Timestamp
    departure_place: str
    arrival_place: str
    max_distance: str
    duration: str
    seats: int

    joiners: list[Mention]
    message: discord.Message

    def serialize(self: Carpool) -> str:
        def remove_delimiter(s: str) -> str:
            if self.DELIMITER in s:
                return remove_delimiter(s.replace(self.DELIMITER, ""))
            return s

        return self.DELIMITER.join(
            (
                str(self.owner),
                str(self.date),
                remove_delimiter(self.departure_place),
                remove_delimiter(self.arrival_place),
                remove_delimiter(self.max_distance),
                remove_delimiter(self.duration),
                str(self.seats),
                ";".join(map(str, self.joiners)),
            ),
        )

    @classmethod
    async def from_message(cls: type[Carpool], message: discord.Message) -> Carpool | None:
        (
            owner,
            date,
            departure_place,
            arrival_place,
            max_distance,
            duration,
            seats,
            joiners,
        ) = message.content.split(cls.DELIMITER)

        date_ = Timestamp(date)
        if date_.timestamp < time.time():
            await message.delete()
            return None

        return cls(
            Mention(owner),
            date_,
            departure_place,
            arrival_place,
            max_distance,
            duration,
            int(seats),
            list(map(Mention, filter(bool, joiners.split(";")))),
            message,
        )

    async def save(self: Carpool) -> None:
        await self.message.edit(content=self.serialize())

    async def delete(self: Carpool) -> list[Mention]:
        await self.message.delete()
        return self.joiners

    def __eq__(self: Carpool, o: object) -> bool:
        if isinstance(o, Carpool):
            return self.message.id == o.message.id
        else:
            return NotImplemented


class CarpoolTransformer(app_commands.Transformer, ABC):
    @abstractmethod
    def check(self: CarpoolTransformer, interaction: discord.Interaction[Client], carpool: Carpool) -> bool:
        ...

    async def autocomplete(
        self: CarpoolTransformer,
        interaction: discord.Interaction[Client],
        value: float | str,
    ) -> list[app_commands.Choice[int | float | str]]:
        return [
            app_commands.Choice[int | float | str](name=str(carpool.message.id), value=str(carpool.message.id))
            for carpool in await interaction.client.carpool_database.fetch_all()
            if str(value) in str(carpool.message.id) and self.check(interaction, carpool)
        ][:25]

    async def transform(
        self: CarpoolTransformer,
        interaction: discord.Interaction[Client],
        value: str,
    ) -> Carpool:
        msg = "Impossible de trouver ce covoiturage"
        try:
            id = int(value.strip())
        except ValueError as e:
            raise UnknownCarpool(msg) from e

        carpool = await interaction.client.carpool_database.fetch(id)
        if carpool is None:
            raise UnknownCarpool(msg)

        return carpool


class OwnedCarpoolTransformer(CarpoolTransformer):
    def check(self: OwnedCarpoolTransformer, interaction: discord.Interaction[Client], carpool: Carpool) -> bool:
        # order is important here, otherwise the comparison fails
        return carpool.owner == interaction.user

    async def transform(
        self: OwnedCarpoolTransformer,
        interaction: discord.Interaction[Client],
        value: str,
    ) -> Carpool:
        carpool = await super().transform(interaction, value)

        if carpool.owner != interaction.user:
            msg = "Vous n'êtes pas le créateur de ce covoiturage"
            raise UnknownCarpool(msg)

        return carpool


class UnjoinedCarpoolTransformer(CarpoolTransformer):
    def check(self: UnjoinedCarpoolTransformer, interaction: discord.Interaction[Client], carpool: Carpool) -> bool:
        return interaction.user not in carpool.joiners

    async def transform(
        self: UnjoinedCarpoolTransformer,
        interaction: discord.Interaction[Client],
        value: str,
    ) -> Carpool:
        carpool = await super().transform(interaction, value)

        if interaction.user in carpool.joiners:
            msg = "Vous faite déjà partie de ce covoiturage"
            raise UnknownCarpool(msg)

        return carpool


class JoinedCarpoolTransformer(CarpoolTransformer):
    def check(self: JoinedCarpoolTransformer, interaction: discord.Interaction[Client], carpool: Carpool) -> bool:
        return interaction.user in carpool.joiners

    async def transform(
        self: JoinedCarpoolTransformer,
        interaction: discord.Interaction[Client],
        value: str,
    ) -> Carpool:
        carpool = await super().transform(interaction, value)

        if interaction.user not in carpool.joiners:
            msg = "Vous ne faites pas partie de ce covoiturage"
            raise UnknownCarpool(msg)

        return carpool
