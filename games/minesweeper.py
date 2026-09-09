"""Minesweeper rules and a solo Discord button board."""

import logging
import random

import discord

from games.views import GAME_TIMEOUT, TimedView

COMMAND = "/arcade minesweeper"
# Leave the fifth action row available for the reveal/flag mode control.
ROWS = 4
COLUMNS = 5
MINE_COUNT = 4
Cell = tuple[int, int]


class MinesweeperGame:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng if rng is not None else random.SystemRandom()
        self.mines: set[Cell] = set()
        self.revealed: set[Cell] = set()
        self.flags: set[Cell] = set()
        self.exploded: Cell | None = None

    @property
    def started(self) -> bool:
        return bool(self.mines)

    @property
    def won(self) -> bool:
        return self.started and len(self.revealed) == ROWS * COLUMNS - MINE_COUNT

    @property
    def finished(self) -> bool:
        return self.exploded is not None or self.won

    def neighbors(self, cell: Cell) -> tuple[Cell, ...]:
        row, column = cell
        return tuple(
            (r, c)
            for r in range(max(0, row - 1), min(ROWS, row + 2))
            for c in range(max(0, column - 1), min(COLUMNS, column + 2))
            if (r, c) != cell
        )

    def adjacent_mines(self, cell: Cell) -> int:
        return sum(neighbor in self.mines for neighbor in self.neighbors(cell))

    def _check_move(self, cell: Cell) -> None:
        if self.finished:
            raise ValueError("This game has ended.")
        row, column = cell
        if not (0 <= row < ROWS and 0 <= column < COLUMNS):
            raise ValueError("Choose a cell on the board.")
        if cell in self.revealed:
            raise ValueError("That cell is already revealed.")

    def reveal(self, cell: Cell) -> None:
        self._check_move(cell)
        if cell in self.flags:
            raise ValueError("Remove that flag before revealing the cell.")
        if not self.started:
            # Delay placement so the first reveal opens a safe, empty area.
            protected = {cell, *self.neighbors(cell)}
            candidates = [
                (row, column)
                for row in range(ROWS)
                for column in range(COLUMNS)
                if (row, column) not in protected
            ]
            self.mines = set(self.rng.sample(candidates, MINE_COUNT))
        if cell in self.mines:
            self.exploded = cell
            return

        pending = [cell]
        while pending:
            current = pending.pop()
            if current in self.revealed or current in self.flags or current in self.mines:
                continue
            self.revealed.add(current)
            if self.adjacent_mines(current) == 0:
                pending.extend(self.neighbors(current))

    def toggle_flag(self, cell: Cell) -> None:
        self._check_move(cell)
        if cell in self.flags:
            self.flags.remove(cell)
        elif len(self.flags) >= MINE_COUNT:
            raise ValueError("All four flags are placed. Remove a flag before placing another.")
        else:
            self.flags.add(cell)


class _CellButton(discord.ui.Button):
    def __init__(self, cell: Cell):
        row, column = cell
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=f"{chr(ord('A') + row)}{column + 1}",
            row=row,
        )
        self.cell = cell

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.on_cell_click(interaction, self.cell)


