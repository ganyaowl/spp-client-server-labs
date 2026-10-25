import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from pathlib import Path
from unittest.mock import patch

from app import create_app
import db
from db import get_db, init_db


class StadiumTest(unittest.TestCase):
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
        for username in ('cashier', "cashier' OR 1=1 --"):
            response = self.post('/login', username=username, password='wrong')
            self.assertIn('Неверный логин или пароль.', response.get_data(as_text=True))
            with self.client.session_transaction() as session:
                self.assertNotIn('user_id', session)
        response = self.post('/login', username='cashier', password='cashier123')
        self.assertIn('Пахтакор', response.get_data(as_text=True))
        self.assertEqual(self.client.post('/logout').status_code, 400)
        self.post('/logout')
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_roles(self):
        self.post('/login', username='cashier', password='cashier123')
        self.assertEqual(self.client.get('/schedule').status_code, 403)
        self.assertEqual(self.client.get('/tickets').status_code, 200)
        self.post('/login', username='admin', password='admin123')
        self.assertEqual(self.client.get('/schedule').status_code, 200)
        self.assertEqual(self.client.get('/tickets').status_code, 403)

    def test_initialization_preserves_data_and_hashes_passwords(self):
        with self.app.app_context():
            connection = get_db()
            connection.execute('UPDATE matches SET price = 90000 WHERE id = 1')
            connection.commit()
            init_db()
            self.assertEqual(connection.execute('SELECT price FROM matches WHERE id = 1').fetchone()[0], 90000)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM users').fetchone()[0], 2)
            self.assertNotEqual(connection.execute('SELECT password_hash FROM users WHERE id = 1').fetchone()[0], 'admin123')
            self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)

    def test_database_constraints(self):
        with self.app.app_context():
            connection = get_db()
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            self.assertEqual(len(tables), 5)
            sale = 'INSERT INTO tickets (match_id, seat_id, cashier_id, price) VALUES (1, 1, 2, 50000)'
            connection.execute(sale)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(sale)
            connection.execute('UPDATE tickets SET returned_at = CURRENT_TIMESTAMP WHERE id = 1')
            connection.execute(sale)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('INSERT INTO matches (home_id, away_id, starts_at, price) VALUES (1, 999, ?, 100)', ('2030-01-01 18:00:00',))

    def test_database_error_is_readable(self):
        with patch('db.get_db', side_effect=sqlite3.OperationalError('private SQL details')):
            response = self.client.get('/login')
        self.assertEqual(response.status_code, 503)
        self.assertIn('Не удалось обработать данные.', response.get_data(as_text=True))
        self.assertNotIn('private SQL details', response.get_data(as_text=True))

    def login(self, username='cashier'):
        return self.post('/login', username=username, password=username + '123')

    def match_form(self, **changes):
        form = dict(home_id='1', away_id='2', starts_at=(db.now() + timedelta(days=30)).strftime('%Y-%m-%dT%H:%M'), price='70000')
        return form | changes

    def test_schedule_create_edit_delete_and_filters(self):
        self.login('admin')
        form = self.match_form()
        self.assertEqual(self.post('/schedule', **form).status_code, 200)
        self.assertEqual(self.post('/schedule', **form).status_code, 400)
        self.assertEqual(self.client.get('/schedule/3/edit').status_code, 200)
        form['price'] = '80000'
        self.assertEqual(self.post('/schedule/3/edit', **form).status_code, 200)
        with self.app.app_context():
            self.assertEqual(db.get_match(3)['price'], 80000)
        response = self.client.get('/?team_id=2&date=' + form['starts_at'][:10] + '&available=1')
        self.assertIn('80 000', response.get_data(as_text=True))
        self.assertNotIn('40 000', response.get_data(as_text=True))
        self.assertEqual(self.post('/schedule/3/delete').status_code, 200)
        self.assertEqual(self.post('/schedule/3/delete').status_code, 404)

    def test_invalid_forms_keep_values(self):
        self.login('admin')
        cases = [dict(home_id='999'), dict(away_id='1'), dict(price='0'), dict(price='1.5'),
                 dict(price='9' * 100), dict(starts_at='wrong'),
                 dict(starts_at='2000-01-01T18:00'), dict(starts_at='2030-01-01T18:00+05:00'),
                 dict(starts_at='2030-01-01')]
        for changes in cases:
            response = self.post('/schedule', **self.match_form(**changes))
            self.assertEqual(response.status_code, 400, changes)
            self.assertIn('name="starts_at"', response.get_data(as_text=True))
        response = self.post('/schedule', **self.match_form(away_id='1', price='123456'))
        self.assertIn('value="123456"', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/?date=invalid').status_code, 400)

    def test_sale_return_resale_and_immutable_schedule(self):
        self.login()
        self.assertEqual(self.client.get('/matches/1/tickets').status_code, 200)
        self.assertEqual(self.post('/matches/1/tickets', seat_id='1', price='1', cashier_id='1').status_code, 200)
        self.assertEqual(self.post('/matches/1/tickets', seat_id='1').status_code, 400)
        self.assertEqual(self.post('/matches/1/tickets', seat_id='999').status_code, 400)
        with self.app.app_context():
            ticket = db.get_tickets('1')[0]
            self.assertEqual(ticket['price'], 50000)
            self.assertEqual(ticket['cashier_id'], 2)
            self.assertEqual(db.get_match(1)['available'], 4)
        self.assertEqual(self.post('/tickets/1/return').status_code, 200)
        self.assertEqual(self.post('/tickets/1/return').status_code, 400)
        self.login('admin')
        self.assertEqual(self.post('/schedule/1/delete').status_code, 400)
        self.assertEqual(self.post('/schedule/1/edit', **self.match_form()).status_code, 400)
        self.login()
        self.assertEqual(self.post('/matches/1/tickets', seat_id='1').status_code, 200)
        self.assertIn('Возвращён', self.client.get('/tickets?status=returned').get_data(as_text=True))
        self.assertNotIn('Возвращён</td>', self.client.get('/tickets?status=active').get_data(as_text=True))
        self.login('admin')
        self.assertEqual(self.post('/schedule/1/delete').status_code, 400)
        self.assertEqual(self.post('/schedule/1/edit', **self.match_form()).status_code, 400)

    def test_any_cashier_can_return_ticket(self):
        self.login()
        self.post('/matches/1/tickets', seat_id='1')
        with self.app.app_context():
            connection = get_db()
            connection.execute("INSERT INTO users SELECT 3, 'other', password_hash, 'Другой кассир', role FROM users WHERE id = 2")
            connection.commit()
        self.post('/login', username='other', password='cashier123')
        self.assertIn('Анна Иванова', self.client.get('/tickets?ticket_id=1&match_id=1').get_data(as_text=True))
        self.assertEqual(self.post('/tickets/1/return').status_code, 200)

    def test_past_match_and_restart(self):
        self.login()
        self.post('/matches/1/tickets', seat_id='1')
        restarted = create_app(self.app.config)
        with restarted.app_context():
            self.assertEqual(len(db.get_tickets()), 1)
        with self.app.app_context():
            connection = get_db()
            connection.execute("UPDATE matches SET starts_at = '2000-01-01 18:00:00' WHERE id = 1")
            connection.commit()
        self.assertEqual(self.post('/matches/1/tickets', seat_id='2').status_code, 400)
        self.assertEqual(self.post('/tickets/1/return').status_code, 400)
        self.login('admin')
        self.assertEqual(self.post('/schedule/1/delete').status_code, 400)

    def test_role_checks_csrf_and_missing_records(self):
        self.login()
        for path in ('/schedule', '/schedule/1/edit', '/schedule/1/delete'):
            self.assertEqual(self.post(path, **self.match_form()).status_code, 403)
        self.assertEqual(self.client.post('/matches/1/tickets', data={'seat_id': '1'}).status_code, 400)
        for path in ('/matches/999/tickets', '/tickets/999/return'):
            self.assertEqual(self.post(path).status_code, 404)
        self.login('admin')
        for path in ('/matches/1/tickets', '/tickets/1/return'):
            self.assertEqual(self.post(path).status_code, 403)
        self.assertEqual(self.client.get('/schedule/999/edit').status_code, 404)

    def test_two_clients_compete_for_one_seat(self):
        clients = [self.app.test_client(), self.app.test_client()]
        for client in clients:
            with client.session_transaction() as session:
                session.update(user_id=2, csrf_token='test-token')
        barrier = Barrier(2)

        def sell(index):
            barrier.wait(timeout=5)
            return clients[index].post('/matches/1/tickets', data={'csrf_token': 'test-token', 'seat_id': '1'}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(sell, range(2))), [302, 400])
        with self.app.app_context():
            self.assertEqual(len(db.get_tickets()), 1)
            self.assertEqual(db.get_match(1)['available'], 4)
