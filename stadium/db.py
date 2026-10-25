import sqlite3
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
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
                [('admin', generate_password_hash('admin123'), 'Администратор', 'admin'),
                 ('cashier', generate_password_hash('cashier123'), 'Анна Иванова', 'cashier')],
            )
            connection.execute('PRAGMA user_version = 1')
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
