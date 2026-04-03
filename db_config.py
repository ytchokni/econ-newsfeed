import os
import re
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV_VARS = ['DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'OPENAI_API_KEY']

_missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
if _missing:
    raise EnvironmentError(
        f"Missing required environment variables: {', '.join(_missing)}\n"
        "Copy .env.example to .env and fill in all required values."
    )

_DB_NAME = os.environ['DB_NAME']
_DB_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$')
if not _DB_NAME_RE.match(_DB_NAME):
    raise EnvironmentError(
        f"DB_NAME '{_DB_NAME}' is invalid. "
        "Must match ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$"
    )

# MySQL configuration
db_config = {
    'host': os.environ['DB_HOST'],
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ['DB_USER'],
    'password': os.environ['DB_PASSWORD'],
    'database': _DB_NAME,
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci',
}

# Optional SSL for managed cloud databases (Cloud SQL, RDS, PlanetScale)
_ssl_ca = os.environ.get('DB_SSL_CA')
if _ssl_ca:
    db_config['ssl_ca'] = _ssl_ca
    db_config['ssl_verify_cert'] = True
