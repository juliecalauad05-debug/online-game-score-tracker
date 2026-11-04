"""
init_db.py - create the tables and the game catalog.

    python init_db.py

MySQL (default): creates the `score_tracker` database and the 7 tables from
schema.sql. SQLite (DB_BACKEND=sqlite): uses schema_sqlite.sql.
Safe to run again: existing tables and games are left alone.
"""
import os
import re
import sys

import db

HERE = os.path.dirname(os.path.abspath(__file__))

# name, icon, category, win_rule, timer (seconds), min players, max players, description
GAMES = [
    ("UNO", "🃏", "card", "lowest", 600, 2, 10,
     "Empty your hand first. Penalty scoring: points left in hand add up, lowest total wins."),
    ("Poker", "♠️", "card", "highest", 900, 2, 9,
     "Points or chips won per hand. Highest total wins."),
    ("Mahjong", "🀄", "board", "highest", 1200, 4, 4,
     "Four-player tile game. Score each hand, highest total wins."),
    ("Chess", "♟️", "board", "highest", 900, 2, 2,
     "Two players. Record 1 point for a win and 0 for a loss (or 3/1/0 in tournaments)."),
    ("Checkers", "🔴", "board", "highest", 600, 2, 2,
     "Two players. Record 1 point for a win and 0 for a loss."),
    ("Clue", "🔍", "board", "highest", 1800, 2, 6,
     "Deduction game. Give points for each correct accusation."),
    ("Dominoes", "🔳", "board", "lowest", 600, 2, 4,
     "Penalty scoring: pips left in hand add up, lowest total wins."),
    ("Game of Life", "🎡", "board", "highest", 1800, 2, 6,
     "Final money total (in thousands) is the score. Highest wins."),
    ("Go", "⚪", "board", "highest", 1800, 2, 2,
     "Score is territory plus captures. Highest wins."),
    ("Ludo", "🎲", "board", "highest", 900, 2, 4,
     "Give points for each token brought home. Highest total wins."),
    ("Monopoly", "🏠", "board", "highest", 3600, 2, 8,
     "Final net worth (in thousands) is the score. Highest wins."),
    ("Pictionary", "✏️", "board", "highest", 60, 4, 16,
     "Points for each correct guess. The 60 second timer matches one drawing turn."),
    ("Risk", "🌍", "board", "highest", 3600, 2, 6,
     "Territories held at the end of the game. Highest wins."),
    ("Tic-Tac-Toe", "❌", "board", "highest", 120, 2, 2,
     "Quick match. 1 point per win."),
    ("Ticket to Ride", "🚂", "board", "highest", 2400, 2, 5,
     "Final point total from routes and tickets. Highest wins."),
    ("Trivial Pursuit", "❓", "board", "highest", 1800, 2, 6,
     "Points for each wedge or correct answer. Highest wins."),
    ("Volleyball", "🏐", "sport", "highest", 1500, 2, 12,
     "Points scored per set. Highest total wins."),
]


def run_script(con, text):
    """Run an .sql file statement by statement (works for MySQL and SQLite)."""
    text = re.sub(r"--[^\n]*", "", text)  # remove comments
    for statement in text.split(";"):
        statement = statement.strip()
        if not statement:
            continue
        if db.backend() == "sqlite":
            con.execute(statement)
        else:
            with con.cursor() as cur:
                cur.execute(statement)


def create_tables():
    if db.backend() == "sqlite":
        con = db.connect()
        with open(os.path.join(HERE, "schema_sqlite.sql"), encoding="utf-8") as f:
            run_script(con, f.read())
        return con
    con = db.connect(use_database=False)
    with open(os.path.join(HERE, "schema.sql"), encoding="utf-8") as f:
        run_script(con, f.read())
    return con


def seed_games(con):
    added = 0
    for name, icon, cat, rule, timer, lo, hi, desc in GAMES:
        if db.backend() == "sqlite":
            exists = con.execute("SELECT 1 FROM games WHERE name = ?", (name,)).fetchone()
            if not exists:
                con.execute(
                    "INSERT INTO games (name, icon, category, win_rule, default_timer_seconds,"
                    " min_players, max_players, description) VALUES (?,?,?,?,?,?,?,?)",
                    (name, icon, cat, rule, timer, lo, hi, desc))
                added += 1
        else:
            with con.cursor() as cur:
                cur.execute("SELECT 1 FROM games WHERE name = %s", (name,))
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO games (name, icon, category, win_rule, default_timer_seconds,"
                        " min_players, max_players, description) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (name, icon, cat, rule, timer, lo, hi, desc))
                    added += 1
    return added


def main():
    try:
        con = create_tables()
        if db.backend() != "sqlite":
            with con.cursor() as cur:
                cur.execute("USE score_tracker")
        added = seed_games(con)
        con.close()
    except Exception as exc:  # show a friendly message for the most common problems
        print("Could not set up the database:", exc)
        print("Check that MySQL is running and that DB_USER / DB_PASSWORD are correct.")
        sys.exit(1)
    where = db.sqlite_path() if db.backend() == "sqlite" else "MySQL database 'score_tracker'"
    print(f"Done. Tables ready in {where}. Games added: {added}.")


if __name__ == "__main__":
    main()
