"""Create or replace the single login account.

uv run python app/create_credentials.py
uv run python app/create_credentials.py --username admin --password s3cret
"""

import argparse
import getpass
import sys

from app.core.credentials import write_credential

MIN_PASSWORD_LENGTH = 8


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username")
    parser.add_argument(
        "--password",
        help="Prompted for interactively when omitted, which keeps it out of your shell history.",
    )
    args = parser.parse_args()

    username = args.username or input("Username: ").strip()
    if not username:
        print("Username cannot be empty.", file=sys.stderr)
        return 1

    password = args.password
    if password is None:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Password (again): "):
            print("Passwords did not match.", file=sys.stderr)
            return 1
    if len(password) < MIN_PASSWORD_LENGTH:
        print(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            file=sys.stderr,
        )
        return 1

    path = write_credential(username, password)
    print(f"Wrote account {username!r} to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
