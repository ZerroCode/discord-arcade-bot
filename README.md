# Discord Arcade Bot

A Discord bot with challenges and interactive game boards.
Requires Python 3.10 or newer; locally tested with Python 3.14.

## Setup

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

```powershell
.\.venv\Scripts\python.exe main.py
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

```bash
python main.py
```

Edit `.env` and set `DISCORD_TOKEN` to your bot token. Optionally set
`DISCORD_GUILD_ID` to register commands in one development server. Leave it
empty to register global commands. Existing environment variables take
precedence over `.env`. Never commit the token.

Install the bot in your server with the `bot` and `applications.commands`
scopes and channel permissions to view the channel, send messages, and embed
links. This bot uses slash commands and buttons; privileged member and
message-content intents are not required.

Commands sync once on startup in the configured scope. Changing between guild
and global registration does not remove commands previously registered in the
other scope; remove obsolete registrations separately if switching scopes.

## MongoDB setup

A reachable MongoDB server is required at startup. Use a local MongoDB instance
or an Atlas cluster. Configure `.env`:

```dotenv
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=discord_arcade
```

For Atlas, replace the URI with your cluster's connection string, configure
network access for the bot host, and use a database user with read/write access
to the selected database. Keep credentials in `.env`; do not commit them.
The bot uses PyMongo's async client, checks connectivity, and creates the match
query indexes before registering commands. Database operations have a five-second
timeout. The client closes when the bot shuts down.

Completed **tic-tac-toe, Connect 4, Battleship, and Rock Paper Scissors** matches
are tracked. Solo games are excluded from competitive stats and total wins.
Each match is stored in `matches` with its unique `_id`, server ID, player IDs,
game, winner (or draw), and UTC completion time. Discord IDs are strings.
The unique `_id` prevents retries from adding a second copy of a match.
Challenges, declines, expired games, and failed board updates do not add results.

- `/arcade stats [member] [game]` shows a player's combined totals across all
  four 1v1 games in this server; omitting the member shows your own totals.
  Choose a game to see only that game's wins, losses, draws, and matches played.
- `/arcade leaderboard [game]` shows the server's top ten players by combined
  1v1 wins, or by a selected game. Rankings use wins, then draws, then fewest
  losses. Player ID breaks remaining ties.

Previously recorded tic-tac-toe results count automatically; no migration is
needed. Matches played before recording was enabled cannot be reconstructed.

Stats are computed from match history. There are no separately updated counters.
If saving fails, the finished board remains visible and the player receives a
notice that saving could not be confirmed. There is no durable retry queue;
an outage or process exit between finishing a game and saving can leave a result
unrecorded. Active boards still do not survive restarts.

## Tests

Run from the repository root after installing `requirements.txt`:

```bash
python -m unittest discover -s tests -v
```

The game and command tests need neither a Discord token nor MongoDB. They cover
wins for either player, supported draws, duplicate and competing final clicks,
challenge acceptance/decline/expiry, spectators, timeouts, failed board updates,
and database failure notices. Each game runs through its actual callbacks with
Discord responses mocked.

To run the five real MongoDB integration tests as well, start a test MongoDB
server and explicitly set `TEST_MONGODB_URI`:

```bash
# macOS / Linux
TEST_MONGODB_URI=mongodb://127.0.0.1:27017 python -m unittest discover -s tests -v
```

```powershell
# Windows PowerShell
$env:TEST_MONGODB_URI = "mongodb://127.0.0.1:27017"
python -m unittest discover -s tests -v
```

Use a test server and an account allowed to create and drop databases. Each test
creates its own `arcade_test_<random UUID>` database and drops it during cleanup.
Tests never load `.env` or use `MONGODB_DATABASE`. If `TEST_MONGODB_URI` is absent,
the integration tests are explicitly skipped; if it is set but unavailable,
they fail. The integration tests check all four game callbacks against MongoDB,
concurrent duplicate inserts, combined and per-game stats, server separation,
solo exclusions, and leaderboard ordering and limits.

### Discord smoke check

With the bot running in a development server, use two accounts to complete each
1v1 game. Check `/arcade stats` for both players and `/arcade leaderboard`, both
with and without a game filter. Each win should add one win for the winner and
one loss for the opponent; a draw should add one draw for each player. Decline a
challenge and let another expire, confirming neither changes totals. Verify a
spectator cannot move, and restart the bot to confirm saved totals remain.
These live Discord checks are manual; the automated suite does not log into Discord.

## Running in VS Code on Windows

Open this project folder in VS Code after creating `.venv` with the setup
commands above. Choose Run and Debug and press F5.
This configuration always starts `main.py` with the project interpreter.

If using Code Runner's **Run Code** button, open `main.py` first. The workspace
settings use `.venv\Scripts\python.exe`, bypassing the Windows Store `python`
shortcut. `bot.py` defines the bot class; it is not the startup script.

If the Python extension still shows an old interpreter, run **Python: Select
Interpreter** and choose `.venv\Scripts\python.exe` for this project.

## Playing

Run `/arcade info` to post a directory of available games, grouped into
**1v1 Activities** and **Solo Activities**. Currently, tic-tac-toe, Connect 4,
Battleship, and Rock Paper Scissors are available as 1v1 games. Wordle, Hangman,
and Minesweeper are available under Solo Activities.

Run `/arcade`, select a game, and choose another server member(if applicable). 
Only that member can accept or decline. Accepting replaces the challenge with a 
board in the same message.

Challenges expire after two minutes. Games expire after five minutes without
an interaction. Finished and expired games disable their buttons and release
their event handlers. Games are held in memory and do not survive bot restarts;
start a new challenge after restarting.

## Layout

```text
discord-arcade-bot/
├── main.py                    # Entry point and logging configuration
├── bot.py                     # GameBot, extensions, command sync
├── config.py                  # Environment loading and validation
├── games/
│   ├── __init__.py
│   ├── arcade.py              # Owns /arcade and registers game commands
│   ├── tictactoe.py           
|   ├── battleship.py          
│   ├── connect4.py            
|   ├── rockpaperscissors.py   
|   ├── wordle.py              
|   ├── hangman.py             
|   ├── minesweeper.py         
│   └── views.py               # Shared challenge and timeout handling
├── data/
│   ├── wordle/                # Wordle official word lists and allowed guesses
│   └── hangman/               # Hangman word lists
├── storage/
│   └── database.py            # Async MongoDB match history and statistics
├── .env.example
├── .gitignore
└── requirements.txt
```

Add new game commands to the `Arcade` cog in `games/arcade.py`, keeping their
rules and views in their own game modules. If a game grows substantially,
split that module into a package with `logic.py` and `views.py`.
Set `extras={"activity": "1v1"}` or `extras={"activity": "solo"}` on each game’s
`@app_commands.command` decorator to include it in `/arcade info`.

Logs go to the console and `discord.log`. The file rotates at approximately
5 MB with two backups; log files and local environment files are ignored by Git.