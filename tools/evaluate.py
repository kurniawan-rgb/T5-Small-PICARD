#!/usr/bin/env python3
"""Resumable paired evaluation for the baseline and PICARD endpoints."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SQL_CLAUSE_KEYWORDS = {
    "WHERE", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "ON",
    "GROUP", "ORDER", "HAVING", "LIMIT", "UNION", "INTERSECT", "EXCEPT",
    "OFFSET", "WINDOW", "USING",
}
TABLE_ALIAS_DECLARATION = re.compile(
    r"\b(?P<clause>FROM|JOIN)\s+(?P<table>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?:(?P<as>AS)\s+)?(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\b",
    flags=re.IGNORECASE,
)


def canonicalize_aliases(sql: str) -> str:
    aliases: dict[str, str] = {}

    def remove_alias(match: re.Match[str]) -> str:
        alias = match.group("alias")
        if alias.upper() in SQL_CLAUSE_KEYWORDS:
            return match.group(0)
        aliases[alias.lower()] = match.group("table")
        return f"{match.group('clause')} {match.group('table')}"

    canonical = TABLE_ALIAS_DECLARATION.sub(remove_alias, sql or "")
    for alias, table in aliases.items():
        canonical = re.sub(
            rf"\b{re.escape(alias)}\s*\.",
            f"{table}.",
            canonical,
            flags=re.IGNORECASE,
        )
    return canonical


def normalize_sql(sql: str) -> str:
    """Normalize syntax while preserving case inside quoted SQL literals."""
    sql = canonicalize_aliases((sql or "").strip().rstrip(";"))
    parts = re.split(r"('(?:''|[^'])*')", sql)
    return "".join(
        part if index % 2 else re.sub(r"\s+", " ", part.lower())
        for index, part in enumerate(parts)
    ).strip()


def rows_key(rows):
    return sorted(repr(tuple(row)) for row in (rows or []))


def call_endpoint(endpoint: str, db_id: str, question: str, timeout: int):
    url = f"{endpoint}/ask/{urllib.parse.quote(db_id)}/{urllib.parse.quote(question, safe='')}"
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            item = payload[0] if isinstance(payload, list) and payload else {}
            return {
                "status": response.status,
                "query": item.get("query", ""),
                "rows": item.get("execution_results"),
                "error": item.get("execution_error"),
                "latency_s": round(time.perf_counter() - started, 3),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("detail", body)
        except json.JSONDecodeError:
            detail = body
        if isinstance(detail, dict):
            query = str(detail.get("query", ""))
            error = str(detail.get("message", json.dumps(detail, ensure_ascii=False)))
        else:
            error = str(detail)
            match = re.search(
                r'while executing "(.*)", the following error occurred:',
                error,
            )
            query = match.group(1) if match else ""
        return {
            "status": exc.code,
            "query": query,
            "rows": None,
            "error": error,
            "latency_s": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:  # Timeout and connection failures are recorded.
        return {
            "status": getattr(exc, "code", None),
            "query": "",
            "rows": None,
            "error": repr(exc),
            "latency_s": round(time.perf_counter() - started, 3),
        }


def metrics(rows):
    return {
        "n": len(rows),
        "api_success": sum(row["status"] == 200 for row in rows),
        "exact_match": sum(row["exact_match"] for row in rows),
        "execution_match": sum(row["execution_match"] for row in rows),
        "mean_latency_s": round(
            sum(row["latency_s"] for row in rows) / len(rows), 3
        ) if rows else 0,
    }


def save_result(path: Path, result: dict, system: str, endpoint: str, rows: list):
    ordered_rows = sorted(rows, key=lambda row: row["id"])
    result["systems"][system] = {
        "endpoint": endpoint,
        "metrics": metrics(ordered_rows),
        "cases": ordered_rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def restart_picard(args):
    project = args.project.resolve()
    compose_file = args.compose_file
    if not compose_file.is_absolute():
        compose_file = project / compose_file
    print("Restarting PICARD API to cancel unfinished generation...", flush=True)
    subprocess.run(
        [
            "docker", "compose", "-p", args.compose_project,
            "-f", str(compose_file), "up", "-d", "--force-recreate",
            args.picard_service,
        ],
        cwd=project,
        check=True,
    )
    deadline = time.monotonic() + args.startup_timeout
    health_url = f"{args.picard_endpoint}/dbs"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=5) as response:
                if response.status == 200:
                    print("PICARD API is ready.", flush=True)
                    return
        except Exception:
            pass
        time.sleep(3)
    raise TimeoutError(f"PICARD API did not become ready: {health_url}")


def evaluate_system(args, result, system, endpoint, cases, db_id, connection):
    existing = result["systems"].get(system, {})
    rows = list(existing.get("cases", []))
    selected_ids = {case["id"] for case in cases}
    if args.rerun:
        rows = [row for row in rows if row["id"] not in selected_ids]
    completed_ids = {row["id"] for row in rows}

    if system == "picard" and args.restart_picard_first:
        restart_picard(args)

    for position, case in enumerate(cases, 1):
        if case["id"] in completed_ids:
            print(f"{system} id={case['id']} already saved; skipping", flush=True)
            continue
        response = call_endpoint(endpoint, db_id, case["question"], args.timeout)
        gold_rows = [
            list(row) for row in connection.execute(case["gold_sql"]).fetchall()
        ]
        row = {
            "id": case["id"],
            "difficulty": case.get("difficulty"),
            "question": case["question"],
            "gold_sql": case["gold_sql"],
            "prediction": response["query"],
            "status": response["status"],
            "latency_s": response["latency_s"],
            "error": response["error"],
            "exact_match": normalize_sql(response["query"])
            == normalize_sql(case["gold_sql"]),
            "execution_match": response["status"] == 200
            and rows_key(response["rows"]) == rows_key(gold_rows),
            "predicted_rows": response["rows"],
            "gold_rows": gold_rows,
        }
        rows.append(row)
        completed_ids.add(case["id"])
        save_result(args.out, result, system, endpoint, rows)
        print(
            f"{system} {position}/{len(cases)} id={row['id']} "
            f"status={row['status']} exact={row['exact_match']} "
            f"exec={row['execution_match']} latency={row['latency_s']}s",
            flush=True,
        )
        if (
            system == "picard"
            and response["status"] is None
            and not args.no_restart_after_timeout
        ):
            restart_picard(args)

    save_result(args.out, result, system, endpoint, rows)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=int, nargs="+")
    parser.add_argument(
        "--systems", nargs="+", choices=("baseline", "picard"),
        default=("baseline", "picard"),
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--baseline-endpoint", default="http://127.0.0.1:8001")
    parser.add_argument("--picard-endpoint", default="http://127.0.0.1:8002")
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument("--compose-project", default="text2sql_picard_unhas")
    parser.add_argument("--picard-service", default="picard-api")
    parser.add_argument("--startup-timeout", type=int, default=180)
    parser.add_argument("--restart-picard-first", action="store_true")
    parser.add_argument("--no-restart-after-timeout", action="store_true")
    parser.add_argument("--rerun", action="store_true")
    parser.add_argument("--fresh", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    db_id = data.get("db_id", "neosia")
    requested_ids = set(args.ids)
    cases = [case for case in data["cases"] if case.get("id") in requested_ids]
    found_ids = {case["id"] for case in cases}
    if found_ids != requested_ids:
        raise ValueError(f"Unknown case IDs: {sorted(requested_ids - found_ids)}")

    if args.out.exists() and not args.fresh:
        result = json.loads(args.out.read_text(encoding="utf-8"))
    else:
        result = {
            "case_file": str(args.cases),
            "db_id": db_id,
            "timeout_s": args.timeout,
            "systems": {},
        }
    result.setdefault("systems", {})

    connection = sqlite3.connect(str(args.database))
    try:
        if "baseline" in args.systems:
            evaluate_system(
                args, result, "baseline", args.baseline_endpoint,
                cases, db_id, connection,
            )
        if "picard" in args.systems:
            evaluate_system(
                args, result, "picard", args.picard_endpoint,
                cases, db_id, connection,
            )
    finally:
        connection.close()

    summary = {
        name: result["systems"][name]["metrics"]
        for name in args.systems
        if name in result["systems"]
    }
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
