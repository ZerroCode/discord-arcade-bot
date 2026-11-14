"""Async MongoDB match history; statistics are derived from immutable results."""

from datetime import datetime, timezone

from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError


COMPETITIVE_GAMES = {
    "tictactoe": "Tic Tac Toe",
    "connect4": "Connect 4",
    "battleship": "Battleship",
    "rockpaperscissors": "Rock Paper Scissors",
}


class MatchStore:
    def __init__(self, uri: str, database: str):
        self.client = AsyncMongoClient(uri, timeoutMS=5000)
        self.matches = self.client[database].matches

    async def connect(self) -> None:
        await self.client.admin.command("ping")
        # MongoDB's built-in unique _id index deduplicates match IDs.
        await self.matches.create_index([("guild_id", 1), ("game", 1), ("player_ids", 1)])
        await self.matches.create_index([("guild_id", 1), ("player_ids", 1)])

    async def close(self) -> None:
        await self.client.close()

    async def record_match(
        self, *, match_id: str, guild_id: int, game: str,
        player_ids: tuple[int, int], winner_id: int | None,
        completed_at: datetime,
    ) -> bool:
        if game not in COMPETITIVE_GAMES:
            raise ValueError("Unknown competitive game.")
        if len(player_ids) != 2 or len(set(player_ids)) != 2 or (winner_id is not None and winner_id not in player_ids):
            raise ValueError("A match needs two distinct players and a valid winner or draw.")
        if completed_at.tzinfo is None:
            raise ValueError("Completion time must include a timezone.")
        document = {
            "_id": match_id, "guild_id": str(guild_id), "game": game,
            "player_ids": [str(player) for player in player_ids],
            "winner_id": str(winner_id) if winner_id is not None else None,
            "outcome": "draw" if winner_id is None else "win",
            "completed_at": completed_at.astimezone(timezone.utc),
        }
        try:
            await self.matches.insert_one(document)
        except DuplicateKeyError:
            return False
        return True

    @staticmethod
    def _totals() -> dict:
        return {"$group": {
            "_id": "$player_ids",
            "played": {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$eq": ["$winner_id", "$player_ids"]}, 1, 0]}},
            "draws": {"$sum": {"$cond": [{"$eq": ["$outcome", "draw"]}, 1, 0]}},
            "losses": {"$sum": {"$cond": [{"$and": [
                {"$eq": ["$outcome", "win"]},
                {"$ne": ["$winner_id", "$player_ids"]},
            ]}, 1, 0]}},
        }}

    @staticmethod
    def _scope(guild_id: int, game: str | None) -> dict:
        if game is not None and game not in COMPETITIVE_GAMES:
            raise ValueError("Unknown competitive game.")
        return {"guild_id": str(guild_id), "game": game if game is not None else {"$in": list(COMPETITIVE_GAMES)}}

    async def get_player_stats(self, guild_id: int, player_id: int, game: str | None = None) -> dict:
        cursor = await self.matches.aggregate([
            {"$match": {**self._scope(guild_id, game), "player_ids": str(player_id)}},
            {"$unwind": "$player_ids"},
            {"$match": {"player_ids": str(player_id)}},
            self._totals(),
        ])
        rows = await cursor.to_list(length=1)
        return rows[0] if rows else {"_id": str(player_id), "played": 0, "wins": 0, "draws": 0, "losses": 0}

    async def get_leaderboard(self, guild_id: int, game: str | None = None) -> list[dict]:
        cursor = await self.matches.aggregate([
            {"$match": self._scope(guild_id, game)},
            {"$unwind": "$player_ids"},
            self._totals(),
            {"$sort": {"wins": -1, "draws": -1, "losses": 1, "_id": 1}},
            {"$limit": 10},
        ])
        return await cursor.to_list(length=10)
