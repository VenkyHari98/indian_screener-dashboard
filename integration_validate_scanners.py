#!/usr/bin/env python
import json
import sys
import time
import traceback
from collections import OrderedDict
from datetime import datetime, UTC
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import server
from pkscreener.classes.CandlePatterns import CandlePatterns
from pkscreener.classes.MenuOptions import menus


ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "results" / "Data" / "scan_validation_report.json"
BASE_URL = "http://127.0.0.1:5050"
SCAN_TIMEOUT = 720
SMOKE_SCAN_OPTION = "X:12:1"


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def http_json(path: str, timeout: int = 30) -> dict:
    url = BASE_URL + path
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc

    try:
        return json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}: {body[:400]}") from exc


def normalize_option(option: str) -> str:
    return str(option or "").strip().replace(":D", "")


def _explicit_cases(cup_and_handle_index: str) -> list[tuple[str, str]]:
    return [
        ("X:12:4:5", "prompt-lowest-volume"),
        ("X:12:5:55:68", "prompt-rsi-range"),
        ("X:12:6", "prompt-reversal-default"),
        ("X:12:6:4:50", "prompt-reversal-ma-length"),
        ("X:12:6:6:4", "prompt-reversal-nr"),
        ("X:12:6:7:1", "prompt-reversal-lorentz"),
        ("X:12:6:10:3", "prompt-reversal-rsi-ma"),
        ("X:12:7", "prompt-chart-default"),
        ("X:12:7:1:3:1", "prompt-inside-bar-bullish"),
        ("X:12:7:2:3:1", "prompt-inside-bar-bearish"),
        ("X:12:7:3:0.8:4", "prompt-super-confluence"),
        ("X:12:7:4", "prompt-vcp"),
        (f"X:12:7:7:{cup_and_handle_index}:0", "prompt-cup-handle"),
        ("X:12:7:6:1", "prompt-bbands"),
        ("X:12:7:9:1", "prompt-ma-signals"),
        ("X:12:8:110:300", "prompt-cci-range"),
        ("X:12:9:2.5", "prompt-volume-ratio"),
        ("X:12:21:1", "prompt-popular-submenu"),
        ("X:12:22:1", "prompt-performance-submenu"),
        ("X:12:30:1", "prompt-atr-direction"),
        ("X:12:31", "prompt-high-momentum-default"),
        ("X:12:32:1", "prompt-intraday-breakout-direction"),
        ("X:12:33:2", "prompt-potential-profitable"),
        ("X:12:34", "prompt-avwap-default"),
        ("X:12:40:2:2:200", "prompt-price-cross"),
        ("X:12:41:1:2", "prompt-pivot-cross"),
        ("X:12:42", "prompt-super-gainers-default"),
        ("X:12:43", "prompt-super-losers-default"),
    ]


def build_case_matrix(include_menu_cases: bool = True) -> list[dict]:
    cases: OrderedDict[str, dict] = OrderedDict()

    def add_case(option: str, source: str, label: str = ""):
        normalized = normalize_option(option)
        if not normalized or not normalized.upper().startswith("X:"):
            return
        if normalized not in cases:
            cases[normalized] = {
                "option": normalized,
                "label": label or normalized,
                "sources": [],
            }
        if source not in cases[normalized]["sources"]:
            cases[normalized]["sources"].append(source)
        if label and (cases[normalized]["label"] == normalized):
            cases[normalized]["label"] = label

    scanners_payload = http_json("/scanners", timeout=30)
    for item in scanners_payload.get("scanners", []) or []:
        add_case(item.get("option", ""), "catalog", item.get("label", ""))

    if include_menu_cases:
        menu_options, menu_labels = menus.allMenus(topLevel="X", index=12)
        for option in menu_options:
            normalized = normalize_option(option)
            add_case(normalized, "menu", menu_labels.get(normalized, normalized))

    cup_and_handle_index = str(CandlePatterns.reversalPatternsBullish.index("Cup and Handle") + 1)
    for option, label in _explicit_cases(cup_and_handle_index):
        add_case(option, "explicit", label)

    return list(cases.values())


def build_live_case_matrix() -> list[dict]:
    # Live integration: all surfaced catalog scanners + one representative case
    # for every distinct typed-input / nested-submenu path.
    return build_case_matrix(include_menu_cases=False)


def classify_case_response(payload: dict) -> tuple[str, list[str]]:
    warnings = []
    ok_flag = bool(payload.get("ok", False))
    total = int(payload.get("total", 0) or 0)
    errors = payload.get("errors", []) or []
    error_messages = [str(err.get("message", "")).strip() for err in errors if isinstance(err, dict)]
    error_text = " | ".join([m for m in error_messages if m])
    all_logs = payload.get("all_logs", []) or []
    market = payload.get("market", {}) or {}

    if not all_logs:
        warnings.append("missing_all_logs")
    if not isinstance(market, dict) or "nifty" not in market:
        warnings.append("missing_market_snapshot")

    if ok_flag and total > 0:
        return "passed_with_results", warnings

    if ok_flag and total == 0:
        warnings.append("zero_rows_but_ok")
        return "passed_no_rows", warnings

    acceptable_no_result_markers = [
        "no report csv generated",
        "no stocks found",
        "latest csv parsed",
    ]
    if (not ok_flag) and total == 0 and any(marker in (error_text.lower() + " " + str(payload.get("message", "")).lower()) for marker in acceptable_no_result_markers):
        warnings.append("no_result_path")
        return "acceptable_no_result", warnings

    if payload.get("status") == 504:
        return "failed_timeout", warnings

    return "failed", warnings


