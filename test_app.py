"""
Automated tests. They run the whole website on SQLite (no MySQL needed):

    python -m unittest discover -s tests -v
"""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_tmp = tempfile.mkdtemp()
os.environ["DB_BACKEND"] = "sqlite"
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import app as tracker  # noqa: E402
import db  # noqa: E402
import init_db  # noqa: E402


def fresh_database():
    if os.path.exists(db.sqlite_path()):
        os.remove(db.sqlite_path())
    init_db.main()


class Client:
    """Tiny wrapper that logs in and posts forms with the CSRF token."""

    def __init__(self):
        self.c = tracker.app.test_client()

    def token(self):
        with self.c.session_transaction() as s:
            if "_csrf" not in s:
                self.c.get("/login")
                with self.c.session_transaction() as s2:
                    return s2["_csrf"]
            return s["_csrf"]

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data["_csrf"] = self.token()
        return self.c.post(url, data=data, **kw)

    def register(self, name):
        return self.post("/register", dict(username=name, email=f"{name}@x.com",
                                           password="secret1", confirm="secret1"))

    def login(self, name):
        self.c.get("/logout")
        return self.post("/login", dict(username=name, password="secret1"))


def one(sql, args=()):
    with tracker.app.app_context():
        return db.query(sql, args, one=True)


def many(sql, args=()):
    with tracker.app.app_context():
        return db.query(sql, args)


def game_id(name):
    return one("SELECT game_id FROM games WHERE name = %s", (name,))["game_id"]


class BaseCase(unittest.TestCase):
    def setUp(self):
        fresh_database()
        self.admin = Client()
        self.admin.register("boss")       # first account -> admin
        self.admin.login("boss")
        for name in ("maria", "juan", "ana", "carlo"):
            Client().register(name)
        self.admin.post(f"/admin/users/{self.uid('maria')}/role", dict(role="host"))
        self.host = Client()
        self.host.login("maria")

    def uid(self, name):
        return one("SELECT user_id FROM users WHERE username = %s", (name,))["user_id"]

    def make_session(self, game="UNO", players=("juan", "ana", "carlo"), **extra):
        data = dict(title="Test game", game_id=game_id(game), mode="casual")
        data.update(extra)
        r = self.host.post("/sessions/new", data)
        self.assertEqual(r.status_code, 302)
        sid = one("SELECT MAX(session_id) AS m FROM sessions")["m"]
        for p in players:
            self.host.post(f"/sessions/{sid}/players/add", dict(username=p))
        return sid

    def add_round_scores(self, sid, scores):
        self.host.post(f"/sessions/{sid}/rounds/add", dict(time_limit="60"))
        rid = one("SELECT MAX(round_id) AS m FROM rounds WHERE session_id = %s", (sid,))["m"]
        self.host.post(f"/rounds/{rid}/scores",
                       {f"points_{self.uid(n)}": v for n, v in scores.items()})
        return rid


class AccountTests(BaseCase):
    def test_first_user_is_admin_others_are_players(self):
        self.assertEqual(one("SELECT role FROM users WHERE username='boss'")["role"], "admin")
        self.assertEqual(one("SELECT role FROM users WHERE username='juan'")["role"], "player")

    def test_login_required_and_wrong_password(self):
        self.assertEqual(Client().get("/").status_code, 302)
        c = Client()
        r = c.post("/login", dict(username="juan", password="nope"), follow_redirects=True)
        self.assertIn(b"Wrong username or password", r.data)

    def test_csrf_blocks_post_without_token(self):
        r = tracker.app.test_client().post("/login", data=dict(username="juan", password="secret1"))
        self.assertEqual(r.status_code, 400)

    def test_duplicate_and_invalid_registration(self):
        r = Client().register("juan")
        self.assertEqual(r.status_code, 200)  # re-rendered form with errors
        self.assertEqual(one("SELECT COUNT(*) AS n FROM users WHERE username='juan'")["n"], 1)
        r = Client().post("/register", dict(username="a b", email="bad", password="1", confirm="2"))
        self.assertIn(b"Username must be", r.data)

    def test_players_cannot_create_sessions_or_admin_pages(self):
        c = Client()
        c.login("juan")
        self.assertEqual(c.get("/sessions/new").status_code, 403)
        self.assertEqual(c.get("/admin/users").status_code, 403)

    def test_admin_changes_role_immediately(self):
        c = Client()
        c.login("juan")
        self.assertEqual(c.get("/sessions/new").status_code, 403)
        self.admin.post(f"/admin/users/{self.uid('juan')}/role", dict(role="host"))
        self.assertEqual(c.get("/sessions/new").status_code, 200)


