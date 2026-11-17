from types import SimpleNamespace
from unittest.mock import AsyncMock

from games import battleship, connect4, rockpaperscissors, tictactoe

GAMES = {
    'tictactoe': (tictactoe.TicTacToeView, tictactoe.ChallengeView),
    'connect4': (connect4.Connect4View, connect4.ChallengeView),
    'battleship': (battleship.BattleshipView, battleship.ChallengeView),
    'rockpaperscissors': (rockpaperscissors.RockPaperScissorsView, rockpaperscissors.ChallengeView),
}


def member(identifier):
    return SimpleNamespace(id=identifier, mention=f'<@{identifier}>', display_name=str(identifier))


def interaction(identifier=1):
    return SimpleNamespace(
        user=member(identifier), guild_id=123, message=None,
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def game_view(game, store, guild_id=123):
    return GAMES[game][0](member(1), member(2), store=store, guild_id=guild_id)


async def prepare_finish(game, view, winner=1):
    """Play to the final move; return a callable for that move and its interaction."""
    if game == 'tictactoe':
        moves = {1: [0, 3, 1, 4, 2], 2: [0, 3, 1, 4, 8, 5],
                 None: [0, 1, 2, 4, 3, 5, 7, 6, 8]}[winner]
        for turn, cell in enumerate(moves[:-1]):
            await view.on_cell_click(interaction(1 + turn % 2), view.children[cell], cell)
        event = interaction(1 + (len(moves) - 1) % 2)
        return lambda: view.on_cell_click(event, view.children[moves[-1]], moves[-1]), event
    if game == 'connect4':
        if winner is None:
            # A legal full draw board, missing the final top-left red piece.
            view.board = [list(row) for row in ['RRYYRRY', 'YYRRYYR'] * 3]
            assert connect4.check_winner(view.board) == 'Draw'
            view.board[0][0] = None
            event = interaction(1)
            return lambda: view.on_column_click(event, 0), event
        moves = [0, 1, 0, 1, 0, 1, 0] if winner == 1 else [0, 1, 0, 1, 2, 1, 2, 1]
        for turn, column in enumerate(moves[:-1]):
            await view.on_column_click(interaction(1 + turn % 2), column)
        event = interaction(winner)
        return lambda: view.on_column_click(event, moves[-1]), event
    if game == 'rockpaperscissors':
        await view.on_choice(interaction(1), 'rock')
        event = interaction(2)
        choice = {1: 'scissors', 2: 'paper', None: 'rock'}[winner]
        return lambda: view.on_choice(event, choice), event
    # Put a generated fleet one shot from defeat. Battleship cannot draw.
    view.ready_players = {1, 2}
    view.current_turn = winner
    target = view.fleets[3 - winner]
    occupied = set().union(*target.ships.values())
    last = occupied.pop()
    target.shots = occupied
    coordinate = f'{chr(65 + last[0])}{last[1] + 1}'
    event = interaction(winner)
    return lambda: view.on_shot(event, coordinate, winner, 0), event
