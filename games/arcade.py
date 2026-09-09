"""The single owner of /arcade; add new game commands to this cog."""

from typing import TYPE_CHECKING
import logging

import discord
from discord import app_commands
from discord.ext import commands

# Import the ChallengeView classes for each game
from games.tictactoe import ChallengeView as TicTacToeChallengeView
from games.connect4 import ChallengeView as Connect4ChallengeView
from games.battleship import ChallengeView as BattleshipChallengeView
from games.rockpaperscissors import ChallengeView as RockPaperScissorsChallengeView
from games.views import ChallengeView as BaseChallengeView
from games.wordle import WordleView
from games.hangman import HangmanView
from games.minesweeper import MinesweeperView

if TYPE_CHECKING:
    from bot import GameBot

# This cog is the single owner of the /arcade command group. Add new game commands to this cog.
@app_commands.guild_only()
class Arcade(commands.GroupCog, group_name="arcade", group_description="Arcade games"):

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
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Start games in a server.", ephemeral=True)
            return
        view = MinesweeperView(interaction.user)
        try:
            await interaction.response.send_message(embed=view.make_embed(), view=view)
            view.message = await interaction.original_response()
        except Exception:
            view.close()
            raise

    @app_commands.command(description="Play a solo game of Hangman.", extras={"activity": "solo"})
    async def hangman(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Start games in a server.", ephemeral=True)
            return
        try:
            view = HangmanView(interaction.user)
        except (OSError, ValueError):
            logging.getLogger(__name__).exception("Could not load Hangman word list")
            await interaction.response.send_message("Hangman's word list is unavailable. Please try again later.", ephemeral=True)
            return
        try:
            await interaction.response.send_message(embed=view.make_embed(), view=view)
            view.message = await interaction.original_response()
        except Exception:
            view.close()
            raise

    @app_commands.command(description="Play a solo game of Wordle.", extras={"activity": "solo"})
    async def wordle(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Start games in a server.", ephemeral=True)
            return
        try:
            view = WordleView(interaction.user)
        except (OSError, ValueError):
            logging.getLogger(__name__).exception("Could not load Wordle word lists")
            await interaction.response.send_message("Wordle's word lists are unavailable. Please try again later.", ephemeral=True)
            return
        try:
            await interaction.response.send_message(embed=view.make_embed(), view=view)
            view.message = await interaction.original_response()
        except Exception:
            view.close()
            raise

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

        view = view_type(interaction.user, opponent)
        embed = discord.Embed(
            title=f"{game_name} Challenge",
            description=f"{interaction.user.mention} has challenged {opponent.mention} to {game_name}.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Challenge expires after 2 minutes.")
        try:
            await interaction.response.send_message(embed=embed, view=view)
            view.message = await interaction.original_response()
        except Exception:
            view.close()
            raise


async def setup(bot: "GameBot") -> None:
    if bot.command_guild is None:
        await bot.add_cog(Arcade())
    else:
        await bot.add_cog(Arcade(), guild=bot.command_guild)
