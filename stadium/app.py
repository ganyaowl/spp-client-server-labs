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
    app.config.update(SECRET_KEY=key.read_bytes(), DATABASE=str(instance / 'stadium.sqlite'),
                      SESSION_COOKIE_NAME='stadium_session', SESSION_COOKIE_SAMESITE='Lax')
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
                return redirect(url_for('matches'))
            flash('Неверный логин или пароль.', 'error')
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    def require_role(role):
        if g.user['role'] != role:
            abort(403, description='У вашей роли нет доступа к этому действию.')

    @app.context_processor
    def clock():
        return {'now': db.now().isoformat(' ', timespec='seconds')}

    @app.get('/')
    def matches():
        rows = db.get_matches(request.args.get('team_id', ''), request.args.get('date', ''),
                              request.args.get('available') == '1')
        return render_template('matches.html', matches=rows, teams=db.get_teams())

    @app.route('/schedule', methods=['GET', 'POST'])
    def schedule():
        require_role('admin')
        if request.method == 'POST':
            try:
                db.save_match(request.form)
            except ValueError as error:
                flash(str(error), 'error')
                return schedule_form(request.form, status=400)
            flash('Матч добавлен в расписание.')
            return redirect(url_for('schedule'))
        return schedule_form({})

    def schedule_form(form, editing=None, status=200):
        return render_template('matches.html', matches=db.get_matches(), teams=db.get_teams(),
                               scheduling=True, form=form, editing=editing), status

    @app.route('/schedule/<int:match_id>/edit', methods=['GET', 'POST'])
    def edit_match(match_id):
        require_role('admin')
        match = db.get_match(match_id)
        db.check_editable(match)
        if request.method == 'POST':
            try:
                db.save_match(request.form, match_id)
            except ValueError as error:
                flash(str(error), 'error')
                return schedule_form(request.form, editing=match_id, status=400)
            flash('Расписание обновлено.')
            return redirect(url_for('schedule'))
        return schedule_form(dict(match), editing=match_id)

    @app.post('/schedule/<int:match_id>/delete')
    def delete_match(match_id):
        require_role('admin')
        db.delete_match(match_id)
        flash('Матч удалён из расписания.')
        return redirect(url_for('schedule'))

    @app.route('/matches/<int:match_id>/tickets', methods=['GET', 'POST'])
    def sell_ticket(match_id):
        require_role('cashier')
        match = db.get_match(match_id)
        db.check_future(match)
        if request.method == 'POST':
            try:
                ticket_id = db.sell_ticket(match_id, request.form.get('seat_id', ''), g.user['id'])
            except ValueError as error:
                flash(str(error), 'error')
                return render_template('tickets.html', match=match, seats=db.get_seats(match_id)), 400
            price = db.get_tickets(str(ticket_id))[0]['price']
            flash(f'Билет №{ticket_id} продан за {price} сум.')
            return redirect(url_for('tickets', ticket_id=ticket_id))
        return render_template('tickets.html', match=match, seats=db.get_seats(match_id))

    @app.get('/tickets')
    def tickets():
        require_role('cashier')
        rows = db.get_tickets(request.args.get('ticket_id', ''), request.args.get('match_id', ''),
                             request.args.get('status', ''))
        return render_template('tickets.html', rows=rows, matches=db.get_matches())

    @app.post('/tickets/<int:ticket_id>/return')
    def return_ticket(ticket_id):
        require_role('cashier')
        db.return_ticket(ticket_id)
        flash('Билет возвращён. Место снова доступно.')
        return redirect(url_for('tickets', ticket_id=ticket_id))

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
    create_app().run(host='127.0.0.1', port=5001)