class ScoringTests(BaseCase):
    def test_sum_totals_and_wins_highest_rule(self):
        sid = self.make_session("Poker")
        self.add_round_scores(sid, dict(juan=15, ana=20, carlo=5))
        self.add_round_scores(sid, dict(juan=20, ana=20, carlo=1))
        self.add_round_scores(sid, dict(juan=10, ana=0, carlo=3))
        with tracker.app.app_context():
            s = tracker.load_session(sid)
            board = tracker.build_board(s, tracker.session_players(sid))
        by = {r["username"]: r for r in board}
        self.assertEqual(by["juan"]["total_points"], 45)     # 15 + 20 + 10, the README example
        self.assertEqual(by["ana"]["total_points"], 40)
        self.assertEqual(by["juan"]["wins"], 2)              # round 2 tie counts for both
        self.assertEqual(by["ana"]["wins"], 2)
        self.assertEqual(by["carlo"]["wins"], 0)
        self.assertEqual(board[0]["username"], "juan")
        self.assertEqual(board[0]["rank"], 1)

    def test_lowest_rule_for_uno(self):
        sid = self.make_session("UNO")
        self.add_round_scores(sid, dict(juan=0, ana=30, carlo=12))
        self.add_round_scores(sid, dict(juan=40, ana=0, carlo=9))
        with tracker.app.app_context():
            board = tracker.build_board(tracker.load_session(sid), tracker.session_players(sid))
        # maria (the host) is in the session but has no scores: she must not rank above the scorers
        self.assertEqual([r["username"] for r in board], ["carlo", "ana", "juan", "maria"])
        self.assertEqual(board[0]["username"], "carlo")      # 21 points, lowest total
        self.assertIsNone(board[-1]["rank"])
        self.assertEqual({r["username"]: r["wins"] for r in board},
                         dict(juan=1, ana=1, carlo=0, maria=0))

    def test_ties_share_rank(self):
        sid = self.make_session("Poker", players=("juan", "ana"))
        self.add_round_scores(sid, dict(juan=10, ana=10))
        with tracker.app.app_context():
            board = tracker.build_board(tracker.load_session(sid), tracker.session_players(sid))
        self.assertEqual([r["rank"] for r in board if r["username"] in ("juan", "ana")], [1, 1])

    def test_invalid_score_is_rejected_and_blank_clears(self):
        sid = self.make_session("Poker", players=("juan",))
        rid = self.add_round_scores(sid, dict(juan=7))
        self.host.post(f"/rounds/{rid}/scores", {f"points_{self.uid('juan')}": "abc"})
        self.assertEqual(one("SELECT points FROM scores WHERE round_id=%s", (rid,))["points"], 7)
        self.host.post(f"/rounds/{rid}/scores", {f"points_{self.uid('juan')}": ""})
        self.assertIsNone(one("SELECT 1 FROM scores WHERE round_id=%s", (rid,)))

    def test_only_host_or_admin_can_edit_scores(self):
        sid = self.make_session("Poker")
        rid = self.add_round_scores(sid, dict(juan=1))
        other = Client()
        other.login("juan")
        r = other.post(f"/rounds/{rid}/scores", {f"points_{self.uid('juan')}": "99"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.admin.post(f"/rounds/{rid}/scores",
                                         {f"points_{self.uid('juan')}": "5"}).status_code, 302)

    def test_finish_saves_results_and_reopen_removes_them(self):
        sid = self.make_session("Poker")
        self.add_round_scores(sid, dict(juan=15, ana=20, carlo=5))
        self.host.post(f"/sessions/{sid}/finish")
        self.assertEqual(one("SELECT status FROM sessions WHERE session_id=%s", (sid,))["status"], "finished")
        rows = many("SELECT * FROM results WHERE session_id=%s ORDER BY rank_no", (sid,))
        self.assertEqual(len(rows), 4)   # juan, ana, carlo + the host who joined automatically
        self.assertEqual((rows[0]["user_id"], rows[0]["total_points"], rows[0]["is_winner"]),
                         (self.uid("ana"), 20, 1))
        # finished sessions are locked
        rid = one("SELECT MAX(round_id) AS m FROM rounds")["m"]
        self.host.post(f"/rounds/{rid}/scores", {f"points_{self.uid('juan')}": "50"})
        self.assertEqual(one("SELECT points FROM scores WHERE round_id=%s AND user_id=%s",
                             (rid, self.uid("juan")))["points"], 15)
        self.host.post(f"/sessions/{sid}/reopen")
        self.assertEqual(many("SELECT * FROM results WHERE session_id=%s", (sid,)), [])

    def test_cannot_finish_without_scores(self):
        sid = self.make_session("Poker")
        self.host.post(f"/sessions/{sid}/finish")
        self.assertEqual(one("SELECT status FROM sessions WHERE session_id=%s", (sid,))["status"], "open")

    def test_max_players_enforced(self):
        sid = self.make_session("Chess", players=("juan",))   # host + juan = 2 = max
        self.host.post(f"/sessions/{sid}/players/add", dict(username="ana"))
        self.assertEqual(one("SELECT COUNT(*) AS n FROM session_players WHERE session_id=%s", (sid,))["n"], 2)

    def test_remove_player_removes_their_scores(self):
        sid = self.make_session("Poker")
        rid = self.add_round_scores(sid, dict(juan=15, ana=20))
        self.host.post(f"/sessions/{sid}/players/{self.uid('juan')}/remove")
        self.assertIsNone(one("SELECT 1 FROM scores WHERE round_id=%s AND user_id=%s", (rid, self.uid("juan"))))

    def test_delete_last_round_and_session_cascade(self):
        sid = self.make_session("Poker")
        self.add_round_scores(sid, dict(juan=1))
        self.add_round_scores(sid, dict(juan=2))
        self.host.post(f"/sessions/{sid}/rounds/delete-last")
        self.assertEqual(one("SELECT COUNT(*) AS n FROM rounds WHERE session_id=%s", (sid,))["n"], 1)
        self.host.post(f"/sessions/{sid}/delete")
        self.assertEqual(one("SELECT COUNT(*) AS n FROM scores")["n"], 0)
        self.assertEqual(one("SELECT COUNT(*) AS n FROM session_players")["n"], 0)

    def test_csv_export(self):
        sid = self.make_session("Poker")
        self.add_round_scores(sid, dict(juan=15, ana=20, carlo=5))
        r = self.host.get(f"/sessions/{sid}/export.csv")
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("Rank,Player,Round 1,Total points,Rounds won", r.data.decode())
        self.assertIn("ana,20,20,1", r.data.decode())


class TimerTests(BaseCase):
    def test_timer_start_stop_reset_and_status(self):
        sid = self.make_session("Poker")
        rid = self.add_round_scores(sid, dict(juan=1))
        status = lambda: self.host.get(f"/rounds/{rid}/timer/status").get_json()
        self.assertEqual(status()["state"], "idle")
        self.assertEqual(status()["remaining"], 60)
        self.host.post(f"/rounds/{rid}/timer/start")
        self.assertEqual(status()["state"], "running")
        self.host.post(f"/rounds/{rid}/timer/stop")
        self.assertEqual(status()["state"], "stopped")
        self.host.post(f"/rounds/{rid}/timer/reset")
        self.assertEqual(status()["state"], "idle")

    def test_timer_finishes_when_time_is_up(self):
        from datetime import timedelta
        sid = self.make_session("Poker")
        rid = self.add_round_scores(sid, dict(juan=1))
        with tracker.app.app_context():
            db.execute("UPDATE rounds SET started_at = %s WHERE round_id = %s",
                       (db.now() - timedelta(seconds=90), rid))
        data = self.host.get(f"/rounds/{rid}/timer/status").get_json()
        self.assertEqual((data["state"], data["remaining"]), ("finished", 0))

    def test_players_cannot_control_timer(self):
        sid = self.make_session("Poker")
        rid = self.add_round_scores(sid, dict(juan=1))
        other = Client()
        other.login("juan")
        self.assertEqual(other.post(f"/rounds/{rid}/timer/start").status_code, 403)


class TournamentTests(BaseCase):
    def test_tournament_standings_and_champion(self):
        for stage, scores in (("Group", dict(juan=10, ana=5)), ("Final", dict(juan=3, ana=8))):
            sid = self.make_session("Poker", players=("juan", "ana"), mode="tournament",
                                    tournament_name="Cup", tournament_stage=stage, title=f"M {stage}")
            self.add_round_scores(sid, scores)
            self.host.post(f"/sessions/{sid}/players/{self.uid('maria')}/remove")
        gid = game_id("Poker")
        page = self.host.get(f"/tournaments/{gid}/Cup")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Tournament in progress", page.data)
        for sid in [r["session_id"] for r in many("SELECT session_id FROM sessions")]:
            self.host.post(f"/sessions/{sid}/finish")
        page = self.host.get(f"/tournaments/{gid}/Cup")
        self.assertIn(b"Champion", page.data)
        with tracker.app.app_context():
            found = tracker.stats(tracker.TOURNAMENT_WHERE, (gid, "Cup"))
        self.assertEqual(found[self.uid("juan")]["total_points"], 13)
        self.assertEqual(found[self.uid("ana")]["total_points"], 13)  # tie -> both champions
        self.assertEqual(self.host.get("/tournaments").status_code, 200)

    def test_tournament_requires_name(self):
        r = self.host.post("/sessions/new", dict(title="X", game_id=game_id("UNO"), mode="tournament"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(one("SELECT COUNT(*) AS n FROM sessions")["n"], 0)


class GamesTests(BaseCase):
    def test_admin_adds_edits_deletes_game(self):
        data = dict(name="Scrabble", icon="🔤", category="board", win_rule="highest",
                    default_timer_seconds="1200", min_players="2", max_players="4", description="Words")
        self.admin.post("/games/add", data)
        gid = game_id("Scrabble")
        data["default_timer_seconds"] = "1500"
        self.admin.post(f"/games/{gid}/edit", data)
        self.assertEqual(one("SELECT default_timer_seconds AS t FROM games WHERE game_id=%s", (gid,))["t"], 1500)
        self.admin.post(f"/games/{gid}/delete")
        self.assertIsNone(one("SELECT 1 FROM games WHERE game_id=%s", (gid,)))

    def test_invalid_game_rejected_and_used_game_not_deleted(self):
        self.admin.post("/games/add", dict(name="", category="x", win_rule="y",
                                           default_timer_seconds="1", min_players="5", max_players="2"))
        self.assertIsNone(one("SELECT 1 FROM games WHERE name = ''"))
        self.make_session("UNO")
        self.admin.post(f"/games/{game_id('UNO')}/delete")
        self.assertIsNotNone(one("SELECT 1 FROM games WHERE name = 'UNO'"))

    def test_catalog_has_the_requested_games(self):
        names = {g["name"] for g in many("SELECT name FROM games")}
        for wanted in ("UNO", "Chess", "Mahjong", "Checkers", "Clue", "Dominoes", "Game of Life", "Go",
                       "Ludo", "Monopoly", "Pictionary", "Poker", "Risk", "Tic-Tac-Toe",
                       "Ticket to Ride", "Trivial Pursuit", "Volleyball"):
            self.assertIn(wanted, names)
        self.assertEqual(len(many("SELECT 1 FROM games WHERE default_timer_seconds > 0")), len(names))


class SchemaTests(unittest.TestCase):
    def test_exactly_seven_tables(self):
        fresh_database()
        names = {r["name"] for r in many("SELECT name FROM sqlite_master WHERE type='table'"
                                         " AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(names, {"users", "games", "sessions", "session_players", "rounds",
                                 "scores", "results"})

    def test_mysql_schema_file_has_seven_tables(self):
        text = open(os.path.join(ROOT, "schema.sql"), encoding="utf-8").read()
        self.assertEqual(len(re.findall(r"CREATE TABLE", text)), 7)


class SeedAndPagesTests(unittest.TestCase):
    """Run the real seed script, then open every page as admin."""

    @classmethod
    def setUpClass(cls):
        fresh_database()
        sys.argv = ["seed.py"]
        import seed
        seed.main()
        cls.c = Client()
        cls.c.post("/login", dict(username="admin", password="demo1234"))

    def test_every_page_renders(self):
        urls = ["/", "/sessions", "/sessions?status=ongoing&mode=tournament&q=Chess", "/leaderboard",
                "/players", "/games", "/tournaments", "/admin/users", "/sessions/new", "/logout"]
        sessions = [r["session_id"] for r in many("SELECT session_id FROM sessions")]
        self.assertEqual(len(sessions), 15)
        urls = urls[:-1] + [f"/sessions/{s}" for s in sessions] + [f"/sessions/{s}/export.csv" for s in sessions[:2]]
        urls += [f"/players/{r['user_id']}" for r in many("SELECT user_id FROM users")]
        urls += [f"/games/{r['game_id']}/edit" for r in many("SELECT game_id FROM games LIMIT 2")]
        urls += [f"/leaderboard?game={r['game_id']}" for r in many("SELECT game_id FROM games")]
        for t in many("SELECT DISTINCT game_id, tournament_name FROM sessions WHERE mode='tournament'"):
            urls.append(f"/tournaments/{t['game_id']}/{t['tournament_name']}")
        for url in urls:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, url)

    def test_seed_story(self):
        # finished tournament has a champion, chess cup is still running
        gid = game_id("UNO")
        page = self.c.get(f"/tournaments/{gid}/Barkada UNO League").data
        self.assertIn(b"Champion", page)
        chess = self.c.get(f"/tournaments/{game_id('Chess')}/Section Chess Cup 2026").data
        self.assertIn(b"Tournament in progress", chess)
        # the Pictionary session has a running timer
        sid = one("SELECT session_id FROM sessions WHERE title='Pictionary Party'")["session_id"]
        self.assertIn(b'class="timer running"', self.c.get(f"/sessions/{sid}").data)
        # results table was filled for every finished session
        done = one("SELECT COUNT(*) AS n FROM sessions WHERE status='finished'")["n"]
        self.assertEqual(one("SELECT COUNT(DISTINCT session_id) AS n FROM results")["n"], done)

    def test_dark_mode_toggle_is_on_every_page(self):
        for url in ("/", "/leaderboard"):
            self.assertIn(b'id="theme-toggle"', self.c.get(url).data)
        self.assertIn(b'id="theme-toggle"', Client().get("/login").data)   # also when logged out
        css = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()
        self.assertIn(':root[data-theme="dark"]', css)

    def test_seed_is_not_repeated(self):
        import seed
        before = one("SELECT COUNT(*) AS n FROM sessions")["n"]
        seed.main()
        self.assertEqual(one("SELECT COUNT(*) AS n FROM sessions")["n"], before)


if __name__ == "__main__":
    unittest.main()
