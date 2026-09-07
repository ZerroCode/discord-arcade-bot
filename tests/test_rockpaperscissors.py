import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from games.rockpaperscissors import (
    ROCK, PAPER, SCISSORS, ChallengeView, RockPaperScissorsView, check_winner,
)


def player(user_id):
    return SimpleNamespace(id=user_id, mention=f"<@{user_id}>", display_name=f"Player {user_id}")


def interaction(user):
    return SimpleNamespace(
        user=user,
        response=SimpleNamespace(
            send_message=AsyncMock(), edit_message=AsyncMock(), is_done=Mock(return_value=False),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
    )


OUTCOMES = (
    (ROCK, ROCK, 0), (ROCK, PAPER, 2), (ROCK, SCISSORS, 1),
    (PAPER, ROCK, 1), (PAPER, PAPER, 0), (PAPER, SCISSORS, 2),
    (SCISSORS, ROCK, 2), (SCISSORS, PAPER, 1), (SCISSORS, SCISSORS, 0),
)


class RulesTests(unittest.TestCase):
    def test_all_nine_outcomes(self):
        for first, second, expected in OUTCOMES:
            with self.subTest(first=first, second=second):
                self.assertEqual(check_winner(first, second), expected)

    def test_invalid_choices(self):
        for first, second in (("lizard", ROCK), (PAPER, ""), ("invalid", "invalid")):
            with self.assertRaises(ValueError):
                check_winner(first, second)


class ViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.p1, self.p2 = player(1), player(2)

    def game(self):
        game = RockPaperScissorsView(self.p1, self.p2)
        game.message = SimpleNamespace(edit=AsyncMock())
        self.addCleanup(game.close)
        return game

    async def choose(self, game, user, choice):
        event = interaction(user)
        button = next(button for button in game.children if button.choice == choice)
        await button.callback(event)
        return event

    async def test_first_choice_cannot_be_inferred_from_public_embed_or_buttons(self):
        snapshots = []
        for choice in (ROCK, PAPER, SCISSORS):
            game = self.game()
            event = await self.choose(game, self.p1, choice)
            sent = event.response.edit_message.call_args.kwargs
            snapshots.append((sent["embed"].to_dict(), [
                (button.label, str(button.emoji), button.style, button.disabled)
                for button in sent["view"].children
            ]))
            self.assertEqual(game.choices, {1: choice})
            self.assertFalse(game.is_finished())
            self.assertTrue(all(not button.disabled for button in game.children))
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])

    async def test_all_results_in_either_selection_order(self):
        for first, second, expected in OUTCOMES:
            for reverse in (False, True):
                with self.subTest(first=first, second=second, reverse=reverse):
                    game = self.game()
                    selections = [(self.p1, first), (self.p2, second)]
                    for user, choice in reversed(selections) if reverse else selections:
                        await self.choose(game, user, choice)
                    self.assertTrue(game.is_finished())
                    self.assertTrue(all(button.disabled for button in game.children))
                    embed = game.make_embed()
                    self.assertIn(f"**{first.title()}**", embed.description)
                    self.assertIn(f"**{second.title()}**", embed.description)
                    if expected == 0:
                        self.assertEqual(embed.title, "Rock Paper Scissors - Draw")
                    else:
                        self.assertEqual(embed.title, "Rock Paper Scissors - Game Over")
                        self.assertEqual(embed.fields[0].value, f"<@{expected}> wins!")
                    event = await self.choose(game, self.p1, PAPER)
                    event.response.edit_message.assert_not_awaited()
                    self.assertEqual(game.choices, {1: first, 2: second})

    async def test_spectators_invalid_choices_and_changing_choice_are_rejected(self):
        game = self.game()
        outsider = await self.choose(game, player(3), ROCK)
        self.assertTrue(outsider.response.send_message.call_args.kwargs["ephemeral"])
        await game.on_choice(interaction(self.p1), "invalid")
        self.assertEqual(game.choices, {})
        await self.choose(game, self.p1, ROCK)
        event = await self.choose(game, self.p1, PAPER)
        self.assertTrue(event.response.send_message.call_args.kwargs["ephemeral"])
        event.response.edit_message.assert_not_awaited()
        self.assertEqual(game.choices, {1: ROCK})

    async def test_competing_clicks_can_retry_without_overwriting_choices(self):
        game = self.game()
        entered, release = asyncio.Event(), asyncio.Event()
        event = interaction(self.p1)

        async def slow_edit(**kwargs):
            entered.set()
            await release.wait()

        event.response.edit_message.side_effect = slow_edit
        pending = asyncio.create_task(game.children[0].callback(event))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            for user in (self.p1, self.p2):
                rejected = await self.choose(game, user, PAPER)
                rejected.response.edit_message.assert_not_awaited()
            self.assertEqual(game.choices, {1: ROCK})
        finally:
            release.set()
            await pending
        await self.choose(game, self.p2, PAPER)
        self.assertEqual(game.choices, {1: ROCK, 2: PAPER})
        self.assertTrue(game.closed)

    async def test_timeout_waits_for_pending_choice_and_does_not_reveal_it(self):
        game = self.game()
        entered, release = asyncio.Event(), asyncio.Event()
        event = interaction(self.p1)

        async def slow_edit(**kwargs):
            entered.set()
            await release.wait()

        event.response.edit_message.side_effect = slow_edit
        pending = asyncio.create_task(game.children[0].callback(event))
        timeout = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            timeout = asyncio.create_task(game.on_timeout())
            await asyncio.sleep(0)
            game.message.edit.assert_not_awaited()
        finally:
            release.set()
            await pending
            if timeout:
                await timeout
        self.assertTrue(game.is_finished())
        self.assertTrue(all(button.disabled for button in game.children))
        embed = game.message.edit.call_args.kwargs["embed"]
        self.assertEqual(embed.title, "Rock Paper Scissors - Timed Out")
        self.assertIn("/arcade rockpaperscissors", embed.description)
        self.assertNotIn("**Rock**", embed.description)
        await self.choose(game, self.p2, PAPER)
        self.assertEqual(game.choices, {1: ROCK})

    async def test_finished_result_survives_timeout(self):
        game = self.game()
        await self.choose(game, self.p1, ROCK)
        await self.choose(game, self.p2, SCISSORS)
        await game.on_timeout()
        game.message.edit.assert_not_awaited()

    async def test_failed_first_and_final_edits_close_the_game(self):
        for final in (False, True):
            game = self.game()
            if final:
                await self.choose(game, self.p1, PAPER)
            event = interaction(self.p2 if final else self.p1)
            event.response.edit_message.side_effect = RuntimeError("network failure")
            with self.assertRaisesRegex(RuntimeError, "network failure"):
                await game.children[0].callback(event)
            self.assertTrue(game.is_finished())
            self.assertTrue(all(button.disabled for button in game.children))

    async def test_challenge_authorization_accept_decline_timeout_and_failed_edit(self):
        for ending in ("accept", "decline", "timeout", "failure"):
            challenge = ChallengeView(self.p1, self.p2)
            self.addCleanup(challenge.close)
            challenge.message = SimpleNamespace(edit=AsyncMock())
            for action in (challenge.accept, challenge.decline):
                await action.callback(interaction(self.p1))
                self.assertFalse(challenge.closed)
            event = interaction(self.p2)
            if ending == "timeout":
                await challenge.on_timeout()
            elif ending == "failure":
                event.response.edit_message.side_effect = RuntimeError("network failure")
                with self.assertRaisesRegex(RuntimeError, "network failure"):
                    await challenge.accept.callback(event)
                self.assertTrue(event.response.edit_message.call_args.kwargs["view"].is_finished())
            else:
                await getattr(challenge, ending).callback(event)
                if ending == "accept":
                    game = event.response.edit_message.call_args.kwargs["view"]
                    self.addCleanup(game.close)
                    self.assertIsInstance(game, RockPaperScissorsView)
                    self.assertIs(game.message, event.message)
            self.assertTrue(challenge.is_finished())
            for action in (challenge.accept, challenge.decline):
                duplicate = interaction(self.p2)
                await action.callback(duplicate)
                duplicate.response.edit_message.assert_not_awaited()
