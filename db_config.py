import os
import sys
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV_VARS = ['DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'OPENAI_API_KEY']

_missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
if _missing:
    print(
        f"ERROR: Missing required environment variables: {', '.join(_missing)}\n"
        "Copy .env.example to .env and fill in all required values.",
        file=sys.stderr,
    )
    sys.exit(1)

# MySQL configuration
db_config = {
    'host': os.environ['DB_HOST'],
    'user': os.environ['DB_USER'],
    'password': os.environ['DB_PASSWORD'],
    'database': os.environ['DB_NAME'],
}
