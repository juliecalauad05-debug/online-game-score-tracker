"""
Online Card/Board Game Score Tracker Website - version 2
Python (Flask) + MySQL.

What it does
    * Players, hosts and admins with login (the first account becomes admin)
    * Game sessions with rounds; score per player per round
    * SUM() totals per player, round wins, ranking with ties, leaderboards
    * Tournament per game (a tournament = several sessions with the same name)
    * Timer for every round of every game (server time, shared by all viewers)
    * Dashboard, all-time leaderboard, player profiles, CSV export, admin tools

Run:  python init_db.py   (once)   then   python app.py
"""
import csv
import io
import os
import re
import secrets
from datetime import datetime
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
db.init_app(app)

ROLES = ("player", "host", "admin")
CATEGORIES = ("card", "board", "sport")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ====================================================================
# Small helpers
# ====================================================================
def parse_int(raw, low, high):
    """Turn form text into an int in [low, high] or raise ValueError."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError("Enter a whole number.")
    if not low <= value <= high:
        raise ValueError(f"Enter a number from {low} to {high}.")
    return value


def mmss(seconds):
    seconds = max(int(seconds or 0), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def fmt_dt(value):
    parsed = db.parse_dt(value)
    return parsed.strftime("%b %d, %Y %I:%M %p") if parsed else "-"


app.jinja_env.filters["mmss"] = mmss
app.jinja_env.filters["fmt_dt"] = fmt_dt


# ====================================================================
# Request hooks: current user + CSRF protection
# ====================================================================
@app.before_request
def load_user_and_check_csrf():
    if request.endpoint == "static":
        return
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    g.user = None
    if session.get("user_id"):
        g.user = db.query("SELECT user_id, username, role FROM users WHERE user_id = %s",
                          (session["user_id"],), one=True)
        if g.user is None:  # account was removed
            token = session["_csrf"]
            session.clear()
            session["_csrf"] = token
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not secrets.compare_digest(sent, session["_csrf"]):
            abort(400)


@app.context_processor
def inject_globals():
    return dict(me=g.get("user"), csrf_token=session.get("_csrf", ""))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.", "warn")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.errorhandler(400)
def bad_request(_e):
    return render_template("error.html", code=400, title="Bad request",
                           message="The form expired or was invalid. Go back and try again."), 400


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, title="Not allowed",
                           message="You do not have permission to do that."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, title="Not found",
                           message="That page does not exist."), 404


# ====================================================================
# Statistics (the SUM() queries)
# ====================================================================
# One query serves a session, a game, a tournament or everything: only the
# WHERE changes. A "round win" means the best score of that round, where
# "best" is the highest score or the lowest score depending on games.win_rule.
STATS_SQL = """
SELECT u.user_id, u.username,
       COALESCE(SUM(sc.points), 0)  AS total_points,
       COUNT(sc.score_id)           AS rounds_played,
       COALESCE(SUM(CASE WHEN sc.points = CASE WHEN g.win_rule = 'lowest'
                                               THEN b.lo ELSE b.hi END
                         THEN 1 ELSE 0 END), 0) AS wins
