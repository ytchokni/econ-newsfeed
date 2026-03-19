"""Validate .env file has all required variables with correct constraints.

Reads .env via dotenv_values() (no side effects on os.environ).
Exits 0 if valid, 1 with descriptive errors if not.
"""
import re
import sys
from dotenv import dotenv_values

REQUIRED_VARS = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "OPENAI_API_KEY", "SCRAPE_API_KEY"]
DB_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def main() -> int:
    values = dotenv_values(".env")
    if not values:
        print("ERROR: .env file not found or empty. Copy .env.example to .env and fill in values.")
        return 1

    errors = []
    for var in REQUIRED_VARS:
        if not values.get(var):
            errors.append(f"  Missing or empty: {var}")

    db_name = values.get("DB_NAME", "")
    if db_name and not DB_NAME_RE.match(db_name):
        errors.append(f"  Invalid DB_NAME '{db_name}': must match ^[a-zA-Z_][a-zA-Z0-9_]{{0,63}}$")

    scrape_key = values.get("SCRAPE_API_KEY", "")
    if scrape_key and len(scrape_key) < 16:
        errors.append(f"  SCRAPE_API_KEY is too short ({len(scrape_key)} chars, min 16). "
                      "The default 'changeme' from .env.example is not valid.")

    if errors:
        print("ENV VALIDATION FAILED:")
        for e in errors:
            print(e)
        return 1

    print("ENV OK: all required variables present and valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
