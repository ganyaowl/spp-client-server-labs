BEGIN;
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'reader'))
);
CREATE TABLE authors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE books (
    id INTEGER PRIMARY KEY,
    author_id INTEGER NOT NULL REFERENCES authors(id),
    title TEXT NOT NULL,
    total INTEGER NOT NULL CHECK (total > 0)
);
CREATE TABLE requests (
    id INTEGER PRIMARY KEY,
    reader_id INTEGER NOT NULL REFERENCES users(id),
    book_id INTEGER NOT NULL REFERENCES books(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX one_pending_request ON requests(reader_id, book_id) WHERE status = 'pending';
CREATE TABLE loans (
    id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL UNIQUE REFERENCES requests(id),
    issued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    returned_at TEXT
);
INSERT INTO authors VALUES (1, 'Александр Пушкин'), (2, 'Лев Толстой'), (3, 'Антон Чехов');
INSERT INTO books VALUES (1, 1, 'Капитанская дочка', 3), (2, 2, 'Война и мир', 2), (3, 3, 'Рассказы', 4);