FROM scores sc
JOIN rounds   r ON r.round_id   = sc.round_id
JOIN sessions s ON s.session_id = r.session_id
JOIN games    g ON g.game_id    = s.game_id
JOIN users    u ON u.user_id    = sc.user_id
JOIN (SELECT round_id, MAX(points) AS hi, MIN(points) AS lo
      FROM scores GROUP BY round_id) b ON b.round_id = sc.round_id
{where}
GROUP BY u.user_id, u.username
"""


def stats(where="", args=()):
    """Return {user_id: stats row}. `where` is a fixed SQL fragment from this file."""
    rows = db.query(STATS_SQL.format(where=where), args)
    return {row["user_id"]: row for row in rows}


def rank_rows(rows, mode="highest"):
    """Sort rows and set row['rank'] (ties share a rank).

    mode: 'highest' / 'lowest'  -> rank by total points (session, game rules)
          'wins'                -> rank by round wins, then session wins
    """
    def key(row):
        # players who have not scored yet go last (otherwise 0 points would win "lowest" games)
        unscored = 0 if row["rounds_played"] else 1
        if mode == "wins":
            return (unscored, -row["wins"], -row.get("session_wins", 0), -row["total_points"],
                    row["username"].lower())
        points = row["total_points"] if mode == "lowest" else -row["total_points"]
        return (unscored, points, -row["wins"], row["username"].lower())

    rows.sort(key=key)
    previous, rank = None, 0
    for position, row in enumerate(rows, start=1):
        signature = ((row["wins"], row.get("session_wins", 0)) if mode == "wins"
                     else row["total_points"])
        if signature != previous:
            rank, previous = position, signature
        row["rank"] = rank if row["rounds_played"] else None
    return rows


def session_results_by_user(where="", args=()):
    """Sessions played and sessions won per user, from the results table (D7)."""
    rows = db.query(
        f"""SELECT rs.user_id, COUNT(*) AS sessions_played,
                   COALESCE(SUM(rs.is_winner), 0) AS session_wins
            FROM results rs JOIN sessions s ON s.session_id = rs.session_id
            {where} GROUP BY rs.user_id""", args)
    return {row["user_id"]: row for row in rows}


def build_board(s, players):
    """Leaderboard of one session. Players without scores still appear (0 points)."""
    found = stats("WHERE s.session_id = %s", (s["session_id"],))
    rows = []
    for p in players:
        row = found.get(p["user_id"], {})
        rows.append(dict(user_id=p["user_id"], username=p["username"],
                         total_points=row.get("total_points", 0),
                         rounds_played=row.get("rounds_played", 0),
                         wins=row.get("wins", 0)))
    return rank_rows(rows, s["win_rule"])


def round_winners(rounds, grid, win_rule):
    """{round_id: set(user_ids)} with the best score of each round."""
    winners = {}
    pick = min if win_rule == "lowest" else max
    for r in rounds:
        points = {uid: pts for (rid, uid), pts in grid.items() if rid == r["round_id"]}
        best = pick(points.values()) if points else None
        winners[r["round_id"]] = {uid for uid, pts in points.items() if pts == best}
    return winners


# ====================================================================
# Timer
# ====================================================================
def timer_info(r):
    """State of a round timer: idle, running, stopped or finished (time is up)."""
    limit = r["time_limit_seconds"]
    started, ended = db.parse_dt(r["started_at"]), db.parse_dt(r["ended_at"])
    if not started:
        return dict(state="idle", remaining=limit, limit=limit)
    elapsed = int(((ended or db.now()) - started).total_seconds())
    remaining = max(limit - elapsed, 0)
    if ended:
        state = "stopped"
    elif remaining == 0:
        state = "finished"
    else:
        state = "running"
    return dict(state=state, remaining=remaining, limit=limit)


# ====================================================================
# Data loaders
# ====================================================================
def load_session(sid):
    s = db.query(
        """SELECT s.*, g.name AS game_name, g.icon, g.win_rule, g.default_timer_seconds,
                  g.min_players, g.max_players, u.username AS host_name
           FROM sessions s
           JOIN games g ON g.game_id = s.game_id
           JOIN users u ON u.user_id = s.host_id
           WHERE s.session_id = %s""", (sid,), one=True)
    if s is None:
        abort(404)
    return s


def session_players(sid):
    return db.query(
        """SELECT u.user_id, u.username FROM session_players sp
           JOIN users u ON u.user_id = sp.user_id
           WHERE sp.session_id = %s ORDER BY sp.sp_id""", (sid,))


def can_manage(s):
    return g.user is not None and (g.user["user_id"] == s["host_id"] or g.user["role"] == "admin")


def require_manager(s):
    if not can_manage(s):
        abort(403)


def list_sessions(where="", args=(), limit=None):
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    return db.query(
        f"""SELECT s.*, g.name AS game_name, g.icon, u.username AS host_name,
                   (SELECT COUNT(*) FROM session_players sp
                     WHERE sp.session_id = s.session_id) AS players,
                   (SELECT COUNT(*) FROM rounds r
                     WHERE r.session_id = s.session_id) AS games_played
            FROM sessions s
            JOIN games g ON g.game_id = s.game_id
            JOIN users u ON u.user_id = s.host_id
            {where}
            ORDER BY s.session_id DESC {limit_sql}""", args)


# ====================================================================
# Accounts
# ====================================================================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        errors = []
        if not USERNAME_RE.match(username):
            errors.append("Username must be 3-30 letters, numbers or underscores.")
        if not EMAIL_RE.match(email):
            errors.append("Enter a valid email address.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != request.form.get("confirm", ""):
            errors.append("Passwords do not match.")
        if not errors and db.query(
                "SELECT 1 FROM users WHERE LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s)",
                (username, email), one=True):
            errors.append("That username or email is already used.")
        if errors:
            for message in errors:
                flash(message, "error")
        else:
            is_first = db.scalar("SELECT COUNT(*) FROM users") == 0
            db.execute(
                "INSERT INTO users (username, email, password_hash, role, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (username, email, generate_password_hash(password),
                 "admin" if is_first else "player", db.now()))
            flash("Account created. The first account is the admin." if is_first
                  else "Account created. You can log in now.", "ok")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = db.query("SELECT * FROM users WHERE LOWER(username) = LOWER(%s)",
                        (request.form.get("username", "").strip(),), one=True)
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            token = session["_csrf"]
            session.clear()
            session["_csrf"] = token
            session["user_id"] = user["user_id"]
            return redirect(url_for("index"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ====================================================================
# Dashboard and session list
# ====================================================================
@app.route("/")
@login_required
def index():
    tiles = dict(
        sessions=db.scalar("SELECT COUNT(*) FROM sessions"),
        ongoing=db.scalar("SELECT COUNT(*) FROM sessions WHERE status = 'ongoing'"),
        players=db.scalar("SELECT COUNT(*) FROM users"),
        rounds=db.scalar("SELECT COUNT(*) FROM rounds"),
        points=db.scalar("SELECT COALESCE(SUM(points), 0) FROM scores"),
    )
    wins_by_user = session_results_by_user()
    top = []
    for row in stats().values():
        row = dict(row, **{"session_wins": wins_by_user.get(row["user_id"], {}).get("session_wins", 0)})
        top.append(row)
    top = rank_rows(top, "wins")[:5]
    popular = db.query(
        """SELECT g.game_id, g.name, g.icon, COUNT(s.session_id) AS n
           FROM games g JOIN sessions s ON s.game_id = g.game_id
           GROUP BY g.game_id, g.name, g.icon ORDER BY n DESC, g.name LIMIT 6""")
    return render_template(
        "index.html", tiles=tiles, top=top, popular=popular,
        recent=list_sessions(limit=8),
        live=list_sessions("WHERE s.status = 'ongoing'", limit=6))


@app.route("/sessions")
@login_required
def sessions_page():
    where, args = ["1 = 1"], []
    q = request.args.get("q", "").strip()
    game = request.args.get("game", "").strip()
    status = request.args.get("status", "").strip()
    mode = request.args.get("mode", "").strip()
    if q:
        where.append("s.title LIKE %s")
        args.append("%" + q + "%")
    if game.isdigit():
        where.append("s.game_id = %s")
        args.append(int(game))
    if status in ("open", "ongoing", "finished"):
        where.append("s.status = %s")
        args.append(status)
    if mode in ("casual", "tournament"):
        where.append("s.mode = %s")
        args.append(mode)
    rows = list_sessions("WHERE " + " AND ".join(where), tuple(args), limit=200)
    return render_template("sessions.html", rows=rows, q=q, game=game, status=status,
                           mode=mode, games=db.query("SELECT game_id, name FROM games ORDER BY name"))


# ====================================================================
# Games catalog
# ====================================================================
def read_game_form():
    """Validate the game form. Returns (values dict, list of error messages)."""
    f, errors, v = request.form, [], {}
    v["name"] = f.get("name", "").strip()
    v["icon"] = f.get("icon", "").strip() or "🎲"
    v["description"] = f.get("description", "").strip()[:255]
    v["category"] = f.get("category", "board")
    v["win_rule"] = f.get("win_rule", "highest")
    if not v["name"] or len(v["name"]) > 60:
        errors.append("Game name is required (60 characters max).")
    if len(v["icon"]) > 8:
        errors.append("Icon must be a short emoji.")
    if v["category"] not in CATEGORIES:
        errors.append("Pick a valid category.")
    if v["win_rule"] not in ("highest", "lowest"):
        errors.append("Pick a valid win rule.")
    for field, low, high in (("default_timer_seconds", 10, 86400), ("min_players", 1, 50),
                             ("max_players", 1, 50)):
        try:
            v[field] = parse_int(f.get(field), low, high)
        except ValueError as exc:
            errors.append(f"{field.replace('_', ' ').capitalize()}: {exc}")
    if "min_players" in v and "max_players" in v and v["min_players"] > v["max_players"]:
        errors.append("Minimum players cannot be more than maximum players.")
    return v, errors


@app.route("/games")
@login_required
def games():
    rows = db.query(
        """SELECT g.*, (SELECT COUNT(*) FROM sessions s WHERE s.game_id = g.game_id) AS sessions
           FROM games g ORDER BY g.name""")
    return render_template("games.html", games=rows)


@app.route("/games/add", methods=["POST"])
@role_required("admin")
def add_game():
    v, errors = read_game_form()
    if not errors and db.query("SELECT 1 FROM games WHERE LOWER(name) = LOWER(%s)",
                               (v["name"],), one=True):
        errors.append("A game with that name already exists.")
    if errors:
        for message in errors:
            flash(message, "error")
    else:
        db.execute(
            "INSERT INTO games (name, icon, description, category, win_rule,"
            " default_timer_seconds, min_players, max_players) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (v["name"], v["icon"], v["description"], v["category"], v["win_rule"],
             v["default_timer_seconds"], v["min_players"], v["max_players"]))
        flash(f"Added {v['name']}.", "ok")
    return redirect(url_for("games"))


@app.route("/games/<int:game_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def edit_game(game_id):
    game = db.query("SELECT * FROM games WHERE game_id = %s", (game_id,), one=True)
    if game is None:
        abort(404)
    if request.method == "POST":
        v, errors = read_game_form()
        if not errors and db.query(
                "SELECT 1 FROM games WHERE LOWER(name) = LOWER(%s) AND game_id <> %s",
                (v["name"], game_id), one=True):
            errors.append("Another game already has that name.")
        if errors:
            for message in errors:
                flash(message, "error")
        else:
            db.execute(
                "UPDATE games SET name=%s, icon=%s, description=%s, category=%s, win_rule=%s,"
                " default_timer_seconds=%s, min_players=%s, max_players=%s WHERE game_id=%s",
                (v["name"], v["icon"], v["description"], v["category"], v["win_rule"],
                 v["default_timer_seconds"], v["min_players"], v["max_players"], game_id))
            flash("Game updated.", "ok")
            return redirect(url_for("games"))
    return render_template("game_form.html", game=game)


@app.route("/games/<int:game_id>/delete", methods=["POST"])
@role_required("admin")
def delete_game(game_id):
    if db.scalar("SELECT COUNT(*) FROM sessions WHERE game_id = %s", (game_id,)):
        flash("This game already has sessions, so it cannot be deleted.", "error")
    else:
        db.execute("DELETE FROM games WHERE game_id = %s", (game_id,))
        flash("Game deleted.", "ok")
    return redirect(url_for("games"))


# ====================================================================
# Sessions
# ====================================================================
@app.route("/sessions/new", methods=["GET", "POST"])
@role_required("host", "admin")
def new_session():
    all_games = db.query("SELECT * FROM games ORDER BY name")
    names = db.query("SELECT DISTINCT tournament_name FROM sessions"
                     " WHERE mode = 'tournament' ORDER BY tournament_name")
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        mode = request.form.get("mode", "casual")
        game = db.query("SELECT game_id FROM games WHERE game_id = %s",
                        (request.form.get("game_id", 0),), one=True)
        t_name = request.form.get("tournament_name", "").strip()[:100]
        t_stage = request.form.get("tournament_stage", "").strip()[:40]
        errors = []
        if not title or len(title) > 100:
            errors.append("Session title is required (100 characters max).")
        if game is None:
            errors.append("Pick a game.")
        if mode not in ("casual", "tournament"):
            errors.append("Pick a valid mode.")
        if mode == "tournament" and not t_name:
            errors.append("Enter the tournament name.")
        if errors:
            for message in errors:
                flash(message, "error")
        else:
            sid = db.execute(
                "INSERT INTO sessions (game_id, host_id, title, mode, tournament_name,"
                " tournament_stage, status, created_at) VALUES (%s,%s,%s,%s,%s,%s,'open',%s)",
                (game["game_id"], g.user["user_id"], title, mode,
                 t_name if mode == "tournament" else None,
                 (t_stage or None) if mode == "tournament" else None, db.now()))
            db.execute("INSERT INTO session_players (session_id, user_id, joined_at)"
                       " VALUES (%s,%s,%s)", (sid, g.user["user_id"], db.now()))
            flash("Session created. Add players and start the first round.", "ok")
            return redirect(url_for("view_session", sid=sid))
    return render_template("new_session.html", games=all_games, names=names,
                           preselect=request.args.get("game", ""))


@app.route("/sessions/<int:sid>")
@login_required
def view_session(sid):
    s = load_session(sid)
    players = session_players(sid)
    rounds = db.query("SELECT * FROM rounds WHERE session_id = %s ORDER BY round_no", (sid,))
    grid = {(row["round_id"], row["user_id"]): row["points"] for row in db.query(
        """SELECT sc.round_id, sc.user_id, sc.points FROM scores sc
           JOIN rounds r ON r.round_id = sc.round_id WHERE r.session_id = %s""", (sid,))}
    board = build_board(s, players)
    manager = can_manage(s)
    member = any(p["user_id"] == g.user["user_id"] for p in players)
    full = len(players) >= s["max_players"]
    return render_template(
        "session.html", s=s, players=players, rounds=rounds, grid=grid, board=board,
        winners=round_winners(rounds, grid, s["win_rule"]),
        timers={r["round_id"]: timer_info(r) for r in rounds},
        max_points=max([row["total_points"] for row in board] + [0]),
        manager=manager, editable=manager and s["status"] != "finished",
        member=member, full=full,
        results=db.query("SELECT 1 FROM results WHERE session_id = %s", (sid,)))


@app.route("/sessions/<int:sid>/edit", methods=["POST"])
@login_required
def edit_session(sid):
    s = load_session(sid)
    require_manager(s)
    title = request.form.get("title", "").strip()
    stage = request.form.get("tournament_stage", "").strip()[:40] or None
    if not title or len(title) > 100:
        flash("Session title is required (100 characters max).", "error")
    else:
        db.execute("UPDATE sessions SET title = %s, tournament_stage = %s WHERE session_id = %s",
                   (title, stage if s["mode"] == "tournament" else None, sid))
        flash("Session details saved.", "ok")
    return redirect(url_for("view_session", sid=sid))


@app.route("/sessions/<int:sid>/delete", methods=["POST"])
@login_required
def delete_session(sid):
    s = load_session(sid)
    require_manager(s)
    db.execute("DELETE FROM sessions WHERE session_id = %s", (sid,))  # cascades
    flash("Session deleted.", "ok")
    return redirect(url_for("sessions_page"))


def add_player_to_session(s, user_id):
    """Add a player if there is room. Returns an error message or None."""
    if s["status"] == "finished":
        return "This session is finished."
    if db.query("SELECT 1 FROM session_players WHERE session_id = %s AND user_id = %s",
                (s["session_id"], user_id), one=True):
        return "That player is already in the session."
    count = db.scalar("SELECT COUNT(*) FROM session_players WHERE session_id = %s",
                      (s["session_id"],))
    if count >= s["max_players"]:
        return f"{s['game_name']} allows at most {s['max_players']} players."
    db.execute("INSERT INTO session_players (session_id, user_id, joined_at) VALUES (%s,%s,%s)",
               (s["session_id"], user_id, db.now()))
    return None


@app.route("/sessions/<int:sid>/join", methods=["POST"])
@login_required
def join_session(sid):
    error = add_player_to_session(load_session(sid), g.user["user_id"])
    flash(error or "You joined the session.", "error" if error else "ok")
    return redirect(url_for("view_session", sid=sid))


@app.route("/sessions/<int:sid>/players/add", methods=["POST"])
@login_required
def add_player(sid):
    s = load_session(sid)
    require_manager(s)
    user = db.query("SELECT user_id FROM users WHERE LOWER(username) = LOWER(%s)",
                    (request.form.get("username", "").strip(),), one=True)
    if user is None:
        flash("No account with that username. Ask the player to register first.", "error")
    else:
        error = add_player_to_session(s, user["user_id"])
        flash(error or "Player added.", "error" if error else "ok")
    return redirect(url_for("view_session", sid=sid))


@app.route("/sessions/<int:sid>/players/<int:uid>/remove", methods=["POST"])
@login_required
def remove_player(sid, uid):
    s = load_session(sid)
    if not (can_manage(s) or g.user["user_id"] == uid):
        abort(403)
    if s["status"] == "finished":
        flash("This session is finished.", "error")
    else:
        db.execute("DELETE FROM scores WHERE user_id = %s AND round_id IN"
                   " (SELECT round_id FROM rounds WHERE session_id = %s)", (uid, sid))
        db.execute("DELETE FROM session_players WHERE session_id = %s AND user_id = %s", (sid, uid))
        flash("Player removed (their scores in this session were removed too). "
              "The host keeps control of the session even when not playing.", "ok")
    return redirect(url_for("view_session", sid=sid))


# ---- rounds and scores ------------------------------------------------
@app.route("/sessions/<int:sid>/rounds/add", methods=["POST"])
@login_required
def add_round(sid):
    s = load_session(sid)
    require_manager(s)
    if s["status"] == "finished":
        flash("This session is finished. Reopen it to add rounds.", "error")
        return redirect(url_for("view_session", sid=sid))
    try:
        limit = parse_int(request.form.get("time_limit") or s["default_timer_seconds"], 10, 86400)
    except ValueError as exc:
        flash(f"Timer: {exc}", "error")
        return redirect(url_for("view_session", sid=sid))
    next_no = db.scalar("SELECT COALESCE(MAX(round_no), 0) + 1 FROM rounds WHERE session_id = %s", (sid,))
    db.execute("INSERT INTO rounds (session_id, round_no, time_limit_seconds) VALUES (%s,%s,%s)",
               (sid, next_no, limit))
    db.execute("UPDATE sessions SET status = 'ongoing' WHERE session_id = %s", (sid,))
    flash(f"Round {next_no} added.", "ok")
    return redirect(url_for("view_session", sid=sid))


@app.route("/sessions/<int:sid>/rounds/delete-last", methods=["POST"])
@login_required
def delete_last_round(sid):
    s = load_session(sid)
    require_manager(s)
    last = db.query("SELECT round_id, round_no FROM rounds WHERE session_id = %s"
                    " ORDER BY round_no DESC LIMIT 1", (sid,), one=True)
    if s["status"] == "finished" or last is None:
        flash("There is no round to delete.", "error")
    else:
        db.execute("DELETE FROM rounds WHERE round_id = %s", (last["round_id"],))
        flash(f"Round {last['round_no']} deleted.", "ok")
    return redirect(url_for("view_session", sid=sid))


def round_and_session(rid):
    r = db.query("SELECT * FROM rounds WHERE round_id = %s", (rid,), one=True)
    if r is None:
        abort(404)
    return r, load_session(r["session_id"])


@app.route("/rounds/<int:rid>/scores", methods=["POST"])
@login_required
def save_scores(rid):
    r, s = round_and_session(rid)
    require_manager(s)
    if s["status"] == "finished":
        flash("This session is finished. Reopen it to change scores.", "error")
        return redirect(url_for("view_session", sid=s["session_id"]))
    parsed, errors = {}, []
    for p in session_players(s["session_id"]):
        raw = request.form.get(f"points_{p['user_id']}", "").strip()
        if raw == "":
            parsed[p["user_id"]] = None  # blank = no score
            continue
        try:
            parsed[p["user_id"]] = parse_int(raw, -100000, 100000)
        except ValueError:
            errors.append(f"{p['username']}: score must be a whole number from -100000 to 100000.")
    if errors:
        for message in errors:
            flash(message, "error")
    else:
        for uid, points in parsed.items():
            exists = db.query("SELECT score_id FROM scores WHERE round_id = %s AND user_id = %s",
                              (rid, uid), one=True)
            if points is None:
                if exists:
                    db.execute("DELETE FROM scores WHERE score_id = %s", (exists["score_id"],))
            elif exists:
                db.execute("UPDATE scores SET points = %s WHERE score_id = %s",
                           (points, exists["score_id"]))
            else:
                db.execute("INSERT INTO scores (round_id, user_id, points) VALUES (%s,%s,%s)",
                           (rid, uid, points))
        flash(f"Scores saved for round {r['round_no']}.", "ok")
    return redirect(url_for("view_session", sid=s["session_id"]))


# ---- timer ------------------------------------------------------------
@app.route("/rounds/<int:rid>/timer/<action>", methods=["POST"])
@login_required
def round_timer(rid, action):
    r, s = round_and_session(rid)
    require_manager(s)
    if s["status"] == "finished":
        flash("This session is finished.", "error")
    elif action == "start" and not r["started_at"]:
        db.execute("UPDATE rounds SET started_at = %s, ended_at = NULL WHERE round_id = %s",
                   (db.now(), rid))
    elif action == "stop" and r["started_at"] and not r["ended_at"]:
        db.execute("UPDATE rounds SET ended_at = %s WHERE round_id = %s", (db.now(), rid))
    elif action == "reset":
        db.execute("UPDATE rounds SET started_at = NULL, ended_at = NULL WHERE round_id = %s", (rid,))
    elif action not in ("start", "stop", "reset"):
        abort(404)
    return redirect(url_for("view_session", sid=s["session_id"]))


@app.route("/rounds/<int:rid>/timer/status")
@login_required
def timer_status(rid):
    r, _s = round_and_session(rid)
    return jsonify(timer_info(r))


# ---- finish, reopen, export -------------------------------------------
def finalize_session(s):
    """Write the final results (table D7) from the SUM() totals and mark the session finished.
    Returns the final leaderboard, or None when no score was entered yet."""
    sid = s["session_id"]
    board = build_board(s, session_players(sid))
    if not any(row["rounds_played"] for row in board):
        return None
    db.execute("DELETE FROM results WHERE session_id = %s", (sid,))
    for row in board:
        db.execute(
            "INSERT INTO results (session_id, user_id, total_points, rounds_won, rank_no, is_winner)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (sid, row["user_id"], row["total_points"], row["wins"], row["rank"] or len(board),
             1 if row["rank"] == 1 else 0))
    db.execute("UPDATE sessions SET status = 'finished', finished_at = %s WHERE session_id = %s",
               (db.now(), sid))
    return board


@app.route("/sessions/<int:sid>/finish", methods=["POST"])
@login_required
def finish_session(sid):
    s = load_session(sid)
    require_manager(s)
    if s["status"] == "finished":
        return redirect(url_for("view_session", sid=sid))
    if finalize_session(s) is None:
        flash("Enter at least one score before finishing the session.", "error")
    else:
        flash("Session finished. Final results saved.", "ok")
    return redirect(url_for("view_session", sid=sid))


@app.route("/sessions/<int:sid>/reopen", methods=["POST"])
@login_required
def reopen_session(sid):
    s = load_session(sid)
    require_manager(s)
    if s["status"] == "finished":
        db.execute("DELETE FROM results WHERE session_id = %s", (sid,))
        db.execute("UPDATE sessions SET status = 'ongoing', finished_at = NULL WHERE session_id = %s", (sid,))
        flash("Session reopened. Finish it again to refresh the final results.", "ok")
    return redirect(url_for("view_session", sid=sid))


@app.route("/sessions/<int:sid>/export.csv")
@login_required
def export_session(sid):
    s = load_session(sid)
    players = session_players(sid)
    rounds = db.query("SELECT * FROM rounds WHERE session_id = %s ORDER BY round_no", (sid,))
    grid = {(x["round_id"], x["user_id"]): x["points"] for x in db.query(
        """SELECT sc.round_id, sc.user_id, sc.points FROM scores sc
           JOIN rounds r ON r.round_id = sc.round_id WHERE r.session_id = %s""", (sid,))}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Rank", "Player"] + [f"Round {r['round_no']}" for r in rounds]
                    + ["Total points", "Rounds won"])
    for row in build_board(s, players):
        writer.writerow([row["rank"] or "", row["username"]]
                        + [grid.get((r["round_id"], row["user_id"]), "") for r in rounds]
                        + [row["total_points"], row["wins"]])
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", s["title"]) or "session"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}.csv"})


# ====================================================================
# Leaderboard, players
# ====================================================================
@app.route("/leaderboard")
@login_required
def leaderboard():
    game_id = request.args.get("game", "").strip()
    where, args = "", ()
    chosen = None
    if game_id.isdigit():
        chosen = db.query("SELECT * FROM games WHERE game_id = %s", (int(game_id),), one=True)
        if chosen:
            where, args = "WHERE s.game_id = %s", (chosen["game_id"],)
    results = session_results_by_user(where, args)
    rows = [dict(r, session_wins=results.get(r["user_id"], {}).get("session_wins", 0),
                 sessions_played=results.get(r["user_id"], {}).get("sessions_played", 0))
            for r in stats(where, args).values()]
    rows = rank_rows(rows, "wins")
    return render_template("leaderboard.html", rows=rows, chosen=chosen,
                           games=db.query("SELECT game_id, name, icon FROM games ORDER BY name"),
                           top_wins=max([r["wins"] for r in rows] + [0]))


@app.route("/players")
@login_required
def players():
    results = session_results_by_user()
    totals = stats()
    rows = []
    for u in db.query("SELECT user_id, username, role, created_at FROM users ORDER BY username"):
        t = totals.get(u["user_id"], {})
        rows.append(dict(u, rounds_played=t.get("rounds_played", 0), wins=t.get("wins", 0),
                         sessions_played=results.get(u["user_id"], {}).get("sessions_played", 0),
                         session_wins=results.get(u["user_id"], {}).get("session_wins", 0)))
    return render_template("players.html", rows=rows)


@app.route("/players/<int:uid>")
@login_required
def player(uid):
    u = db.query("SELECT user_id, username, role, created_at FROM users WHERE user_id = %s",
                 (uid,), one=True)
    if u is None:
        abort(404)
    per_game = db.query(
        """SELECT g.game_id, g.name, g.icon,
                  COALESCE(SUM(sc.points), 0) AS total_points,
                  COUNT(sc.score_id) AS rounds_played,
                  COUNT(DISTINCT s.session_id) AS sessions,
                  COALESCE(SUM(CASE WHEN sc.points = CASE WHEN g.win_rule = 'lowest'
                                                           THEN b.lo ELSE b.hi END
                                    THEN 1 ELSE 0 END), 0) AS wins
           FROM scores sc
           JOIN rounds r ON r.round_id = sc.round_id
           JOIN sessions s ON s.session_id = r.session_id
           JOIN games g ON g.game_id = s.game_id
           JOIN (SELECT round_id, MAX(points) AS hi, MIN(points) AS lo
                 FROM scores GROUP BY round_id) b ON b.round_id = sc.round_id
           WHERE sc.user_id = %s
           GROUP BY g.game_id, g.name, g.icon ORDER BY wins DESC, g.name""", (uid,))
    for row in per_game:
        row["win_rate"] = round(100 * row["wins"] / row["rounds_played"]) if row["rounds_played"] else 0
    recent = db.query(
        """SELECT s.session_id, s.title, g.name AS game_name, g.icon, rs.total_points,
                  rs.rank_no, rs.is_winner
           FROM results rs JOIN sessions s ON s.session_id = rs.session_id
           JOIN games g ON g.game_id = s.game_id
           WHERE rs.user_id = %s ORDER BY s.session_id DESC LIMIT 10""", (uid,))
    rounds_played = sum(r["rounds_played"] for r in per_game)
    wins = sum(r["wins"] for r in per_game)
    session_stats = session_results_by_user("WHERE rs.user_id = %s", (uid,)).get(uid, {})
    summary = dict(rounds_played=rounds_played, wins=wins,
                   win_rate=round(100 * wins / rounds_played) if rounds_played else 0,
                   points=sum(r["total_points"] for r in per_game),
                   sessions_played=session_stats.get("sessions_played", 0),
                   session_wins=session_stats.get("session_wins", 0))
    return render_template("player.html", u=u, per_game=per_game, recent=recent, summary=summary)


# ====================================================================
# Tournaments
# ====================================================================
TOURNAMENT_WHERE = "WHERE s.mode = 'tournament' AND s.game_id = %s AND s.tournament_name = %s"


@app.route("/tournaments")
@login_required
def tournaments():
    rows = db.query(
        """SELECT g.game_id, g.name AS game_name, g.icon, s.tournament_name,
                  COUNT(s.session_id) AS matches,
                  SUM(CASE WHEN s.status = 'finished' THEN 1 ELSE 0 END) AS finished
           FROM sessions s JOIN games g ON g.game_id = s.game_id
           WHERE s.mode = 'tournament'
           GROUP BY g.game_id, g.name, g.icon, s.tournament_name
           ORDER BY MAX(s.session_id) DESC""")
    for row in rows:
        row["players"] = db.scalar(
            """SELECT COUNT(DISTINCT sp.user_id) FROM session_players sp
               JOIN sessions s ON s.session_id = sp.session_id
               WHERE s.mode = 'tournament' AND s.game_id = %s AND s.tournament_name = %s""",
            (row["game_id"], row["tournament_name"]))
    return render_template("tournaments.html", rows=rows)


@app.route("/tournaments/<int:game_id>/<path:name>")
@login_required
def tournament(game_id, name):
    game = db.query("SELECT * FROM games WHERE game_id = %s", (game_id,), one=True)
    matches = db.query(
        """SELECT s.session_id, s.title, s.tournament_stage, s.status
           FROM sessions s WHERE s.mode = 'tournament' AND s.game_id = %s
           AND s.tournament_name = %s ORDER BY s.session_id""", (game_id, name))
    if game is None or not matches:
        abort(404)
    participants = db.query(
        """SELECT DISTINCT u.user_id, u.username FROM session_players sp
           JOIN sessions s ON s.session_id = sp.session_id
           JOIN users u ON u.user_id = sp.user_id
           WHERE s.mode = 'tournament' AND s.game_id = %s AND s.tournament_name = %s""",
        (game_id, name))
    found = stats(TOURNAMENT_WHERE, (game_id, name))
    standings = []
    for p in participants:
        row = found.get(p["user_id"], {})
        standings.append(dict(user_id=p["user_id"], username=p["username"],
                              total_points=row.get("total_points", 0),
                              rounds_played=row.get("rounds_played", 0), wins=row.get("wins", 0)))
    standings = rank_rows(standings, game["win_rule"])
    winners_by_match = {}
    for w in db.query(
            """SELECT rs.session_id, u.username FROM results rs
               JOIN users u ON u.user_id = rs.user_id
               JOIN sessions s ON s.session_id = rs.session_id
               WHERE rs.is_winner = 1 AND s.mode = 'tournament' AND s.game_id = %s
               AND s.tournament_name = %s""", (game_id, name)):
        winners_by_match.setdefault(w["session_id"], []).append(w["username"])
    stages = {}
    for m in matches:
        m["winners"] = winners_by_match.get(m["session_id"], [])
        stages.setdefault(m["tournament_stage"] or "Matches", []).append(m)
    all_done = all(m["status"] == "finished" for m in matches)
    champions = [r for r in standings if r["rank"] == 1] if all_done else []
    return render_template("tournament.html", game=game, name=name, standings=standings,
                           stages=stages, champions=champions, all_done=all_done,
                           max_points=max([r["total_points"] for r in standings] + [0]))


# ====================================================================
# Admin
# ====================================================================
@app.route("/admin/users", methods=["GET"])
@role_required("admin")
def admin_users():
    rows = db.query("SELECT user_id, username, email, role, created_at FROM users ORDER BY user_id")
    return render_template("admin_users.html", rows=rows, roles=ROLES)


@app.route("/admin/users/<int:uid>/role", methods=["POST"])
@role_required("admin")
def set_role(uid):
    role = request.form.get("role", "")
    if role not in ROLES:
        abort(400)
    if uid == g.user["user_id"]:
        flash("You cannot change your own role.", "error")
    elif db.query("SELECT 1 FROM users WHERE user_id = %s", (uid,), one=True) is None:
        abort(404)
    else:
        db.execute("UPDATE users SET role = %s WHERE user_id = %s", (role, uid))
        flash("Role updated.", "ok")
    return redirect(url_for("admin_users"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") == "1")
