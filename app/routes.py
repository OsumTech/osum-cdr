import hashlib
import hmac
from datetime import timedelta, timezone
from io import BytesIO

from cryptography.fernet import Fernet
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, select, text
from werkzeug.security import check_password_hash, generate_password_hash

from .models import Call, Did, Invoice, Ledger, LoginThrottle, SipAccount, User, db, utcnow

portal = Blueprint('portal', __name__)
DUMMY_HASH = generate_password_hash('unusable-placeholder-password')


@portal.before_request
def separate_admin_workspace():
    if current_user.is_authenticated and current_user.is_admin and request.endpoint not in ('portal.login', 'portal.logout', 'portal.health'):
        return redirect(url_for('admin.dashboard'))


@portal.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('portal.dashboard'))
    email = request.form.get('email', '').strip().lower()[:254]
    if request.method == 'POST':
        now = utcnow()
        # Database-backed throttling works across Gunicorn processes and restarts.
        key = hmac.new(current_app.secret_key.encode(), (request.remote_addr or 'unknown').encode(), hashlib.sha256).hexdigest()
        if db.engine.dialect.name == 'postgresql':
            db.session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(key[:15], 16)})
        throttle = db.session.get(LoginThrottle, key)
        if not throttle:
            throttle = LoginThrottle(key=key, attempts=0, window_start=now)
            db.session.add(throttle)
        if throttle.window_start.replace(tzinfo=timezone.utc) < now - timedelta(minutes=15):
            throttle.attempts, throttle.window_start = 0, now
        if throttle.attempts >= 10:
            db.session.rollback()
            return render_template('login.html', email=email, error='Too many sign-in attempts. Please try again in 15 minutes.'), 429
        throttle.attempts += 1
        db.session.commit()
        user = db.session.scalar(select(User).where(User.email == email))
        valid = check_password_hash(user.password_hash if user else DUMMY_HASH, request.form.get('password', ''))
        if user and user.is_active and valid:
            session.clear()
            login_user(user)
            session.permanent = True
            return redirect(url_for('portal.dashboard'))
        return render_template('login.html', email=email, error='Email or password is incorrect.'), 401
    return render_template('login.html', email=email)


@portal.post('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('portal.login'))


@portal.get('/')
@login_required
def dashboard():
    tenant_id = current_user.tenant_id
    month = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    calls = db.session.scalars(select(Call).where(Call.tenant_id == tenant_id).order_by(Call.started_at.desc(), Call.id.desc()).limit(50)).all()
    usage = db.session.scalar(select(func.coalesce(func.sum(Call.cost), 0)).where(Call.tenant_id == tenant_id, Call.started_at >= month))
    seconds = db.session.scalar(select(func.coalesce(func.sum(Call.duration), 0)).where(Call.tenant_id == tenant_id, Call.started_at >= month))
    accounts = db.session.scalars(select(SipAccount).where(SipAccount.tenant_id == tenant_id, SipAccount.active.is_(True))).all()
    return render_template('dashboard.html', title='Overview', calls=calls, usage=usage, seconds=seconds, accounts=accounts)


@portal.get('/calls')
@login_required
def calls():
    query = request.args.get('destination', '').strip()[:32]
    statement = select(Call).where(Call.tenant_id == current_user.tenant_id)
    if query:
        statement = statement.where(Call.destination.contains(query, autoescape=True))
    pagination = db.paginate(statement.order_by(Call.started_at.desc(), Call.id.desc()), per_page=50, max_per_page=50, error_out=False)
    return render_template('calls.html', title='Call history', pagination=pagination, query=query)


@portal.route('/trunks', methods=['GET', 'POST'])
@login_required
def trunks():
    revealed, error = None, None
    if request.method == 'POST':
        account = db.session.scalar(select(SipAccount).where(SipAccount.id == request.form.get('account_id', type=int), SipAccount.tenant_id == current_user.tenant_id))
        if not account:
            abort(404)
        if not check_password_hash(current_user.password_hash, request.form.get('password', '')):
            error = 'Your password was incorrect. The SIP password remains hidden.'
        else:
            revealed = (account.id, Fernet(current_app.config['ENCRYPTION_KEY']).decrypt(account.password_encrypted.encode()).decode())
    accounts = db.session.scalars(select(SipAccount).where(SipAccount.tenant_id == current_user.tenant_id).order_by(SipAccount.id)).all()
    dids = db.session.scalars(select(Did).where(Did.tenant_id == current_user.tenant_id).order_by(Did.number)).all()
    return render_template('trunks.html', title='SIP accounts', accounts=accounts, dids=dids, revealed=revealed, error=error)


@portal.get('/billing')
@login_required
def billing():
    entries = db.paginate(select(Ledger).where(Ledger.tenant_id == current_user.tenant_id).order_by(Ledger.id.desc()), per_page=50, max_per_page=50, error_out=False)
    invoices = db.session.scalars(select(Invoice).where(Invoice.tenant_id == current_user.tenant_id).order_by(Invoice.period.desc())).all()
    return render_template('billing.html', title='Billing & invoices', entries=entries, invoices=invoices)


@portal.get('/invoices/<int:invoice_id>/download')
@login_required
def invoice_download(invoice_id):
    from .invoices import render_invoice
    invoice = db.session.scalar(select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == current_user.tenant_id))
    if not invoice:
        abort(404)
    from werkzeug.utils import secure_filename
    customer = secure_filename(invoice.customer_name) or 'Customer'
    return send_file(BytesIO(render_invoice(invoice)), mimetype='application/pdf', as_attachment=True,
                     download_name=f'Osumtech - Invoice {invoice.number} - {customer}.pdf', max_age=0)


@portal.get('/health')
def health():
    db.session.execute(text('SELECT 1'))
    return {'status': 'ok'}


@portal.app_errorhandler(400)
@portal.app_errorhandler(403)
@portal.app_errorhandler(404)
@portal.app_errorhandler(500)
def error_page(error):
    code = getattr(error, 'code', 500)
    if code == 500:
        db.session.rollback()
    return render_template('error.html', title='Something went wrong', code=code), code
