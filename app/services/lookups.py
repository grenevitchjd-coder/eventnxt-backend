# eventnxt-backend: app/services/lookups.py
"""
Case-insensitive EXACT match, for lookups where the input is an identifier
(a promo code, an email address, a token) rather than a search phrase.

Why this exists: SQLAlchemy's `.ilike(value)` runs value through SQL's
LIKE, where `%` and `_` are wildcards — `Column.ilike(user_input)` with
UNESCAPED user input lets a value like "%" match every row. For an
identifier lookup that's a real bug, not just a style nit: a buyer typing
"%" into the promo-code box at checkout could get an arbitrary promo
code's discount applied to a paid order. `ci_equals` does a plain
case-insensitive EQUALITY comparison instead, so there is no pattern
matching to exploit — the safe default for anything meant to match one
specific row by an exact value a person typed.

Deliberately not used for genuine substring search (e.g. an admin
free-text search box over order buyer name/email) — there `ilike` with an
explicitly-wrapped `%term%` is the intended behavior, not a bug.
"""

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement


def ci_equals(column: ColumnElement, value: str) -> ColumnElement:
    """`column`, compared case-insensitively, equals `value` exactly.
    No `%`/`_` in `value` is ever treated as a wildcard."""
    return func.lower(column) == value.lower()