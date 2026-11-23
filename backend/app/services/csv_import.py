"""Parse and validate an uploaded transactions CSV.

Expected format (header required, column names case-insensitive):

    date,description,amount
    2026-09-03,SQ *STARBUCKS #1182 COLLEGE PARK MD,-6.45

Validation is all-or-nothing: if any row is bad, CsvImportError lists the problems and
nothing should be imported. File contents are only ever treated as data.
"""

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app import config

EXPECTED_HEADER = ["date", "description", "amount"]
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y"]
MAX_DESCRIPTION_LENGTH = 255
MAX_ABS_AMOUNT = Decimal("9999999999.99")  # fits DECIMAL(12, 2)
MAX_REPORTED_ERRORS = 20


@dataclass(frozen=True)
class ParsedRow:
    """One valid transaction row from the file."""

    line: int  # line number in the file (header is line 1)
    date: date
    description: str
    amount: Decimal


@dataclass(frozen=True)
class RowError:
    """A problem with one line of the file (line is None for whole-file problems)."""

    line: int | None
    message: str


class CsvImportError(Exception):
    """Raised when a file can't be imported; carries a summary message and the row errors."""

    # Keep the summary and row errors for the API response
    def __init__(self, message: str, errors: list[RowError] | None = None):
        """Store the summary message and the per-line errors (if any)."""
        super().__init__(message)
        self.message = message
        self.errors = errors or []


# Entry point: bytes in, validated rows out
def parse_csv(data: bytes) -> list[ParsedRow]:
    """Parse and validate an uploaded CSV file; return its rows or raise CsvImportError listing every problem."""
    try:
        text = data.decode("utf-8-sig")  # tolerates the BOM Excel adds
    except UnicodeDecodeError:
        raise CsvImportError("File is not valid UTF-8 text")

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader, None)
        if header is None or [h.strip().lower() for h in header] != EXPECTED_HEADER:
            raise CsvImportError(f"Header must be exactly: {','.join(EXPECTED_HEADER)}")

        rows: list[ParsedRow] = []
        errors: list[RowError] = []
        for fields in reader:
            if not any(f.strip() for f in fields):
                continue  # ignore blank lines
            if len(rows) + len(errors) >= config.MAX_IMPORT_ROWS:
                raise CsvImportError(f"File has more than {config.MAX_IMPORT_ROWS} rows")
            try:
                rows.append(_parse_row(reader.line_num, fields))
            except ValueError as e:
                errors.append(RowError(reader.line_num, str(e)))
    except csv.Error as e:
        raise CsvImportError(f"Malformed CSV: {e}")

    if errors:
        shown = errors[:MAX_REPORTED_ERRORS]
        more = f" (showing first {len(shown)})" if len(errors) > len(shown) else ""
        raise CsvImportError(f"{len(errors)} invalid row(s){more}; nothing was imported", shown)
    if not rows:
        raise CsvImportError("File has no transactions")
    return rows


# Validate one row's columns
def _parse_row(line: int, fields: list[str]) -> ParsedRow:
    """Validate one CSV row (3 columns) and convert it to a ParsedRow; raise ValueError with a readable message if invalid."""
    if len(fields) != 3:
        raise ValueError(f"Expected 3 columns, got {len(fields)} (quote descriptions that contain commas)")
    raw_date, raw_description, raw_amount = (f.strip() for f in fields)
    return ParsedRow(line, _parse_date(raw_date), _parse_description(raw_description), _parse_amount(raw_amount))


# Validate the date column
def _parse_date(value: str) -> date:
    """Parse a YYYY-MM-DD or MM/DD/YYYY date between 1990 and 2100."""
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        if not 1990 <= d.year <= 2100:
            raise ValueError(f"Date out of range: {value!r}")
        return d
    raise ValueError(f"Invalid date {value!r} (use YYYY-MM-DD or MM/DD/YYYY)")


# Validate the description column
def _parse_description(value: str) -> str:
    """Check the description is present and at most 255 characters."""
    if not value:
        raise ValueError("Description is empty")
    if len(value) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(f"Description is longer than {MAX_DESCRIPTION_LENGTH} characters")
    return value


# Validate the amount column
def _parse_amount(value: str) -> Decimal:
    """Parse an amount like -6.45, $1,234.50 or $-4 into a 2-decimal Decimal that fits the database column."""
    cleaned = value.replace("$", "").replace(",", "").replace(" ", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Invalid amount {value!r}")
    if not amount.is_finite():
        raise ValueError(f"Invalid amount {value!r}")
    if amount.as_tuple().exponent < -2:
        raise ValueError(f"Amount has more than 2 decimal places: {value!r}")
    if abs(amount) > MAX_ABS_AMOUNT:
        raise ValueError(f"Amount too large: {value!r}")
    return amount.quantize(Decimal("0.01"))


# Dedup fingerprints for each row
def import_hashes(rows: list[ParsedRow]) -> list[str]:
    """A stable fingerprint per row, used to skip rows that were already imported.

    Includes how many identical rows came before it in the file, so two real identical
    charges in one file are both kept, while re-importing the same file adds nothing.
    """
    seen: dict[tuple[str, str, str], int] = {}
    hashes = []
    for r in rows:
        key = (r.date.isoformat(), f"{r.amount:.2f}", " ".join(r.description.upper().split()))
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        hashes.append(hashlib.sha256("|".join([*key, str(occurrence)]).encode()).hexdigest())
    return hashes
