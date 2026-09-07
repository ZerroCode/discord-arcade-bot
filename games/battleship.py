"""Battleship rules and Discord views, independent of command registration."""

import random
import re

import discord

from games.views import ChallengeView as BaseChallengeView, GAME_TIMEOUT, TimedView

COMMAND = "/arcade battleship"
SIZE = 10
SHIPS = (("Carrier", 5), ("Battleship", 4), ("Cruiser", 3), ("Submarine", 3), ("Destroyer", 2))
Cell = tuple[int, int]


def parse_coordinate(value: str) -> Cell:
    """Convert A1 through J10 to a zero-based row and column."""
    match = re.fullmatch(r"([A-J])(10|[1-9])", value.strip().upper())
    if match is None:
        raise ValueError("Enter a coordinate from A1 to J10, such as B7.")
    return ord(match[1]) - ord("A"), int(match[2]) - 1


class Fleet:
    def __init__(self, rng: random.Random | None = None):
        rng = rng or random.SystemRandom()
        self.ships: dict[str, frozenset[Cell]] = {}
        self.shots: set[Cell] = set()
        occupied: set[Cell] = set()
        for name, length in SHIPS:
            # Enumerate legal placements so generation cannot spin on collisions.
            placements = []
            for dr, dc in ((0, 1), (1, 0)):
                for row in range(SIZE - (length - 1) * dr):
                    for column in range(SIZE - (length - 1) * dc):
                        cells = frozenset((row + i * dr, column + i * dc) for i in range(length))
                        if not cells & occupied:
                            placements.append(cells)
            cells = rng.choice(placements)
            self.ships[name] = cells
            occupied.update(cells)

    @property
    def defeated(self) -> bool:
        return all(cells <= self.shots for cells in self.ships.values())

    @property
    def remaining(self) -> int:
        return sum(not cells <= self.shots for cells in self.ships.values())

    def fire(self, cell: Cell) -> str:
        row, column = cell
        if not (0 <= row < SIZE and 0 <= column < SIZE):
            raise ValueError("Choose a coordinate from A1 to J10.")
        if cell in self.shots:
            raise ValueError("You've already fired there. Choose another coordinate.")
        self.shots.add(cell)
        for name, cells in self.ships.items():
            if cell in cells:
                return f"Sunk {name}!" if cells <= self.shots else "Hit!"
        return "Miss."

    def render(self, *, reveal: bool = False) -> str:
        occupied = set().union(*self.ships.values())
        sunk = set().union(*(cells for cells in self.ships.values() if cells <= self.shots))
        lines = ["   1 2 3 4 5 6 7 8 9 10"]
        for row in range(SIZE):
            tokens = []
            for column in range(SIZE):
                cell = row, column
                if cell in self.shots:
                    token = "#" if cell in sunk else "X" if cell in occupied else "o"
                else:
                    token = "S" if reveal and cell in occupied else "."
                tokens.append(token)
            lines.append(f"{chr(ord('A') + row)}  " + " ".join(tokens))
        return "```text\n" + "\n".join(lines) + "\n```"


class _ShotModal(discord.ui.Modal, title="Battleship - Fire a shot"):
    coordinate = discord.ui.TextInput(label="Coordinate (A1-J10)", placeholder="B7", max_length=5)

    def __init__(self, game: "BattleshipView", user_id: int):
        super().__init__(timeout=GAME_TIMEOUT)
        self.game = game
        self.user_id = user_id
        self.turn_number = game.turn_number

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.game.on_shot(interaction, self.coordinate.value, self.user_id, self.turn_number)
        finally:
            self.stop()
            if self.game.shot_modal is self:
                self.game.shot_modal = None

    async def on_timeout(self) -> None:
        if self.game.shot_modal is self:
            self.game.shot_modal = None

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await self.game.on_error(interaction, error, self)


