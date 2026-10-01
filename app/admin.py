from datetime import date
from decimal import InvalidOperation
import re

from cryptography.fernet import Fernet
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from .billing import activate_did, billing_mode_lock, decimal_amount, locked_tenant, normalise_number, top_up
from .integrations import preview_calls, settings
from .models import CallEstimate, ProviderRate
from .models import AuditEvent, Call, CdrPartition, CsvReconciliation, Did, Integration, Ledger, SipAccount, Tenant, User, WebhookBilling, WebhookEvent, WebhookSettings, db, utcnow

admin = Blueprint('admin', __name__, url_prefix='/admin')


@admin.route('/webhooks', methods=['GET', 'POST'])
def webhook_settings():
    import hashlib
    import secrets
    from .webhooks import mapping
    config = db.session.get(WebhookSettings, 1)
    error = None
    if request.method == 'POST':
        try:
            action = request.form.get('action')
            if action == 'activate_billing':
                billing_mode_lock(exclusive=True)
                if not config or not config.enabled:
                    raise ValueError('Enable the receiver before activating billing.')
                if not db.session.get(WebhookBilling, 1):
                    db.session.add(WebhookBilling(id=1, started_at=utcnow()))
                    integration = db.session.get(Integration, 1)
                    if integration:
                        integration.enabled = False
                        integration.reconciliation_enabled = False
                    audit('webhook.billing.activate', 'didlogic')
            elif action == 'rotate':
                secret = secrets.token_urlsafe(36)
                if not config:
                    config = WebhookSettings(id=1)
                    db.session.add(config)
                config.secret_hash = hashlib.sha256(secret.encode()).hexdigest()
                config.secret_encrypted = Fernet(current_app.config['ENCRYPTION_KEY']).encrypt(secret.encode()).decode()
                config.enabled = True
                audit('webhook.rotate', 'didlogic', {'mode': 'shadow'})
            elif action in ('pause', 'resume') and config:
                config.enabled = action == 'resume'
                audit('webhook.' + action, 'didlogic')
            elif action == 'remap':
                event = db.session.scalar(select(WebhookEvent).where(WebhookEvent.id == request.form.get('event_id', type=int)).with_for_update())
                if not event or event.status != 'unmapped':
                    raise ValueError('Only unmapped events can be checked again.')
                did = mapping(event.payload)
                if not did:
                    raise ValueError('Assign the active CLI or receiving DID to the client first.')
                event.did_id, event.tenant_id, event.status = did.id, did.tenant_id, 'mapped'
                audit('webhook.remap', str(event.id), {'tenant_id': did.tenant_id})
            else:
                raise ValueError('Unknown webhook action.')
            db.session.commit()
            flash('Webhook settings updated. Existing events were not charged.')
            return redirect(url_for('admin.webhook_settings'))
        except ValueError as exc:
            db.session.rollback()
            error = str(exc)
    receiver_url = None
    if config:
        secret = Fernet(current_app.config['ENCRYPTION_KEY']).decrypt(config.secret_encrypted.encode()).decode()
        receiver_url = url_for('webhooks.didlogic', secret=secret, _external=True, _scheme='https')
    selected_status = request.args.get('status', '')
    selected_direction = request.args.get('direction', '')
    selected_tenant = request.args.get('tenant_id', type=int)
    per_page = request.args.get('per_page', 10, type=int)
    if per_page not in (10, 25, 50, 100):
        abort(400)
    if selected_status not in ('', 'mapped', 'unmapped', 'conflict') or selected_direction not in ('', 'inbound', 'outbound'):
        abort(400)
    query = select(WebhookEvent)
    if selected_status:
        query = query.where(WebhookEvent.status == selected_status)
    if selected_direction:
        query = query.where(WebhookEvent.direction == selected_direction)
    if selected_tenant:
        query = query.where(WebhookEvent.tenant_id == selected_tenant)
    counts = dict(db.session.execute(select(WebhookEvent.status, func.count(WebhookEvent.id)).group_by(WebhookEvent.status)).all())
    last_received = db.session.scalar(select(func.max(WebhookEvent.last_received_at)))
    events = db.paginate(query.order_by(WebhookEvent.received_at.desc(), WebhookEvent.id.desc()),
                         per_page=per_page, error_out=False)
    names = dict(db.session.execute(select(Tenant.id, Tenant.name)).all())
    return render_template('admin/webhooks.html', title='Live calls', config=config, receiver_url=receiver_url,
                           events=events, tenant_names=names, error=error, counts=counts,
                           last_received=last_received, selected_status=selected_status,
                           selected_direction=selected_direction, selected_tenant=selected_tenant,
                           per_page=per_page, billing=db.session.get(WebhookBilling, 1)), (400 if error else 200)


