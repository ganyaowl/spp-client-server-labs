import secrets
import sqlite3
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from db import close_db, get_db, init_db


def create_app(test_config=None):
    app = Flask(__name__)
    instance = Path(app.instance_path)
    instance.mkdir(exist_ok=True)
    key = instance / 'secret.key'
    try:
        with key.open('xb') as file:
            file.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    app.config.update(SECRET_KEY=key.read_bytes(), DATABASE=str(instance / 'library.sqlite'),
                      SESSION_COOKIE_NAME='library_session', SESSION_COOKIE_SAMESITE='Lax')
    if test_config:
        app.config.update(test_config)
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    @app.before_request
    def check_access():
        session.setdefault('csrf_token', secrets.token_hex(32))
        g.user = get_db().execute('SELECT * FROM users WHERE id = ?',
                                  (session.get('user_id'),)).fetchone()
        if request.method == 'POST' and not secrets.compare_digest(
                session['csrf_token'].encode(), request.form.get('csrf_token', '').encode()):
            abort(400, description='Форма устарела. Обновите страницу и повторите действие.')
        if request.endpoint not in ('login', 'static') and g.user is None:
            return redirect(url_for('login'))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            user = get_db().execute('SELECT * FROM users WHERE username = ?',
                                    (request.form.get('username', '').strip(),)).fetchone()
            if user and check_password_hash(user['password_hash'], request.form.get('password', '')):
                session.clear()
                session['user_id'] = user['id']
                return redirect(url_for('catalog'))
            flash('Неверный логин или пароль.')
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    def catalog():
        books = get_db().execute('''
            SELECT b.id, b.title, a.name AS author, b.total - (
                SELECT COUNT(*) FROM loans l JOIN requests r ON r.id = l.request_id
                WHERE r.book_id = b.id AND l.returned_at IS NULL
            ) AS available FROM books b JOIN authors a ON a.id = b.author_id ORDER BY b.title
        ''').fetchall()
        return render_template('catalog.html', books=books)

    @app.get('/requests')
    def requests_page():
        if g.user['role'] != 'admin':
            abort(403, description='Эта страница доступна только библиотекарю.')
        rows = get_db().execute('''
            SELECT r.id, r.status, r.created_at, u.name AS reader, b.title
            FROM requests r JOIN users u ON u.id = r.reader_id JOIN books b ON b.id = r.book_id
            ORDER BY r.id DESC
        ''').fetchall()
        return render_template('requests.html', rows=rows)

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def show_error(error):
        message = 'Страница не найдена.' if error.code == 404 else error.description
        return render_template('error.html', message=message), error.code

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.error('Ошибка SQLite: %s', error)
        return render_template('error.html', message='Не удалось обработать данные. Повторите попытку.'), 503

    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=5000)
