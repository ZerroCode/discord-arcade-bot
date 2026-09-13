"""Shared challenge and timeout lifecycle for arcade games."""

import asyncio
import logging

import discord

CHALLENGE_TIMEOUT = 120
GAME_TIMEOUT = 300


class TimedView(discord.ui.View):
    def __init__(
        self, *, command: str, timeout: float, timeout_title: str,
        user_ids: tuple[int, ...], unauthorized_message: str = "You're not part of this game.",
    ):
        super().__init__(timeout=timeout)
        self.command = command
        self.message: discord.Message | discord.InteractionMessage | None = None
        self.closed = False
        self.timed_out = False
        self.timeout_title = timeout_title
        self.user_ids = frozenset(user_ids)
        self.unauthorized_message = unauthorized_message
        self._lock = asyncio.Lock()

    async def allowed(self, interaction: discord.Interaction) -> bool:
        error = None
        if interaction.user.id not in self.user_ids:
            error = self.unauthorized_message
        elif self.closed or self.is_finished():
            error = "This game has ended."
        elif self._lock.locked():
            error = "A move is being updated. Please try again."
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # discord.py refreshes the timeout after this check, before the callback.
        return await self.allowed(interaction)

    def make_embed(self) -> discord.Embed:
        raise NotImplementedError

    async def send(self, interaction: discord.Interaction, *, embed: discord.Embed | None = None) -> None:
        try:
            await interaction.response.send_message(embed=embed if embed is not None else self.make_embed(), view=self)
            self.message = await interaction.original_response()
        except Exception:
            self.close()
            raise

    async def update_board(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.edit_message(embed=self.make_embed(), view=self)
        except Exception:
            self.close()
            raise

    def make_timeout_embed(self) -> discord.Embed:
        return discord.Embed(
            title=self.timeout_title,
            description=f"Start a new game with {self.command}.",
            color=discord.Color.greyple(),
        )

    def close(self) -> None:
        self.closed = True
        for child in self.children:
            child.disabled = True
        self.stop()

    async def on_timeout(self) -> None:
        # Wait for an in-flight move so a stale board cannot overwrite a timeout.
        async with self._lock:
            if self.closed:
                return
            self.timed_out = True
            self.close()
            embed = self.make_timeout_embed()
            if self.message is not None:
                try:
                    await self.message.edit(embed=embed, view=self)
                except discord.HTTPException:
                    logging.getLogger(type(self).__module__).exception("Could not update expired game message")

    async def on_error(self, interaction, error, item) -> None:
        logging.getLogger(type(self).__module__).error("Game interaction failed", exc_info=(type(error), error, error.__traceback__))
        self.close()
        try:
            message = f"The game could not be updated. Start a new game with {self.command}."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logging.getLogger(type(self).__module__).exception("Could not report game error to player")
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logging.getLogger(type(self).__module__).exception("Could not disable failed game controls")


class ChallengeView(TimedView):
    def __init__(self, challenger: discord.Member, opponent: discord.Member, *, command: str):
        super().__init__(
            command=command, timeout=CHALLENGE_TIMEOUT, timeout_title="Challenge Expired",
            user_ids=(opponent.id,), unauthorized_message="Only the challenged user can respond.",
        )
        self.challenger = challenger
        self.opponent = opponent

    def create_game(self):
        raise NotImplementedError

    async def _claim(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Only the challenged user can respond.", ephemeral=True)
            return False
        if self.closed or self.is_finished():
            await interaction.response.send_message("This challenge has already ended.", ephemeral=True)
            return False
        # Reserve before any network await, including competing Accept/Decline clicks.
        self.close()
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._claim(interaction):
            return
        game = self.create_game()
        game.message = interaction.message
        try:
            await interaction.response.edit_message(embed=game.make_embed(), view=game)
        except Exception:
            game.close()
            raise

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._claim(interaction):
            return
        embed = discord.Embed(
            title="Challenge Declined",
            description=f"{self.opponent.mention} declined {self.challenger.mention}'s challenge.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=self)


