"""Schema fingerprint: identifies a file's column set, independent of column order.

Exact column names are used (after header whitespace is stripped), because
mappings reference exact source field names: ``Email`` and ``email`` are
different schemas. The version prefix lets the definition change later without
colliding with stored fingerprints.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

FINGERPRINT_VERSION = "sfp1"


def schema_fingerprint(columns: Iterable[str]) -> str:
    canonical = json.dumps(sorted(columns), ensure_ascii=False, separators=(",", ":"))
    return f"{FINGERPRINT_VERSION}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