@admin.before_request
def administrator_only():
    if not current_user.is_authenticated:
        return current_app.login_manager.unauthorized()
    if not current_user.is_admin:
        abort(403)


def audit(action, target, details=None):
    db.session.add(AuditEvent(actor_id=current_user.id, action=action, target=str(target), details=details or {}))


def form_text(name, limit=160, required=True):
    value = request.form.get(name, '').strip()
    if (required and not value) or len(value) > limit:
        raise ValueError(f'{name.replace("_", " ").capitalize()} is required and must be at most {limit} characters.')
    return value


def amount(name, optional=False):
    raw = form_text(name, 40, required=not optional)
    if optional and not raw:
        return None
    try:
        value = decimal_amount(raw)
    except (ValueError, InvalidOperation):
        raise ValueError(f'{name.replace("_", " ").capitalize()} must be a valid non-negative amount.') from None
    if value < 0:
        raise ValueError('Rates and charges must not be negative.')
    return value


def rate_values():
    try:
        increment = int(request.form.get('billing_increment', '1'))
    except ValueError:
        raise ValueError('Choose a valid billing increment.') from None
    if increment not in (1, 6, 30, 60):
        raise ValueError('Choose a valid billing increment.')
    return {**{key: amount(key) for key in ('landline_rate', 'mobile_rate', 'cli_initial_cost', 'cli_setup_cost', 'cli_monthly_cost')},
            'fallback_rate': amount('fallback_rate', optional=True), 'billing_increment': increment,
            'vendor_cost_pricing': request.form.get('vendor_cost_pricing') == 'on'}


def login_values():
    email = form_text('email', 254).lower()
    password = request.form.get('password', '')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or not 12 <= len(password) <= 1024:
        raise ValueError('Use a valid email address and a password of 12–1024 characters.')
    return email, generate_password_hash(password)


@admin.get('/')
def dashboard():
    estimated = db.session.execute(select(func.count(CallEstimate.call_id),
        func.coalesce(func.sum(CallEstimate.cost), 0), func.coalesce(func.sum(Call.cost - CallEstimate.cost), 0))
        .join(Call, Call.id == CallEstimate.call_id).where(Call.wholesale_cost.is_(None))).one()
    clients = db.session.scalar(select(func.count(Tenant.id)))
    totals = db.session.execute(select(func.coalesce(func.sum(Call.cost), 0),
        func.coalesce(func.sum(Call.wholesale_cost), 0), func.count(Call.id))).one()
    comparable = db.session.scalar(select(func.coalesce(func.sum(Call.cost - Call.wholesale_cost), 0)).where(Call.wholesale_cost.is_not(None)))
    missing = db.session.scalar(select(func.count(Call.id)).where(Call.wholesale_cost.is_(None)))
    calls = db.session.execute(select(Call, Tenant.name).join(Tenant, Tenant.id == Call.tenant_id).order_by(Call.id.desc()).limit(50)).all()
    events = db.session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(10)).all()
    return render_template('admin/dashboard.html', title='Admin overview', clients=clients, totals=totals,
                           margin=comparable, missing=missing, calls=calls, integration=settings(), events=events, estimated=estimated)


@admin.get('/clients')
def clients():
    pagination = db.paginate(select(Tenant).order_by(Tenant.name), per_page=50, error_out=False)
    return render_template('admin/clients.html', title='Clients', pagination=pagination)


@admin.route('/clients/new', methods=['GET', 'POST'])
def create_client():
    error = None
    if request.method == 'POST':
        try:
            name, values = form_text('name'), rate_values()
            email, password_hash = login_values()
            tenant = Tenant(name=name, **values)
            db.session.add(tenant)
            db.session.flush()
            db.session.add(User(tenant_id=tenant.id, email=email, password_hash=password_hash))
            audit('client.create', tenant.id, {'name': name, 'rates': {k: str(v) for k, v in values.items()}})
            db.session.commit()
            flash('Client created. Add their real SIP accounts and numbers below.')
            return redirect(url_for('admin.client', tenant_id=tenant.id))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            error = 'That email is already in use.' if isinstance(exc, IntegrityError) else str(exc)
    return render_template('admin/client_new.html', title='Create client', error=error), (400 if error else 200)


