"""
seed.py - fill the database with demo data so every page has something to show.

    python seed.py            add demo data (does nothing if it is already there)
    python seed.py --reset    delete ALL users, sessions and scores first, then add demo data

Demo accounts (password for all of them: demo1234)
    admin        admin
    host_maria   host      host_jun   host
    juan, ana, carlo, bea, miguel, lara, paolo, nina   players

The demo contains finished and ongoing sessions, a running timer, an open session,
a finished tournament (Barkada UNO League) and a tournament in progress (Section Chess Cup).
"""
import random
import sys
from datetime import timedelta

from werkzeug.security import generate_password_hash

import app as tracker
import db
import init_db

PASSWORD = "demo1234"
rng = random.Random(2026)  # fixed seed: same demo data every time
USERS = {}      # username -> user_id
GAMES = {}      # game name -> game row


def add_user(name, role):
    uid = db.execute(
        "INSERT INTO users (username, email, password_hash, role, created_at) VALUES (%s,%s,%s,%s,%s)",
        (name, f"{name}@example.com", generate_password_hash(PASSWORD), role,
         db.now() - timedelta(days=30)))
    USERS[name] = uid


def make_session(title, game, host, players, rounds, days_ago=1, mode="casual",
                 tournament=None, stage=None, finish=False, host_plays=False):
    """rounds = list of (scores dict {username: points}, timer) where timer is
    'idle', 'done' or 'running'."""
    g = GAMES[game]
    created = db.now() - timedelta(days=days_ago, hours=1)
    sid = db.execute(
        "INSERT INTO sessions (game_id, host_id, title, mode, tournament_name, tournament_stage,"
        " status, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (g["game_id"], USERS[host], title, mode, tournament, stage,
         "ongoing" if rounds else "open", created))
    members = list(players) + ([host] if host_plays and host not in players else [])
    for name in members:
        db.execute("INSERT INTO session_players (session_id, user_id, joined_at) VALUES (%s,%s,%s)",
                   (sid, USERS[name], created))
    for number, (scores, timer) in enumerate(rounds, start=1):
        limit = g["default_timer_seconds"]
        started = ended = None
        if timer == "done":
            started = created + timedelta(minutes=10 * number)
            ended = started + timedelta(seconds=int(limit * 0.7))
        elif timer == "running":
            started = db.now() - timedelta(seconds=45)
        rid = db.execute(
            "INSERT INTO rounds (session_id, round_no, time_limit_seconds, started_at, ended_at)"
            " VALUES (%s,%s,%s,%s,%s)", (sid, number, limit, started, ended))
        for name, points in scores.items():
            db.execute("INSERT INTO scores (round_id, user_id, points) VALUES (%s,%s,%s)",
                       (rid, USERS[name], points))
    if finish:
        board = tracker.finalize_session(tracker.load_session(sid))
        db.execute("UPDATE sessions SET finished_at = %s WHERE session_id = %s",
                   (created + timedelta(hours=2), sid))
        return sid, board
    return sid, None


def uno_rounds(players, count):
    """UNO penalty scoring: the player who goes out scores 0, the others get points left in hand."""
    out = []
    for _ in range(count):
        winner = rng.choice(players)
        out.append(({p: 0 if p == winner else rng.randint(5, 45) for p in players}, "done"))
    return out


def chess_rounds(a, b, count):
    out = []
    for _ in range(count):
        a_wins = rng.random() < 0.5
        out.append(({a: int(a_wins), b: int(not a_wins)}, "done"))
    return out


def chess_match(title, stage, a, b, days_ago, finish=True):
    rounds = chess_rounds(a, b, 3)
    sid, board = make_session(title, "Chess", "host_maria", [a, b], rounds, days_ago=days_ago,
                              mode="tournament", tournament="Section Chess Cup 2026",
                              stage=stage, finish=finish)
    return board[0]["username"]  # winner (3 rounds, no draws)


def wipe():
    for table in ("results", "scores", "rounds", "session_players", "sessions", "users"):
        db.execute(f"DELETE FROM {table}")


