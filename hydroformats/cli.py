"""Command-line interface.

    hydroformats info SURVEY.RAW
    hydroformats records SURVEY.HSX --type RMB --limit 5 [--json]
    hydroformats to-csv SURVEY.RAW --type EC1 -o depths.csv
    hydroformats to-jsonl SURVEY.HSX -o records.jsonl

Exit codes: 0 success, 1 file/parse-level failure, 2 usage error (argparse).
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import dataclasses
import json
import sys
from pathlib import Path

from .records import MalformedRecord, UnknownRecord
from .session import Session

_SKIP_CSV_FIELDS = {"tag"}


def _record_dict(record) -> dict:
    payload = dataclasses.asdict(record)
    return {"tag": record.tag, **{k: v for k, v in payload.items() if k != "tag"}}


def _json_default(value):
    if isinstance(value, tuple):
        return list(value)
    return str(value)


def _cmd_info(args: argparse.Namespace) -> int:
    session = Session(args.file)
    summary = session.summary()
    unknown = summary["record_counts"].get("", 0)
    print(f"file:      {summary['path']}")
    print(f"dialect:   {summary['dialect']}")
    print(f"started:   {summary['survey_started'] or 'unknown'}")
    print("devices:")
    for number, name in summary["devices"].items():
        print(f"  {number}: {name}")
    print("records:")
    for tag, count in summary["record_counts"].items():
        print(f"  {tag or '(untagged)'}: {count}")
    return 0 if not unknown else 0


def _cmd_records(args: argparse.Namespace) -> int:
    session = Session(args.file)
    shown = 0
    for record in session.records():
        if args.type and record.tag != args.type:
            continue
        if args.json:
            print(json.dumps(_record_dict(record), default=_json_default))
        else:
            print(record)
        shown += 1
        if args.limit and shown >= args.limit:
            break
    if shown == 0:
        print(f"no records matched (type={args.type or 'any'})", file=sys.stderr)
    return 0


@contextlib.contextmanager
def _sink(output: Path | None, newline: str | None = None):
    if output is None:
        yield sys.stdout
    else:
        with open(output, "w", newline=newline) as handle:
            yield handle


def _cmd_to_csv(args: argparse.Namespace) -> int:
    session = Session(args.file)
    rows = (
        _record_dict(record)
        for record in session.records()
        if record.tag == args.type
        and not isinstance(record, (UnknownRecord, MalformedRecord))
    )
    first = next(rows, None)
    if first is None:
        print(f"no parseable {args.type} records in {args.file}", file=sys.stderr)
        return 1
    with _sink(args.output, newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(first.keys()))
        writer.writeheader()
        writer.writerow(first)
        for row in rows:
            writer.writerow(row)
    return 0


def _cmd_to_jsonl(args: argparse.Namespace) -> int:
    session = Session(args.file)
    with _sink(args.output) as out:
        for record in session.records():
            out.write(json.dumps(_record_dict(record), default=_json_default) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hydroformats",
        description="Parse HYPACK RAW / HYSWEEP HSX survey logs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="file summary: dialect, devices, record counts")
    p_info.add_argument("file", type=Path)
    p_info.set_defaults(fn=_cmd_info)

    p_records = sub.add_parser("records", help="print records (optionally filtered)")
    p_records.add_argument("file", type=Path)
    p_records.add_argument("--type", help="record tag, e.g. POS, EC1, RMB")
    p_records.add_argument("--limit", type=int, default=0, help="stop after N records")
    p_records.add_argument("--json", action="store_true", help="one JSON object per line")
    p_records.set_defaults(fn=_cmd_records)

    p_csv = sub.add_parser("to-csv", help="export one record type as CSV")
    p_csv.add_argument("file", type=Path)
    p_csv.add_argument("--type", required=True, help="record tag to export")
    p_csv.add_argument("-o", "--output", type=Path, help="output path (default stdout)")
    p_csv.set_defaults(fn=_cmd_to_csv)

    p_jsonl = sub.add_parser("to-jsonl", help="export every record as JSON lines")
    p_jsonl.add_argument("file", type=Path)
    p_jsonl.add_argument("-o", "--output", type=Path, help="output path (default stdout)")
    p_jsonl.set_defaults(fn=_cmd_to_jsonl)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.file.exists():
        print(f"error: no such file: {args.file}", file=sys.stderr)
        return 1
    try:
        return args.fn(args)
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
