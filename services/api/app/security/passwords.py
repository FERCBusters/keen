from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from app.core.config import settings

# -----------------------------------------------------------------------------
# Password hashing and the rules a new password meets
# -----------------------------------------------------------------------------
#
# Format:
#   pbkdf2_sha256$<iterations>$<salt_b64url>$<hash_b64url>
#
# Using PBKDF2-HMAC-SHA256 from Python's stdlib keeps the runtime dependency
# surface small while providing a modern, slow password hash.


_ALGO = "pbkdf2_sha256"
# OWASP's work factor for PBKDF2-HMAC-SHA256. A hash stored with fewer
# iterations is verified at this cost too, and replaced at the account's next
# correct password (needs_rehash).
_DEFAULT_ITERS = 600_000
_SALT_BYTES = 16
# The sign-in form accepts this many characters, so a longer password could be
# set and never typed.
MAX_PASSWORD_LENGTH = 1024

# Passwords that guessing lists try first. A new password is compared without
# regard to case, without the digits and punctuation around it, and with and
# without the spaces and punctuation inside it, so "Password123!",
# "passwordpassword" and "pass-word pass-word" are refused with "password".
_COMMON_PASSWORDS = frozenset(
    """
    1q2w3e 1q2w3e4r 1q2w3e4r5t 1qaz2wsx 1qaz2wsx3edc a1b2c3d4 abc abcd access
    admin administrator baseball batman changeme changethis computer
    correcthorsebatterystaple default dragon football freedom guest hello
    iloveyou internet jennifer keen keenadmin letmein letmeinnow login master
    michael monkey mypassword newpassword p@ssw0rd p@ssword pass passphrase
    passw0rd password princess qwerty qwertyuiop root secret security shadow
    starwars sunshine superman temp temporary test testing thequickbrownfox
    trustno1 welcome whatever zaq12wsx
    """.split()
)
_AROUND = "0123456789 !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
# Runs a keyboard or a count gives without thought, each long enough to hold
# the longest password.
_RUNS = tuple(
    run * (MAX_PASSWORD_LENGTH // len(run) + 2)
    for sequence in (
        "0123456789",
        "abcdefghijklmnopqrstuvwxyz",
        "qwertyuiopasdfghjklzxcvbnm",
    )
    for run in (sequence, sequence[::-1])
)
_FEWEST_DISTINCT = 5


@dataclass(frozen=True)
class ParsedHash:
    algo: str
    iterations: int
    salt: bytes
    digest: bytes


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _pbkdf2_sha256(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations)
    )


def _parse_hash(password_hash: str) -> ParsedHash | None:
    parts = str(password_hash).split("$")
    if len(parts) != 4:
        return None
    algo, iters_s, salt_s, digest_s = parts
    if algo != _ALGO:
        return None
    try:
        iters = int(iters_s)
        salt = _b64u_decode(salt_s)
        digest = _b64u_decode(digest_s)
    except Exception:
        return None
    if iters <= 0 or not salt or not digest:
        return None
    return ParsedHash(algo=algo, iterations=iters, salt=salt, digest=digest)


def _made_of(text: str, word: str) -> bool:
    """Whether ``text`` is ``word`` written one or more times."""
    return bool(text) and not text.replace(word, "")


def new_password_problem(password: str, *, username: str = "") -> str | None:
    """Why ``password`` may not become an account's password, or None.

    NIST SP 800-63B-4 section 3.1.1.2: a length a password needs as an
    account's only factor (KEEN_PASSWORD_MIN_LENGTH), no composition rules,
    and a comparison with passwords that are commonly used or expected here.
    Each code point counts as one character.
    """
    minimum = settings.password_min_length
    if len(password) < minimum:
        return f"Password must be at least {minimum} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters"
    folded = password.casefold()
    if len(set(folded)) < _FEWEST_DISTINCT:
        return "Password repeats too few characters; choose a longer phrase"
    if any(folded in run for run in _RUNS):
        return "Password is a keyboard or counting sequence; choose a phrase"
    core = folded.strip(_AROUND)
    joined = "".join(c for c in core if c.isalnum())
    if any(
        _made_of(core, word) or _made_of(joined, word) for word in _COMMON_PASSWORDS
    ):
        return "Password is too common; choose a phrase of several unrelated words"
    name = username.strip().casefold()
    if len(name) >= 4 and name in folded:
        return "Password must not contain the username"
    return None


def derive_password_hash(password: str) -> str:
    """Hash ``password`` at the current work factor, without judging it.

    For a password an account already holds (a rehash at sign-in) or one no
    person chose. A new password goes through hash_password.
    """
    if not password:
        raise ValueError("Password is required")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _pbkdf2_sha256(password, salt, _DEFAULT_ITERS)
    return f"{_ALGO}${_DEFAULT_ITERS}${_b64u_encode(salt)}${_b64u_encode(digest)}"


def hash_password(password: str, *, username: str = "") -> str:
    """Hash a new password for ``username``, refusing one the rules do not allow."""
    problem = new_password_problem(password or "", username=username)
    if problem:
        raise ValueError(problem)
    return derive_password_hash(password)


def needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash was made with fewer iterations than new ones are."""
    parsed = _parse_hash(password_hash)
    return parsed is not None and parsed.iterations < _DEFAULT_ITERS


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    parsed = _parse_hash(password_hash)
    if not parsed:
        return False
    try:
        calc = _pbkdf2_sha256(password, parsed.salt, parsed.iterations)
        if parsed.iterations < _DEFAULT_ITERS:
            # A hash from before the work factor rose costs the same to refuse
            # as a current one, so its age shows nothing about the account.
            _pbkdf2_sha256(password, parsed.salt, _DEFAULT_ITERS - parsed.iterations)
    except Exception:
        return False
    return hmac.compare_digest(calc, parsed.digest)
