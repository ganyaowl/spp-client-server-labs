BEGIN;
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'cashier'))
);
CREATE TABLE teams (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE matches (
    id INTEGER PRIMARY KEY,
    home_id INTEGER NOT NULL REFERENCES teams(id),
    away_id INTEGER NOT NULL REFERENCES teams(id),
    starts_at TEXT NOT NULL UNIQUE,
    price INTEGER NOT NULL CHECK (price > 0),
    CHECK (home_id <> away_id)
);
CREATE TABLE seats (
    id INTEGER PRIMARY KEY,
    sector TEXT NOT NULL,
    row_number INTEGER NOT NULL CHECK (row_number > 0),
    seat_number INTEGER NOT NULL CHECK (seat_number > 0),
    UNIQUE (sector, row_number, seat_number)
);
CREATE TABLE tickets (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    seat_id INTEGER NOT NULL REFERENCES seats(id),
    cashier_id INTEGER NOT NULL REFERENCES users(id),
    price INTEGER NOT NULL CHECK (price > 0),
    sold_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    returned_at TEXT
);
CREATE UNIQUE INDEX one_active_ticket ON tickets(match_id, seat_id) WHERE returned_at IS NULL;
INSERT INTO teams VALUES (1, 'Пахтакор'), (2, 'Бунёдкор'), (3, 'Насаф');
INSERT INTO matches VALUES
    (1, 1, 2, datetime('now', '+7 days', 'start of day', '+18 hours'), 50000),
    (2, 3, 1, datetime('now', '+14 days', 'start of day', '+18 hours'), 40000);
INSERT INTO seats VALUES (1, 'А', 1, 1), (2, 'А', 1, 2), (3, 'А', 1, 3), (4, 'Б', 1, 1), (5, 'Б', 1, 2);
