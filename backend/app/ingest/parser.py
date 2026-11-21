"""Strict, deterministic parsing of uploaded CSV and JSON files.

Contract:

* Every cell comes out as ``str`` or ``None``. No float is ever created: JSON
  number literals are captured as their exact source text, so ``19.990`` stays
  ``"19.990"`` until the registry's ``parse_decimal`` handles it.
* A file either parses completely (possibly with warnings) or is rejected with
  problems that point at a physical line and, where it applies, a data row.
  Callers never receive rows from a file that had errors.
* Cells are data. Nothing here interprets their content; text that looks like
  an instruction is only flagged (see ``injection_scan``).
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from app import policy
from app.ingest import injection_scan

Cell = str | None
Row = dict[str, Cell]


@dataclass(frozen=True)
class ParseIssue:
    code: str
    message: str
    line_number: int | None = None
    row_number: int | None = None  # 1-based data row (header excluded) / JSON array element
    column: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "line_number": self.line_number,
            "row_number": self.row_number,
            "column": self.column,
        }


@dataclass
class ParsedFile:
    file_format: str
    columns: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    row_line_numbers: list[int] = field(default_factory=list)
    errors: list[ParseIssue] = field(default_factory=list)
    warnings: list[ParseIssue] = field(default_factory=list)
    error_count: int = 0  # total, including errors beyond the reporting cap

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def add_error(self, code: str, message: str, *, line_number: int | None = None,
                  row_number: int | None = None, column: str | None = None) -> None:
        self.error_count += 1
        if len(self.errors) < policy.MAX_REPORTED_PARSE_ERRORS:
            self.errors.append(ParseIssue(code, message, line_number, row_number, column))

    def add_warning(self, code: str, message: str, *, line_number: int | None = None,
                    row_number: int | None = None, column: str | None = None) -> None:
        self.warnings.append(ParseIssue(code, message, line_number, row_number, column))


def detect_format(filename: str) -> str | None:
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    return policy.ALLOWED_FILE_EXTENSIONS.get(suffix)


def parse_bytes(content: bytes, file_format: str) -> ParsedFile:
    result = ParsedFile(file_format=file_format)
    text = _decode(content, result)
    if text is not None:
        if file_format == "csv":
            _parse_csv(text, result)
        elif file_format == "json":
            _parse_json(text, result)
        else:
            raise ValueError(f"unsupported file format {file_format!r}")
    if result.ok:
        _flag_suspicious_cells(result)
    else:
        # Never hand back partial data alongside errors.
        result.rows.clear()
        result.row_line_numbers.clear()
    return result


# --- Encoding ------------------------------------------------------------------------


def _decode(content: bytes, result: ParsedFile) -> str | None:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        result.add_error("invalid_encoding", "The file is UTF-16 encoded. Re-save it as UTF-8 "
                         "(in Excel: 'CSV UTF-8').", line_number=1)
        return None
    nul = content.find(b"\x00")
    if nul != -1:
        line = content.count(b"\n", 0, nul) + 1
        result.add_error("nul_byte", f"Line {line} contains a NUL byte. The file is probably binary or UTF-16 "
                         "encoded; re-save it as UTF-8 text.", line_number=line)
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        # UTF-8 never uses 0x0A inside a multi-byte sequence, so lines decode independently.
        for line_no, raw in enumerate(content.split(b"\n"), start=1):
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                result.add_error(
                    "invalid_encoding",
                    f"Line {line_no} is not valid UTF-8 (byte 0x{raw[exc.start]:02X} at position "
                    f"{exc.start + 1}). Re-save the file as UTF-8.",
                    line_number=line_no,
                )
        return None
    if text.startswith("\ufeff"):  # UTF-8 BOM, as written by Excel's "CSV UTF-8"
        text = text[1:]
    if not text.strip():
        result.add_error("empty_file", "The file is empty.")
        return None
    return text


# --- CSV -------------------------------------------------------------------------------


def _parse_csv(text: str, result: ParsedFile) -> None:
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    header: list[str] | None = None
    header_line = 0
    blank_lines: list[int] = []
    data_rows = 0
    last_record_line = 0

    while True:
        start_line = reader.line_num + 1
        try:
            record = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            _csv_structure_error(result, exc, start_line, None if header is None else data_rows + 1)
            return
        if not record or (len(record) == 1 and not record[0].strip()):
            blank_lines.append(start_line)
            continue
        if header is None:
            header_line = start_line
            header = _read_header(record, start_line, result)
            if header is None:
                return
            continue

        data_rows += 1
        last_record_line = start_line
        if data_rows > policy.MAX_ROWS:
            result.add_error("too_many_rows", f"The file has more than {policy.MAX_ROWS:,} data rows "
                             f"(line {start_line}).", line_number=start_line, row_number=data_rows)
            return
        if len(record) != len(header):
            result.add_error("field_count_mismatch",
                             _field_count_message(data_rows, start_line, len(record), len(header)),
                             line_number=start_line, row_number=data_rows)
            continue
        result.rows.append(dict(zip(header, record, strict=True)))
        result.row_line_numbers.append(start_line)

    if header is None:
        result.add_error("missing_header", "No header row found. The first non-blank line must name the columns.")
        return
    result.columns = header
    if data_rows == 0:
        result.add_error("no_data_rows", f"The file has a header (line {header_line}) but no data rows.",
                         line_number=header_line)
        return
    skipped = [n for n in blank_lines if n < last_record_line]
    if skipped:
        shown = ", ".join(str(n) for n in skipped[:10]) + (" …" if len(skipped) > 10 else "")
        result.add_warning("blank_lines_skipped", f"Skipped {len(skipped)} blank line(s): {shown}.")


def _read_header(record: list[str], line: int, result: ParsedFile) -> list[str] | None:
    names: list[str] = []
    seen: dict[str, int] = {}
    ok = True
    for position, raw in enumerate(record, start=1):
        name = raw.strip()
        if name != raw and name:
            result.add_warning("header_whitespace_stripped",
                               f"Column {position} header {raw!r} had surrounding whitespace; using {name!r}.",
                               line_number=line, column=name)
        if not name:
            result.add_error("blank_header", f"Column {position} in the header (line {line}) has no name.",
                             line_number=line)
            ok = False
        elif name in seen:
            result.add_error("duplicate_header",
                             f"Column name {name!r} appears twice in the header (columns {seen[name]} and "
                             f"{position}).", line_number=line, column=name)
            ok = False
        else:
            seen[name] = position
        names.append(name)
    if len(names) > policy.MAX_COLUMNS:
        result.add_error("too_many_columns",
                         f"The header has {len(names)} columns; the limit is {policy.MAX_COLUMNS}.",
                         line_number=line)
        ok = False
    return names if ok else None


def _field_count_message(row: int, line: int, got: int, expected: int) -> str:
    message = f"Row {row} (line {line}) has {got} field{'' if got == 1 else 's'} but the header has {expected}."
    if got > expected:
        return message + " A value probably contains an unquoted comma; wrap that value in double quotes."
    return message + " Values are missing; empty values still need their separating commas."


def _csv_structure_error(result: ParsedFile, exc: csv.Error, line: int, row: int | None) -> None:
    detail = str(exc)
    if "field larger than field limit" in detail:
        code = "field_too_large"
        message = f"Line {line}: a value is longer than {csv.field_size_limit():,} characters."
    elif "unexpected end of data" in detail:
        code = "malformed_quoting"
        message = (f"Line {line}: a quoted value is never closed (the file ends inside quotes). "
                   "Check for a missing closing double quote.")
    else:
        code = "malformed_quoting"
        message = (f"Line {line}: malformed quoting ({detail}). Wrap values containing quotes in double quotes "
                   'and double any quote inside them ("").')
    result.add_error(code, message + " Parsing stopped here.", line_number=line, row_number=row)


# --- JSON ------------------------------------------------------------------------------


class _NonFiniteNumber(ValueError):
    pass


class _JsonObject(dict):
    duplicate_keys: tuple[str, ...] = ()


def _keep_literal(literal: str) -> str:
    return literal


def _reject_constant(name: str) -> None:
    raise _NonFiniteNumber(name)


def _object_pairs(pairs: list[tuple[str, Any]]) -> _JsonObject:
    obj = _JsonObject()
    duplicates: list[str] = []
    for key, value in pairs:
        if key in obj:
            duplicates.append(key)
        obj[key] = value
    if duplicates:
        obj.duplicate_keys = tuple(dict.fromkeys(duplicates))
    return obj


_DECODER = json.JSONDecoder(
    parse_float=_keep_literal,
    parse_int=_keep_literal,
    parse_constant=_reject_constant,
    object_pairs_hook=_object_pairs,
)
_WHITESPACE = re.compile(r"[ \t\n\r]*")


class _LineCounter:
    """Maps character offsets to 1-based line numbers in O(n) overall."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._pos = 0
        self._line = 1

    def at(self, pos: int) -> int:
        if pos < self._pos:
            return self._text.count("\n", 0, pos) + 1
        self._line += self._text.count("\n", self._pos, pos)
        self._pos = pos
        return self._line


