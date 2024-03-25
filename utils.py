from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Coroutine, TypedDict, cast

import dateutil
import dateutil.parser
import discord
from discord import app_commands

if TYPE_CHECKING:
    from asyncio import Task

TIMEZONE = datetime.utcnow().astimezone().tzinfo


class InteractionData(TypedDict):
    type: int
    name: str


class ArgumentInteractionData(InteractionData):
    value: Any


class CommandInteractionData(InteractionData):
    options: list[InteractionData]


class ToplevelInteractionData(CommandInteractionData):
    id: str


def format_interaction(interaction: discord.Interaction[discord.Client] | InteractionData) -> str:
    if isinstance(interaction, discord.Interaction):
        is_toplevel = True
        data: InteractionData = cast(ToplevelInteractionData, interaction.data)
    else:
        is_toplevel = False
        data = cast(InteractionData, interaction)

    if "options" in data:
        data1 = cast(CommandInteractionData, data)
        return (
            f"{'/' if is_toplevel else ''}{data1['name']} {' '.join(format_interaction(x) for x in data1['options'])}"
        )
    else:
        data2 = cast(ArgumentInteractionData, data)
        return f"{data2['name']}={data2['value']}"


class Mention:
    id: int

    def __init__(self: Mention, user: Mention | discord.User | discord.Member | int | str) -> None:
        if isinstance(user, (Mention, discord.User, discord.Member)):
            self.id = user.id
        elif isinstance(user, str):
            self.id = int(user.removeprefix("<@").removesuffix(">"))
        else:
            self.id = user

    def __eq__(self: Mention, o: object) -> bool:
        if isinstance(o, (Mention, discord.User, discord.Member)):
            return self.id == o.id
        elif isinstance(o, int):
            return self.id == o
        else:
            return NotImplemented

    def __str__(self: Mention) -> str:
        return f"<@{self.id}>"

    __repr__ = __str__


class ChannelMention:
    id: int

    def __init__(self: ChannelMention, channel: ChannelMention | discord.TextChannel | int | str) -> None:
        if isinstance(channel, (ChannelMention, discord.TextChannel)):
            self.id = channel.id
        elif isinstance(channel, str):
            self.id = int(channel.removeprefix("<#").removesuffix(">"))
        else:
            self.id = channel

    def __eq__(self: ChannelMention, o: object) -> bool:
        if isinstance(o, (ChannelMention, discord.TextChannel)):
            return self.id == o.id
        elif isinstance(o, int):
            return self.id == o
        else:
            return NotImplemented

    def __str__(self: ChannelMention) -> str:
        return f"<#{self.id}>"

    __repr__ = __str__


class Timestamp:
    timestamp: int

    def __init__(self: Timestamp, value: int | str | datetime) -> None:
        if isinstance(value, datetime):
            self.timestamp = int(value.timestamp())
        elif isinstance(value, str):
            self.timestamp = int(value.split(":")[1].removesuffix(">"))
        else:
            self.timestamp = value

    def as_datetime(self: Timestamp) -> datetime:
        return datetime.fromtimestamp(self.timestamp, TIMEZONE)

    def __str__(self: Timestamp) -> str:
        return f"<t:{self.timestamp}>"

    __repr__ = __str__


class TaskPool:
    tasks: set[Task[None]]

    def __init__(self: TaskPool) -> None:
        self.tasks = set()

    def run(self: TaskPool, task: Callable[[], Coroutine[Any, Any, None]]) -> None:
        event_loop = asyncio.get_running_loop()
        task_ = event_loop.create_task(task())
        self.tasks.add(task_)
        task_.add_done_callback(self.tasks.discard)

    def run_after(self: TaskPool, delay: float, callback: Callable[[], Awaitable[None]]) -> None:
        async def inner_callback() -> None:
            await asyncio.sleep(delay)
            await callback()

        self.run(inner_callback)


# dateutil typing is non-existant and type stubs are terrible, just ignore the next errors
class DatetimeConversionError(Exception):
    pass


class TimeTransformer(app_commands.Transformer):
    class ParserInfo(dateutil.parser.parserinfo):
        HMS = [
            ("h", "heure", "heures"),
            ("m", "min", "minute", "minutes"),
            ("s", "seconde", "secondes"),
        ]

    async def transform(
        self: TimeTransformer,
        _interaction: discord.Interaction[discord.Client],
        value: str,
    ) -> datetime:
        value = value.strip()
        try:
            return dateutil.parser.parse(value, parserinfo=self.ParserInfo()).astimezone(TIMEZONE)
        except dateutil.parser.ParserError as e:
            msg = f"Impossible de convertir {value} en heure"
            raise DatetimeConversionError(msg) from e


class DateTransformer(app_commands.Transformer):
    class ParserInfo(dateutil.parser.parserinfo):
        WEEKDAYS = [
            ("lun", "lundi"),
            ("mar", "mardi"),
            ("mer", "mercredi"),
            ("jeu", "jeudi"),
            ("ven", "vendredi"),
            ("sam", "samedi"),
            ("dim", "dimanche"),
        ]
        MONTHS = [
            ("janv", "janvier"),
            ("févr", "février", "fevr", "fevrier"),
            ("mars", "mars"),
            ("avril", "avril"),
            ("mai", "mai"),
            ("juin", "juin"),
            ("juil", "juillet"),
            ("août", "aout"),
            ("sept", "septembre"),
            ("oct", "octobre"),
            ("nov", "novembre"),
            ("déc", "décembre", "dec", "decembre"),
        ]

    async def transform(
        self: DateTransformer,
        _interaction: discord.Interaction[discord.Client],
        value: str,
    ) -> datetime:
        value = value.strip()
        try:
            return dateutil.parser.parse(value, parserinfo=self.ParserInfo(), dayfirst=True).astimezone(TIMEZONE)
        except dateutil.parser.ParserError as e:
            msg = f"Impossible de convertir {value} en jour"
            raise DatetimeConversionError(msg) from e


class TimedeltaTransformer(app_commands.Transformer):
    async def transform(
        self: TimedeltaTransformer,
        interaction: discord.Interaction[discord.Client],
        value: str,
    ) -> timedelta:
        try:
            time: datetime = await TimeTransformer().transform(interaction, value)
            return datetime.combine(date(1, 1, 1), time.time(), TIMEZONE) - datetime(1, 1, 1, tzinfo=TIMEZONE)
        except DatetimeConversionError as e:
            msg = f"Impossible de convertir {value} en durée"
            raise DatetimeConversionError(msg) from e
