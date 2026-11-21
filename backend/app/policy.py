"""Platform policy fixed in Phase 0 (see docs/DECISIONS.md).

These values are hardcoded on purpose: they are part of the product contract,
not deployment knobs. Changing one is a reviewed code change, never an
environment variable.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, NamedTuple

# --- Scope -------------------------------------------------------------------

ENTITY_TYPES: Final = ("customer", "order")
DESTINATION_TYPES: Final = ("mock", "stripe")

# --- Upload limits -----------------------------------------------------------

MAX_UPLOAD_BYTES: Final = 10 * 1024 * 1024  # 10 MiB hard limit on the file itself
# Allowance for multipart framing on top of the file when checking the raw
# request body. The handler re-checks the file bytes against MAX_UPLOAD_BYTES.
MULTIPART_OVERHEAD_BYTES: Final = 64 * 1024
MAX_COLUMNS: Final = 200
MAX_ROWS: Final = 100_000
MAX_REPORTED_PARSE_ERRORS: Final = 200
MAX_REPORTED_SUSPICIOUS_CELLS: Final = 50

ALLOWED_FILE_EXTENSIONS: Final = MappingProxyType({".csv": "csv", ".json": "json"})

# --- Money -------------------------------------------------------------------

# ISO 4217 code -> number of minor-unit digits. An amount with more fractional
# digits than its currency allows is rejected, never rounded.
SUPPORTED_CURRENCIES: Final = MappingProxyType(
    {"USD": 2, "EUR": 2, "GBP": 2, "INR": 2, "JPY": 0, "CAD": 2, "AUD": 2}
)

# --- Dates -------------------------------------------------------------------


class DateFormat(NamedTuple):
    name: str
    shape: str
    example: str


DATE_FORMATS: Final = MappingProxyType(
    {
        f.name: f
        for f in (
            DateFormat("ISO_DATE", "YYYY-MM-DD", "2026-04-03"),
            DateFormat("ISO_DATETIME", "YYYY-MM-DDTHH:MM:SS[.ffffff][Z|+HH:MM]", "2026-04-03T14:05:00Z"),
            DateFormat("YMD_SLASH", "YYYY/MM/DD", "2026/04/03"),
            DateFormat("DMY_SLASH", "DD/MM/YYYY", "03/04/2026"),
            DateFormat("MDY_SLASH", "MM/DD/YYYY", "04/03/2026"),
            DateFormat("DMY_DASH", "DD-MM-YYYY", "03-04-2026"),
            DateFormat("MDY_DASH", "MM-DD-YYYY", "04-03-2026"),
            DateFormat("D_MON_YYYY", "D Mon YYYY", "3 Apr 2026"),
            DateFormat("MON_D_YYYY", "Mon D, YYYY", "Apr 3, 2026"),
        )
    }
)

# If a column parses cleanly under more than one member of a group, the format
# is ambiguous and the operator must choose. The system never picks.
AMBIGUOUS_DATE_FORMAT_GROUPS: Final = (
    frozenset({"DMY_SLASH", "MDY_SLASH"}),
    frozenset({"DMY_DASH", "MDY_DASH"}),
)


def describe() -> dict:
    """Policy as plain data, served by GET /api/policy so the UI never hardcodes it."""
    return {
        "entity_types": list(ENTITY_TYPES),
        "destination_types": list(DESTINATION_TYPES),
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_columns": MAX_COLUMNS,
        "max_rows": MAX_ROWS,
        "allowed_file_extensions": sorted(ALLOWED_FILE_EXTENSIONS),
        "currencies": dict(SUPPORTED_CURRENCIES),
        "date_formats": [f._asdict() for f in DATE_FORMATS.values()],
        "ambiguous_date_format_groups": [sorted(g) for g in AMBIGUOUS_DATE_FORMAT_GROUPS],
    }