def main():
    init_db.main()  # tables + game catalog (safe to repeat)
    with tracker.app.app_context():
        if "--reset" in sys.argv:
            wipe()
        elif db.query("SELECT 1 FROM users WHERE username = 'admin'", one=True):
            print("Demo data is already there. Use  python seed.py --reset  to rebuild it.")
            return
        elif db.scalar("SELECT COUNT(*) FROM users"):
            print("The database already has users. Use  python seed.py --reset  to replace them with demo data.")
            return

        for name, role in [("admin", "admin"), ("host_maria", "host"), ("host_jun", "host")]:
            add_user(name, role)
        everyone = ["juan", "ana", "carlo", "bea", "miguel", "lara", "paolo", "nina"]
        for name in everyone:
            add_user(name, "player")
        for g in db.query("SELECT * FROM games"):
            GAMES[g["name"]] = g

        # --- casual sessions -------------------------------------------------
        p = ["host_maria", "juan", "ana", "carlo", "bea"]
        make_session("Friday Night UNO", "UNO", "host_maria", p, uno_rounds(p, 5),
                     days_ago=9, finish=True, host_plays=True)

        p = ["host_jun", "juan", "miguel", "lara"]
        poker = [({n: rng.randint(20, 140) for n in p}, "done") for _ in range(4)]
        make_session("Sunday Poker Night", "Poker", "host_jun", p, poker, days_ago=7,
                     finish=True, host_plays=True)

        p = ["ana", "bea", "paolo", "nina"]
        make_session("Family Monopoly", "Monopoly", "host_maria", p,
                     [({n: rng.randint(1500, 4200) for n in p}, "done")], days_ago=5, finish=True)

        # --- tournament finished: Barkada UNO League -----------------------------
        for match, group, days in (("Match 1", ["juan", "ana", "carlo", "bea"], 4),
                                   ("Match 2", ["miguel", "lara", "paolo", "nina"], 3)):
            make_session(f"UNO League - {match}", "UNO", "host_maria", group, uno_rounds(group, 4),
                         days_ago=days, mode="tournament", tournament="Barkada UNO League",
                         stage=match, finish=True)

        # --- tournament in progress: Section Chess Cup 2026 ----------------------
        winners = [chess_match(f"Chess Cup - Group {i + 1}", "Group Stage", a, b, 6 - i // 2)
                   for i, (a, b) in enumerate(zip(everyone[0::2], everyone[1::2]))]
        finalists = [chess_match("Chess Cup - Semi-final 1", "Semi-final", winners[0], winners[1], 2),
                     chess_match("Chess Cup - Semi-final 2", "Semi-final", winners[2], winners[3], 2)]
        a, b = finalists
        final_rounds = chess_rounds(a, b, 2) + [({}, "running")]  # round 3 timer is running now
        make_session("Chess Cup - Final", "Chess", "host_maria", [a, b], final_rounds, days_ago=0,
                     mode="tournament", tournament="Section Chess Cup 2026", stage="Final")

        # --- live and open sessions ----------------------------------------------
        p = ["juan", "bea", "miguel", "nina"]
        make_session("Weekend Mahjong", "Mahjong", "host_jun", p,
                     [({n: rng.randint(10, 80) for n in p}, "done") for _ in range(3)] + [({}, "idle")],
                     days_ago=1)
        p = ["ana", "carlo", "lara", "paolo", "nina"]
        make_session("Pictionary Party", "Pictionary", "host_maria", p,
                     [({n: rng.randint(0, 6) for n in p}, "done"), ({}, "running")], days_ago=0)
        make_session("Ludo Afternoon", "Ludo", "host_jun", ["lara", "paolo"], [], days_ago=0,
                     host_plays=False)

        print("Demo data added: 3 staff accounts, 8 players, 15 sessions.")
        print(f"Log in as  admin / {PASSWORD}  (or host_maria, host_jun, juan, ana ...).")


if __name__ == "__main__":
    main()