def _skip_ws(text: str, pos: int) -> int:
    return _WHITESPACE.match(text, pos).end()  # type: ignore[union-attr]


def _parse_json(text: str, result: ParsedFile) -> None:
    lines = _LineCounter(text)
    pos = _skip_ws(text, 0)
    if text[pos] != "[":
        result.add_error("json_not_array", 'The top-level JSON value must be an array of objects, like '
                         '[{"id": "C-1"}, {"id": "C-2"}].', line_number=lines.at(pos))
        return

    columns: dict[str, None] = {}
    records: list[tuple[int, dict[str, Cell]]] = []
    row = 0
    pos = _skip_ws(text, pos + 1)
    if pos < len(text) and text[pos] == "]":
        pos += 1
    else:
        while True:
            if pos >= len(text):
                result.add_error("invalid_json", "The file ends before the array is closed with ']'.",
                                 line_number=lines.at(len(text)))
                return
            line = lines.at(pos)
            row += 1
            if text[pos] == "]" and row > 1:
                result.add_error("invalid_json", f"Line {line}: trailing comma after the last record is not "
                                 "allowed in JSON. Parsing stopped here.", line_number=line)
                return
            try:
                value, pos = _DECODER.raw_decode(text, pos)
            except json.JSONDecodeError as exc:
                result.add_error("invalid_json", f"Line {exc.lineno}, column {exc.colno}: {exc.msg}. "
                                 "Parsing stopped here.", line_number=exc.lineno, row_number=row)
                return
            except _NonFiniteNumber as exc:
                result.add_error("invalid_json", f"Record {row} (line {line}) contains {exc.args[0]}, which is "
                                 "not a valid value. Parsing stopped here.", line_number=line, row_number=row)
                return
            if row > policy.MAX_ROWS:
                result.add_error("too_many_rows", f"The file has more than {policy.MAX_ROWS:,} records "
                                 f"(line {line}).", line_number=line, row_number=row)
                return
            record = _json_record(value, row, line, result)
            if record is not None:
                records.append((line, record))
                columns.update(dict.fromkeys(record))

            pos = _skip_ws(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos = _skip_ws(text, pos + 1)
                continue
            if pos < len(text) and text[pos] == "]":
                pos += 1
                break
            if pos >= len(text):
                continue  # reported at the top of the loop
            result.add_error("invalid_json", f"Line {lines.at(pos)}: expected ',' or ']' after record {row}. "
                             "Parsing stopped here.", line_number=lines.at(pos), row_number=row)
            return

    pos = _skip_ws(text, pos)
    if pos < len(text):
        result.add_error("invalid_json", f"Line {lines.at(pos)}: unexpected content after the closing ']'.",
                         line_number=lines.at(pos))
        return
    if row == 0:
        result.add_error("no_data_rows", "The JSON array is empty.")
        return
    if len(columns) > policy.MAX_COLUMNS:
        result.add_error("too_many_columns",
                         f"The records use {len(columns)} distinct keys; the limit is {policy.MAX_COLUMNS}.")
    if not result.ok:
        return
    result.columns = list(columns)
    for line, record in records:
        result.rows.append({column: record.get(column) for column in result.columns})
        result.row_line_numbers.append(line)


def _json_record(value: Any, row: int, line: int, result: ParsedFile) -> dict[str, Cell] | None:
    if not isinstance(value, dict):
        found = "an array" if isinstance(value, list) else "null" if value is None else "a single value"
        result.add_error("row_not_object", f"Record {row} (line {line}) is {found}, but every record must be an "
                         'object like {"id": "C-1"}.', line_number=line, row_number=row)
        return None
    ok = True
    for key in getattr(value, "duplicate_keys", ()):
        result.add_error("duplicate_key", f"Record {row} (line {line}) has the key {key!r} more than once.",
                         line_number=line, row_number=row, column=key)
        ok = False
    record: dict[str, Cell] = {}
    for key, cell in value.items():
        if not key.strip():
            result.add_error("blank_key", f"Record {row} (line {line}) has an empty key.",
                             line_number=line, row_number=row)
            ok = False
        elif isinstance(cell, (dict, list)):
            kind = "object" if isinstance(cell, dict) else "array"
            result.add_error("nested_value", f"Record {row} (line {line}), key {key!r}: nested {kind}s are not "
                             "supported. Flatten the file so every value is a string, number, boolean or null.",
                             line_number=line, row_number=row, column=key)
            ok = False
        else:
            record[key] = _json_cell(cell)
    return record if ok else None


def _json_cell(cell: Any) -> Cell:
    if cell is None:
        return None
    if cell is True:
        return "true"
    if cell is False:
        return "false"
    return cell  # a JSON string, or a number literal kept as its exact text


# --- Suspicious content (flag only; never acted on) -----------------------------------------


def _flag_suspicious_cells(result: ParsedFile) -> None:
    found = 0
    cap = policy.MAX_REPORTED_SUSPICIOUS_CELLS
    for column in result.columns:
        rule = injection_scan.match(column)
        if rule is not None:
            found += 1
            result.add_warning("suspicious_instruction_text",
                               f"Header {injection_scan.excerpt(column)!r} looks like an instruction to an AI "
                               f"system ({rule}). It is treated as a plain column name.", column=column)
    for row_no, (row, line) in enumerate(zip(result.rows, result.row_line_numbers, strict=True), start=1):
        for column, value in row.items():
            if value is None:
                continue
            rule = injection_scan.match(value)
            if rule is None:
                continue
            found += 1
            if found <= cap:
                result.add_warning(
                    "suspicious_instruction_text",
                    f"Row {row_no} (line {line}), column {column!r} contains text that looks like an instruction "
                    f"to an AI system ({rule}). It is kept verbatim and treated as data only: "
                    f"{injection_scan.excerpt(value)!r}",
                    line_number=line, row_number=row_no, column=column,
                )
    if found > cap:
        result.add_warning("suspicious_instruction_text",
                           f"{found - cap} more suspicious cell(s) not listed individually.")
