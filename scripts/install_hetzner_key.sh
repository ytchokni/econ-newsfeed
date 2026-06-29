#!/usr/bin/env bash
# Materialize the Hetzner SSH private key from the HETZNER_SSH_KEY secret into a
# valid OpenSSH key file, robust to how the secret is stored.
#
# Why this exists: a naive `echo "$HETZNER_SSH_KEY" > ~/.ssh/hetzner` fails with
# `Load key ... error in libcrypto` whenever the secret is NOT a verbatim,
# newline-preserving PEM — which is the common case, because pasting a multi-line
# key into a single-line secret field either truncates it or strips newlines.
#
# This script auto-detects and handles all three shapes:
#   1. Proper multi-line PEM (BEGIN/END + real newlines)  -> written as-is
#   2. PEM with literal "\n" sequences instead of newlines  -> newlines restored
#   3. base64 of the raw OpenSSH key body (no PEM armor)     -> wrapped in armor
#
# Usage:
#   bash scripts/install_hetzner_key.sh            # writes ~/.ssh/hetzner
#   KEY_PATH=/tmp/k bash scripts/install_hetzner_key.sh
# Then:
#   ssh -i ~/.ssh/hetzner -o IdentitiesOnly=yes root@167.233.132.217
set -euo pipefail

KEY_PATH="${KEY_PATH:-$HOME/.ssh/hetzner}"

if [ -z "${HETZNER_SSH_KEY:-}" ]; then
    echo "ERROR: HETZNER_SSH_KEY is not set in the environment." >&2
    exit 1
fi

mkdir -p "$(dirname "$KEY_PATH")"
chmod 700 "$(dirname "$KEY_PATH")" 2>/dev/null || true

KEY_PATH="$KEY_PATH" python3 - <<'PY'
import base64
import os
import sys
import textwrap

raw = os.environ["HETZNER_SSH_KEY"]
path = os.environ["KEY_PATH"]

def write(pem: str) -> None:
    if not pem.endswith("\n"):
        pem += "\n"
    with open(path, "w") as f:
        f.write(pem)
    os.chmod(path, 0o600)

if "-----BEGIN" in raw:
    # PEM provided directly. Restore newlines if they were flattened to "\n".
    pem = raw.replace("\\n", "\n") if "\n" not in raw and "\\n" in raw else raw
    write(pem.strip())
else:
    # Assume base64 of the raw OpenSSH key body (no armor). Validate it really
    # is an OpenSSH key before wrapping, to fail loudly on a truncated secret.
    try:
        decoded = base64.b64decode(raw.strip(), validate=False)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"ERROR: HETZNER_SSH_KEY is neither PEM nor valid base64: {e}")
    if not decoded.startswith(b"openssh-key-v1"):
        sys.exit(
            "ERROR: HETZNER_SSH_KEY looks truncated/malformed "
            "(base64 body does not start with 'openssh-key-v1'). "
            "Re-enter the FULL key, base64-encoded (`base64 -w0 ~/.ssh/hetzner`)."
        )
    body = "\n".join(textwrap.wrap(raw.strip(), 70))
    write(f"-----BEGIN OPENSSH PRIVATE KEY-----\n{body}\n-----END OPENSSH PRIVATE KEY-----")

print(f"Wrote {path}")
PY

# Validate the key parses (exercises libcrypto, prints the public key on success).
if ssh-keygen -y -f "$KEY_PATH" >/dev/null 2>&1; then
    echo "Key validated OK: $KEY_PATH"
else
    echo "ERROR: ssh-keygen could not parse $KEY_PATH — the secret is malformed." >&2
    exit 1
fi
