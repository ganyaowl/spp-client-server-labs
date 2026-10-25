import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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


def now():
    return datetime.now(timezone(timedelta(hours=5))).replace(tzinfo=None)


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


def get_teams():
    return get_db().execute('SELECT * FROM teams ORDER BY name').fetchall()


MATCHES = '''SELECT m.*, h.name AS home, a.name AS away,
    (SELECT COUNT(*) FROM seats) - (SELECT COUNT(*) FROM tickets t
        WHERE t.match_id = m.id AND t.returned_at IS NULL) AS available,
    EXISTS(SELECT 1 FROM tickets t WHERE t.match_id = m.id) AS sold
    FROM matches m JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id'''


def get_matches(team_id='', date='', only_available=False):
    if date:
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise ValueError('Укажите корректную дату матча.') from None
    return get_db().execute(f'''SELECT * FROM ({MATCHES}) WHERE
        (? = '' OR home_id = ? OR away_id = ?) AND (? = '' OR substr(starts_at, 1, 10) = ?)
        AND (? = 0 OR (available > 0 AND starts_at > ?)) ORDER BY starts_at''',
        (team_id, team_id, team_id, date, date, only_available, now().isoformat(' ', timespec='seconds'))).fetchall()


def get_match(match_id):
    row = get_db().execute(MATCHES + ' WHERE m.id = ?', (str(match_id),)).fetchone()
    if row is None:
        raise LookupError('Матч не найден.')
    return row


def check_future(match):
    if datetime.fromisoformat(match['starts_at']) <= now():
        raise ValueError('Матч уже начался. Операция недоступна.')


def check_editable(match):
    check_future(match)
    if match['sold']:
        raise ValueError('После первой продажи билета матч нельзя изменять или удалять.')


def save_match(form, match_id=None):
    try:
        start = datetime.fromisoformat(form.get('starts_at', ''))
        price = int(form.get('price', ''))
    except (ValueError, TypeError):
        raise ValueError('Укажите корректные дату, время и целую цену билета.') from None
    if start.tzinfo is not None or len(form.get('starts_at', '')) < 16:
        raise ValueError('Укажите дату и время по Ташкенту (UTC+5) без часового смещения.')
    if not 0 < price <= 9223372036854775807:
        raise ValueError('Укажите положительную целую цену не больше 9223372036854775807 сум.')
    home, away = form.get('home_id', ''), form.get('away_id', '')
    start_text = start.replace(microsecond=0).isoformat(' ', timespec='seconds')
    with transaction() as connection:
        if start <= now():
            raise ValueError('Укажите будущие дату и время по Ташкенту (UTC+5).')
        if match_id is not None:
            check_editable(get_match(match_id))
        teams = {str(team['id']) for team in get_teams()}
        if home not in teams or away not in teams:
            raise ValueError('Выберите команды из списка.')
        if home == away:
            raise ValueError('В матче должны участвовать разные команды.')
        if connection.execute('SELECT 1 FROM matches WHERE starts_at = ? AND id != ?',
                              (start_text, str(match_id) if match_id is not None else '-1')).fetchone():
            raise ValueError('На это время уже назначен другой матч.')
        if match_id is None:
            connection.execute('INSERT INTO matches (home_id, away_id, starts_at, price) VALUES (?, ?, ?, ?)',
                               (home, away, start_text, price))
        else:
            connection.execute('UPDATE matches SET home_id = ?, away_id = ?, starts_at = ?, price = ? WHERE id = ?',
                               (home, away, start_text, price, str(match_id)))


def delete_match(match_id):
    with transaction() as connection:
        check_editable(get_match(match_id))
        connection.execute('DELETE FROM matches WHERE id = ?', (str(match_id),))


def get_seats(match_id):
    return get_db().execute('''SELECT * FROM seats s WHERE NOT EXISTS (
        SELECT 1 FROM tickets t WHERE t.match_id = ? AND t.seat_id = s.id AND t.returned_at IS NULL)
        ORDER BY sector, row_number, seat_number''', (str(match_id),)).fetchall()


def sell_ticket(match_id, seat_id, cashier_id):
    with transaction() as connection:
        match = get_match(match_id)
        check_future(match)
        if seat_id not in {str(seat['id']) for seat in get_seats(match_id)}:
            raise ValueError('Место не найдено или уже продано. Выберите свободное место.')
        cursor = connection.execute('INSERT INTO tickets (match_id, seat_id, cashier_id, price) VALUES (?, ?, ?, ?)',
                                    (str(match_id), seat_id, cashier_id, match['price']))
        return cursor.lastrowid


def get_tickets(ticket_id='', match_id='', status=''):
    if status not in ('', 'active', 'returned'):
        raise ValueError('Выберите статус билета из списка.')
    return get_db().execute('''SELECT t.*, h.name AS home, a.name AS away, m.starts_at,
        s.sector, s.row_number, s.seat_number, u.name AS cashier
        FROM tickets t JOIN matches m ON m.id = t.match_id
        JOIN teams h ON h.id = m.home_id JOIN teams a ON a.id = m.away_id
        JOIN seats s ON s.id = t.seat_id JOIN users u ON u.id = t.cashier_id
        WHERE (? = '' OR t.id = ?) AND (? = '' OR t.match_id = ?)
        AND (? = '' OR (? = 'active' AND t.returned_at IS NULL)
            OR (? = 'returned' AND t.returned_at IS NOT NULL)) ORDER BY t.id DESC''',
        (ticket_id, ticket_id, match_id, match_id, status, status, status)).fetchall()


def return_ticket(ticket_id):
    with transaction() as connection:
        ticket = connection.execute('SELECT * FROM tickets WHERE id = ?', (str(ticket_id),)).fetchone()
        if ticket is None:
            raise LookupError('Билет не найден.')
        if ticket['returned_at'] is not None:
            raise ValueError('Этот билет уже возвращён.')
        check_future(get_match(ticket['match_id']))
        connection.execute('UPDATE tickets SET returned_at = CURRENT_TIMESTAMP WHERE id = ?', (str(ticket_id),))
