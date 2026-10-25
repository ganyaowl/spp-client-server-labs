import secrets
import sqlite3
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import db


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
    app.teardown_appcontext(db.close_db)
    with app.app_context():
        db.init_db()

    @app.before_request
    def check_access():
        session.setdefault('csrf_token', secrets.token_hex(32))
        g.user = db.get_user(session.get('user_id'))
        if request.method == 'POST' and not secrets.compare_digest(
                session['csrf_token'].encode(), request.form.get('csrf_token', '').encode()):
            abort(400, description='Форма устарела. Обновите страницу и повторите действие.')
        if request.endpoint not in ('login', 'static') and g.user is None:
            return redirect(url_for('login'))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            user = db.find_user(request.form.get('username', '').strip())
            if user and check_password_hash(user['password_hash'], request.form.get('password', '')):
                session.clear()
                session['user_id'] = user['id']
                return redirect(url_for('catalog'))
            flash('Неверный логин или пароль.', 'error')
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    def catalog():
        books = db.get_books(request.args.get('title', ''), request.args.get('author_id', ''),
                             request.args.get('available') == '1')
        return render_template('catalog.html', books=books, authors=db.get_authors())

    def require_role(role):
        if g.user['role'] != role:
            abort(403, description='У вашей роли нет доступа к этому действию.')

    @app.post('/books/<int:book_id>/request')
    def submit_request(book_id):
        require_role('reader')
        db.submit_request(g.user['id'], book_id)
        flash('Заявка отправлена библиотекарю.')
        return redirect(url_for('requests_page'))

    @app.get('/requests')
    def requests_page():
        reader_id = g.user['id'] if g.user['role'] == 'reader' else None
        return render_template('requests.html', rows=db.get_requests(reader_id))

    @app.post('/requests/<int:request_id>/issue')
    def issue(request_id):
        require_role('admin')
        db.process_request(request_id, issue=True)
        flash('Книга выдана читателю.')
        return redirect(url_for('requests_page'))

    @app.post('/requests/<int:request_id>/reject')
    def reject(request_id):
        require_role('admin')
        db.process_request(request_id, issue=False)
        flash('Заявка отклонена.')
        return redirect(url_for('requests_page'))

    @app.post('/loans/<int:loan_id>/return')
    def return_book(loan_id):
        require_role('admin')
        db.return_book(loan_id)
        flash('Возврат принят. Экземпляр снова доступен.')
        return redirect(url_for('requests_page'))

    @app.errorhandler(ValueError)
    def validation_error(error):
        return render_template('error.html', message=str(error)), 400

    @app.errorhandler(LookupError)
    def missing_record(error):
        return render_template('error.html', message=str(error)), 404

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
