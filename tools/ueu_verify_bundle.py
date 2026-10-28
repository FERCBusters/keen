#!/usr/bin/env python3
"""Check a KEEN UEU verification bundle without access to KEEN.

Each record ends in one of three states:

``ok``
    The stored envelope hashes to its evidence hash, the batch manifest lists
    that evidence ID and hash, the manifest names the record's producer and
    key, and its Ed25519 signature verifies with that producer's key.
``unverified``
    Nothing contradicts the record, but something needed to prove it is
    absent: the producer did not sign the batch, the key is not in the
    bundle, KEEN changed the envelope before storing it (custody changes),
    or the ``cryptography`` package is not installed.
``FAIL``
    A check failed: the record has been altered or does not belong to the
    batch, producer or key it claims.

Records from one producer stream must also form a hash chain where they are
consecutive.

Keys are taken from the bundle unless you pin them. A bundle can be rewritten
whole, keys included, so pin the keys you obtained independently (for example
by running ``keen-agent -command key`` on the agent's host, which prints its
current key in this form) with ``--key KEY_ID=PUBLIC_KEY`` or ``--keys FILE``
(a JSON object mapping key IDs to unpadded base64url public keys). Pin every
key the agent held during the bundle's period: renewal replaces the key, and
with pins a record signed by any other key fails.

Requires Python 3.9+ and the ``cryptography`` package for signatures.
Exit status: 0 when every record is ok, 2 when none failed but some are
unverified, 1 when any failed or the bundle cannot be read.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import zipfile

MAX_MEMBER = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024


class Reader:
    """Reads bundle members within a per-member and a total size bound."""

    def __init__(self, zf: zipfile.ZipFile):
        self.zf = zf
        self.total = 0

    def __call__(self, name: str) -> bytes:
        info = self.zf.getinfo(name)
        if info.file_size > MAX_MEMBER:
            raise ValueError(shown(name) + " is too large")
        self.total += info.file_size
        if self.total > MAX_TOTAL:
            raise ValueError("bundle is too large to check")
        data = self.zf.read(info)
        if len(data) > MAX_MEMBER:
            raise ValueError(shown(name) + " is too large")
        return data


def shown(value) -> str:
    """Bundle text made safe for a terminal: control characters escaped."""
    text = str(value)
    return "".join(
        c if c.isprintable() else c.encode("unicode_escape").decode("ascii")
        for c in text
    )


def b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verifier():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError:
        return None

    def verify(public_key: str, message: bytes, signature: str) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(b64url(public_key)).verify(
                b64url(signature), message
            )
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False

    return verify


def _signature(record, batch, keys, pinned, verify, problems, gaps):
    """Check the record's batch manifest and signature."""
    if record.get("verification") == "unsigned" and not (batch or {}).get("signature"):
        gaps.append("the producer did not sign this batch")
        return
    if not batch or not batch.get("manifest") or not batch.get("signature"):
        problems.append("batch manifest or signature missing from bundle")
        return
    try:
        manifest = json.loads(batch["manifest"])
    except ValueError:
        problems.append("batch manifest is not JSON")
        return
    entry = {
        "evidence_hash": record["evidence_hash"],
        "evidence_id": record["evidence_id"],
    }
    if entry not in manifest.get("evidence", []):
        problems.append("batch manifest does not list this evidence")
    if manifest.get("producer") != record.get("agent_id"):
        problems.append("batch manifest names another producer")
    key_id = batch.get("key_id")
    if manifest.get("key_id") != key_id:
        problems.append("batch manifest names another key")
    if pinned is not None:
        public_key = pinned.get(key_id)
        if public_key is None:
            problems.append("signed by a key you did not pin")
            return
    else:
        key = keys.get(key_id)
        if key is None:
            gaps.append("signed by a key not in this bundle")
            return
        if key.get("agent_id") != record.get("agent_id"):
            problems.append("signing key belongs to another producer")
            return
        public_key = key.get("public_key", "")
        if key.get("state") == "revoked":
            gaps.append("signed by a key since revoked")
    if verify is None:
        gaps.append("install 'cryptography' to check signatures")
    elif not verify(public_key, batch["manifest"].encode(), batch["signature"]):
        problems.append("batch signature does not verify")


