import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from db import get_db, init_db


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test',
                               'DATABASE': str(Path(self.temp.name) / 'test.sqlite')})
        self.client = self.app.test_client()

    def post(self, path, **data):
        self.client.get('/login')
        with self.client.session_transaction() as session:
            data['csrf_token'] = session['csrf_token']
        return self.client.post(path, data=data, follow_redirects=True)

    def test_login_logout_and_invalid_forms(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertEqual(self.client.post('/login').status_code, 400)
        self.assertEqual(self.client.post('/login', data={'csrf_token': 'неверный'}).status_code, 400)
        for username in ('reader', "reader' OR 1=1 --"):
            response = self.post('/login', username=username, password='wrong')
            self.assertIn('Неверный логин или пароль.', response.get_data(as_text=True))
            with self.client.session_transaction() as session:
                self.assertNotIn('user_id', session)
        response = self.post('/login', username='reader', password='reader123')
        self.assertIn('Капитанская дочка', response.get_data(as_text=True))
        self.assertEqual(self.client.post('/logout').status_code, 400)
        self.post('/logout')
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_roles(self):
        self.post('/login', username='reader', password='reader123')
        self.assertEqual(self.client.get('/requests').status_code, 403)
        self.post('/login', username='admin', password='admin123')
        self.assertEqual(self.client.get('/requests').status_code, 200)

    def test_initialization_preserves_data_and_hashes_passwords(self):
        with self.app.app_context():
            connection = get_db()
            connection.execute('UPDATE books SET total = 9 WHERE id = 1')
            connection.commit()
            init_db()
            self.assertEqual(connection.execute('SELECT total FROM books WHERE id = 1').fetchone()[0], 9)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM users').fetchone()[0], 2)
            self.assertNotEqual(connection.execute('SELECT password_hash FROM users WHERE id = 1').fetchone()[0], 'admin123')
            self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)

    def test_database_constraints(self):
        with self.app.app_context():
            connection = get_db()
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            self.assertEqual(len(tables), 5)
            connection.execute('INSERT INTO requests (reader_id, book_id) VALUES (2, 1)')
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('INSERT INTO requests (reader_id, book_id) VALUES (2, 1)')
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('INSERT INTO requests (reader_id, book_id) VALUES (2, 999)')

    def test_database_error_is_readable(self):
        with patch('app.get_db', side_effect=sqlite3.OperationalError('private SQL details')):
            response = self.client.get('/login')
        self.assertEqual(response.status_code, 503)
        self.assertIn('Не удалось обработать данные.', response.get_data(as_text=True))
        self.assertNotIn('private SQL details', response.get_data(as_text=True))