@admin.route('/clients/<int:tenant_id>', methods=['GET', 'POST'])
def client(tenant_id):
    tenant = db.get_or_404(Tenant, tenant_id)
    error = None
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            if action == 'settings':
                values, name = rate_values(), form_text('name')
                tenant = locked_tenant(tenant_id)
                before = {key: str(getattr(tenant, key)) for key in values}
                for key, value in values.items():
                    setattr(tenant, key, value)
                tenant.name = name
                tenant.active = request.form.get('active') == 'on'
                audit('client.settings', tenant_id, {'before': before, 'after': {k: str(v) for k, v in values.items()}, 'active': tenant.active})
            elif action == 'user':
                email, password_hash = login_values()
                db.session.add(User(tenant_id=tenant_id, email=email, password_hash=password_hash))
                audit('client.add_user', tenant_id, {'email': email})
            elif action == 'user_update':
                user = db.session.scalar(select(User).where(User.id == request.form.get('user_id', type=int), User.tenant_id == tenant_id, User.is_admin.is_(False)).with_for_update())
                if not user:
                    abort(404)
                password = request.form.get('password', '')
                if password:
                    if not 12 <= len(password) <= 1024:
                        raise ValueError('Use a password of 12–1024 characters.')
                    user.password_hash = generate_password_hash(password)
                user.active = request.form.get('active') == 'on'
                user.auth_version += 1
                audit('client.update_user', user.id, {'password_changed': bool(password), 'active': user.active})
            elif action == 'sip':
                provider_id, label = form_text('provider_id'), form_text('label', 100)
                username, host = form_text('username'), form_text('host', 254)
                password = request.form.get('sip_password', '')
                if not password or len(password) > 1024:
                    raise ValueError('Enter the real SIP password (up to 1024 characters).')
                db.session.add(SipAccount(tenant_id=tenant_id, provider_id=provider_id, label=label, username=username,
                    host=host, password_encrypted=Fernet(current_app.config['ENCRYPTION_KEY']).encrypt(password.encode()).decode()))
                audit('client.map_sip', tenant_id, {'provider_id': provider_id, 'label': label})
            elif action == 'sip_update':
                account = db.session.scalar(select(SipAccount).where(SipAccount.id == request.form.get('account_id', type=int), SipAccount.tenant_id == tenant_id).with_for_update())
                if not account:
                    abort(404)
                account.label, account.host = form_text('label', 100), form_text('host', 254)
                password = request.form.get('sip_password', '')
                if len(password) > 1024:
                    raise ValueError('SIP password is too long.')
                if password:
                    account.password_encrypted = Fernet(current_app.config['ENCRYPTION_KEY']).encrypt(password.encode()).decode()
                account.active = request.form.get('active') == 'on'
                audit('client.update_sip', account.id, {'active': account.active, 'password_changed': bool(password)})
            elif action == 'did':
                number = form_text('number', 32)
                initial, setup, monthly = amount('initial_cost'), amount('setup_cost'), amount('monthly_charge')
                if request.form.get('mode') == 'new':
                    if request.form.get('confirm_charge') != 'on':
                        raise ValueError('Confirm the displayed initial CLI and setup charges before activation.')
                    activate_did(tenant_id, number, initial, setup, monthly, utcnow().date(), current_user.id)
                elif request.form.get('mode') == 'existing':
                    due = date.fromisoformat(form_text('next_billing_date', 10))
                    if due < utcnow().date():
                        raise ValueError('For an existing number, choose today or a future renewal date.')
                    db.session.add(Did(tenant_id=tenant_id, number='+' + normalise_number(number), monthly_charge=monthly,
                                       next_billing_date=due, billing_day=due.day, initial_cost=0, setup_cost=0))
                    audit('client.map_existing_did', tenant_id, {'number': number, 'monthly_charge': str(monthly), 'due': str(due)})
                else:
                    raise ValueError('Choose new activation or existing number.')
            elif action == 'did_update':
                locked_tenant(tenant_id)
                did = db.session.scalar(select(Did).where(Did.id == request.form.get('did_id', type=int), Did.tenant_id == tenant_id).with_for_update())
                if not did:
                    abort(404)
                did.monthly_charge = amount('monthly_charge')
                did.active = request.form.get('active') == 'on'
                audit('client.update_did', did.id, {'monthly_charge': str(did.monthly_charge), 'active': did.active})
            elif action == 'topup':
                if request.form.get('payment_verified') != 'on':
                    raise ValueError('Confirm that payment was received before adding credit.')
                top_up(tenant_id, amount('amount'), form_text('reference'), actor_id=current_user.id)
            else:
                raise ValueError('Unknown client action.')
            db.session.commit()
            flash('Client changes saved.')
            return redirect(url_for('admin.client', tenant_id=tenant_id))
        except (ValueError, InvalidOperation, IntegrityError) as exc:
            db.session.rollback()
            error = 'That email, SIP identifier or number is already assigned. No duplicate charge was posted.' if isinstance(exc, IntegrityError) else str(exc)
    users = db.session.scalars(select(User).where(User.tenant_id == tenant_id).order_by(User.id)).all()
    accounts = db.session.scalars(select(SipAccount).where(SipAccount.tenant_id == tenant_id).order_by(SipAccount.id)).all()
    dids = db.session.scalars(select(Did).where(Did.tenant_id == tenant_id).order_by(Did.id)).all()
    entries = db.session.scalars(select(Ledger).where(Ledger.tenant_id == tenant_id).order_by(Ledger.id.desc()).limit(50)).all()
    return render_template('admin/client.html', title=tenant.name, tenant=tenant, users=users, accounts=accounts,
                           dids=dids, entries=entries, error=error, today=utcnow().date()), (400 if error else 200)


