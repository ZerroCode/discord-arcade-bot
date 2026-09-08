"""Hangman rules and a solo Discord game with a letter-entry form."""

import logging
from pathlib import Path
import random
import re

import discord

from games.views import GAME_TIMEOUT, TimedView

COMMAND = "/arcade hangman"
MAX_MISTAKES = 6
WORDLIST_PATH = Path(__file__).resolve().parent.parent / "data" / "hangman" / "wordlist.txt"


def load_words(path: Path = WORDLIST_PATH) -> tuple[str, ...]:
    """Read one word per line for each new game, ignoring blanks and duplicates."""
    words = dict.fromkeys(
        line.strip().lower()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    )
    if not words:
        raise ValueError(f"Word list {path.name} is empty.")
    for word in words:
        if re.fullmatch(r"[a-z]{2,30}", word) is None:
            raise ValueError(f"Invalid word in {path.name}: {word!r}. Use 2-30 letters A-Z.")
    return tuple(words)


class HangmanGame:
    def __init__(self, answer: str):
        answer = answer.strip().lower()
        if re.fullmatch(r"[a-z]{2,30}", answer) is None:
            raise ValueError("The answer must contain 2-30 letters using A-Z.")
        self.answer = answer
        self.guesses: list[str] = []

    @property
    def wrong_guesses(self) -> list[str]:
        return [letter for letter in self.guesses if letter not in self.answer]

    @property
    def remaining(self) -> int:
        return MAX_MISTAKES - len(self.wrong_guesses)

    @property
    def won(self) -> bool:
        return set(self.answer) <= set(self.guesses)

    @property
    def finished(self) -> bool:
        return self.won or self.remaining == 0

    @property
    def masked_word(self) -> str:
        return " ".join(letter.upper() if letter in self.guesses else "_" for letter in self.answer)

    def submit(self, value: str) -> None:
        if self.finished:
            raise ValueError("This game has ended.")
        letter = value.strip().lower()
        if re.fullmatch(r"[a-z]", letter) is None:
            raise ValueError("Choose a single letter using A-Z.")
        if letter in self.guesses:
            raise ValueError("You've already guessed that letter.")
        self.guesses.append(letter)


class _GuessModal(discord.ui.Modal, title="Hangman - Guess a letter"):
    letter = discord.ui.TextInput(label="Letter (A-Z)", min_length=1, max_length=1)

    def __init__(self, view: "HangmanView"):
        super().__init__(timeout=GAME_TIMEOUT)
        self.game_view = view
        self.attempt = len(view.game.guesses)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.game_view.on_guess(interaction, self.letter.value, self.attempt)
        finally:
            self.stop()
            if self.game_view.guess_modal is self:
                self.game_view.guess_modal = None

    async def on_timeout(self) -> None:
        if self.game_view.guess_modal is self:
            self.game_view.guess_modal = None

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await self.game_view.on_error(interaction, error, self)


class HangmanView(TimedView):
    def __init__(self, player: discord.Member, *, game: HangmanGame | None = None):
        if game is None:
            game = HangmanGame(random.SystemRandom().choice(load_words()))
        super().__init__(command=COMMAND, timeout=GAME_TIMEOUT, timeout_title="Hangman - Timed Out")
        self.player = player
        self.game = game
        self.timed_out = False
        self.guess_modal: _GuessModal | None = None

    def close(self) -> None:
        if self.guess_modal is not None:
            self.guess_modal.stop()
            self.guess_modal = None
        super().close()

    def make_embed(self) -> discord.Embed:
        mistakes = len(self.game.wrong_guesses)
        head = "O" if mistakes >= 1 else " "
        torso = "|" if mistakes >= 2 else " "
        left_arm = "/" if mistakes >= 3 else " "
        right_arm = "\\" if mistakes >= 4 else " "
        left_leg = "/" if mistakes >= 5 else " "
        right_leg = "\\" if mistakes >= 6 else " "
        drawing = f" +---+\n |   |\n {head}   |\n{left_arm}{torso}{right_arm}  |\n{left_leg} {right_leg}  |\n     |\n======="
        embed = discord.Embed(
            title="Hangman",
            description=f"{self.player.mention}\n```text\n{drawing}\n\n{self.game.masked_word}\n```",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Mistakes left", value=f"{self.game.remaining}/{MAX_MISTAKES}")
        embed.add_field(name="Incorrect letters", value=", ".join(self.game.wrong_guesses).upper() or "None")
        if self.game.won:
            embed.title = "Hangman - Solved!"
            embed.color = discord.Color.gold()
            embed.add_field(name="Result", value=f"You found **{self.game.answer.upper()}** in {len(self.game.guesses)} guesses.", inline=False)
        elif self.game.finished or self.timed_out:
            embed.title = self.timeout_title if self.timed_out else "Hangman - Game Over"
            embed.color = discord.Color.greyple() if self.timed_out else discord.Color.red()
            embed.add_field(name="Answer", value=f"**{self.game.answer.upper()}**", inline=False)
        else:
            embed.set_footer(text="Expires after 5 minutes of inactivity.")
        return embed

    async def allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(f"This is someone else's game. Start your own with {COMMAND}.", ephemeral=True)
            return False
        if self.closed or self.is_finished():
            await interaction.response.send_message("This game has ended.", ephemeral=True)
            return False
        if self._lock.locked():
            await interaction.response.send_message("A guess is being updated. Please try again.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.primary)
    async def guess(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        async with self._lock:
            if self.guess_modal is not None:
                self.guess_modal.stop()
            modal = self.guess_modal = _GuessModal(self)
            try:
                await interaction.response.send_modal(modal)
            except Exception:
                self.close()
                raise

    async def on_guess(self, interaction: discord.Interaction, value: str, attempt: int | None = None) -> None:
        if not await self.allowed(interaction):
            return
        async with self._lock:
            if attempt is not None and attempt != len(self.game.guesses):
                await interaction.response.send_message("This guess form is out of date. Press Guess again.", ephemeral=True)
                return
            try:
                self.game.submit(value)
            except ValueError as error:
                await interaction.response.send_message(f"{error} Press Guess to try again.", ephemeral=True)
                return
            if self.game.finished:
                self.close()
            else:
                # Modal submissions don't refresh the parent view's timeout.
                self.timeout = GAME_TIMEOUT
            try:
                await interaction.response.edit_message(embed=self.make_embed(), view=self)
            except Exception:
                self.close()
                raise

    async def on_timeout(self) -> None:
        async with self._lock:
            if self.closed:
                return
            self.timed_out = True
            self.close()
            if self.message is not None:
                try:
                    await self.message.edit(embed=self.make_embed(), view=self)
                except discord.HTTPException:
                    logging.getLogger(__name__).exception("Could not update expired Hangman message")
