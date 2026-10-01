import os
from datetime import timedelta

from cryptography.fernet import Fernet
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from .models import User, db

login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(test_config=None):
    app = Flask(__name__)
    production = os.getenv('APP_ENV', 'production') == 'production'
    app.config.update(
        SECRET_KEY=os.getenv('SECRET_KEY'),
        ENCRYPTION_KEY=os.getenv('ENCRYPTION_KEY'),
        SQLALCHEMY_DATABASE_URI=os.getenv('DATABASE_URL', 'sqlite:///portal.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_SECURE=production,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=1024 * 1024,
        TRUSTED_HOSTS=[os.getenv('DOMAIN', 'cdr.osumtech.net')] if production else ['localhost', '127.0.0.1'],
        BILLING_INCREMENT_SECONDS=int(os.getenv('BILLING_INCREMENT_SECONDS', '1')),
        FALLBACK_RATE=os.getenv('FALLBACK_RATE') or None,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config['SECRET_KEY'] or len(app.config['SECRET_KEY']) < 32:
        raise RuntimeError('Set SECRET_KEY to a random value of at least 32 characters.')
    if not app.config['ENCRYPTION_KEY']:
        raise RuntimeError('Set ENCRYPTION_KEY to a generated Fernet key.')
    Fernet(app.config['ENCRYPTION_KEY'])
    if app.config['BILLING_INCREMENT_SECONDS'] not in (1, 6, 30, 60):
        raise RuntimeError('Billing increment must be 1, 6, 30 or 60 seconds.')
    if production and not app.config.get('TESTING') and not app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql'):
        raise RuntimeError('Production requires PostgreSQL.')
    if production:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'portal.login'
    login_manager.login_message = 'Please sign in to continue.'
    csrf.init_app(app)
    from .routes import portal
    from .admin import admin
    from .cli import register_commands
    app.register_blueprint(portal)
    app.register_blueprint(admin)
    from .webhooks import webhooks
    csrf.exempt(webhooks)
    app.register_blueprint(webhooks)
    register_commands(app)

    @app.template_filter('money')
    def money(value):
        return f'{value:,.2f}'

    from .formatting import customer_amount
    app.add_template_filter(customer_amount, 'customer_amount')

    @app.template_filter('precise')
    def precise(value):
        return f'{value:,.6f}'

    @app.after_request
    def secure_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if not response.headers.get('Cache-Control') or not response.headers['Cache-Control'].startswith('public'):
            response.headers['Cache-Control'] = 'no-store'
        if production:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    return app


@login_manager.user_loader
def load_user(user_id):
    try:
        identifier, version = user_id.split(':', 1)
        user = db.session.get(User, int(identifier))
        return user if user and user.is_active and user.auth_version == int(version) else None
    except (ValueError, TypeError):
        return None
