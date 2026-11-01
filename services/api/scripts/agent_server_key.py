#!/usr/bin/env python3
"""Show or replace KEEN's agent identity key (run inside keen-api).

The identity key's fingerprint ends every enrolment code and bootstrap key,
and every keen-agent pins it. Replace it only if its private key may have been
exposed: afterwards every agent must enrol again with a new code (Issue
enrolment code on its card), and every enrolment profile needs a new
bootstrap key.

    python scripts/agent_server_key.py
    python scripts/agent_server_key.py --reset --confirm-fingerprint=<fingerprint>
"""

import argparse
import sys

from app.agents import server_keys
from app.db.session import SessionLocal


def attached(argv):
    """Attach the value to --confirm-fingerprint. A fingerprint is base64url,
    so it can start with "-", which argparse would otherwise take for an
    option."""
    out, rest = [], list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--confirm-fingerprint" and rest:
            arg += "=" + rest.pop(0)
        out.append(arg)
    return out


def main(argv=None, session=SessionLocal):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--confirm-fingerprint",
        metavar="FINGERPRINT",
        help="the current fingerprint, required with --reset",
    )
    args = parser.parse_args(attached(sys.argv[1:] if argv is None else argv))
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