class BattleshipView(TimedView):
    def __init__(self, player1: discord.Member, player2: discord.Member):
        super().__init__(command=COMMAND, timeout=GAME_TIMEOUT, timeout_title="Battleship - Timed Out")
        self.player1, self.player2 = player1, player2
        self.fleets = {player1.id: Fleet(), player2.id: Fleet()}
        self.ready_players: set[int] = set()
        self.current_turn = player1.id
        self.turn_number = 0
        self.winner: int | None = None
        self.last_shot = ""
        self.shot_modal: _ShotModal | None = None

    def close(self) -> None:
        if self.shot_modal is not None:
            self.shot_modal.stop()
            self.shot_modal = None
        super().close()

    def other_player(self, user_id: int):
        return self.player2 if user_id == self.player1.id else self.player1

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(title="Battleship", color=discord.Color.blurple())
        if len(self.ready_players) < 2:
            embed.description = (
                f"{self.player1.mention} vs {self.player2.mention}\n\n"
                "Your five ships have been placed automatically. Use **My fleet** to see them privately, "
                "**Shuffle fleet** to change their positions, then **Ready** to lock them in.\n"
                "Fleet: Carrier (5), Battleship (4), Cruiser (3), Submarine (3), Destroyer (2).\n\n"
                + "\n".join(
                    f"{player.mention}: {'Ready' if player.id in self.ready_players else 'Preparing'}"
                    for player in (self.player1, self.player2)
                )
            )
        else:
            embed.description = (
                "Use **Fire shot** and enter A1-J10. Each shot ends your turn, including hits. "
                "Sink all five enemy ships to win.\n"
                "`.` unknown | `o` miss | `X` hit | `#` sunk\n\n" + self.last_shot
            )
            for player in (self.player1, self.player2):
                target = self.fleets[self.other_player(player.id).id]
                embed.add_field(
                    name=f"{player.display_name[:80]}'s shots - {target.remaining} enemy ships left",
                    value=target.render(), inline=False,
                )
            if self.winner is not None:
                player = self.player1 if self.winner == self.player1.id else self.player2
                embed.title = "Battleship - Game Over"
                embed.color = discord.Color.gold()
                embed.add_field(name="Winner", value=f"🎉 {player.mention} sank the entire enemy fleet!")
            else:
                embed.add_field(name="Turn", value=f"<@{self.current_turn}> — choose Fire shot.")
        if self.winner is None:
            embed.set_footer(text="Expires after 5 minutes of inactivity. Private fleet views are snapshots.")
        return embed

    def private_embed(self, user_id: int) -> discord.Embed:
        fleet = self.fleets[user_id]
        return discord.Embed(
            title="Battleship - Your fleet",
            description=(
                fleet.render(reveal=True)
                + "\n`S` ship | `.` water | `o` enemy miss | `X` enemy hit | `#` sunk\n"
                + "\n".join(f"{name} ({len(cells)}): {'Sunk' if cells <= fleet.shots else 'Afloat'}"
                            for name, cells in fleet.ships.items())
                + "\n\nSnapshot only. Use My fleet again to refresh."
            ),
            color=discord.Color.blurple(),
        )

    async def allowed(self, interaction: discord.Interaction, *, firing: bool = False) -> bool:
        error = None
        if interaction.user.id not in self.fleets:
            error = "You're not part of this game."
        elif self.closed or self.is_finished():
            error = "This game has ended."
        elif self._lock.locked():
            error = "A move is being updated. Please try again."
        elif firing and len(self.ready_players) < 2:
            error = "Both players must be ready before firing."
        elif firing and interaction.user.id != self.current_turn:
            error = "It's not your turn."
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return False
        return True

    async def update_board(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.edit_message(embed=self.make_embed(), view=self)
        except Exception:
            self.close()
            raise

    @discord.ui.button(label="My fleet", style=discord.ButtonStyle.secondary)
    async def my_fleet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await self.allowed(interaction):
            await interaction.response.send_message(embed=self.private_embed(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Shuffle fleet", style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        async with self._lock:
            if interaction.user.id in self.ready_players:
                await interaction.response.send_message("Your fleet is locked because you're ready.", ephemeral=True)
                return
            self.fleets[interaction.user.id] = Fleet()
            await interaction.response.send_message(embed=self.private_embed(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Ready", style=discord.ButtonStyle.success)
    async def ready(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        async with self._lock:
            if interaction.user.id in self.ready_players:
                await interaction.response.send_message("You're already ready. Waiting for your opponent.", ephemeral=True)
                return
            self.ready_players.add(interaction.user.id)
            if len(self.ready_players) == 2:
                self.ready.disabled = self.shuffle.disabled = True
                self.fire_shot.disabled = False
            await self.update_board(interaction)

    @discord.ui.button(label="Fire shot", style=discord.ButtonStyle.danger, disabled=True)
    async def fire_shot(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.allowed(interaction, firing=True):
            return
        async with self._lock:
            if self.shot_modal is not None:
                self.shot_modal.stop()
            modal = self.shot_modal = _ShotModal(self, interaction.user.id)
            try:
                await interaction.response.send_modal(modal)
            except Exception:
                self.close()
                raise

    async def on_shot(self, interaction: discord.Interaction, coordinate: str, user_id: int, turn_number: int) -> None:
        if not await self.allowed(interaction, firing=True):
            return
        async with self._lock:
            if interaction.user.id != user_id or turn_number != self.turn_number:
                await interaction.response.send_message("This shot form is out of date. Use Fire shot again.", ephemeral=True)
                return
            target = self.fleets[self.other_player(user_id).id]
            try:
                cell = parse_coordinate(coordinate)
                result = target.fire(cell)
            except ValueError as error:
                await interaction.response.send_message(str(error) + " Use Fire shot to try again.", ephemeral=True)
                return
            self.last_shot = f"<@{user_id}> fired at **{chr(65 + cell[0])}{cell[1] + 1}**: {result}"
            self.turn_number += 1
            if target.defeated:
                self.winner = user_id
                self.close()
            else:
                self.current_turn = self.other_player(user_id).id
                # Modal submissions don't dispatch through the parent View.
                self.timeout = GAME_TIMEOUT
            await self.update_board(interaction)


class ChallengeView(BaseChallengeView):
    def __init__(self, challenger: discord.Member, opponent: discord.Member):
        super().__init__(challenger, opponent, command=COMMAND)

    def create_game(self) -> BattleshipView:
        return BattleshipView(self.challenger, self.opponent)
