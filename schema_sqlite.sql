-- SQLite version of the same 7 tables (used for DB_BACKEND=sqlite: quick demo and tests).
-- The MySQL schema in schema.sql is the official one.

CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'player' CHECK (role IN ('player','host','admin')),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    game_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL UNIQUE,
    icon                  TEXT NOT NULL DEFAULT '🎲',
    description           TEXT NOT NULL DEFAULT '',
    category              TEXT NOT NULL DEFAULT 'board' CHECK (category IN ('card','board','sport')),
    win_rule              TEXT NOT NULL DEFAULT 'highest' CHECK (win_rule IN ('highest','lowest')),
    default_timer_seconds INTEGER NOT NULL DEFAULT 300,
    min_players           INTEGER NOT NULL DEFAULT 2,
    max_players           INTEGER NOT NULL DEFAULT 6
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id          INTEGER NOT NULL REFERENCES games(game_id),
    host_id          INTEGER NOT NULL REFERENCES users(user_id),
    title            TEXT NOT NULL,
    mode             TEXT NOT NULL DEFAULT 'casual' CHECK (mode IN ('casual','tournament')),
    tournament_name  TEXT NULL,
    tournament_stage TEXT NULL,
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','ongoing','finished')),
    created_at       TEXT NOT NULL,
    finished_at      TEXT NULL
);

CREATE TABLE IF NOT EXISTS session_players (
    sp_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    joined_at  TEXT NOT NULL,
    UNIQUE (session_id, user_id)
);

CREATE TABLE IF NOT EXISTS rounds (
    round_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    round_no           INTEGER NOT NULL,
    time_limit_seconds INTEGER NOT NULL DEFAULT 300,
    started_at         TEXT NULL,
    ended_at           TEXT NULL,
    UNIQUE (session_id, round_no)
);

CREATE TABLE IF NOT EXISTS scores (
    score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(user_id),
    points   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (round_id, user_id)
);

CREATE TABLE IF NOT EXISTS results (
    result_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(user_id),
    total_points INTEGER NOT NULL DEFAULT 0,
    rounds_won   INTEGER NOT NULL DEFAULT 0,
    rank_no      INTEGER NOT NULL,
    is_winner    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (session_id, user_id)
);
