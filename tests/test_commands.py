import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pymongo.errors import ServerSelectionTimeoutError
from games.arcade import Arcade
from tests.helpers import interaction, member


class StatsCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = SimpleNamespace(
            get_player_stats=AsyncMock(return_value=dict(played=4, wins=2, losses=1, draws=1)),
            get_leaderboard=AsyncMock(return_value=[]),
        )
        self.cog = Arcade(SimpleNamespace(store=self.store))

    async def test_stats_default_and_game_filter(self):
        for game, target in ((None, None), ('battleship', member(2))):
            with self.subTest(game=game):
                event = interaction()
                await self.cog.stats.callback(self.cog, event, member=target, game=game)
                self.store.get_player_stats.assert_awaited_with(123, target.id if target else 1, game=game)
                event.response.defer.assert_awaited_once()
                embed = event.followup.send.call_args.kwargs['embed']
                self.assertEqual([field.value for field in embed.fields], ['4', '2', '1', '1'])
                self.assertIn('Battleship' if game else 'All 1v1 Games', embed.title)

    async def test_empty_and_populated_leaderboard(self):
        event = interaction()
        await self.cog.leaderboard.callback(self.cog, event)
        self.assertIn('No completed matches', event.followup.send.call_args.kwargs['embed'].description)
        self.store.get_leaderboard.return_value = [dict(_id='2', wins=5, losses=2, draws=1)]
        event = interaction()
        await self.cog.leaderboard.callback(self.cog, event, game='connect4')
        self.store.get_leaderboard.assert_awaited_with(123, game='connect4')
        sent = event.followup.send.call_args.kwargs
        self.assertIn('1. <@2> — 5W / 2L / 1D', sent['embed'].description)
        self.assertFalse(sent['allowed_mentions'].users)

    async def test_unavailable_database_reports_error(self):
        for command, method in ((self.cog.stats, self.store.get_player_stats),
                                (self.cog.leaderboard, self.store.get_leaderboard)):
            with self.subTest(command=command.name):
                method.side_effect = ServerSelectionTimeoutError('offline')
                event = interaction()
                with self.assertLogs('games.arcade', level='ERROR'):
                    await command.callback(self.cog, event)
                self.assertIn('unavailable', event.followup.send.call_args.args[0])

    async def test_dm_does_not_query_database(self):
        for command in (self.cog.stats, self.cog.leaderboard):
            event = interaction()
            event.guild_id = None
            await command.callback(self.cog, event)
            event.response.send_message.assert_awaited_once()
        self.store.get_player_stats.assert_not_awaited()
        self.store.get_leaderboard.assert_not_awaited()
