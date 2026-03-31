#!/usr/bin/env python
import json
import time
from datetime import datetime, UTC
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import server


ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:5050"
OUT = ROOT / "results" / "Data" / "small_validation_report.json"


def iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def get_json(path: str, timeout: int = 60) -> dict:
    req = Request(BASE + path, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return json.loads(body or "{}")
    except URLError as exc:
        return {"ok": False, "error": str(exc), "status": 599}


def scan_files_snapshot() -> dict:
    reports_dir = ROOT / "results" / "Reports"
    csv_files = []
    xlsx_files = []

    if reports_dir.exists():
        for p in reports_dir.rglob("*.csv"):
            csv_files.append({"path": str(p.relative_to(ROOT)).replace("\\", "/"), "mtime": p.stat().st_mtime})
        for p in reports_dir.rglob("*.xlsx"):
            xlsx_files.append({"path": str(p.relative_to(ROOT)).replace("\\", "/"), "mtime": p.stat().st_mtime})

    # PKScreener may also save excel outside results/Reports depending on config.
    for p in ROOT.rglob("*.xlsx"):
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        if not any(item["path"] == rel for item in xlsx_files):
            xlsx_files.append({"path": rel, "mtime": p.stat().st_mtime})

    return {
        "csv": sorted(csv_files, key=lambda x: x["mtime"], reverse=True),
        "xlsx": sorted(xlsx_files, key=lambda x: x["mtime"], reverse=True),
    }


def files_delta(before: dict, after: dict) -> dict:
    def _delta(kind: str):
        b = {x["path"]: x["mtime"] for x in before.get(kind, [])}
        a = {x["path"]: x["mtime"] for x in after.get(kind, [])}
        created = [p for p in a if p not in b]
        updated = [p for p in a if p in b and a[p] > b[p] + 1e-6]
        return {"created": created, "updated": updated}

    return {"csv": _delta("csv"), "xlsx": _delta("xlsx")}


def main() -> int:
    report = {
        "started_at": iso_now(),
        "base": BASE,
        "steps": {},
    }

    health = get_json("/health", timeout=20)
    report["steps"]["health"] = {
        "ok": bool(health.get("ok", False)),
        "scan_running": bool(health.get("scan_running", False)),
        "features": health.get("features", {}),
        "has_market": isinstance(health.get("market", None), dict),
    }

    scanners = get_json("/scanners", timeout=30)
    options = [str(x.get("option", "")).strip() for x in (scanners.get("scanners", []) or []) if str(x.get("option", "")).strip()]

    mapping = []
    mapping_failures = []
    for opt in options:
        normalized = server._normalize_scan_code(opt)
        scripted = server._inputs_for_option(opt)
        valid = (
            bool(normalized)
            and normalized == opt
            and normalized.upper().startswith("X:")
            and len(scripted) >= 2
            and all(
                (str(tok).strip().replace(".", "", 1).isdigit() or str(tok).strip() in {"X"})
                for tok in scripted
            )
        )
        item = {
            "option": opt,
            "normalized": normalized,
            "scripted_inputs": scripted,
            "valid": valid,
        }
        mapping.append(item)
        if not valid:
            mapping_failures.append(item)

    report["steps"]["input_mapping"] = {
        "total_options": len(options),
        "options_with_extra_inputs": sum(1 for x in mapping if len(x["scripted_inputs"]) > 2),
        "failures": mapping_failures,
        "sample": mapping[:20],
    }

    probe_options = [
        "X:12:1",
        "X:12:5:55:68",
        "X:12:6:4:50",
        "X:12:6:7:1",
        "X:12:7:3:0.008:4",
        "X:12:8:110:300",
        "X:12:9:2.5",
        "X:12:30:1",
        "X:12:40:2:2:200",
        "X:12:41:1:2",
    ]

    before_files = scan_files_snapshot()
    live = []
    terminal_checks = []

    for opt in probe_options:
        t0 = time.perf_counter()
        res = get_json(f"/scan?option={quote(opt, safe=':')}", timeout=540)
        dt = round(time.perf_counter() - t0, 2)
        runtime = get_json("/runtime", timeout=20)
        progress = get_json("/scan-progress", timeout=20)
        term = get_json("/terminal-output", timeout=20)
        joined = str(term.get("joined", ""))

        live_item = {
            "option": opt,
            "duration_sec": dt,
            "ok": bool(res.get("ok", False)),
            "status": res.get("status"),
            "total": int(res.get("total", 0) or 0),
            "error": res.get("error", ""),
            "errors": res.get("errors", []),
            "has_market": isinstance(res.get("market", None), dict),
            "has_all_logs": bool(res.get("all_logs")),
            "has_debug_output": bool(str(res.get("debug_output", "")).strip()),
            "runtime_ok": bool(runtime.get("ok", False)),
            "progress_ok": bool(progress.get("ok", False)),
        }
        live.append(live_item)

        terminal_checks.append(
            {
                "option": opt,
                "has_scan_command_log": (f"-o {opt}" in joined),
                "has_scripted_inputs_log": ("[scan] scripted_inputs sequence:" in joined),
            }
        )

    after_files = scan_files_snapshot()
    report["steps"]["live_probes"] = live
    report["steps"]["terminal_checks"] = terminal_checks
    report["steps"]["file_generation"] = {
        "before_top_csv": before_files.get("csv", [])[:10],
        "after_top_csv": after_files.get("csv", [])[:10],
        "before_top_xlsx": before_files.get("xlsx", [])[:10],
        "after_top_xlsx": after_files.get("xlsx", [])[:10],
        "delta": files_delta(before_files, after_files),
    }

    report["summary"] = {
        "input_mapping_passed": len(mapping_failures) == 0,
        "input_mapping_failure_count": len(mapping_failures),
        "live_probe_count": len(live),
        "live_probe_failures": [x for x in live if not x["ok"] and x.get("status") not in (200,)],
        "live_probe_with_rows": sum(1 for x in live if x.get("total", 0) > 0),
        "terminal_log_gaps": [x for x in terminal_checks if not (x["has_scan_command_log"] and x["has_scripted_inputs_log"])],
        "csv_created_or_updated": bool(report["steps"]["file_generation"]["delta"]["csv"]["created"] or report["steps"]["file_generation"]["delta"]["csv"]["updated"]),
        "xlsx_created_or_updated": bool(report["steps"]["file_generation"]["delta"]["xlsx"]["created"] or report["steps"]["file_generation"]["delta"]["xlsx"]["updated"]),
    }

    report["finished_at"] = iso_now()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())