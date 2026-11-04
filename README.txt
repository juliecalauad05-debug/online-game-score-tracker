# Online Card/Board Game Score Tracker (version 2)

A web-based system that lets groups log and track scores across multiple rounds of card or board games
(UNO, Chess, Mahjong, Checkers, Clue, Dominoes, Game of Life, Go, Ludo, Monopoly, Pictionary, Poker, Risk,
Tic-Tac-Toe, Ticket to Ride, Trivial Pursuit, Volleyball). It replaces manual score sheets and keeps a
running history of who is winning.

Language: Python (Flask) · Database: MySQL (7 tables) · Front end: HTML + CSS + a little JavaScript

## Features

| Requirement | How it works |
|---|---|
| Records each player's score per round | Hosts enter scores for every round (table `scores`) |
| Tracks total games played per session | Number of rounds in the session, shown on every session |
| Automatically calculates total points per player | `SUM(points)` over all rounds (see `STATS_SQL` in `app.py`) |
| Calculates total wins per player | A round win = best score of that round (highest or lowest, set per game) |
| Displays a leaderboard per game session | Scoreboard on each session page, ties share a rank |
| Add tournament every game | A tournament is a group of sessions of one game with the same tournament name, with its own standings and champion |
| Add timers for all the games | Every game has a default timer; every round has its own countdown (start, stop, reset, beep at zero) |

Extra features in version 2:

- Dashboard with live sessions, top players, most played games and totals
- All-time leaderboard (all games, or one game) with podium and win rate
- Player profiles with stats per game and recent results
- Search and filter sessions by title, game, status and mode
- Host tools: add players by username, remove players, edit title or stage, delete last round, reopen a finished session
- Game catalog with description, category, player limits, win rule and timer (admin can add, edit, delete)
- Admin page to promote users to host or admin (no SQL needed)
- CSV export of a session's scoreboard
- Security basics: hashed passwords, CSRF protection on every form, role checks (player, host, admin)
- Light and dark mode: follows your device, and the 🌙/☀️ button in the top bar switches it (your choice is remembered). Works on phones too
- Demo data (`seed.py`) and 30 automated tests

## Quick start (Windows, Mac, Linux)

You need Python 3.9 or newer. Open a terminal inside this folder.

**1. Install the packages**

```
pip install -r requirements.txt
```

**2a. Run with MySQL (the official database)**

1. Install and start MySQL (on Windows the easiest way is XAMPP: click Start next to MySQL).
2. If your MySQL root user has a password, set it first.
   Windows (Command Prompt): `set DB_PASSWORD=your_password`
   Mac/Linux: `export DB_PASSWORD=your_password`
3. Create the database, tables and game catalog:
   ```
   python init_db.py
   ```
4. Optional but recommended, add demo data:
   ```
   python seed.py
   ```
5. Start the website:
   ```
   python app.py
   ```
6. Open http://127.0.0.1:5000

**2b. Run without MySQL (quick demo with SQLite)**

```
set DB_BACKEND=sqlite          (Windows)      export DB_BACKEND=sqlite   (Mac/Linux)
python init_db.py
python seed.py
python app.py
```

This stores everything in a file called `scoretracker.db`. The website behaves the same.
Use MySQL for your final submission because the project requires it.

## Logging in

- With demo data: `admin` / `demo1234` (also `host_maria`, `host_jun`, `juan`, `ana`, ... all with password `demo1234`).
- Without demo data: click Register. **The first account you create becomes the admin.**
- Admins can promote other accounts to host on the Users page. Hosts create sessions and enter scores.

## Settings (environment variables)

| Name | Default | Meaning |
|---|---|---|
| `DB_BACKEND` | `mysql` | `mysql` or `sqlite` |
| `DB_HOST`, `DB_PORT` | `localhost`, `3306` | MySQL server |
| `DB_USER`, `DB_PASSWORD` | `root`, (empty) | MySQL login |
| `DB_NAME` | `score_tracker` | MySQL database name |
| `DB_PATH` | `scoretracker.db` | SQLite file |
| `SECRET_KEY` | dev value | Change this before putting the site online |
| `FLASK_DEBUG` | `1` | Set to `0` to turn debug mode off |

## Project structure

```
app.py                Flask routes and all SQL (SUM totals, wins, ranking, timer, tournaments)
db.py                 Database helper (MySQL or SQLite)
init_db.py            Creates tables and the 17-game catalog
seed.py               Demo data
schema.sql            MySQL schema, exactly 7 tables
schema_sqlite.sql     Same 7 tables for SQLite
templates/            HTML pages (Jinja2)
static/style.css      Design
tests/test_app.py     Automated tests
docs/USER_GUIDE.md    How to use the website step by step
docs/DATABASE.md      Data dictionary, relationships and key queries
docs/DIAGRAM_UPDATES.md  What to draw in the flowcharts, DFD and ERD
```

## Run the tests

```
python -m unittest discover -s tests -v
```

The tests run on SQLite, so no MySQL is needed. They cover scoring and SUM totals, round wins for highest
and lowest games, ties, rankings, timers, tournaments, permissions, CSRF, CSV export and every page.

## How the main calculations work

- **Total points per player**: `SUM(scores.points)` for that player over the rounds of the session
  (or over a game, a tournament, or everything on the leaderboards).
  Example: scores of 15, 20 and 10 give 45.
- **Wins per player**: a player wins a round when their score equals the best score of that round.
  For most games the best score is the highest. For games set to "lowest wins" (UNO, Dominoes) it is the lowest.
  Ties give a win to everyone tied.
- **Ranking**: by total points (highest or lowest depending on the game), then wins. Equal totals share a rank.
  Players who have no score yet are listed last.
- **Final results**: when the host finishes a session, the totals are saved into the `results` table
  (rank, points, rounds won, winner flag) so history stays fixed. Reopening the session removes them again.
- **Tournaments**: standings are the SUM of points over all matches of that tournament. The champion appears
  when every match is finished.
- **Timers**: start and stop times are stored on the round using the server clock, so everyone sees the same time.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Could not set up the database` | MySQL is not running, or `DB_USER` / `DB_PASSWORD` is wrong |
| `No module named flask` or `pymysql` | Run `pip install -r requirements.txt` |
| `Access denied for user 'root'` | Set `DB_PASSWORD` as shown above |
| Page says 403 | Your role is not allowed. Ask the admin to make you a host |
| Game list is empty | Run `python init_db.py` |
| Want to start over | `python seed.py --reset` (deletes all users, sessions and scores, then adds demo data) |
| Forgot the admin password | Run `python seed.py --reset`, or edit the `users` table |

## Notes for your teacher and report

- The schema has **7 tables**: `users`, `games`, `sessions`, `session_players`, `rounds`, `scores`, `results`.
  Tournament and timer features use extra columns, not extra tables. See `docs/DATABASE.md`.
- `docs/DIAGRAM_UPDATES.md` lists what to add to your flowcharts, DFD (Level 2, Gane-Sarson) and ERD.
- The SQL in `app.py` is written to run on both MySQL and SQLite. It was tested on SQLite (30 tests).
  The MySQL connection and `schema.sql` import are the only parts not covered by the automated tests.
