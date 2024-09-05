#!/usr/bin/env nix-shell
#!nix-shell -i python3 -p "python3.withPackages(ps: [ ps.discordpy ps.dateutil ])"
from __future__ import annotations

import datetime
import os
import time
import traceback
from functools import partial

import discord
from discord import app_commands

from carpool import (
    Carpool,
    JoinedCarpoolTransformer,
    OwnedCarpoolTransformer,
    UnjoinedCarpoolTransformer,
    UnknownCarpool,
)
from carpool import Database as CarpoolDatabase
from rank import Database as RankDatabase
from reminder import Database as ReminderDatabase
from reminder import Reminder
from utils import (
    DatetimeConversionError,
    DateTransformer,
    Mention,
    Timestamp,
    TimeTransformer,
    format_interaction,
)

CARPOOL_DATABASE_CHANNEL = 1220434775312171110
REMINDER_DATABASE_CHANNEL = 1219923754411364452
RANK_DATABASE_CHANNEL = 1221238734079393853


class UnreachableError(Exception):
    pass


class Yuft(discord.Client):
    carpool_database: CarpoolDatabase
    reminder_database: ReminderDatabase[Carpool]

    def __init__(self: Yuft, *, intents: discord.Intents) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self: Yuft) -> None:
        if self.user is None:  # makes mypy happy
            raise UnreachableError

        print(f"Logged in as {self.user.name}#{self.user.discriminator}")
        self.carpool_database = CarpoolDatabase(self.get_channel(CARPOOL_DATABASE_CHANNEL))
        self.reminder_database = ReminderDatabase(
            self.get_channel(REMINDER_DATABASE_CHANNEL),
            lambda x: str(x.message.id),
            lambda x: self.carpool_database.fetch(int(x)),
        )
        self.rank_database = RankDatabase(self.get_channel(RANK_DATABASE_CHANNEL))

        self.reminder_database.task_pool.run(partial(self.reminder_database.run, self.send_reminder))

        await self.tree.sync()
        print("Commands are synced")

    async def send_reminder(self: Yuft, reminder: Reminder[Carpool]) -> None:
        await group_dispatch(
            client,
            [reminder.user],
            await self.fetch_channel(reminder.fallback_channel.id),
            f"Votre covoiturage part dans {int((reminder.event_date.timestamp - time.time()) / 60)} minutes",
        )


intents = discord.Intents.default()
client = Yuft(intents=intents)