@admin.route('/integrations', methods=['GET', 'POST'])
def integration():
    config, error = settings(), None
    if request.method == 'POST':
        try:
            if config and request.form.get('action') != 'test':
                config = db.session.execute(select(Integration).where(Integration.id == 1).with_for_update().execution_options(populate_existing=True)).scalar_one()
            if not config:
                config = Integration(id=1)
                db.session.add(config)
                db.session.flush()
            action = request.form.get('action')
            if action == 'save':
                token = request.form.get('api_token', '').strip()
                field = form_text('call_id_field', 80, required=False)
                if field and (not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', field) or any(word in field.lower() for word in ('token', 'secret', 'password')) or field.lower() in ('key', 'api_key')):
                    raise ValueError('Enter a call-ID field name, never a credential field.')
                if token and (len(token) > 4096 or any(c.isspace() for c in token)):
                    raise ValueError('Enter the token only, without Bearer or whitespace.')
                start_raw = form_text('start_date', 10, required=False)
                start = date.fromisoformat(start_raw) if start_raw else None
                if start and start > utcnow().date():
                    raise ValueError('The start date cannot be in the future.')
                config.call_id_field, config.start_date = field, start
                config.currency_confirmed = request.form.get('currency_confirmed') == 'on'
                if token:
                    config.token_encrypted = Fernet(current_app.config['ENCRYPTION_KEY']).encrypt(token.encode()).decode()
                config.token_version += 1
                config.enabled = config.last_test_ok = config.sync_requested = False
                config.reconciliation_enabled = False
                config.preview_rows = config.sample_fields = None
                config.last_test_message = 'Settings saved. Test the connection before enabling sync.'
                audit('integration.save', 'didlogic', {'token_replaced': bool(token), 'call_id_field': field, 'start_date': str(start)})
            elif action == 'test':
                version = config.token_version
                fields, rows = preview_calls(config)
                config = db.session.execute(select(Integration).where(Integration.id == 1).with_for_update().execution_options(populate_existing=True)).scalar_one()
                if config.token_version != version:
                    raise ValueError('Settings changed while testing. Please test the current settings again.')
                config.last_test_at, config.last_test_ok = utcnow(), True
                config.sample_fields, config.preview_rows = fields, rows
                config.last_test_message = f'Connected. Fetched {len(rows)} real CDRs from the last seven days. No charges posted.'
                audit('integration.test', 'didlogic', {'records': len(rows)})
            elif action == 'shadow':
                if not config.last_test_ok or not config.start_date or not config.currency_confirmed:
                    raise ValueError('Save a start date, confirm USD and test the connection first.')
                if (not db.session.scalar(select(SipAccount.id).where(SipAccount.active.is_(True)))
                        and not db.session.scalar(select(Did.id).where(Did.active.is_(True)))):
                    raise ValueError('Map at least one SIP account or receiving DID to a client first.')
                config.enabled = False
                config.reconciliation_enabled = config.sync_requested = True
                audit('integration.shadow', 'didlogic', {'start_date': str(config.start_date)})
            elif action == 'enable':
                if db.session.scalar(select(CdrPartition.id).where(CdrPartition.accepted_at.is_not(None))):
                    raise ValueError('Reconciled calls have been billed. A reviewed cutover is required before switching to provider-ID billing.')
                if not config.last_test_ok or not config.start_date or not config.currency_confirmed or not config.call_id_field:
                    raise ValueError('Save a start date, confirm USD costs, choose the stable ID field and test the connection first.')
                ids = [row.get('call_id') for row in config.preview_rows or []]
                if not ids or any(not identifier for identifier in ids) or len(set(ids)) != len(ids):
                    raise ValueError('The preview must contain distinct, non-empty call IDs before billing can be enabled.')
                if request.form.get('id_confirmed') != 'on':
                    raise ValueError('Confirm with DID Logic that this field uniquely identifies each call.')
                if not db.session.scalar(select(SipAccount.id).where(SipAccount.active.is_(True))):
                    raise ValueError('Map at least one real SIP account to a client first.')
                config.enabled, config.sync_requested = True, True
                config.reconciliation_enabled = False
                audit('integration.enable', 'didlogic', {'start_date': str(config.start_date), 'call_id_field': config.call_id_field})
            elif action == 'disable':
                config.enabled = config.sync_requested = False
                config.reconciliation_enabled = False
                audit('integration.disable', 'didlogic')
            elif action == 'sync':
                if not config.enabled and not config.reconciliation_enabled:
                    raise ValueError('Enable synchronisation first.')
                config.sync_requested = True
                audit('integration.request_sync', 'didlogic')
            else:
                raise ValueError('Unknown integration action.')
            db.session.commit()
            flash('Integration updated.' if action != 'test' else config.last_test_message)
            return redirect(url_for('admin.integration'))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            error = 'Settings changed in another session. Reload and try again.' if isinstance(exc, IntegrityError) else str(exc)
            if request.form.get('action') == 'test' and config:
                config.last_test_at, config.last_test_ok, config.enabled = utcnow(), False, False
                config.reconciliation_enabled = False
                config.last_test_message, config.preview_rows = error[:300], None
                db.session.commit()
    return render_template('admin/integrations.html', title='DID Logic integration', integration=config, error=error), (400 if error else 200)


@admin.route('/reconciliation', methods=['GET', 'POST'])
def reconciliation():
    from .reconciliation import approve
    error = None
    if request.method == 'POST':
        try:
            if request.form.get('reviewed') != 'on':
                raise ValueError('Confirm that you reviewed the provider totals and client pricing.')
            count = approve(request.form.get('partition_id', type=int), request.form.get('digest', ''), current_user.id)
            flash(f'Accepted snapshot. {count} new calls charged. Repeated acceptance does not charge again.')
            return redirect(url_for('admin.reconciliation'))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            error = str(exc) if isinstance(exc, ValueError) else 'Concurrent update detected. Refresh and try again.'
    pagination = db.paginate(select(CdrPartition).order_by(CdrPartition.day.desc(), CdrPartition.id.desc()),
                             per_page=20, error_out=False)
    return render_template('admin/reconciliation.html', title='CDR reconciliation', pagination=pagination, error=error), (400 if error else 200)


@admin.get('/calls')
def calls():
    per_page = request.args.get('per_page', 10, type=int)
    if per_page not in (10, 25, 50, 100):
        abort(400)
    tenant_id = request.args.get('tenant_id', type=int)
    statement = select(Call).order_by(Call.started_at.desc(), Call.id.desc())
    if tenant_id:
        statement = statement.where(Call.tenant_id == tenant_id)
    pagination = db.paginate(statement, per_page=per_page, error_out=False)
    tenants = db.session.scalars(select(Tenant).order_by(Tenant.name)).all()
    return render_template('admin/calls.html', title='Wholesale & retail', pagination=pagination,
                           tenants=tenants, tenant_names={t.id: t.name for t in tenants}, selected_tenant=tenant_id, per_page=per_page)


@admin.route('/wholesale-rates', methods=['GET', 'POST'])
def wholesale_rates():
    current = db.session.scalar(select(ProviderRate).order_by(ProviderRate.created_at.desc(), ProviderRate.id.desc()).limit(1))
    error = None
    if request.method == 'POST':
        try:
            increment = int(request.form.get('billing_increment', '1'))
            if increment not in (1, 6, 30, 60):
                raise ValueError('Choose a supported billing increment.')
            values = {'landline_rate': amount('landline_rate'), 'mobile_rate': amount('mobile_rate'),
                      'fallback_rate': amount('fallback_rate', optional=True), 'billing_increment': increment}
            record = ProviderRate(**values)
            db.session.add(record)
            db.session.flush()
            audit('provider.rates.save', record.id, {key: str(value) for key, value in values.items()})
            db.session.commit()
            flash('Wholesale rates saved for calls starting from now. Existing costs and customer charges are unchanged.')
            return redirect(url_for('admin.wholesale_rates'))
        except ValueError as exc:
            db.session.rollback()
            error = str(exc)
    source = request.form if error else ({key: getattr(current, key) for key in
        ('landline_rate', 'mobile_rate', 'fallback_rate', 'billing_increment')} if current else {})
    return render_template('admin/wholesale_rates.html', title='Provider wholesale rates', source=source,
                           current=current, error=error), (400 if error else 200)


@admin.route('/reconciliation/<int:partition_id>/csv', methods=['GET', 'POST'])
def reconcile_csv(partition_id):
    from .csv_reconciliation import compare_csv, MAX_BYTES
    partition = db.get_or_404(CdrPartition, partition_id)
    error = None
    if request.method == 'POST':
        try:
            if request.form.get('complete_export') != 'on':
                raise ValueError('Confirm this export covers the complete selected UTC day and SIP account.')
            upload = request.files.get('csv_file')
            if not upload:
                raise ValueError('Choose a provider CSV file.')
            offset = request.form.get('offset_minutes', type=int)
            if offset is None:
                raise ValueError('Choose the UTC offset used by the export.')
            partition = db.session.execute(select(CdrPartition).where(CdrPartition.id == partition_id)
                .with_for_update().execution_options(populate_existing=True)).scalar_one()
            report = compare_csv(upload.read(MAX_BYTES + 1), partition, offset)
            result = CsvReconciliation(partition_id=partition.id, actor_id=current_user.id, report=report)
            db.session.add(result)
            db.session.flush()
            audit('reconciliation.csv', str(result.id), {'partition_id': partition.id,
                  'matched': report['matched'], 'file_sha256': report['file_sha256'], 'snapshot_digest': partition.digest})
            db.session.commit()
            return redirect(url_for('admin.reconcile_csv', partition_id=partition.id, report_id=result.id))
        except ValueError as exc:
            db.session.rollback()
            error = str(exc)
    report_id = request.args.get('report_id', type=int)
    query = select(CsvReconciliation).where(CsvReconciliation.partition_id == partition.id)
    if report_id:
        query = query.where(CsvReconciliation.id == report_id)
    result = db.session.scalar(query.order_by(CsvReconciliation.id.desc()).limit(1))
    return render_template('admin/csv_reconciliation.html', title='Compare provider CSV', partition=partition,
                           result=result, error=error), (400 if error else 200)


@admin.get('/reconciliation/csv/<int:report_id>/download')
def reconciliation_csv_download(report_id):
    import csv
    from io import StringIO, BytesIO
    from flask import send_file
    result = db.get_or_404(CsvReconciliation, report_id)
    output = StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Source', 'Time UTC', 'Caller', 'Destination', 'Seconds', 'Wholesale USD per call', 'Occurrences'])
    for row in result.report['differences']:
        values = [row[k] for k in ('source', 'time', 'caller', 'destination', 'seconds', 'cost', 'occurrences')]
        # Spreadsheet applications must not interpret provider text as formulas.
        writer.writerow(["'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v for v in values])
    return send_file(BytesIO(output.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True,
                     download_name=f'Osumtech - Reconciliation {result.id}.csv', max_age=0)
