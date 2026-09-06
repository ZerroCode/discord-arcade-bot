import asyncio
import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from games.battleship import BattleshipView, ChallengeView, Fleet, SHIPS, parse_coordinate


def player(user_id):
    return SimpleNamespace(id=user_id, mention=f"<@{user_id}>", display_name=f"Player {user_id}")


def interaction(user):
    return SimpleNamespace(
        user=user,
        response=SimpleNamespace(
            send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock(),
            is_done=Mock(return_value=False),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
    )


class RulesTests(unittest.TestCase):
    def test_coordinates(self):
        for row in range(10):
            for column in range(10):
                self.assertEqual(parse_coordinate(f" {chr(97 + row)}{column + 1} "), (row, column))
        for value in ("", "A0", "A11", "K1", "1A", "A01", "A 1", "AA1", "A1\nB2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_coordinate(value)

    def test_random_fleets_are_straight_contiguous_in_bounds_and_disjoint(self):
        for seed in range(200):
            fleet = Fleet(random.Random(seed))
            occupied = set()
            for name, length in SHIPS:
                cells = fleet.ships[name]
                self.assertEqual(len(cells), length)
                self.assertFalse(cells & occupied)
                occupied.update(cells)
                rows, columns = zip(*sorted(cells))
                self.assertTrue(all(0 <= value < 10 for value in rows + columns))
                if len(set(rows)) == 1:
                    self.assertEqual(list(columns), list(range(min(columns), min(columns) + length)))
                else:
                    self.assertEqual(len(set(columns)), 1)
                    self.assertEqual(list(rows), list(range(min(rows), min(rows) + length)))
            self.assertEqual(len(occupied), 17)

    def test_hits_misses_sinking_repeat_and_victory(self):
        fleet = Fleet(random.Random(0))
        occupied = set().union(*fleet.ships.values())
        miss = next((r, c) for r in range(10) for c in range(10) if (r, c) not in occupied)
        self.assertEqual(fleet.fire(miss), "Miss.")
        self.assertEqual(fleet.remaining, 5)
        for name, cells in fleet.ships.items():
            for index, cell in enumerate(cells):
                expected = f"Sunk {name}!" if index == len(cells) - 1 else "Hit!"
                self.assertEqual(fleet.fire(cell), expected)
        self.assertTrue(fleet.defeated)
        self.assertEqual(fleet.remaining, 0)
        before = fleet.shots.copy()
        for cell in (miss, (-1, 0), (0, 10)):
            with self.assertRaises(ValueError):
                fleet.fire(cell)
            self.assertEqual(fleet.shots, before)

    def test_public_render_hides_unhit_ships(self):
        first, second = Fleet(random.Random(1)), Fleet(random.Random(2))
        self.assertEqual(first.render(), second.render())
        self.assertNotIn("S", first.render())
        self.assertEqual(first.render(reveal=True).count("S"), 17)
        ship = first.ships["Destroyer"]
        first.fire(next(iter(ship)))
        self.assertEqual(first.render().count("X"), 1)
        first.fire(next(cell for cell in ship if cell not in first.shots))
        self.assertEqual(first.render().count("#"), 2)
        self.assertEqual(first.render(reveal=True).count("S"), 15)


class ViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.p1, self.p2 = player(1), player(2)
        self.game = BattleshipView(self.p1, self.p2)
        self.game.message = SimpleNamespace(edit=AsyncMock())
        self.addCleanup(self.game.close)

    async def start(self):
        for user in (self.p1, self.p2):
            await self.game.ready.callback(interaction(user))

    async def shoot(self, coordinate, user=None, turn=None):
        user = user or (self.p1 if self.game.current_turn == 1 else self.p2)
        event = interaction(user)
        await self.game.on_shot(event, coordinate, user.id, self.game.turn_number if turn is None else turn)
        return event

    async def test_setup_private_fleet_shuffle_and_ready(self):
        game = self.game
        self.assertTrue(game.fire_shot.disabled)
        self.assertLessEqual(len(game.to_components()), 5)
        event = interaction(self.p1)
        await game.my_fleet.callback(event)
        sent = event.response.send_message.call_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertIn(game.fleets[1].render(reveal=True), sent["embed"].description)
        before = game.fleets[1]
        event = interaction(self.p1)
        await game.shuffle.callback(event)
        self.assertIsNot(game.fleets[1], before)
        self.assertTrue(event.response.send_message.call_args.kwargs["ephemeral"])
        await game.ready.callback(interaction(self.p1))
        before = game.fleets[1]
        await game.shuffle.callback(interaction(self.p1))
        self.assertIs(game.fleets[1], before)
        await game.ready.callback(interaction(self.p1))
        self.assertEqual(game.ready_players, {1})
        await game.ready.callback(interaction(self.p2))
        self.assertTrue(game.ready.disabled and game.shuffle.disabled)
        self.assertFalse(game.fire_shot.disabled)
        self.assertIn(self.p1.mention, game.make_embed().fields[-1].value)
        self.assertLess(len(game.make_embed()), 6000)
        self.assertTrue(all(len(field.value) <= 1024 for field in game.make_embed().fields))

    async def test_permissions_and_setup_reject_shots(self):
        for button in self.game.children:
            event = interaction(player(3))
            await button.callback(event)
            self.assertTrue(event.response.send_message.call_args.kwargs["ephemeral"])
            event.response.edit_message.assert_not_awaited()
        await self.shoot("A1")
        self.assertFalse(self.game.fleets[2].shots)
        await self.start()
        await self.shoot("A1", self.p2)
        self.assertFalse(self.game.fleets[1].shots)
        event = interaction(self.p2)
        await self.game.fire_shot.callback(event)
        event.response.send_modal.assert_not_awaited()

    async def test_modal_submission_turns_invalid_and_repeated_shots(self):
        await self.start()
        event = interaction(self.p1)
        await self.game.fire_shot.callback(event)
        modal = event.response.send_modal.call_args.args[0]
        modal.coordinate._value = "a1"
        await modal.on_submit(interaction(self.p1))
        self.assertEqual(self.game.fleets[2].shots, {(0, 0)})
        self.assertEqual(self.game.current_turn, 2)
        self.assertTrue(modal.is_finished())
        await self.shoot("J10")
        for coordinate in ("A1", "K1"):
            event = await self.shoot(coordinate)
            event.response.edit_message.assert_not_awaited()
            self.assertEqual(self.game.current_turn, 1)
            self.assertEqual(self.game.turn_number, 2)
        event = await self.shoot("B2", turn=0)
        event.response.edit_message.assert_not_awaited()
        self.assertNotIn((1, 1), self.game.fleets[2].shots)
        await self.shoot("B2")
        self.assertEqual(self.game.current_turn, 2)

    async def test_entire_game_and_late_interactions(self):
        await self.start()
        targets = sorted(set().union(*self.game.fleets[2].ships.values()))
        own_cells = set().union(*self.game.fleets[1].ships.values())
        misses = iter((r, c) for r in range(10) for c in range(10) if (r, c) not in own_cells)
        for index, (row, column) in enumerate(targets):
            await self.shoot(f"{chr(65 + row)}{column + 1}")
            if index < len(targets) - 1:
                r, c = next(misses)
                await self.shoot(f"{chr(65 + r)}{c + 1}")
        self.assertEqual(self.game.winner, 1)
        self.assertTrue(self.game.is_finished())
        self.assertTrue(all(button.disabled for button in self.game.children))
        self.assertEqual(self.game.make_embed().title, "Battleship - Game Over")
        self.assertIn(self.p1.mention, self.game.make_embed().fields[-1].value)
        before = self.game.turn_number
        event = await self.shoot("J10")
        event.response.edit_message.assert_not_awaited()
        self.assertEqual(self.game.turn_number, before)

    async def test_overlapping_shot_and_timeout_wait_for_update(self):
        await self.start()
        event = interaction(self.p1)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_edit(**kwargs):
            entered.set()
            await release.wait()

        event.response.edit_message.side_effect = slow_edit
        pending = asyncio.create_task(self.game.on_shot(event, "A1", 1, 0))
        timeout = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            rejected = await self.shoot("A2", self.p2)
            rejected.response.edit_message.assert_not_awaited()
            self.assertFalse(self.game.fleets[1].shots)
            timeout = asyncio.create_task(self.game.on_timeout())
            await asyncio.sleep(0)
            self.game.message.edit.assert_not_awaited()
        finally:
            release.set()
            await pending
            if timeout:
                await timeout
        self.assertTrue(self.game.closed)
        self.assertIn("/arcade battleship", self.game.message.edit.call_args.kwargs["embed"].description)

    async def test_timeout_stops_open_modal_and_rejects_submission(self):
        await self.start()
        event = interaction(self.p1)
        await self.game.fire_shot.callback(event)
        modal = self.game.shot_modal
        await self.game.on_timeout()
        self.assertTrue(modal.is_finished())
        modal.coordinate._value = "A1"
        event = interaction(self.p1)
        await modal.on_submit(event)
        self.assertFalse(self.game.fleets[2].shots)
        event.response.edit_message.assert_not_awaited()

    async def test_failed_update_closes_game_and_reports_modal_error(self):
        await self.start()
        opened = interaction(self.p1)
        await self.game.fire_shot.callback(opened)
        modal = self.game.shot_modal
        modal.coordinate._value = "A1"
        event = interaction(self.p1)
        error = RuntimeError("network failure")
        event.response.edit_message.side_effect = error
        with self.assertRaisesRegex(RuntimeError, "network failure"):
            await modal.on_submit(event)
        with self.assertLogs("games.battleship", level="ERROR"):
            await modal.on_error(event, error)
        self.assertTrue(self.game.is_finished())
        self.assertTrue(modal.is_finished())
        self.assertTrue(all(button.disabled for button in self.game.children))
        event.response.send_message.assert_awaited_once()

    async def test_challenge_accept_decline_and_expiry(self):
        for ending in ("accept", "decline", "timeout"):
            challenge = ChallengeView(self.p1, self.p2)
            self.addCleanup(challenge.close)
            challenge.message = SimpleNamespace(edit=AsyncMock())
            outsider = interaction(self.p1)
            await challenge.accept.callback(outsider)
            self.assertFalse(challenge.closed)
            event = interaction(self.p2)
            if ending == "timeout":
                await challenge.on_timeout()
            else:
                await getattr(challenge, ending).callback(event)
            if ending == "accept":
                game = event.response.edit_message.call_args.kwargs["view"]
                self.addCleanup(game.close)
                self.assertIsInstance(game, BattleshipView)
                self.assertIs(game.message, event.message)
            duplicate = interaction(self.p2)
            await challenge.accept.callback(duplicate)
            duplicate.response.edit_message.assert_not_awaited()
            self.assertTrue(challenge.is_finished())
