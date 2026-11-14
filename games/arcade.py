"""The single owner of /arcade; add new game commands to this cog."""

from collections.abc import Callable
from typing import TYPE_CHECKING
import logging

import discord
from discord import app_commands
from discord.ext import commands
from pymongo.errors import PyMongoError
from storage.database import COMPETITIVE_GAMES

# Import the ChallengeView classes for each game
from games.tictactoe import ChallengeView as TicTacToeChallengeView
from games.connect4 import ChallengeView as Connect4ChallengeView
from games.battleship import ChallengeView as BattleshipChallengeView
from games.rockpaperscissors import ChallengeView as RockPaperScissorsChallengeView
from games.views import ChallengeView as BaseChallengeView, TimedView
from games.wordle import WordleView
from games.hangman import HangmanView
from games.minesweeper import MinesweeperView

if TYPE_CHECKING:
    from bot import GameBot

# This cog is the single owner of the /arcade command group. Add new game commands to this cog.
@app_commands.guild_only()
class Arcade(commands.GroupCog, group_name="arcade", group_description="Arcade games"):
    def __init__(self, bot: "GameBot"):
        super().__init__()
        self.store = bot.store

    @app_commands.command(description="Show total 1v1 stats or filter by game in this server.")
    @app_commands.choices(game=[app_commands.Choice(name=name, value=key) for key, name in COMPETITIVE_GAMES.items()])
    async def stats(self, interaction: discord.Interaction, member: discord.Member | None = None, game: str | None = None) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("View stats in a server.", ephemeral=True)
            return
        await interaction.response.defer()
        member = member or interaction.user
        try:
            stats = await self.store.get_player_stats(interaction.guild_id, member.id, game=game)
        except PyMongoError:
            logging.getLogger(__name__).exception("Could not read player stats")
            await interaction.followup.send("Stats are unavailable. Please try again later.")
            return
        title = COMPETITIVE_GAMES.get(game, "All 1v1 Games")
        embed = discord.Embed(title=f"{title} — Server Stats", description=member.mention)
        for label, key in (("Played", "played"), ("Total wins", "wins"), ("Losses", "losses"), ("Draws", "draws")):
            embed.add_field(name=label, value=str(stats[key]))
        embed.set_footer(text="Completed 1v1 matches only. Solo games and timeouts are excluded.")
        await interaction.followup.send(embed=embed)

    @app_commands.command(description="Show this server's top 10 players by total 1v1 wins, or filter by game.")
    @app_commands.choices(game=[app_commands.Choice(name=name, value=key) for key, name in COMPETITIVE_GAMES.items()])
    async def leaderboard(self, interaction: discord.Interaction, game: str | None = None) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("View leaderboards in a server.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            rows = await self.store.get_leaderboard(interaction.guild_id, game=game)
        except PyMongoError:
            logging.getLogger(__name__).exception("Could not read leaderboard")
            await interaction.followup.send("The leaderboard is unavailable. Please try again later.")
            return
        lines = [
            f"{rank}. <@{row['_id']}> — {row['wins']}W / {row['losses']}L / {row['draws']}D"
            for rank, row in enumerate(rows, 1)
        ]
        title = COMPETITIVE_GAMES.get(game, "All 1v1 Games")
        embed = discord.Embed(
            title=f"{title} — Server Leaderboard",
            description="\n".join(lines) or "No completed matches yet. Start a 1v1 game with /arcade!",
        )
        embed.set_footer(text="Ranked by wins, then draws, then fewest losses. Completed 1v1 matches only.")
        await interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    # Arcade command to show available /arcade commands
    @app_commands.command(description="Show the bot's available /arcade commands.")
    async def info(self, interaction: discord.Interaction) -> None:
        embeds = []
        for category, title, color in (
            ("1v1", "⚔️ | 1v1 Activities", discord.Color.blurple()),
            ("solo", "🗡️ | Solo Activities", discord.Color.blurple()),
        ):
            commands = [
                f"`/{command.qualified_name}`"
                for command in self.walk_app_commands()
                if command.extras.get("activity") == category
            ]
            embed = discord.Embed(
                title=title,
                description="\n".join(commands) or "No commands in this category yet.",
                color=color,
            )
            embeds.append(embed)
        await interaction.response.send_message(embeds=embeds)

    # 1v1 game commands
    @app_commands.command(description="Challenge another member to tic-tac-toe.", extras={"activity": "1v1"})
    async def tictactoe(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._challenge(interaction, opponent, TicTacToeChallengeView, "Tic Tac Toe")

    @app_commands.command(description="Challenge another member to Connect 4.", extras={"activity": "1v1"})
    async def connect4(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._challenge(interaction, opponent, Connect4ChallengeView, "Connect 4")

    @app_commands.command(description="Challenge another member to Battleship.", extras={"activity": "1v1"})
    async def battleship(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._challenge(interaction, opponent, BattleshipChallengeView, "Battleship")

    @app_commands.command(description="Challenge another member to Rock Paper Scissors.", extras={"activity": "1v1"})
    async def rockpaperscissors(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._challenge(interaction, opponent, RockPaperScissorsChallengeView, "Rock Paper Scissors")

    # Solo game commands
    @app_commands.command(description="Play a solo game of Minesweeper.", extras={"activity": "solo"})
    async def minesweeper(self, interaction: discord.Interaction) -> None:
        await self._solo(interaction, MinesweeperView, "Minesweeper")

    @app_commands.command(description="Play a solo game of Hangman.", extras={"activity": "solo"})
    async def hangman(self, interaction: discord.Interaction) -> None:
        await self._solo(interaction, HangmanView, "Hangman")

    @app_commands.command(description="Play a solo game of Wordle.", extras={"activity": "solo"})
    async def wordle(self, interaction: discord.Interaction) -> None:
        await self._solo(interaction, WordleView, "Wordle")

    async def _solo(
        self, interaction: discord.Interaction, view_type: Callable[[discord.Member], TimedView], game_name: str,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Start games in a server.", ephemeral=True)
            return
        try:
            view = view_type(interaction.user)
        except (OSError, ValueError):
            logging.getLogger(__name__).exception("Could not start %s", game_name)
            await interaction.response.send_message(f"{game_name} is unavailable. Please try again later.", ephemeral=True)
            return
        await view.send(interaction)

    async def _challenge(
        self, interaction: discord.Interaction, opponent: discord.Member,
        view_type: type[BaseChallengeView], game_name: str,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Start games in a server.", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message("You can't challenge a bot.", ephemeral=True)
            return
        if opponent.id == interaction.user.id:
            await interaction.response.send_message("You can't challenge yourself.", ephemeral=True)
            return

        view = view_type(interaction.user, opponent, store=self.store, guild_id=interaction.guild_id)
        embed = discord.Embed(
            title=f"{game_name} Challenge",
            description=f"{interaction.user.mention} has challenged {opponent.mention} to {game_name}.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Challenge expires after 2 minutes.")
        await view.send(interaction, embed=embed)


async def setup(bot: "GameBot") -> None:
    if bot.command_guild is None:
        await bot.add_cog(Arcade(bot))
    else:
        await bot.add_cog(Arcade(bot), guild=bot.command_guild)