class MinesweeperView(TimedView):
    def __init__(self, player: discord.Member, *, game: MinesweeperGame | None = None):
        super().__init__(command=COMMAND, timeout=GAME_TIMEOUT, timeout_title="Minesweeper - Timed Out")
        self.player = player
        self.game = game if game is not None else MinesweeperGame()
        self.flag_mode = False
        self.timed_out = False
        for row in range(ROWS):
            for column in range(COLUMNS):
                self.add_item(_CellButton((row, column)))
        if self.game.finished:
            self.close()
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        show_board = self.game.finished or self.timed_out
        for button in self.children:
            if not isinstance(button, _CellButton):
                continue
            cell = button.cell
            row, column = cell
            button.label = f"{chr(ord('A') + row)}{column + 1}"
            button.emoji = None
            button.style = discord.ButtonStyle.secondary
            button.disabled = (
                self.closed or self.game.finished or cell in self.game.revealed
                or (cell in self.game.flags and not self.flag_mode)
            )
            if show_board and cell in self.game.mines:
                button.emoji = "💥" if cell == self.game.exploded else "💣"
                button.style = discord.ButtonStyle.danger
            elif show_board and self.game.started and cell in self.game.flags:
                button.emoji = "❌"
                button.style = discord.ButtonStyle.danger
            elif cell in self.game.revealed or (show_board and self.game.started):
                count = self.game.adjacent_mines(cell)
                button.label = str(count) if count else "·"
                button.style = discord.ButtonStyle.primary if count else discord.ButtonStyle.secondary
            elif cell in self.game.flags:
                button.emoji = "🚩"
                button.style = discord.ButtonStyle.success
        self.toggle_mode.label = "Reveal mode" if self.flag_mode else "Flag mode"
        self.toggle_mode.style = discord.ButtonStyle.success if self.flag_mode else discord.ButtonStyle.primary
        self.toggle_mode.disabled = self.closed or self.game.finished

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Minesweeper",
            description=(
                f"{self.player.mention}\n\n"
                f"Reveal all **{ROWS * COLUMNS - MINE_COUNT} safe cells** without hitting a mine. "
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Safe cells revealed", value=f"{len(self.game.revealed)}/{ROWS * COLUMNS - MINE_COUNT}")
        embed.add_field(name="Mines / flags placed", value=f"{MINE_COUNT} mines / {len(self.game.flags)} flags")
        if self.game.won:
            embed.title = "Minesweeper - Cleared!"
            embed.color = discord.Color.gold()
            embed.add_field(name="Result", value="You revealed every safe cell!", inline=False)
        elif self.game.exploded is not None:
            embed.title = "Minesweeper - Game Over"
            embed.color = discord.Color.red()
            embed.add_field(name="Result", value="You hit a mine. 💣 = mine; ❌ = incorrect flag.", inline=False)
        elif self.timed_out:
            embed.title = self.timeout_title
            embed.color = discord.Color.greyple()
            embed.add_field(name="Result", value="Expired after 5 minutes of inactivity.", inline=False)
        else:
            embed.set_footer(text="Expires after 5 minutes of inactivity.")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Reject spectators before discord.py refreshes this game's timeout.
        return await self.allowed(interaction)

    async def allowed(self, interaction: discord.Interaction) -> bool:
        error = None
        if interaction.user.id != self.player.id:
            error = f"This is someone else's game. Start your own with {COMMAND}."
        elif self.closed or self.is_finished():
            error = "This game has ended."
        elif self._lock.locked():
            error = "A move is being updated. Please try again."
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return False
        return True

    async def _update_message(self, interaction: discord.Interaction) -> None:
        if self.game.finished:
            self.close()
        self.refresh_buttons()
        try:
            await interaction.response.edit_message(embed=self.make_embed(), view=self)
        except Exception:
            self.close()
            raise

    async def on_cell_click(self, interaction: discord.Interaction, cell: Cell) -> None:
        if not await self.allowed(interaction):
            return
        async with self._lock:
            try:
                if self.flag_mode:
                    self.game.toggle_flag(cell)
                else:
                    self.game.reveal(cell)
            except ValueError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return
            await self._update_message(interaction)

    @discord.ui.button(label="Flag mode", style=discord.ButtonStyle.primary, row=4)
    async def toggle_mode(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        async with self._lock:
            self.flag_mode = not self.flag_mode
            await self._update_message(interaction)

    async def on_timeout(self) -> None:
        async with self._lock:
            if self.closed:
                return
            self.timed_out = True
            self.close()
            self.refresh_buttons()
            if self.message is not None:
                try:
                    await self.message.edit(embed=self.make_embed(), view=self)
                except discord.HTTPException:
                    logging.getLogger(__name__).exception("Could not update expired Minesweeper message")
