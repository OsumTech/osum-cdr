from app.models import User, db


def test_reset_password_revokes_existing_session(app, signed_in):
    result = app.test_cli_runner().invoke(args=['reset-password', '--email', 'a@example.test', '--password', 'replacement-password-123'])
    assert result.exit_code == 0, result.output
    # A real HTTP request gets a fresh app context; do not reuse the fixture's
    # cached Flask-Login user after changing its authentication version.
    with app.app_context():
        assert signed_in.get('/').status_code == 302


def test_operator_top_up_idempotency(app):
    runner = app.test_cli_runner()
    command = ['top-up', '--tenant-id', '1', '--amount', '20', '--reference', 'operator-payment']
    assert 'Credit posted' in runner.invoke(args=command).output
    assert 'already recorded' in runner.invoke(args=command).output
