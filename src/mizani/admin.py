"""`mizani-admin`: account tasks on the server itself, for hospital IT.

  docker compose run --rm --no-deps app mizani-admin create-admin <user name> "<full name>"
  docker compose run --rm --no-deps app mizani-admin reset-password <user name>

Each prints a one-time password, which the person must change at their first sign-in. Use it for
the first admin when you prefer a command to the line in the server log, or when every admin is
locked out. Everything else (nurses, doctors, the chief flag) is done in the page, Admin › Users.
Each change goes into the audit log under "mizani-admin (server console)".
"""

import argparse
import sys

from mizani import auth
from mizani.db import db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizani-admin", description=__doc__.split("\n\n")[0])
    tasks = parser.add_subparsers(dest="task", required=True)
    create = tasks.add_parser("create-admin", help="a new admin account")
    create.add_argument("username")
    create.add_argument("full_name", nargs="?", help="as records will show it (default: the name)")
    reset = tasks.add_parser("reset-password", help="a new one-time password for any account")
    reset.add_argument("username")
    args = parser.parse_args(argv)

    con = db()
    try:
        if args.task == "create-admin":
            account, otp = auth.create_account(
                con, auth.CONSOLE, args.username, args.full_name or args.username, "admin"
            )
        else:
            account = auth.find(con, args.username)
            if account is None:
                raise auth.AuthError(f"No account {args.username}.")
            otp = auth.reset_password(con, auth.CONSOLE, account.id)
            if not account.active:
                print(f"Note: {account.username} is deactivated; an admin can reactivate it.")
    except auth.AuthError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"{account.full_name} ({account.username}, {account.role})")
    print(f"One-time password: {otp}")
    print("It must be changed at the first sign-in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