def run_live_case(case: dict) -> dict:
    option = case["option"]
    path = f"/scan?option={quote(option, safe=':')}"
    started = time.perf_counter()
    payload = http_json(path, timeout=SCAN_TIMEOUT)
    duration = round(time.perf_counter() - started, 2)
    status, warnings = classify_case_response(payload)
    return {
        "option": option,
        "label": case["label"],
        "sources": case["sources"],
        "scripted_inputs": server._inputs_for_option(option),
        "duration_sec": duration,
        "status": status,
        "warnings": warnings,
        "ok": bool(payload.get("ok", False)),
        "http_status": payload.get("status"),
        "total": int(payload.get("total", 0) or 0),
        "errors": payload.get("errors", []),
        "message": payload.get("message", payload.get("error", "")),
        "has_market": isinstance(payload.get("market", None), dict),
        "has_all_logs": bool(payload.get("all_logs")),
        "has_debug_output": bool(str(payload.get("debug_output", "")).strip()),
        "scan_summary_count": len(payload.get("scan_summary", []) or []),
    }


def runtime_smoke() -> dict:
    scan_result: dict[str, object] = {}

    def _runner():
        nonlocal scan_result
        scan_result = http_json(f"/scan?option={quote(SMOKE_SCAN_OPTION, safe=':')}", timeout=SCAN_TIMEOUT)

    thread = Thread(target=_runner, daemon=True)
    thread.start()

    runtime_samples = []
    progress_samples = []
    running_seen = False

    for _ in range(4):
        time.sleep(2)
        runtime = http_json("/runtime", timeout=15)
        progress = http_json("/scan-progress", timeout=15)
        runtime_samples.append({
            "scan_running": bool(runtime.get("scan_running")),
            "scan_elapsed_sec": runtime.get("scan_elapsed_sec"),
            "has_last_scan_command": bool(runtime.get("last_scan_command")),
            "has_market": isinstance(runtime.get("market", None), dict),
        })
        progress_samples.append({
            "scan_running": bool(progress.get("scan_running")),
            "total": int(progress.get("total", 0) or 0),
            "has_csv_path": bool(progress.get("csv_path")),
            "has_market": isinstance(progress.get("market", None), dict),
        })
        running_seen = running_seen or bool(runtime.get("scan_running"))
        if not thread.is_alive():
            break

    thread.join(timeout=SCAN_TIMEOUT)
    if thread.is_alive():
        raise RuntimeError("Runtime smoke scan did not finish within timeout")

    return {
        "option": SMOKE_SCAN_OPTION,
        "running_seen": running_seen,
        "runtime_samples": runtime_samples,
        "progress_samples": progress_samples,
        "final_ok": bool(scan_result.get("ok", False)),
        "final_total": int(scan_result.get("total", 0) or 0),
        "final_status": scan_result.get("status"),
    }


def validate_input_padding(cases: list[dict]) -> dict:
    results = []
    failures = []
    for case in cases:
        option = case["option"]
        raw_parts = [p for p in option.split(":")[1:] if p]
        padded = server._inputs_for_option(option)
        entry = {
            "option": option,
            "raw_parts": raw_parts,
            "padded": padded,
            "pass": len(padded) >= len(raw_parts) and padded[: len(raw_parts)] == raw_parts,
        }
        results.append(entry)
        if not entry["pass"]:
            failures.append(entry)
    return {
        "total": len(results),
        "failures": failures,
        "samples": results[:25],
    }


def summarize(live_results: list[dict]) -> dict:
    buckets = {
        "passed_with_results": 0,
        "passed_no_rows": 0,
        "acceptable_no_result": 0,
        "failed_timeout": 0,
        "failed": 0,
    }
    failed_options = []
    warning_count = 0
    for item in live_results:
        buckets[item["status"]] = buckets.get(item["status"], 0) + 1
        warning_count += len(item.get("warnings", []))
        if item["status"].startswith("failed"):
            failed_options.append({
                "option": item["option"],
                "status": item["status"],
                "message": item.get("message", ""),
                "errors": item.get("errors", []),
            })
    return {
        "counts": buckets,
        "warnings_total": warning_count,
        "failures": failed_options,
    }


def main() -> int:
    started_at = now_iso()
    report = {
        "started_at": started_at,
        "base_url": BASE_URL,
        "steps": {},
    }

    try:
        health = http_json("/health", timeout=15)
        report["steps"]["health"] = {
            "ok": bool(health.get("ok", False)),
            "scan_running": bool(health.get("scan_running", False)),
            "has_market": isinstance(health.get("market", None), dict),
            "features": health.get("features", {}),
        }

        static_cases = build_case_matrix(include_menu_cases=True)
        live_cases = build_live_case_matrix()
        report["steps"]["case_matrix"] = {
            "static_count": len(static_cases),
            "live_count": len(live_cases),
            "static_sample": static_cases[:20],
            "live_sample": live_cases[:20],
        }

        report["steps"]["input_padding"] = validate_input_padding(static_cases)

        runtime_report = runtime_smoke()
        report["steps"]["runtime_smoke"] = runtime_report

        live_results = []
        for index, case in enumerate(live_cases, start=1):
            result = run_live_case(case)
            result["sequence"] = index
            live_results.append(result)
            print(
                f"[{index}/{len(live_cases)}] {result['option']} -> {result['status']} "
                f"({result['total']} rows, {result['duration_sec']}s)",
                flush=True,
            )

        report["steps"]["live_results"] = live_results
        report["summary"] = summarize(live_results)
        report["finished_at"] = now_iso()

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

        print(json.dumps(report["summary"], indent=2), flush=True)
        return 0 if not report["summary"]["failures"] else 2
    except Exception as exc:
        report["error"] = {
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        report["finished_at"] = now_iso()
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())