def check(path: str, pinned: dict | None = None) -> int:
    verify = verifier()
    failed = unverified = 0
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        print("cannot read bundle: " + shown(exc))
        return 1
    with zf:
        read = Reader(zf)
        try:
            manifest = json.loads(read("manifest.json"))
        except (KeyError, ValueError) as exc:
            print("cannot read manifest.json: " + shown(exc))
            return 1
        if manifest.get("format") != "keen-ueu-bundle-1":
            print("unsupported bundle format")
            return 1
        batches, keys = {}, {}
        try:
            for name in zf.namelist():
                if name.startswith("batches/") and name.endswith(".json"):
                    batch = json.loads(read(name))
                    batches[batch["id"]] = batch
                elif name.startswith("keys/") and name.endswith(".json"):
                    key = json.loads(read(name))
                    keys[key["key_id"]] = key
        except (KeyError, ValueError, TypeError) as exc:
            print("cannot read bundle: " + shown(exc))
            return 1
        if pinned is None:
            print("· keys taken from the bundle; pin them with --key or --keys")
        last = {}
        records = sorted(
            manifest.get("records", []),
            key=lambda r: (str(r.get("agent_id") or ""), r.get("sequence") or 0),
        )
        for record in records:
            problems, gaps = [], []
            try:
                data = read(record["file"])
                envelope = json.loads(data)
            except (KeyError, ValueError) as exc:
                problems.append("envelope unreadable: " + shown(exc))
                data, envelope = b"", {}
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
            changes = (record.get("custody") or {}).get("changes") or []
            if changes:
                gaps.append(
                    "KEEN changed the envelope before storing it (%d changes: %s), "
                    "so its content cannot be checked against the signed hash"
                    % (
                        len(changes),
                        ", ".join(
                            shown(c.get("path", "")) + " " + shown(c.get("reason", ""))
                            for c in changes
                        ),
                    )
                )
            elif digest != record.get("evidence_hash"):
                problems.append("stored envelope does not match its evidence hash")
            if not problems:
                _signature(
                    record,
                    batches.get(record.get("batch_id")),
                    keys,
                    pinned,
                    verify,
                    problems,
                    gaps,
                )
            integrity = (
                envelope.get("integrity", {}) if isinstance(envelope, dict) else {}
            )
            stream = integrity.get("stream")
            sequence = integrity.get("sequence")
            previous = last.get(stream)
            if previous and isinstance(sequence, int) and sequence == previous[0] + 1:
                if integrity.get("previous_evidence_hash") != previous[1]:
                    problems.append("does not follow the previous record's hash")
            if isinstance(sequence, int):
                last[stream] = (sequence, record.get("evidence_hash"))
            if problems:
                failed += 1
                status = "FAIL"
            elif gaps:
                unverified += 1
                status = "unverified"
            else:
                status = "ok"
            print(
                f"{status:10} {shown(record.get('evidence_id'))} "
                f"{shown(record.get('event_type'))}"
            )
            for text in problems:
                print("           - " + text)
            for text in gaps:
                print("           · " + text)
    print(f"{len(records)} records, {failed} failed, {unverified} unverified")
    if failed:
        return 1
    return 2 if unverified else 0


def pins(args) -> dict | None:
    pinned = {}
    if args.keys:
        with open(args.keys, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise SystemExit("--keys must hold a JSON object of key ID to key")
        pinned.update({str(k): str(v) for k, v in loaded.items()})
    for item in args.key or []:
        key_id, sep, public_key = item.rpartition("=")
        if not sep or not key_id or not public_key:
            raise SystemExit("--key takes KEY_ID=PUBLIC_KEY")
        pinned[key_id] = public_key
    return pinned or None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", help="bundle ZIP downloaded from KEEN")
    parser.add_argument(
        "--key",
        action="append",
        metavar="KEY_ID=PUBLIC_KEY",
        help="trust only this key for its key ID (repeatable)",
    )
    parser.add_argument(
        "--keys", metavar="FILE", help="JSON object of trusted key IDs to keys"
    )
    args = parser.parse_args(argv)
    return check(args.bundle, pins(args))


if __name__ == "__main__":
    sys.exit(main())
