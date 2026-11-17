import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pymongo.errors import ServerSelectionTimeoutError
from tests.helpers import GAMES, game_view, interaction, member, prepare_finish


class GameRecordingTests(unittest.IsolatedAsyncioTestCase):
    def new_view(self, name):
        store = SimpleNamespace(record_match=AsyncMock())
        view = game_view(name, store)
        self.addCleanup(view.close)
        return view, store

    async def test_each_player_win_and_supported_draw_records_once(self):
        for name in GAMES:
            for winner in ([1, 2] if name == 'battleship' else [1, 2, None]):
                with self.subTest(game=name, winner=winner):
                    view, store = self.new_view(name)
                    finish, event = await prepare_finish(name, view, winner)
                    store.record_match.assert_not_awaited()
                    await finish()
                    await finish()  # Duplicate click/modal must not count again.
                    store.record_match.assert_awaited_once()
                    result = store.record_match.call_args.kwargs
                    self.assertEqual(result['winner_id'], winner)
                    self.assertEqual(result['game'], name)
                    self.assertEqual(result['guild_id'], 123)
                    self.assertEqual(result['player_ids'], (1, 2))
                    self.assertIsNotNone(result['completed_at'].tzinfo)
                    self.assertTrue(view.closed)
                    self.assertTrue(all(child.disabled for child in view.children))
                    event.response.edit_message.assert_awaited_once()

    async def test_timeout_and_spectator_do_not_record(self):
        for name in GAMES:
            with self.subTest(game=name):
                view, store = self.new_view(name)
                finish, event = await prepare_finish(name, view)
                event.user = member(999)
                await finish()
                store.record_match.assert_not_awaited()
                self.assertFalse(view.closed)
                await view.on_timeout()
                event.user = member(1)
                await finish()
                self.assertTrue(view.timed_out)
                store.record_match.assert_not_awaited()

    async def test_database_failure_leaves_finished_board_and_reports(self):
        for name in GAMES:
            with self.subTest(game=name):
                view, store = self.new_view(name)
                store.record_match.side_effect = ServerSelectionTimeoutError('offline')
                finish, event = await prepare_finish(name, view)
                with self.assertLogs('games.' + name, level='ERROR'):
                    await finish()
                self.assertTrue(view.closed)
                event.response.edit_message.assert_awaited_once()
                event.followup.send.assert_awaited_once()
                self.assertIn('could not be confirmed', event.followup.send.call_args.args[0])

    async def test_failed_board_update_does_not_record(self):
        for name in GAMES:
            with self.subTest(game=name):
                view, store = self.new_view(name)
                finish, event = await prepare_finish(name, view)
                event.response.edit_message.side_effect = RuntimeError('edit failed')
                with self.assertRaises(RuntimeError):
                    await finish()
                store.record_match.assert_not_awaited()
                self.assertTrue(view.closed)

    async def test_competing_final_callbacks_record_once(self):
        for name in GAMES:
            with self.subTest(game=name):
                view, store = self.new_view(name)
                finish, event = await prepare_finish(name, view)
                entered, release = asyncio.Event(), asyncio.Event()
                async def delayed_edit(**kwargs):
                    entered.set()
                    await release.wait()
                event.response.edit_message.side_effect = delayed_edit
                task = asyncio.create_task(finish())
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    await finish()
                finally:
                    release.set()
                    await task
                store.record_match.assert_awaited_once()

    async def test_challenge_accept_decline_and_expiry(self):
        for name, (_, challenge_class) in GAMES.items():
            for action in ('accept', 'decline', 'expire'):
                with self.subTest(game=name, action=action):
                    store = SimpleNamespace(record_match=AsyncMock())
                    challenge = challenge_class(member(1), member(2), store=store, guild_id=123)
                    self.addCleanup(challenge.close)
                    event = interaction(2)
                    if action == 'expire':
                        await challenge.on_timeout()
                    else:
                        await getattr(challenge, action).callback(event)
                        if action == 'accept':
                            view = event.response.edit_message.call_args.kwargs['view']
                            self.addCleanup(view.close)
                            self.assertIs(view.store, store)
                            self.assertEqual(view.guild_id, 123)
                    await challenge.accept.callback(event)
                    self.assertEqual(event.response.edit_message.await_count, int(action != 'expire'))
                    store.record_match.assert_not_awaited()
