"""Real MongoDB tests. Never use the application's database or load its .env."""
import asyncio
import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from storage.database import MatchStore
from tests.helpers import GAMES, game_view, prepare_finish


@unittest.skipUnless(os.getenv('TEST_MONGODB_URI'), 'Set TEST_MONGODB_URI for real MongoDB tests')
class MongoDBTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database_name = 'arcade_test_' + uuid4().hex
        self.store = MatchStore(os.environ['TEST_MONGODB_URI'], self.database_name)
        self.addAsyncCleanup(self.cleanup_database)
        await self.store.connect()

    async def cleanup_database(self):
        try:
            await self.store.client.drop_database(self.database_name)
        finally:
            await self.store.close()

    async def record(self, *, winner=1, players=(1, 2), guild=123, game='tictactoe', match_id=None):
        return await self.store.record_match(
            match_id=match_id or uuid4().hex, guild_id=guild, game=game,
            player_ids=players, winner_id=winner, completed_at=datetime.now(timezone.utc),
        )

    async def test_all_game_callbacks_save_real_results_and_totals(self):
        for name in GAMES:
            view = game_view(name, self.store)
            self.addCleanup(view.close)
            finish, _ = await prepare_finish(name, view)
            await finish()
            await finish()
            stats = await self.store.get_player_stats(123, 1, game=name)
            self.assertEqual(stats, dict(_id='1', played=1, wins=1, losses=0, draws=0))
        self.assertEqual(await self.store.matches.count_documents({}), 4)
        self.assertEqual(await self.store.get_player_stats(123, 1),
                         dict(_id='1', played=4, wins=4, losses=0, draws=0))
        self.assertEqual((await self.store.get_player_stats(123, 2))['losses'], 4)

    async def test_duplicate_retries_and_concurrent_inserts(self):
        results = await asyncio.gather(*(self.record(match_id='same-match') for _ in range(8)))
        self.assertEqual(results.count(True), 1)
        self.assertFalse(await self.record(match_id='same-match', winner=2))
        self.assertEqual(await self.store.matches.count_documents({}), 1)
        self.assertEqual((await self.store.get_player_stats(123, 1))['wins'], 1)

    async def test_draws_server_game_and_solo_isolation(self):
        await self.record()
        await self.record(winner=2)
        await self.record(winner=None)
        await self.record(game='connect4')
        await self.record(guild=999)
        await self.store.matches.insert_one(dict(_id='solo', guild_id='123', game='wordle',
                                                player_ids=['1'], winner_id='1', outcome='win'))
        self.assertEqual(await self.store.get_player_stats(123, 1, 'tictactoe'),
                         dict(_id='1', played=3, wins=1, losses=1, draws=1))
        total = dict(_id='1', played=4, wins=2, losses=1, draws=1)
        self.assertEqual(await self.store.get_player_stats(123, 1), total)
        self.assertEqual((await self.store.get_leaderboard(123))[0], total)
        self.assertEqual((await self.store.get_player_stats(999, 1))['played'], 1)
        self.assertEqual((await self.store.get_player_stats(123, 999))['played'], 0)
        self.assertEqual(await self.store.get_leaderboard(555), [])

    async def test_leaderboard_limit_and_tie_breaks(self):
        # Twelve players win once. Player 99 also wins once and draws once;
        # player 12 beats them on losses, followed by the players without draws.
        for player in range(10, 22):
            await self.record(players=(player, 99), winner=player)
        await self.record(players=(12, 99), winner=None)
        await self.record(players=(11, 99), winner=99)
        rows = await self.store.get_leaderboard(123)
        self.assertEqual(len(rows), 10)
        self.assertEqual([row['_id'] for row in rows],
                         ['12', '99', '10', '13', '14', '15', '16', '17', '18', '19'])

    async def test_invalid_result_never_inserted(self):
        for args in ({'winner': 3}, {'players': (1, 1)}, {'game': 'wordle'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                await self.record(**args)
        self.assertEqual(await self.store.matches.count_documents({}), 0)
