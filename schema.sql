-- =====================================================================
-- Online Card/Board Game Score Tracker Website  (version 2)
-- MySQL schema - exactly 7 tables:
--   D1 users, D2 games, D3 sessions, D4 session_players, D5 rounds,
--   D6 scores, D7 results
-- Tournament -> columns in `sessions`;  Timer -> columns in `games` + `rounds`
-- Run it with:  python init_db.py        (also adds the game catalog)
-- or import this file in phpMyAdmin / the mysql client.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS score_tracker CHARACTER SET utf8mb4;
USE score_tracker;

-- D1 ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id       INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(30)  NOT NULL UNIQUE,
    email         VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          ENUM('player','host','admin') NOT NULL DEFAULT 'player',
    created_at    DATETIME NOT NULL
);

-- D2 ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS games (
    game_id               INT AUTO_INCREMENT PRIMARY KEY,
    name                  VARCHAR(60)  NOT NULL UNIQUE,
    icon                  VARCHAR(8)   NOT NULL DEFAULT '🎲',
    description           VARCHAR(255) NOT NULL DEFAULT '',
    category              ENUM('card','board','sport') NOT NULL DEFAULT 'board',
    win_rule              ENUM('highest','lowest') NOT NULL DEFAULT 'highest',
    default_timer_seconds INT NOT NULL DEFAULT 300,      -- timer for every game
    min_players           INT NOT NULL DEFAULT 2,
    max_players           INT NOT NULL DEFAULT 6
);

-- D3 ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    session_id       INT AUTO_INCREMENT PRIMARY KEY,
    game_id          INT NOT NULL,
    host_id          INT NOT NULL,
    title            VARCHAR(100) NOT NULL,
    mode             ENUM('casual','tournament') NOT NULL DEFAULT 'casual',
    tournament_name  VARCHAR(100) NULL,                   -- tournament per game
    tournament_stage VARCHAR(40)  NULL,                   -- Group Stage, Semi-final, Final
    status           ENUM('open','ongoing','finished') NOT NULL DEFAULT 'open',
    created_at       DATETIME NOT NULL,
    finished_at      DATETIME NULL,
    FOREIGN KEY (game_id) REFERENCES games(game_id),
    FOREIGN KEY (host_id) REFERENCES users(user_id)
);

-- D4 ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS session_players (
    sp_id      INT AUTO_INCREMENT PRIMARY KEY,
    session_id INT NOT NULL,
    user_id    INT NOT NULL,
    joined_at  DATETIME NOT NULL,
    UNIQUE KEY uq_session_user (session_id, user_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
);

-- D5 ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rounds (
    round_id           INT AUTO_INCREMENT PRIMARY KEY,
    session_id         INT NOT NULL,
    round_no           INT NOT NULL,
    time_limit_seconds INT NOT NULL DEFAULT 300,           -- timer
    started_at         DATETIME NULL,
    ended_at           DATETIME NULL,
    UNIQUE KEY uq_session_round (session_id, round_no),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

-- D6 ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scores (
    score_id INT AUTO_INCREMENT PRIMARY KEY,
    round_id INT NOT NULL,
    user_id  INT NOT NULL,
    points   INT NOT NULL DEFAULT 0,
    UNIQUE KEY uq_round_user (round_id, user_id),
    FOREIGN KEY (round_id) REFERENCES rounds(round_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)  REFERENCES users(user_id)
);

-- D7  (filled when a session is finished, using SUM()) -----------------
CREATE TABLE IF NOT EXISTS results (
    result_id    INT AUTO_INCREMENT PRIMARY KEY,
    session_id   INT NOT NULL,
    user_id      INT NOT NULL,
    total_points INT NOT NULL DEFAULT 0,
    rounds_won   INT NOT NULL DEFAULT 0,
    rank_no      INT NOT NULL,
    is_winner    TINYINT(1) NOT NULL DEFAULT 0,
    UNIQUE KEY uq_result (session_id, user_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
);
