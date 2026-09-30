"""Create private first-install configuration without printing secret values."""
import base64
import os
from pathlib import Path
import secrets


def main():
    root = Path(__file__).resolve().parent.parent
    target = root / '.env'
    template = (root / '.env.example').read_text(encoding='utf-8')
    values = {'SECRET_KEY': secrets.token_hex(32),
              'POSTGRES_PASSWORD': secrets.token_hex(32),
              'ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}
    for name, value in values.items():
        template = template.replace(f'{name}=\n', f'{name}={value}\n')
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit('.env already exists; preserved without modification.')
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
        output.write(template)
    print('Created private .env with generated secrets. Live CDR sync is disabled.')
    print('Back up this file securely; never commit it or paste its contents into chat.')


if __name__ == '__main__':
    main()
