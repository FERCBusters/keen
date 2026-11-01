#!/usr/bin/env python3
"""Show or replace KEEN's agent identity key (run inside keen-api).

The identity key's fingerprint ends every enrolment code and bootstrap key,
and every keen-agent pins it. Replace it only if its private key may have been
exposed: afterwards every agent must enrol again with a new code (Rotate on
its card), and every enrolment profile needs a new bootstrap key.
"""

import argparse

from app.agents import server_keys
from app.db.session import SessionLocal


def main(argv=None, session=SessionLocal):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--confirm-fingerprint",
        help="the current fingerprint, required with --reset",
    )
    args = parser.parse_args(argv)
    with session() as db:
        current = server_keys.current_fingerprint(db)
        if not args.reset:
            print(
                current
                or "KEEN has no agent identity key yet; it is made when the "
                "first agent credential is issued."
            )
            return
        if current is None or args.confirm_fingerprint != current:
            parser.error("--confirm-fingerprint must match the current fingerprint")
        replacement = server_keys.reset_identity(db)
        db.commit()
    print(
        "New identity fingerprint: "
        + replacement.fingerprint
        + ". Restart the API and worker so no process keeps the old keys. Every "
        "agent must enrol again with a new code, and every enrolment profile "
        "needs a new bootstrap key."
    )


if __name__ == "__main__":
    main()