@client.tree.error
async def on_error(interaction: discord.Interaction[discord.Client], error: BaseException) -> None:
    if isinstance(error, app_commands.TransformerError) and isinstance(
        error.__cause__,
        (UnknownCarpool, DatetimeConversionError),
    ):
        try:
            await interaction.response.send_message(error.__cause__.args[0], ephemeral=True)
        except discord.InteractionResponded:
            await interaction.followup.send(error.__cause__.args[0], ephemeral=True)
        return

    error_msg_intro = (
        f"\x1b[31m[ERROR]\x1b[0m Following interaction generated an error `{format_interaction(interaction)}`"
    )
    error_msg = "\n".join(f"\x1b[31m[ERROR]\x1b[0m {line}" for line in traceback.format_exc().split("\n"))
    print(f"{error_msg_intro}\n{error_msg}")

    if isinstance(error, (app_commands.CommandInvokeError, app_commands.TransformerError)):
        error = error.__cause__

    if isinstance(error, discord.Forbidden):
        msg = "**ERREUR**: Je n'ai pas assez de permissions pour lancer cette commande"
    elif isinstance(error, discord.app_commands.CommandNotFound):
        return
    else:
        msg = f"**{error.__class__.__name__}**: {error}"

    try:
        await interaction.response.send_message(msg, ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(msg, ephemeral=True)


async def group_dispatch(
    client: discord.Client,
    users: list[Mention],
    channel: discord.abc.Messageable,
    message: str,
) -> None:
    cannot_dm = []
    for mention in users:
        user = await client.fetch_user(mention.id)
        if user is None:
            cannot_dm.append(mention)
            continue

        try:
            if user.dm_channel is None:
                await user.create_dm()
            await user.dm_channel.send(message)
        except discord.Forbidden:
            print(f"Cannot DM user: {user.display_name}")
            cannot_dm.append(user)

    if cannot_dm:
        await channel.send(message + "\n" + " ".join(map(str, cannot_dm)))


class Covoiturage(app_commands.Group):
    """Gestion des covoiturages."""

    @app_commands.describe(jour="Le jour du départ")
    @app_commands.describe(heure="L'heure du départ")
    @app_commands.describe(lieu_depart="Le lieu d'où vous partez")
    @app_commands.describe(lieu_arrivee="Le lieu d'arrivée")
    @app_commands.describe(
        distance="La distance à laquelle vous acceptez d'aller chechez des gens par rapport au lieu de départ",
    )
    @app_commands.describe(duree="La durée approximative du trajet")
    @app_commands.describe(places="Le nombre de places disponibles")
    async def _creer(
        self: Covoiturage,
        interaction: discord.Interaction[discord.Client],
        jour: app_commands.Transform[datetime.datetime, DateTransformer],
        heure: app_commands.Transform[datetime.datetime, TimeTransformer],
        lieu_depart: str,
        lieu_arrivee: str,
        distance: str,
        duree: str,
        places: int,
    ) -> None:
        """Créer un nouveau covoiturage."""
        await client.carpool_database.create(
            interaction.user,
            datetime.datetime.combine(jour.date(), heure.time(), heure.tzinfo),
            lieu_depart,
            lieu_arrivee,
            distance,
            duree,
            places,
        )

        await interaction.response.send_message("Votre covoiturage à bien été ajouté", ephemeral=True)

        rank = await client.rank_database.get(interaction.user)
        rank.proposed += 1
        await rank.save()

    @app_commands.describe(carpool="L'id du covoiturage")
    @app_commands.rename(carpool="id")
    @app_commands.describe(jour="Le jour du départ")
    @app_commands.describe(heure="L'heure du départ")
    @app_commands.describe(lieu_depart="Le lieu d'où vous partez")
    @app_commands.describe(lieu_arrivee="Le lieu d'arrivée")
    @app_commands.describe(
        distance="La distance à laquelle vous acceptez d'aller chechez des gens par rapport au lieu de départ",
    )
    @app_commands.describe(duree="La durée approximative du trajet")
    @app_commands.describe(places="Le nombre de places disponibles")
    async def _modifier(
        self: Covoiturage,
        interaction: discord.Interaction[discord.Client],
        carpool: app_commands.Transform[Carpool, OwnedCarpoolTransformer],
        jour: app_commands.Transform[datetime.datetime, DateTransformer] | None = None,
        heure: app_commands.Transform[datetime.datetime, TimeTransformer] | None = None,
        lieu_depart: str | None = None,
        lieu_arrivee: str | None = None,
        distance: str | None = None,
        duree: str | None = None,
        places: int | None = None,
    ) -> None:
        """Modifier un covoiturage."""
        date = carpool.date.as_datetime()
        changelog = []
        if jour is not None:
            changelog.append(f"**Jour**: {date.strftime('%d %b %Y')} -> {jour.strftime('%d %b %Y')}")
            carpool.date = Timestamp(datetime.datetime.combine(jour.date(), date.time(), date.tzinfo))
        if heure is not None:
            changelog.append(f"**Heure**: {date.strftime('%Hh%Mm')} -> {heure.strftime('%Hh%Mm')}")
            carpool.date = Timestamp(datetime.datetime.combine(date.date(), heure.time(), date.tzinfo))
        if lieu_depart is not None:
            changelog.append(f"**Lieu de départ**: {carpool.departure_place} -> {lieu_depart}")
            carpool.departure_place = lieu_depart
        if lieu_arrivee is not None:
            changelog.append(f"**Lieu d'arrivé**: {carpool.arrival_place} -> {lieu_arrivee}")
            carpool.arrival_place = lieu_arrivee
        if distance is not None:
            changelog.append(f"**Distance**: {carpool.max_distance} -> {distance}")
            carpool.max_distance = distance
        if duree is not None:
            changelog.append(f"**Durée du trajet**: {carpool.duration} -> {duree}")
            carpool.duration = duree
        if places is not None:
            changelog.append(f"**Places disponibles**: {carpool.seats} -> {places}")
            carpool.seats = places

        await carpool.save()

        if jour is not None or heure is not None:
            for reminder in await client.reminder_database.fetch_all():
                if reminder.source == carpool:
                    await reminder.delete()

                    await client.reminder_database.create(
                        carpool.date,
                        Timestamp(
                            carpool.date.timestamp - (reminder.event_date.timestamp - reminder.remind_date.timestamp),
                        ),
                        reminder.user,
                        reminder.fallback_channel,
                        reminder.source,
                    )

        await interaction.response.send_message("Le covoiturage à bien été modifié", ephemeral=True)
        c = "\n".join(changelog)
        await group_dispatch(
            interaction.client,
            carpool.joiners,
            interaction.channel,
            f":warning: Un covoiturage auquel vous faite partie à été modifié\n{c}",
        )

    @app_commands.describe(ids_longs="Affiche les IDs entier, utile si plusieurs covoiturages ont le même id raccourci")
    @app_commands.describe(invisible="Vous seul pourrez voir la liste (activé par défaut)")
    async def _liste(
        self: Covoiturage,
        interaction: discord.Interaction[discord.Client],
        *,
        ids_longs: bool = False,
        invisible: bool = True,
    ) -> None:
        """Lister les covoiturage existant."""
        entries = await client.carpool_database.fetch_all()
        entries_str = []
        for entry in entries:
            msg = f"- **Id**: {entry.message.id if ids_longs else str(entry.message.id)[-8:]}\n"
            msg += f"  - **Conducteur**: {entry.owner}\n"
            msg += f"  - **Date**: {entry.date}\n"
            msg += f"  - **Départ**: {entry.departure_place} (+/- {entry.max_distance})\n"
            msg += f"  - **Arrivée**: {entry.arrival_place} en {entry.duration}\n"
            msg += f"  - **Places disponibles**: {entry.seats - len(entry.joiners)} ({entry.seats} en tout)\n"
            msg += f"  - **Réservataires**: {', '.join(map(str, entry.joiners))}\n"
            entries_str.append(msg)

        if entries_str:
            msg = "Voici les covoiturages disponibles:\n" + "\n".join(entries_str)
        else:
            msg = "Il n'y a pas de covoiturages disponibles"
        await interaction.response.send_message(msg, ephemeral=invisible)

    @app_commands.describe(carpool="L'id du covoiturage")
    @app_commands.rename(carpool="id")
    @app_commands.describe(
        rappel="Si présent, le bot vous enverra un rappel x minutes avant l'heure du covoiturage",
    )
    async def _rejoindre(
        self: Covoiturage,
        interaction: discord.Interaction[discord.Client],
        carpool: app_commands.Transform[Carpool, UnjoinedCarpoolTransformer],
        rappel: int | None = None,
    ) -> None:
        """Rejoindre un covoiturage."""
        if len(carpool.joiners) >= carpool.seats:
            await interaction.response.send_message("Il n'y a plus de place dans ce covoiturage", ephemeral=True)
            return

        carpool.joiners.append(Mention(interaction.user))
        await carpool.save()

        if rappel is not None:
            await client.reminder_database.create(
                carpool.date,
                Timestamp(carpool.date.timestamp - rappel * 60),
                interaction.user,
                interaction.channel,
                carpool,
            )

        await interaction.response.send_message("Vous avez été ajouté au covoiturage", ephemeral=True)

        rank = await client.rank_database.get(interaction.user)
        rank.participated += 1
        await rank.save()

    @app_commands.describe(carpool="L'id du covoiturage")
    @app_commands.rename(carpool="id")
    async def _quitter(
        self: Covoiturage,
        interaction: discord.Interaction[discord.Client],
        carpool: app_commands.Transform[Carpool, JoinedCarpoolTransformer],
    ) -> None:
        """Quitter un covoiturage."""
        carpool.joiners.remove(Mention(interaction.user))
        await carpool.save()

        for reminder in await client.reminder_database.fetch_all():
            if reminder.source == carpool:
                await reminder.delete()

        await interaction.response.send_message("Vous avez quitté le covoiturage", ephemeral=True)

        rank = await client.rank_database.get(interaction.user)
        rank.participated -= 1
        await rank.save()

    @app_commands.describe(carpool="L'id du covoiturage")
    @app_commands.rename(carpool="id")
    async def _supprimer(
        self: Covoiturage,
        interaction: discord.Interaction[discord.Client],
        carpool: app_commands.Transform[Carpool, OwnedCarpoolTransformer],
    ) -> None:
        """Supprimer un covoiturage."""
        joiners = await carpool.delete()

        for reminder in await client.reminder_database.fetch_all():
            if reminder.source == carpool:
                await reminder.delete()

        await interaction.response.send_message("Le covoiturage à bien été supprimé", ephemeral=True)
        await group_dispatch(
            interaction.client,
            joiners,
            interaction.channel,
            ":warning: Un covoiturage que vous aviez réservé à été supprimé",
        )

        rank = await client.rank_database.get(interaction.user)
        rank.proposed -= 1
        await rank.save()

    @app_commands.describe(invisible="Vous seul pourrez voir la liste (activé par défaut)")
    async def _rank(
        self: Covoiturage,
        interaction: discord.Interaction[discord.Client],
        *,
        invisible: bool = True,
    ) -> None:
        """Vérifier votre rang."""
        ranks = await client.rank_database.fetch_all()
        ranks.sort(key=lambda x: x.proposed * 1.5 + x.participated, reverse=True)
        position = next((i for i, rank in enumerate(ranks) if rank.owner == interaction.user), len(ranks))
        rank = await client.rank_database.get(interaction.user)

        await interaction.response.send_message(
            f"Vous êtes #{position + 1} avec {rank.proposed} covoiturages proposés et {rank.participated} covoiturages pris",
            ephemeral=invisible,
        )

    @app_commands.describe(invisible="Vous seul pourrez voir la liste (activé par défaut)")
    async def _leaderboard(
        self: Covoiturage,
        interaction: discord.Interaction[discord.Client],
        *,
        invisible: bool = True,
    ) -> None:
        """Montre le rang des gens les mieux classés."""
        ranks = await client.rank_database.fetch_all()
        ranks.sort(key=lambda x: x.proposed * 1.5 + x.participated, reverse=True)

        lines = []
        for i, rank in enumerate(ranks[:10]):
            lines.append(f"#{i + 1} {rank.owner} - {rank.proposed} proposés, {rank.participated} pris")

        await interaction.response.send_message(
            "\n".join(lines),
            ephemeral=invisible,
        )

    creer = app_commands.command(name="creer")(_creer)
    c = app_commands.command(name="c")(_creer)

    modifier = app_commands.command(name="modifier")(_modifier)
    m = app_commands.command(name="m")(_modifier)

    liste = app_commands.command(name="liste")(_liste)
    l = app_commands.command(name="l")(_liste)

    rejoindre = app_commands.command(name="rejoindre")(_rejoindre)
    r = app_commands.command(name="r")(_rejoindre)

    quitter = app_commands.command(name="quitter")(_quitter)
    q = app_commands.command(name="q")(_quitter)

    supprimer = app_commands.command(name="supprimer")(_supprimer)
    s = app_commands.command(name="s")(_supprimer)

    rang = app_commands.command(name="rang")(_rank)
    classement = app_commands.command(name="classement")(_leaderboard)


client.tree.add_command(Covoiturage())
client.tree.add_command(Covoiturage(name="cov"))
client.run(os.getenv("DISCORD_TOKEN"))
