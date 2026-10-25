import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from unittest.mock import patch

from app import create_app
import db
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
        self.assertEqual(self.client.get('/requests').status_code, 200)
        for path in ('/requests/1/issue', '/requests/1/reject', '/loans/1/return'):
            self.assertEqual(self.post(path).status_code, 403)
        self.post('/login', username='admin', password='admin123')
        self.assertEqual(self.client.get('/requests').status_code, 200)
        self.assertEqual(self.post('/books/1/request').status_code, 403)

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
        with patch('db.get_db', side_effect=sqlite3.OperationalError('private SQL details')):
            response = self.client.get('/login')
        self.assertEqual(response.status_code, 503)
        self.assertIn('Не удалось обработать данные.', response.get_data(as_text=True))
        self.assertNotIn('private SQL details', response.get_data(as_text=True))

    def login(self, username='reader'):
        return self.post('/login', username=username, password=username + '123')

    def test_search_and_full_cycle(self):
        self.login()
        response = self.client.get('/?title=КАПИТАНСКАЯ&author_id=1&available=1')
        self.assertIn('Капитанская дочка', response.get_data(as_text=True))
        self.assertNotIn('Война и мир', response.get_data(as_text=True))
        self.assertNotIn('Капитанская дочка', self.client.get('/?author_id=2').get_data(as_text=True))
        self.assertIn('книги не найдены', self.client.get('/?title=НЕСУЩЕСТВУЮЩАЯ').get_data(as_text=True))
        self.assertEqual(self.post('/books/1/request', reader_id='1').status_code, 200)
        with self.app.app_context():
            self.assertEqual(db.get_requests(2)[0]['reader_id'], 2)
            self.assertEqual(db.get_book(1)['available'], 3)
        self.assertEqual(self.post('/books/1/request').status_code, 400)
        self.login('admin')
        self.assertEqual(self.post('/requests/1/issue').status_code, 200)
        self.assertEqual(self.post('/requests/1/issue').status_code, 400)
        self.assertEqual(self.post('/requests/1/reject').status_code, 400)
        with self.app.app_context():
            self.assertEqual(db.get_book(1)['available'], 2)
        self.login()
        self.assertEqual(self.post('/books/1/request').status_code, 400)
        self.assertIn('Выдана', self.client.get('/requests').get_data(as_text=True))
        self.login('admin')
        self.assertEqual(self.post('/loans/1/return').status_code, 200)
        self.assertEqual(self.post('/loans/1/return').status_code, 400)
        self.login()
        self.assertIn('Возвращена', self.client.get('/requests').get_data(as_text=True))
        self.assertEqual(self.post('/books/1/request').status_code, 200)

    def test_rejection_privacy_and_unavailable_books(self):
        with self.app.app_context():
            connection = get_db()
            connection.execute("INSERT INTO users SELECT 3, 'other', password_hash, 'Другой читатель', role FROM users WHERE id = 2")
            connection.execute("INSERT INTO requests (reader_id, book_id) VALUES (3, 2)")
            connection.execute('UPDATE books SET total = 1 WHERE id = 1')
            connection.commit()
        self.login()
        self.assertNotIn('Война и мир', self.client.get('/requests').get_data(as_text=True))
        self.post('/books/1/request')
        self.login('admin')
        self.assertIn('Другой читатель', self.client.get('/requests').get_data(as_text=True))
        self.assertEqual(self.post('/requests/1/reject').status_code, 200)
        self.assertEqual(self.post('/requests/1/reject').status_code, 400)
        self.post('/requests/2/issue')
        with self.app.app_context():
            self.assertEqual(db.get_book(1)['available'], 0)
            with self.assertRaises(ValueError):
                db.submit_request(3, 1)
        self.login()
        self.assertNotIn('Капитанская дочка', self.client.get('/?available=1').get_data(as_text=True))

    def test_missing_records_csrf_and_restart(self):
        self.login()
        self.assertEqual(self.client.post('/books/1/request').status_code, 400)
        self.assertEqual(self.post('/books/999/request').status_code, 404)
        self.post('/books/1/request')
        restarted = create_app(self.app.config)
        with restarted.app_context():
            self.assertEqual(len(db.get_requests(2)), 1)
        self.login('admin')
        for path in ('/requests/999/issue', '/requests/999/reject', '/loans/999/return'):
            self.assertEqual(self.post(path).status_code, 404)

    def test_two_clients_compete_for_last_copy(self):
        with self.app.app_context():
            connection = get_db()
            connection.execute('UPDATE books SET total = 1 WHERE id = 1')
            connection.execute("INSERT INTO users SELECT 3, 'other', password_hash, 'Другой читатель', role FROM users WHERE id = 2")
            connection.commit()
            db.submit_request(2, 1)
            db.submit_request(3, 1)
        clients = [self.app.test_client(), self.app.test_client()]
        tokens = []
        for client in clients:
            with client.session_transaction() as session:
                session.update(user_id=1, csrf_token='test-token')
            tokens.append('test-token')
        barrier = Barrier(2)

        def issue(index):
            barrier.wait(timeout=5)
            return clients[index].post(f'/requests/{index + 1}/issue', data={'csrf_token': tokens[index]}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(issue, range(2))), [302, 400])
        with self.app.app_context():
            self.assertEqual(db.get_book(1)['available'], 0)
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM loans').fetchone()[0], 1)
