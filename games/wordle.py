"""Wordle word lists, scoring, and solo Discord game."""

from collections import Counter
from collections.abc import Collection
from functools import lru_cache
from pathlib import Path
import random
import re

import discord

from games.views import GAME_TIMEOUT, TimedView

COMMAND = "/arcade wordle"
MAX_GUESSES = 6
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "wordle"
ABSENT, PRESENT, CORRECT = "⬛", "🟨", "🟩"


def normalize_word(value: str) -> str:
    word = value.strip().lower()
    if re.fullmatch(r"[a-z]{5}", word) is None:
        raise ValueError("Enter a five-letter word using A-Z.")
    return word


def _read_words(path: Path) -> frozenset[str]:
    words = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            words.add(normalize_word(line))
        except ValueError as error:
            raise ValueError(f"Invalid word in {path.name} at line {line_number}.") from error
    if not words:
        raise ValueError(f"Word list {path.name} is empty.")
    return frozenset(words)


@lru_cache(maxsize=1)
def load_word_lists(directory: Path = DATA_DIR) -> tuple[tuple[str, ...], frozenset[str]]:
    """The supplied allowed-guesses file contains only non-answer words."""
    combined = _read_words(directory / "combined_wordlist.txt")
    non_answers = _read_words(directory / "official_allowed_guesses.txt")
    if not non_answers <= combined:
        raise ValueError("The combined Wordle list must include every allowed guess.")
    answers = tuple(sorted(combined - non_answers))
    if not answers:
        raise ValueError("The Wordle lists contain no possible answers.")
    return answers, combined


def score_guess(answer: str, guess: str) -> tuple[str, ...]:
    answer, guess = normalize_word(answer), normalize_word(guess)
    result = [ABSENT] * 5
    remaining = Counter()
    # Exact matches consume letters before misplaced matches are assigned.
    for index, (expected, actual) in enumerate(zip(answer, guess)):
        if expected == actual:
            result[index] = CORRECT
        else:
            remaining[expected] += 1
    for index, actual in enumerate(guess):
        if result[index] != CORRECT and remaining[actual] > 0:
            result[index] = PRESENT
            remaining[actual] -= 1
    return tuple(result)


class WordleGame:
    def __init__(self, answer: str, allowed_guesses: Collection[str]):
        self.answer = normalize_word(answer)
        allowed = frozenset(allowed_guesses)
        # Normal games share the cached dictionary instead of copying it per player.
        self.allowed_guesses = allowed if self.answer in allowed else allowed | {self.answer}
        self.guesses: list[str] = []

    @property
    def won(self) -> bool:
        return bool(self.guesses and self.guesses[-1] == self.answer)

    @property
    def finished(self) -> bool:
        return self.won or len(self.guesses) >= MAX_GUESSES

    def submit(self, value: str) -> None:
        if self.finished:
            raise ValueError("This game has ended.")
        word = normalize_word(value)
        if word not in self.allowed_guesses:
            raise ValueError("That word isn't in the word list.")
        if word in self.guesses:
            raise ValueError("You've already guessed that word.")
        self.guesses.append(word)


class _GuessModal(discord.ui.Modal, title="Wordle - Make a guess"):
    word = discord.ui.TextInput(label="Five-letter word", min_length=5, max_length=5)

    def __init__(self, view: "WordleView"):
        super().__init__(timeout=GAME_TIMEOUT)
        self.game_view = view
        self.attempt = len(view.game.guesses)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.game_view.on_guess(interaction, self.word.value, self.attempt)
        finally:
            self.stop()
            if self.game_view.guess_modal is self:
                self.game_view.guess_modal = None

    async def on_timeout(self) -> None:
        if self.game_view.guess_modal is self:
            self.game_view.guess_modal = None

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await self.game_view.on_error(interaction, error, self)


class WordleView(TimedView):
    def __init__(self, player: discord.Member, *, game: WordleGame | None = None):
        if game is None:
            answers, allowed = load_word_lists()
            game = WordleGame(random.SystemRandom().choice(answers), allowed)
        super().__init__(
            command=COMMAND, timeout=GAME_TIMEOUT, timeout_title="Wordle - Timed Out",
            user_ids=(player.id,),
            unauthorized_message=f"This is someone else's game. Start your own with {COMMAND}.",
        )
        self.player = player
        self.game = game
        self.guess_modal: _GuessModal | None = None

    def close(self) -> None:
        if self.guess_modal is not None:
            self.guess_modal.stop()
            self.guess_modal = None
        super().close()

    def make_embed(self) -> discord.Embed:
        rows = [
            "".join(score_guess(self.game.answer, guess)) + f"  `{guess.upper()}`"
            for guess in self.game.guesses
        ]
        rows.extend(["⬜" * 5] * (MAX_GUESSES - len(rows)))
        embed = discord.Embed(
            title="Wordle", description=f"{self.player.mention}\n\n" + "\n".join(rows),
            color=discord.Color.blurple(),
        )
        if self.game.won:
            embed.title = "Wordle - Solved!"
            embed.color = discord.Color.gold()
            embed.add_field(name="Result", value=f"🎉 Solved in {len(self.game.guesses)}/{MAX_GUESSES} guesses.")
        elif self.game.finished or self.timed_out:
            embed.title = self.timeout_title if self.timed_out else "Wordle - Game Over"
            embed.color = discord.Color.greyple() if self.timed_out else discord.Color.red()
            embed.add_field(name="Answer", value=f"**{self.game.answer.upper()}**")
        else:
            embed.add_field(name="Guesses", value=f"{len(self.game.guesses) + 1}/{MAX_GUESSES}")
            embed.set_footer(text=f"Expires after 5 minutes of inactivity.")
        return embed

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

    async def on_guess(self, interaction: discord.Interaction, value: str, attempt: int) -> None:
        if not await self.allowed(interaction):
            return
        async with self._lock:
            if attempt != len(self.game.guesses):
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
            await self.update_board(interaction)

    def make_timeout_embed(self) -> discord.Embed:
        return self.make_embed()
