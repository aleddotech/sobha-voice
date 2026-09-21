#!/usr/bin/env python3
"""Existing vs missing ticket handling against data/db.json."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from tickets import load_db, lookup_tickets, matching_open_tickets


def main() -> int:
    db = load_db()
    assert db, "db.json is empty"
    sample = next(r for r in db if r.get("ticket_id", "").startswith("TK-"))
    tid = sample["ticket_id"]
    name = sample.get("resident_name") or ""

    hit = lookup_tickets(db, ticket_id=tid)
    assert hit["found"] is True and hit["handle"] == "existing", hit
    assert hit["tickets"][0]["ticket_id"] == tid

    loose = lookup_tickets(db, ticket_id=tid.replace("-", "").lower())
    assert loose["found"] is True, loose

    by_name = lookup_tickets(db, name_or_id=name.split()[0] if name else tid)
    assert by_name["found"] is True, by_name

    miss = lookup_tickets(db, ticket_id="TK-9999")
    assert miss["found"] is False and miss["handle"] == "new", miss

    none = lookup_tickets(db, ticket_id="ZZ-NOPE", name_or_id="nobody-xyz")
    assert none["found"] is False, none

    open_dup = matching_open_tickets(db, name, sample.get("issue_type") or "")
    print(json.dumps({
        "db_tickets": len(db),
        "existing_id": tid,
        "existing_name": name,
        "lookup_existing": hit["handle"],
        "lookup_missing": miss["handle"],
        "open_same_issue": [t.get("ticket_id") for t in open_dup[:3]],
    }, indent=2))
    print("OK existing ticket handled; missing ticket handled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
