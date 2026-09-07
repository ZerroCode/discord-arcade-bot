"""Rock Paper Scissors rules and simultaneous, hidden-choice Discord UI."""

import discord

from games.views import ChallengeView as BaseChallengeView, GAME_TIMEOUT, TimedView

COMMAND = "/arcade rockpaperscissors"
ROCK, PAPER, SCISSORS = "rock", "paper", "scissors"
EMOJIS = {ROCK: "🪨", PAPER: "📄", SCISSORS: "✂️"}
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}


def check_winner(choice1: str, choice2: str) -> int:
    """Return 1 or 2 for the winning player, or 0 for a draw."""
    if choice1 not in BEATS or choice2 not in BEATS:
        raise ValueError("Choose Rock, Paper, or Scissors.")
    if choice1 == choice2:
        return 0
    return 1 if BEATS[choice1] == choice2 else 2


class _ChoiceButton(discord.ui.Button):
    def __init__(self, choice: str):
        super().__init__(label=choice.title(), emoji=EMOJIS[choice], style=discord.ButtonStyle.primary)
        self.choice = choice

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.on_choice(interaction, self.choice)


class RockPaperScissorsView(TimedView):
    def __init__(self, player1: discord.Member, player2: discord.Member):
        super().__init__(command=COMMAND, timeout=GAME_TIMEOUT, timeout_title="Rock Paper Scissors - Timed Out")
        self.player1, self.player2 = player1, player2
        self.choices: dict[int, str] = {}
        for choice in BEATS:
            self.add_item(_ChoiceButton(choice))

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Rock Paper Scissors",
            description=f"{self.player1.mention} vs {self.player2.mention}",
            color=discord.Color.blurple(),
        )
        if len(self.choices) < 2:
            embed.description += "\n\n" + "\n".join(
                f"{player.mention}: {'Ready' if player.id in self.choices else 'Choosing…'}"
                for player in (self.player1, self.player2)
            )
            embed.set_footer(text="Expires after 5 minutes of inactivity.")
        else:
            for player in (self.player1, self.player2):
                choice = self.choices[player.id]
                embed.description += f"\n{player.mention}: {EMOJIS[choice]} **{choice.title()}**"
            winner = check_winner(self.choices[self.player1.id], self.choices[self.player2.id])
            if winner == 0:
                embed.title = "Rock Paper Scissors - Draw"
                embed.add_field(name="Result", value="You chose the same move. It's a draw!")
            else:
                player = self.player1 if winner == 1 else self.player2
                embed.title = "Rock Paper Scissors - Game Over"
                embed.color = discord.Color.gold()
                embed.add_field(name="Winner", value=f"{player.mention} wins!")
        return embed

    async def on_choice(self, interaction: discord.Interaction, choice: str) -> None:
        user_id = interaction.user.id
        if user_id not in (self.player1.id, self.player2.id):
            await interaction.response.send_message("You're not part of this game.", ephemeral=True)
            return
        if self._lock.locked():
            await interaction.response.send_message("A choice is being updated. Please try again.", ephemeral=True)
            return
        async with self._lock:
            if self.closed or self.is_finished():
                await interaction.response.send_message("This game has ended.", ephemeral=True)
                return
            if user_id in self.choices:
                await interaction.response.send_message("Waiting for your opponent.", ephemeral=True)
                return
            if choice not in BEATS:
                await interaction.response.send_message("Choose Rock, Paper, or Scissors.", ephemeral=True)
                return
            self.choices[user_id] = choice
            if len(self.choices) == 2:
                self.close()
            try:
                await interaction.response.edit_message(embed=self.make_embed(), view=self)
            except Exception:
                self.close()
                raise


class ChallengeView(BaseChallengeView):
    def __init__(self, challenger: discord.Member, opponent: discord.Member):
        super().__init__(challenger, opponent, command=COMMAND)

    def create_game(self) -> RockPaperScissorsView:
        return RockPaperScissorsView(self.challenger, self.opponent)
