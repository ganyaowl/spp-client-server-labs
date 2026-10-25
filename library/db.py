import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
        g.db.create_function('casefold', 1, str.casefold, deterministic=True)
    return g.db


def close_db(error=None):
    connection = g.pop('db', None)
    if connection is not None:
        connection.close()


def init_db():
    connection = get_db()
    if connection.execute('PRAGMA user_version').fetchone()[0] == 0:
        schema = Path(current_app.root_path) / 'database' / 'schema.sql'
        try:
            connection.executescript(schema.read_text(encoding='utf-8'))
            connection.executemany(
                'INSERT INTO users (username, password_hash, name, role) VALUES (?, ?, ?, ?)',
                [('admin', generate_password_hash('admin123'), 'Библиотекарь', 'admin'),
                 ('reader', generate_password_hash('reader123'), 'Иван Петров', 'reader')],
            )
            connection.execute('PRAGMA user_version = 1')
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise


@contextmanager
def transaction():
    connection = get_db()
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        yield connection


def get_user(user_id):
    return get_db().execute('SELECT * FROM users WHERE id = ?', (str(user_id),)).fetchone()


def find_user(username):
    return get_db().execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()


BOOKS = '''SELECT b.*, a.name AS author, b.total - (
    SELECT COUNT(*) FROM loans l JOIN requests r ON r.id = l.request_id
    WHERE r.book_id = b.id AND l.returned_at IS NULL
) AS available FROM books b JOIN authors a ON a.id = b.author_id'''


def get_books(title='', author_id='', only_available=False):
    return get_db().execute(f'''SELECT * FROM ({BOOKS}) WHERE
        instr(casefold(title), casefold(?)) > 0 AND (? = '' OR author_id = ?)
        AND (? = 0 OR available > 0) ORDER BY title''',
        (title.strip(), author_id, author_id, only_available)).fetchall()


def get_authors():
    return get_db().execute('SELECT * FROM authors ORDER BY name').fetchall()


def get_book(book_id):
    book = get_db().execute(BOOKS + ' WHERE b.id = ?', (str(book_id),)).fetchone()
    if book is None:
        raise LookupError('Книга не найдена.')
    return book


def has_request(reader_id, book_id):
    return get_db().execute('''SELECT 1 FROM requests r LEFT JOIN loans l ON l.request_id = r.id
        WHERE r.reader_id = ? AND r.book_id = ?
        AND (r.status = 'pending' OR (l.id IS NOT NULL AND l.returned_at IS NULL))''',
        (reader_id, str(book_id))).fetchone() is not None


def submit_request(reader_id, book_id):
    with transaction() as connection:
        book = get_book(book_id)
        if has_request(reader_id, book_id):
            raise ValueError('У вас уже есть заявка или невозвращённый экземпляр этой книги.')
        if book['available'] <= 0:
            raise ValueError('Свободных экземпляров больше нет.')
        connection.execute('INSERT INTO requests (reader_id, book_id) VALUES (?, ?)',
                           (reader_id, str(book_id)))


def get_requests(reader_id=None):
    return get_db().execute('''SELECT r.*, u.name AS reader, b.title, l.id AS loan_id,
        l.issued_at, l.returned_at FROM requests r JOIN users u ON u.id = r.reader_id
        JOIN books b ON b.id = r.book_id LEFT JOIN loans l ON l.request_id = r.id
        WHERE (? IS NULL OR r.reader_id = ?) ORDER BY r.id DESC''',
        (reader_id, reader_id)).fetchall()


def process_request(request_id, issue):
    with transaction() as connection:
        row = connection.execute('SELECT * FROM requests WHERE id = ?', (str(request_id),)).fetchone()
        if row is None:
            raise LookupError('Заявка не найдена.')
        if row['status'] != 'pending':
            raise ValueError('Эта заявка уже обработана.')
        if issue:
            if get_book(row['book_id'])['available'] <= 0:
                raise ValueError('Выдать книгу нельзя: свободных экземпляров больше нет.')
            connection.execute('INSERT INTO loans (request_id) VALUES (?)', (str(request_id),))
        connection.execute('UPDATE requests SET status = ? WHERE id = ?',
                           ('approved' if issue else 'rejected', str(request_id)))


def return_book(loan_id):
    with transaction() as connection:
        row = connection.execute('SELECT * FROM loans WHERE id = ?', (str(loan_id),)).fetchone()
        if row is None:
            raise LookupError('Выдача не найдена.')
        if row['returned_at'] is not None:
            raise ValueError('Эта книга уже возвращена.')
        connection.execute('UPDATE loans SET returned_at = CURRENT_TIMESTAMP WHERE id = ?', (str(loan_id),))
