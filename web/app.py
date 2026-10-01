"""Research UI and same-origin proxy for the UNHAS Text-to-SQL experiment."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "web" / "static"
EVALUATION_DIR = Path(os.environ.get("EVALUATION_DIR", str(ROOT / "results")))
EVALUATION_FILES = {
    1: "eval_beams_1_run_500.json",
    2: "eval_run_500.json",
    4: "eval_beams_4_run_500.json",
}
UPSTREAMS = {
    "baseline": os.environ.get("BASELINE_URL", "http://baseline-noalias:8000"),
    "picard": os.environ.get("PICARD_URL", "http://picard-api-noalias:8000"),
}
REQUEST_TIMEOUT_S = float(os.environ.get("REQUEST_TIMEOUT_S", "240"))

app = FastAPI(
    title="UNHAS Text-to-SQL Research Console",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class CompareRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    db_id: str = Field(default="neosia", max_length=64)


def _decode_error_payload(raw: bytes) -> Dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"message": raw.decode("utf-8", errors="replace") or "Upstream error"}

    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    if isinstance(detail, dict):
        return detail
    return {"message": str(detail)}


def _query_upstream(system: str, db_id: str, question: str) -> Dict[str, Any]:
    base_url = UPSTREAMS[system].rstrip("/")
    url = f"{base_url}/ask/{quote(db_id, safe='')}/{quote(question, safe='')}"
    started = time.perf_counter()
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
        record = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(record, dict):
            raise ValueError("Upstream returned an unexpected response shape")
        return {
            "system": system,
            "ok": True,
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "query": record.get("query", ""),
            "rows": record.get("execution_results", []),
            "decoder_trace": record.get("decoder_trace"),
            "error": None,
        }
    except HTTPError as exc:
        detail = _decode_error_payload(exc.read())
        return {
            "system": system,
            "ok": False,
            "status": exc.code,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "query": detail.get("query", ""),
            "rows": [],
            "decoder_trace": detail.get("decoder_trace"),
            "error": detail.get("message", "Upstream rejected the request"),
        }
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return {
            "system": system,
            "ok": False,
            "status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "query": "",
            "rows": [],
            "decoder_trace": None,
            "error": str(exc),
        }


def _health(system: str) -> Dict[str, Any]:
    base_url = UPSTREAMS[system].rstrip("/")
    started = time.perf_counter()
    try:
        with urlopen(f"{base_url}/dbs", timeout=5) as response:
            databases = json.loads(response.read().decode("utf-8"))
        return {
            "ok": True,
            "status": response.status,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "databases": databases,
        }
    except Exception as exc:  # Health reports the failure instead of masking it.
        return {
            "ok": False,
            "status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "databases": [],
            "error": str(exc),
        }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/tokens.css", include_in_schema=False)
def design_tokens() -> FileResponse:
    return FileResponse(ROOT / "tokens.css", media_type="text/css")


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    baseline, picard = await asyncio.gather(
        loop.run_in_executor(None, _health, "baseline"),
        loop.run_in_executor(None, _health, "picard"),
    )
    return {"baseline": baseline, "picard": picard}


@app.post("/api/compare")
async def compare(body: CompareRequest) -> Dict[str, Any]:
    question = body.question.strip()
    if len(question) < 3:
        raise HTTPException(status_code=422, detail="Pertanyaan minimal 3 karakter.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", body.db_id):
        raise HTTPException(status_code=422, detail="Nama database tidak valid.")

    loop = asyncio.get_running_loop()
    baseline, picard = await asyncio.gather(
        loop.run_in_executor(None, _query_upstream, "baseline", body.db_id, question),
        loop.run_in_executor(None, _query_upstream, "picard", body.db_id, question),
    )
    return {
        "question": question,
        "db_id": body.db_id,
        "baseline": baseline,
        "picard": picard,
    }


@app.get("/api/evaluation")
def evaluation() -> Dict[str, Any]:
    runs = []
    reference_cases = None
    db_id = None
    for beam, filename in EVALUATION_FILES.items():
        path = EVALUATION_DIR / filename
        if not path.is_file():
            raise HTTPException(status_code=503, detail=f"Berkas evaluasi beam {beam} belum tersedia.")
        try:
            with path.open(encoding="utf-8") as handle:
                report = json.load(handle)
            systems = report["systems"]
            baseline = systems["baseline"]
            picard = systems["picard"]
            baseline_cases = {case["id"]: case for case in baseline["cases"]}
            picard_cases = {case["id"]: case for case in picard["cases"]}
            if len(baseline_cases) != len(baseline["cases"]) or len(picard_cases) != len(picard["cases"]):
                raise ValueError("ID kasus duplikat")
            if set(baseline_cases) != set(picard_cases):
                raise ValueError("ID baseline dan PICARD tidak sepadan")
            metadata = {
                case_id: (case["question"], case["gold_sql"], case["difficulty"])
                for case_id, case in baseline_cases.items()
            }
            if any(
                metadata[case_id] != (case["question"], case["gold_sql"], case["difficulty"])
                for case_id, case in picard_cases.items()
            ):
                raise ValueError("Pertanyaan atau SQL acuan tidak sepadan")
            if reference_cases is None:
                reference_cases = metadata
                db_id = report["db_id"]
            elif metadata != reference_cases or report["db_id"] != db_id:
                raise ValueError("Kasus antarbeam tidak sepadan")

            difficulties: Dict[str, Dict[str, int]] = {}
            for difficulty in ("easy", "medium", "hard"):
                ids = [case_id for case_id, case in baseline_cases.items() if case["difficulty"] == difficulty]
                difficulties[difficulty] = {
                    "n": len(ids),
                    "baseline_execution": sum(bool(baseline_cases[i]["execution_match"]) for i in ids),
                    "picard_execution": sum(bool(picard_cases[i]["execution_match"]) for i in ids),
                }

            both_correct = []
            improved = []
            regressed = []
            both_wrong = []
            for case_id in baseline_cases:
                base_ok = bool(baseline_cases[case_id]["execution_match"])
                pic_ok = bool(picard_cases[case_id]["execution_match"])
                (both_correct if base_ok and pic_ok else
                 regressed if base_ok else
                 improved if pic_ok else both_wrong).append(case_id)

            runs.append({
                "beam": beam,
                "source_file": filename,
                "baseline": baseline["metrics"],
                "picard": picard["metrics"],
                "difficulties": difficulties,
                "paired": {
                    "both_correct": len(both_correct),
                    "improved": len(improved),
                    "regressed": len(regressed),
                    "both_wrong": len(both_wrong),
                    "improved_case_ids": sorted(improved),
                    "regressed_case_ids": sorted(regressed),
                },
            })
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail=f"Data evaluasi beam {beam} tidak valid: {exc}") from exc

    return {"db_id": db_id, "n": len(reference_cases or {}), "runs": runs}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8300)
