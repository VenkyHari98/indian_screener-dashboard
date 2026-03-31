#!/usr/bin/env python
import csv
import configparser
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import threading
import uuid
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "results" / "Reports"
INDICES_DIR = ROOT / "results" / "Indices"
LOG_FILE = ROOT / "results" / "Data" / "pkscreener-logs.txt"
CONFIG_FILE = ROOT / "pkscreener.ini"
ACTION_SCAN_DIR = ROOT / "actions-data-scan"
EXPECTED_VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
EXPECTED_VENV_PREFIX = (ROOT / ".venv").resolve()
_VENV_RELAUNCH_FLAG = "DASHBOARD_VENV_RELAUNCHED"
CONFIG_AUTH_TOKEN = os.getenv("DASHBOARD_CONFIG_TOKEN", "").strip()
DEBUG_MODE = os.getenv("DASHBOARD_DEBUG", "").strip().lower() in {"1", "true", "yes", "y", "on"}
LAST_SCAN_COMMAND = ""
LAST_SCAN_OPTION = ""
LAST_SCAN_AT = ""
SCAN_RUNNING = False
SCAN_STARTED_AT = ""
SCAN_START_TS = 0.0
SCAN_ACTIVE_LABEL = ""
SCAN_TIMEOUT_SEC = 900
SCAN_INACTIVITY_TIMEOUT_SEC = 180
APP_TERMINAL_LINES = deque(maxlen=2000)
SCAN_LOCK = threading.Lock()
MARKET_STATE = {
    "nifty": 0.0,
    "sensex": 0.0,
    "niftyChg": 0.0,
    "sensexChg": 0.0,
    "as_of": "",
}


def _app_log(message: str):
    text = str(message)
    APP_TERMINAL_LINES.append(text)
    print(text, flush=True)


def _debug_log(message: str):
    if DEBUG_MODE:
        _app_log(f"[debug] {message}")


def _is_benign_stderr_line(text: str):
    t = (text or "").strip().lower()
    return ".env.dev file not found. searching" in t


def _market_snapshot():
    return {
        "nifty": float(MARKET_STATE.get("nifty", 0.0)),
        "sensex": float(MARKET_STATE.get("sensex", 0.0)),
        "niftyChg": float(MARKET_STATE.get("niftyChg", 0.0)),
        "sensexChg": float(MARKET_STATE.get("sensexChg", 0.0)),
        "as_of": MARKET_STATE.get("as_of", ""),
    }


def _capture_market_from_line(text: str):
    # Example line from PKScreener stdout:
    # NIFTY 50 (22866.95 | +0.00% | 26-03-27 | 15:25) | SENSEX (73743.44 | +0.00% | 26-03-27 | 15:25)
    if not text:
        return
    m = re.search(
        r"NIFTY\s*50\s*\(([-+0-9.,]+)\s*\|\s*([-+0-9.,]+)%.*?\)\s*\|\s*SENSEX\s*\(([-+0-9.,]+)\s*\|\s*([-+0-9.,]+)%",
        str(text),
        flags=re.IGNORECASE,
    )
    if not m:
        return
    MARKET_STATE["nifty"] = _safe_float(m.group(1), default=0.0)
    MARKET_STATE["niftyChg"] = _safe_float(m.group(2), default=0.0)
    MARKET_STATE["sensex"] = _safe_float(m.group(3), default=0.0)
    MARKET_STATE["sensexChg"] = _safe_float(m.group(4), default=0.0)
    MARKET_STATE["as_of"] = datetime.utcnow().isoformat() + "Z"

SCAN_LABELS = {
    "7": "BREAKOUT",
    "29": "EMA",
    "42": "BTST",
    "6": "VOLUME",
    "3": "REVERSAL",
    "1": "CROSSOVER",
    "9": "RSI",
    "11": "MOMENTUM",
}

_PKS_MENU_CONTEXT = None


def _clean_menu_text(text: str):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _get_pks_menu_context():
    global _PKS_MENU_CONTEXT
    if _PKS_MENU_CONTEXT is not None:
        return _PKS_MENU_CONTEXT

    try:
        from pkscreener.classes import MenuOptions as mo

        _PKS_MENU_CONTEXT = {
            "level0": getattr(mo, "level0MenuDict", {}),
            "level1_p": getattr(mo, "level1_P_MenuDict", {}),
            "level2_p": getattr(mo, "level2_P_MenuDict", {}),
            "level1_d": getattr(mo, "LEVEL_1_DATA_DOWNLOADS", {}),
            "level1_s": getattr(mo, "level1_S_MenuDict", {}),
            "level1_t": getattr(mo, "level1_T_MenuDict", {}),
            "level2_t_l": getattr(mo, "level2_T_MenuDict_L", {}),
            "level2_t_s": getattr(mo, "level2_T_MenuDict_S", {}),
            "level1_x": getattr(mo, "level1_X_MenuDict", {}),
            "level2_x": getattr(mo, "level2_X_MenuDict", {}),
            "reversal": getattr(mo, "level3_X_Reversal_MenuDict", {}),
            "chart": getattr(mo, "level3_X_ChartPattern_MenuDict", {}),
            "popular": getattr(mo, "level3_X_PopularStocks_MenuDict", {}),
            "performance": getattr(mo, "level3_X_StockPerformance_MenuDict", {}),
            "potential": getattr(mo, "level3_X_PotentialProfitable_MenuDict", {}),
            "lorentz": getattr(mo, "level4_X_Lorenzian_MenuDict", {}),
            "chart_ma": getattr(mo, "level4_X_ChartPattern_MASignalMenuDict", {}),
            "chart_conf": getattr(mo, "level4_X_ChartPattern_Confluence_MenuDict", {}),
            "chart_bband": getattr(mo, "level4_X_ChartPattern_BBands_SQZ_MenuDict", {}),
            "candlestick": getattr(mo, "CANDLESTICK_DICT", {}),
            "price_type": getattr(mo, "PRICE_CROSS_SMA_EMA_TYPE_MENUDICT", {}),
            "pivot_type": getattr(mo, "PRICE_CROSS_PIVOT_POINT_TYPE_MENUDICT", {}),
            "price_dir": getattr(mo, "PRICE_CROSS_SMA_EMA_DIRECTION_MENUDICT", {}),
        }
    except Exception as e:
        _debug_log(f"menu context load failed: {e}")
        _PKS_MENU_CONTEXT = {}

    return _PKS_MENU_CONTEXT


def _scan_description_from_code(option_code: str, source: str = ""):
    option = str(option_code or "").strip()
    parts = [p.strip() for p in option.split(":") if str(p).strip() != ""]
    if len(parts) < 3:
        src = source or "catalog"
        return f"PKScreener option | Source: {src} | Option: {option}"

    menu_key = str(parts[0]).upper()
    index_key = str(parts[1]) if len(parts) > 1 else ""
    scan_key = str(parts[2]) if len(parts) > 2 else ""
    level3_key = str(parts[3]) if len(parts) > 3 else ""
    level4_key = str(parts[4]) if len(parts) > 4 else ""

    ctx = _get_pks_menu_context()
    segments = []

    if menu_key == "X" and ctx:
        menu_text = _clean_menu_text(ctx.get("level0", {}).get("X", "Scanners"))
        if menu_text:
            segments.append(menu_text)

        index_text = _clean_menu_text(ctx.get("level1_x", {}).get(index_key, f"Index {index_key}"))
        if index_text:
            segments.append(f"Universe: {index_text}")

        scan_text = _clean_menu_text(ctx.get("level2_x", {}).get(scan_key, f"Scan {scan_key}"))
        if scan_text:
            segments.append(f"Criteria: {scan_text}")

        level3_text = ""
        level4_text = ""

        if level3_key:
            if scan_key == "6":
                level3_text = _clean_menu_text(ctx.get("reversal", {}).get(level3_key, ""))
                if level3_key in {"7", "10"} and level4_key:
                    level4_text = _clean_menu_text(ctx.get("lorentz", {}).get(level4_key, ""))
            elif scan_key == "7":
                level3_text = _clean_menu_text(ctx.get("chart", {}).get(level3_key, ""))
                if level4_key:
                    if level3_key == "3":
                        level4_text = _clean_menu_text(ctx.get("chart_conf", {}).get(level4_key, ""))
                    elif level3_key == "6":
                        level4_text = _clean_menu_text(ctx.get("chart_bband", {}).get(level4_key, ""))
                    elif level3_key == "7":
                        level4_text = _clean_menu_text(ctx.get("candlestick", {}).get(level4_key, ""))
                    elif level3_key == "9":
                        level4_text = _clean_menu_text(ctx.get("chart_ma", {}).get(level4_key, ""))
            elif scan_key == "21":
                level3_text = _clean_menu_text(ctx.get("popular", {}).get(level3_key, ""))
            elif scan_key == "22":
                level3_text = _clean_menu_text(ctx.get("performance", {}).get(level3_key, ""))
            elif scan_key in {"30", "32"}:
                level3_text = _clean_menu_text(ctx.get("lorentz", {}).get(level3_key, ""))
            elif scan_key == "33":
                level3_text = _clean_menu_text(ctx.get("potential", {}).get(level3_key, ""))
            elif scan_key == "40":
                level3_text = _clean_menu_text(ctx.get("price_type", {}).get(level3_key, ""))
                if level4_key:
                    level4_text = _clean_menu_text(ctx.get("price_dir", {}).get(level4_key, ""))
            elif scan_key == "41":
                level3_text = _clean_menu_text(ctx.get("pivot_type", {}).get(level3_key, ""))
                if level4_key:
                    level4_text = _clean_menu_text(ctx.get("price_dir", {}).get(level4_key, ""))

        if level3_text:
            segments.append(f"Filter: {level3_text}")
        if level4_text:
            segments.append(f"Condition: {level4_text}")
    else:
        segments.append(f"Menu: {menu_key}")
        segments.append(f"Criteria: {scan_key}")

    if source:
        segments.append(f"Source: {source}")
    segments.append(f"Option: {option}")
    return " | ".join([s for s in segments if s])


def _menu_options_from_dict(menu_dict, include_keys=None, exclude_keys=None, numeric_only=False):
    include = set(include_keys or [])
    exclude = set(exclude_keys or [])
    options = []
    for key, label in (menu_dict or {}).items():
        k = str(key or "").strip()
        label_text = _clean_menu_text(label)
        label_lc = label_text.lower()
        if not k:
            continue
        if numeric_only and not k.isdigit():
            continue
        if include and k not in include:
            continue
        if k in exclude:
            continue
        if label_lc in {"cancel", "any/all"}:
            continue
        options.append({"key": k, "label": label_text or k})

    options.sort(key=lambda item: (0, int(item["key"])) if item["key"].isdigit() else (1, item["key"]))
    return options


def _children_for_menu_path(path_value: str):
    """
    Return valid next menu choices for a partial PKScreener menu path.
    Examples:
    - ""         -> top menu options (X, D, P, ...)
    - "X"        -> level-1 universe/options under X
    - "X:12"     -> level-2 scan options
    - "X:12:7"   -> level-3 chart pattern options
    """
    ctx = _get_pks_menu_context() or {}
    path = str(path_value or "").strip()
    if not path:
        return {
            "path": "",
            "next_level": 0,
            "options": _menu_options_from_dict(ctx.get("level0", {}), exclude_keys={"M", "Z"}),
        }

    tokens = [t for t in path.split(":") if str(t).strip() != ""]
    tokens = [str(t).strip() for t in tokens]
    if not tokens:
        return {"path": "", "next_level": 0, "options": []}

    menu_key = tokens[0].upper()

    # Generic mapping for top-level menu families. X keeps custom deep handling below.
    generic_level1 = {
        "P": ctx.get("level1_p", {}),
        "D": ctx.get("level1_d", {}),
        "S": ctx.get("level1_s", {}),
        "T": ctx.get("level1_t", {}),
    }

    # Optional level-2 maps for non-X menus. For T, menu key decides Long/Short map.
    generic_level2 = {
        "P": ctx.get("level2_p", {}),
    }

    if menu_key != "X":
        if len(tokens) == 1:
            return {
                "path": menu_key,
                "next_level": 1,
                "options": _menu_options_from_dict(generic_level1.get(menu_key, {}), exclude_keys={"M", "Z"}),
            }

        if len(tokens) == 2:
            level2_map = {}
            if menu_key == "T":
                choice = tokens[1].upper()
                if choice == "L":
                    level2_map = ctx.get("level2_t_l", {})
                elif choice == "S":
                    level2_map = ctx.get("level2_t_s", {})
            elif menu_key == "P":
                # In piped scanners, only predefined branches require a 2nd-level choice.
                # P:1 -> predefined scanners, P:4 -> predefined scanners for watchlist.
                if tokens[1] in {"1", "4"}:
                    level2_map = generic_level2.get(menu_key, {})
            else:
                level2_map = generic_level2.get(menu_key, {})

            return {
                "path": ":".join(tokens),
                "next_level": 2,
                "options": _menu_options_from_dict(level2_map, exclude_keys={"M", "Z"}),
            }

        return {"path": ":".join(tokens), "next_level": len(tokens), "options": []}

    if len(tokens) == 1:
        return {
            "path": "X",
            "next_level": 1,
            "options": _menu_options_from_dict(ctx.get("level1_x", {}), exclude_keys={"M", "Z"}),
        }

    if len(tokens) == 2:
        if tokens[1] not in {str(k).strip() for k in (ctx.get("level1_x", {}) or {}).keys()}:
            return {"path": ":".join(tokens), "next_level": 2, "options": []}
        return {
            "path": ":".join(tokens),
            "next_level": 2,
            "options": _menu_options_from_dict(ctx.get("level2_x", {}), exclude_keys={"M", "Z"}),
        }

    scan_key = tokens[2] if len(tokens) > 2 else ""
    level3_map = None
    if scan_key == "6":
        level3_map = ctx.get("reversal", {})
    elif scan_key == "7":
        level3_map = ctx.get("chart", {})
    elif scan_key == "21":
        level3_map = ctx.get("popular", {})
    elif scan_key == "22":
        level3_map = ctx.get("performance", {})
    elif scan_key == "30":
        level3_map = ctx.get("lorentz", {})
    elif scan_key == "32":
        level3_map = ctx.get("lorentz", {})
    elif scan_key == "33":
        level3_map = ctx.get("potential", {})
    elif scan_key == "40":
        level3_map = ctx.get("price_type", {})
    elif scan_key == "41":
        level3_map = ctx.get("pivot_type", {})

    if len(tokens) == 3:
        return {
            "path": ":".join(tokens),
            "next_level": 3,
            "options": _menu_options_from_dict(level3_map or {}, exclude_keys={"M", "Z"}),
        }

    level3_key = tokens[3] if len(tokens) > 3 else ""
    level4_map = None
    if scan_key == "6" and level3_key in {"7", "10"}:
        level4_map = ctx.get("lorentz", {})
    elif scan_key == "7":
        if level3_key == "3":
            level4_map = ctx.get("chart_conf", {})
        elif level3_key == "6":
            level4_map = ctx.get("chart_bband", {})
        elif level3_key == "7":
            level4_map = ctx.get("candlestick", {})
        elif level3_key == "9":
            level4_map = ctx.get("chart_ma", {})
    elif scan_key == "40":
        level4_map = ctx.get("price_dir", {})
    elif scan_key == "41":
        level4_map = ctx.get("price_dir", {})

    if len(tokens) == 4:
        return {
            "path": ":".join(tokens),
            "next_level": 4,
            "options": _menu_options_from_dict(level4_map or {}, exclude_keys={"M", "Z"}),
        }

    return {"path": ":".join(tokens), "next_level": len(tokens), "options": []}


def _json_response(handler: BaseHTTPRequestHandler, payload: dict, code: int = 200):
    def _json_safe(value):
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
            return value
        if isinstance(value, dict):
            return {k: _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return value

    safe_payload = _json_safe(payload)
    body = json.dumps(safe_payload, allow_nan=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Config-Token")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _ok(handler: BaseHTTPRequestHandler, data: dict = None, message: str = "ok", code: int = 200):
    response = {
        "ok": True,
        "status": code,
        "message": message,
        "request_id": str(uuid.uuid4()),
    }
    if data:
        response.update(data)
    _json_response(handler, response, code=code)


def _fail(handler: BaseHTTPRequestHandler, message: str, code: int = 400, data: dict = None):
    response = {
        "ok": False,
        "status": code,
        "error": message,
        "request_id": str(uuid.uuid4()),
    }
    if data:
        response.update(data)
    _json_response(handler, response, code=code)


def _safe_float(value, default=0.0):
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    text = text.replace("%", "")
    if text == "":
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _extract_stock_name(raw_stock: str):
    if raw_stock is None:
        return "-"
    text = str(raw_stock)
    m = re.search(r'HYPERLINK\("[^"]*",\s*"([^"]+)"\)', text, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return text


def _classify_signal(chg: float, rsi: float):
    if chg >= 1.0 and rsi >= 55:
        return "BUY"
    if chg <= -0.8 or rsi <= 40:
        return "AVOID"
    return "WATCH"


def _latest_csv(after_ts: float):
    if not REPORTS_DIR.exists():
        return None
    csv_files = [p for p in REPORTS_DIR.rglob("*.csv") if p.stat().st_mtime >= after_ts]
    if not csv_files:
        return None
    return max(csv_files, key=lambda p: p.stat().st_mtime)


# Maps PKScreener level-1 universe keys → (local CSV filename, header_rows_to_skip, symbol_col_index)
# Matches PKNSEStockDataFetcher.NSE_INDEX_MAP / REPO_INDEX_MAP
_UNIVERSE_CSV_MAP = {
    "1":  ("ind_nifty50list.csv",         1, 2),
    "2":  ("ind_niftynext50list.csv",     1, 2),
    "3":  ("ind_nifty100list.csv",        1, 2),
    "4":  ("ind_nifty200list.csv",        1, 2),
    "5":  ("ind_nifty500list.csv",        1, 2),
    "6":  ("ind_niftysmallcap50list.csv", 1, 2),
    "7":  ("ind_niftysmallcap100list.csv",1, 2),
    "8":  ("ind_niftysmallcap250list.csv",1, 2),
    "9":  ("ind_niftymidcap50list.csv",   1, 2),
    "10": ("ind_niftymidcap100list.csv",  1, 2),
    "11": ("ind_niftymidcap150list.csv",  1, 2),
    # key 12 = EQUITY_L (all NSE) → no filtering
    # key 14 = FnO → symbol in col 0 after 2 header rows (handled specially below)
}

_UNIVERSE_GITHUB_BASE = "https://raw.githubusercontent.com/pkjmesra/PKScreener/actions-data-download/results/Indices/"


def _download_index_csv(csv_name: str) -> bool:
    """Download an index CSV from GitHub into INDICES_DIR. Returns True on success."""
    try:
        url = _UNIVERSE_GITHUB_BASE + csv_name
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
        INDICES_DIR.mkdir(parents=True, exist_ok=True)
        (INDICES_DIR / csv_name).write_text(content, encoding="utf-8")
        _debug_log(f"Downloaded index CSV: {csv_name}")
        return True
    except Exception as e:
        _debug_log(f"Could not download index CSV {csv_name}: {e}")
        return False


def _get_universe_symbols(index_key: str):
    """
    Return a set of uppercase stock symbols for the given universe key, or None if
    no filtering should be applied (all-stocks universe or unknown key).
    Downloads the index CSV from GitHub if not cached locally.
    """
    key = str(index_key or "").strip()
    if not key or key in ("0", "12", "S", "N", "E", "W", "M"):
        return None  # all-stocks or non-index key – no filter

    entry = _UNIVERSE_CSV_MAP.get(key)
    if not entry:
        return None

    csv_name, header_rows, sym_col = entry
    csv_path = INDICES_DIR / csv_name

    if not csv_path.exists():
        if not _download_index_csv(csv_name):
            return None  # Can't filter without the list

    try:
        symbols = set()
        with csv_path.open("r", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            for _ in range(header_rows):
                next(reader, None)
            for row in reader:
                if len(row) > sym_col:
                    sym = str(row[sym_col]).strip().upper()
                    if sym:
                        symbols.add(sym)
        _debug_log(f"Universe {key} ({csv_name}): {len(symbols)} symbols")
        return symbols if symbols else None
    except Exception as e:
        _debug_log(f"Failed to read universe CSV {csv_name}: {e}")
        return None


def _parse_csv_to_stocks(csv_path: Path, scan_label: str, universe_symbols=None):
    stocks = []
    with csv_path.open("r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stock = _extract_stock_name(row.get("Stock"))
            # Filter to requested universe (e.g. Nifty 50) when a symbol list is provided.
            # PKScreener loads ALL cached stocks when caching is on; this corrects the universe.
            if universe_symbols is not None and str(stock).upper() not in universe_symbols:
                continue
            ltp = _safe_float(row.get("LTP"))
            chg = _safe_float(row.get("%Chng"))
            vol = _safe_float(row.get("volume"), default=1.0)
            rsi = _safe_float(row.get("RSI"), default=50.0)
            w52l = _safe_float(row.get("52Wk-L"), default=0.0)
            w52h = _safe_float(row.get("52Wk-H"), default=max(ltp, 1.0))
            pattern = row.get("Pattern") or row.get("MA-Signal") or scan_label
            signal = _classify_signal(chg, rsi)
            stocks.append(
                {
                    "stock": stock,
                    "sector": "NSE",
                    "ltp": round(ltp, 2),
                    "chg": round(chg, 2),
                    "vol": round(vol, 2),
                    "w52l": round(w52l, 2),
                    "w52h": round(w52h, 2),
                    "rsi": round(rsi, 2),
                    "pattern": str(pattern).upper(),
                    "signal": signal,
                    "d1": round(chg * 0.75, 2),
                    "d2": round(chg * 1.15, 2),
                    "scan": scan_label,
                }
            )
    return stocks


def _parse_stdout_table_stocks(stdout_text: str, scan_label: str, universe_symbols=None):
    """
    Fallback parser for PKScreener console tables when report CSV/XLSX contain
    header only. It extracts stock symbols from rows like:
        |LAURUSLABS|Laurus Labs Ltd|...
    """
    text = str(stdout_text or "")
    if not text:
        return []

    seen = set()
    rows = []
    for line in text.splitlines():
        m = re.match(r"^\|\s*([A-Z0-9&._-]{1,25})\s*\|", str(line or "").strip())
        if not m:
            continue
        sym = str(m.group(1) or "").strip().upper()
        if not sym:
            continue
        if universe_symbols is not None and sym not in universe_symbols:
            continue
        if sym in seen:
            continue
        seen.add(sym)
        rows.append(
            {
                "stock": sym,
                "sector": "NSE",
                "ltp": 0.0,
                "chg": 0.0,
                "vol": 0.0,
                "w52l": 0.0,
                "w52h": 1.0,
                "rsi": 50.0,
                "pattern": str(scan_label or "CUSTOM").upper(),
                "signal": "WATCH",
                "d1": 0.0,
                "d2": 0.0,
                "scan": scan_label,
            }
        )
    return rows


def _effective_universe_for_option(option_value: str):
    """
    Return universe symbols for option when filtering is semantically valid.
    Some PKScreener scans (e.g. scan 21) produce global/fundamental result sets
    that do not align with level-1 index universes; forcing index filters there
    can hide rows visible in terminal/export.
    """
    parts = str(option_value or "").split(":")
    idx_key = parts[1] if len(parts) > 1 else "12"
    return _get_universe_symbols(idx_key)


def _effective_universe_for_report(csv_path: Path, option_value: str = ""):
    """
    Prefer deriving index universe from report path (scan_X_<idx>_...), because
    scan-progress parsing can outlive request-local option handling.
    """
    try:
        ptxt = str(csv_path or "")
        m = re.search(r"scan_X_([^_\\/]+)_", ptxt, flags=re.IGNORECASE)
        if m:
            return _get_universe_symbols(m.group(1))
    except Exception:
        pass
    return _effective_universe_for_option(option_value)


def _persist_fallback_reports(stocks, csv_path: Path):
    """
    Persist fallback rows to report files when PKScreener generated header-only
    CSV/XLSX even though terminal output had data.
    """
    if not stocks or csv_path is None:
        return

    csv_headers = [
        "Stock",
        "LTP",
        "%Chng",
        "volume",
        "RSI",
        "Pattern",
        "Signal",
        "Scan",
    ]

    try:
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_headers)
            writer.writeheader()
            for s in stocks:
                writer.writerow(
                    {
                        "Stock": s.get("stock", ""),
                        "LTP": s.get("ltp", 0.0),
                        "%Chng": s.get("chg", 0.0),
                        "volume": s.get("vol", 0.0),
                        "RSI": s.get("rsi", 50.0),
                        "Pattern": s.get("pattern", ""),
                        "Signal": s.get("signal", "WATCH"),
                        "Scan": s.get("scan", ""),
                    }
                )
    except Exception as e:
        _debug_log(f"could not write fallback csv {csv_path}: {e}")

    xlsx_path = csv_path.with_suffix(".xlsx")
    try:
        import pandas as pd

        rows = []
        for s in stocks:
            rows.append(
                {
                    "Stock": s.get("stock", ""),
                    "LTP": s.get("ltp", 0.0),
                    "%Chng": s.get("chg", 0.0),
                    "volume": s.get("vol", 0.0),
                    "RSI": s.get("rsi", 50.0),
                    "Pattern": s.get("pattern", ""),
                    "Signal": s.get("signal", "WATCH"),
                    "Scan": s.get("scan", ""),
                }
            )
        pd.DataFrame(rows, columns=csv_headers).to_excel(xlsx_path, index=False)
    except Exception as e:
        _debug_log(f"could not write fallback xlsx {xlsx_path}: {e}")


def _tail_log_lines(limit=120):
    if not LOG_FILE.exists():
        return []
    text = LOG_FILE.read_text(encoding="utf-8", errors="ignore")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def _load_ini_raw():
    if not CONFIG_FILE.exists():
        return ""
    return CONFIG_FILE.read_text(encoding="utf-8", errors="ignore")


def _parse_ini(raw_text: str):
    parser = configparser.ConfigParser()
    parser.read_string(raw_text if raw_text.strip() else "[config]\n")
    parsed = {}
    for section in parser.sections():
        parsed[section] = {}
        for key, value in parser.items(section):
            parsed[section][key] = value
    return parsed


def _is_authorized(handler: BaseHTTPRequestHandler, qs: dict):
    if not CONFIG_AUTH_TOKEN:
        return True
    header_token = (handler.headers.get("X-Config-Token") or "").strip()
    query_token = (qs.get("token", [""])[0] or "").strip()
    return header_token == CONFIG_AUTH_TOKEN or query_token == CONFIG_AUTH_TOKEN


def _tcp_probe(host: str, port: int, timeout: float = 1.5):
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = (time.perf_counter() - started) * 1000.0
            return {"ok": True, "latency_ms": round(latency, 2), "error": ""}
    except Exception as e:
        latency = (time.perf_counter() - started) * 1000.0
        return {"ok": False, "latency_ms": round(latency, 2), "error": str(e)}


def _is_local_url(url: str):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _http_probe_local_url(url: str, timeout: float = 2.0):
    started = time.perf_counter()
    try:
        req = Request(url, headers={"User-Agent": "LocalBridge/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read(400).decode("utf-8", errors="ignore")
            latency = (time.perf_counter() - started) * 1000.0
            return {
                "ok": 200 <= int(resp.status) < 400,
                "status": int(resp.status),
                "latency_ms": round(latency, 2),
                "preview": body,
                "error": "",
            }
    except Exception as e:
        latency = (time.perf_counter() - started) * 1000.0
        return {
            "ok": False,
            "status": 0,
            "latency_ms": round(latency, 2),
            "preview": "",
            "error": str(e),
        }


def _elapsed_seconds(started_at_iso: str):
    if not started_at_iso:
        return 0
    try:
        base = started_at_iso.replace("Z", "")
        started = datetime.fromisoformat(base)
        return max(0, int((datetime.utcnow() - started).total_seconds()))
    except Exception:
        return 0


def _stream_scan_process(cmd, scripted_inputs=None):
    """Run scan process with live stdout/stderr streaming and reactive stdin injection."""
    import queue as _queue

    # Every distinct prompt PKScreener can emit before blocking on input().
    # Checked case-insensitively against each stdout line.
    _PROMPT_TRIGGERS = [
        # ── core menu prompts ────────────────────────────────────────────────
        "select option:",       # "[+] Select option:" – levels 0-4 menus
        "[+] select",           # catch "[+] Select Option:" variants
        "enter your choice",
        # ── generic value-entry prompts ──────────────────────────────────────
        "[+] enter",            # catches all "[+] Enter …" prompts
        "enter a number",
        "enter the number",
        "enter value",
        "enter min",            # RSI/CCI min prompts
        "enter max",            # RSI/CCI max prompts
        # ── scan-specific fragments ──────────────────────────────────────────
        "how many candles",     # option 4 (lowest-volume N-days), option 7:1/2
        "ma length",            # option 6:4 MA-reversal
        "nr timeframe",         # option 6:6 Narrow-Range
        "volume ratio",         # option 9 volume multiplier
        "volume should be",     # option 4 alternate phrasing
        "percentage within",    # option 7:3 confluence %
        "price should cross",   # option 40 EMA/SMA
        "anchored-vwap",        # option 34
        "percent change",       # options 42/43 super gainers/losers
        # ── confirm / Y/N prompts ────────────────────────────────────────────
        "press <enter>",
        "choose y or n",
        "[y/n]",
        # ── default-value prompts (many PKScreener prompts show "(Default=X)") ─
        "default=",
        # ── ATR / config prompts ─────────────────────────────────────────────
        "sensitivity",
        "atr period",
        "atr ema",
        # ── VCP / confluence extra prompts ───────────────────────────────────
        "from top:",
        "consolidation legs",
        "enable additional",
    ]

    # Build a FIFO of tokens from the option string (e.g. ["12","6"] for X:12:6).
    # After all scripted tokens are consumed the sentinel makes every further
    # prompt get an empty string → PKScreener accepts its built-in default.
    cleaned_inputs = []
    for t in (scripted_inputs or []):
        tok = str(t).strip()
        if tok:
            cleaned_inputs.append(tok)

    # Pre-feed index/scan tokens because some PKScreener prompts are rendered
    # without newline and cannot be detected via readline() triggers.
    bootstrap_tokens = cleaned_inputs[:2]
    reactive_tokens = cleaned_inputs[2:]

    _stdin_q: _queue.Queue = _queue.Queue()
    for tok in reactive_tokens:
        _stdin_q.put(tok)
    _stdin_q.put(None)  # sentinel: "blanks forever from here"

    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    for tok in bootstrap_tokens:
        try:
            proc.stdin.write(tok + "\n")
            proc.stdin.flush()
            _debug_log(f"stdin bootstrap injected: {repr(tok)}")
        except Exception as e:
            _debug_log(f"stdin bootstrap inject failed: {e}")
            break

    lines = {"stdout": [], "stderr": []}
    io_last_activity = {"ts": time.time()}

    def _inject_next():
        """Write the next scripted token (or blank) to proc.stdin."""
        try:
            tok = _stdin_q.get_nowait()
            if tok is None:          # sentinel – put it back so future prompts get blank
                _stdin_q.put(None)
                tok = ""
        except _queue.Empty:
            tok = ""
        try:
            proc.stdin.write(tok + "\n")
            proc.stdin.flush()
            _debug_log(f"stdin injected: {repr(tok)}")
        except Exception as e:
            _debug_log(f"stdin inject failed: {e}")

    def _reader(pipe, key):
        try:
            for raw in iter(pipe.readline, ""):
                text = (raw or "").rstrip("\r\n")
                if not text:
                    continue
                if key == "stderr" and _is_benign_stderr_line(text):
                    _debug_log("ignored benign stderr: .env.dev file not found")
                    continue
                lines[key].append(text)
                if key == "stdout":
                    _capture_market_from_line(text)
                    lower = text.lower().strip()
                    if any(p in lower for p in _PROMPT_TRIGGERS):
                        _inject_next()
                io_last_activity["ts"] = time.time()
                _app_log(f"[{key}] {text}")
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True)
    t_out.start()
    t_err.start()

    # ── Watchdog: safety-net blank injector ──────────────────────────────────
    # If PKScreener blocks on a prompt whose text didn't match any trigger, it
    # stops printing to stdout.  After WATCHDOG_IDLE_SEC of silence we send a
    # blank "\n" so that PKScreener accepts its built-in default and resumes.
    # With `--accept / -a Y` most defaults are auto-accepted, so this fires
    # only for edge-case prompts that slip through the trigger list.
    WATCHDOG_IDLE_SEC = 8   # seconds of stdout silence before injecting blank
    WATCHDOG_POLL_SEC = 3   # how often the watchdog thread checks

    def _watchdog():
        while proc.poll() is None:
            time.sleep(WATCHDOG_POLL_SEC)
            if proc.poll() is not None:
                break
            if time.time() - io_last_activity["ts"] > WATCHDOG_IDLE_SEC:
                try:
                    # Some PKScreener prompts are printed without a trailing newline.
                    # In those cases readline() never sees a prompt line, so feed the
                    # next scripted token first; once exhausted, _inject_next sends blank.
                    _inject_next()
                    # Update activity so we don't flood – next watchdog feed fires
                    # after another WATCHDOG_IDLE_SEC of silence.
                    io_last_activity["ts"] = time.time()
                    _debug_log("watchdog: injected queued/default stdin to unblock prompt")
                except Exception:
                    pass

    t_wdog = threading.Thread(target=_watchdog, daemon=True)
    t_wdog.start()

    started = time.time()
    while True:
        rc = proc.poll()
        if rc is not None:
            break

        now = time.time()
        if now - started > SCAN_TIMEOUT_SEC:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            t_out.join(timeout=1)
            t_err.join(timeout=1)
            return {
                "timeout": True,
                "inactivity": False,
                "returncode": None,
                "stdout": "\n".join(lines["stdout"]),
                "stderr": "\n".join(lines["stderr"]),
            }

        if now - io_last_activity["ts"] > SCAN_INACTIVITY_TIMEOUT_SEC:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            _app_log(f"[scan] No output for {SCAN_INACTIVITY_TIMEOUT_SEC}s, terminated as stuck")
            t_out.join(timeout=1)
            t_err.join(timeout=1)
            return {
                "timeout": False,
                "inactivity": True,
                "returncode": None,
                "stdout": "\n".join(lines["stdout"]),
                "stderr": "\n".join(lines["stderr"]),
            }

        time.sleep(0.25)

    t_out.join(timeout=1)
    t_err.join(timeout=1)
    return {
        "timeout": False,
        "inactivity": False,
        "returncode": proc.returncode,
        "stdout": "\n".join(lines["stdout"]),
        "stderr": "\n".join(lines["stderr"]),
    }


# ── Input-sequence builder ──────────────────────────────────────────────────
# Map each PKScreener scan option string to its COMPLETE ordered stdin sequence.
# Format: X:<index>:<scan>[:<L3>[:<L4>[:<L5>]]]
# The tokens here mirror what PKScreener would ask for interactively so the
# queue always has the right value at the right prompt.
#
# Scans NOT listed here need only [index, scan] (2 tokens), which is the
# default behaviour (everything after "X:" in the option string).
#
# DEFAULTS used when the option string is shorter than needed:
_SCAN_INPUT_DEFAULTS = {
    # scan → [default_L3, default_L4, ...]
    "4":  ["5"],           # lowest-vol N-candles (default 5)
    "5":  ["55", "68"],    # RSI min, max (default 55–68)
    "6":  ["3"],           # reversal sub-type (default 3 = Momentum Gainers)
    "8":  ["110", "300"],  # CCI min, max
    "9":  ["2.5"],         # volume multiplier (default 2.5×)
    "21": ["1"],           # MF/FII popular stocks sub-type
    "22": ["1"],           # stock performance sub-type (short)
    "30": ["1"],           # ATR trailing-stops direction (1=Buy)
    "32": ["1"],           # intraday breakout direction (1=Buy)
    "33": ["2"],           # potential profitable sub-type
    "40": ["2", "2", "200"],  # SMA/EMA type, direction, MA period
    "41": ["1", "2"],      # pivot level, direction (PP, from below)
    # scan 6 sub-types that need an extra L4 value:
    "6:4": ["50"],         # MA-reversal: MA length (default 50)
    "6:6": ["4"],          # NR: NR timeframe (default 4)
    "6:7": ["1"],          # Lorentzian direction (1=Buy)
    "6:10": ["3"],         # RSI-MA direction (3=Any)
    # scan 7 sub-types:
    "7:1": ["3", "1"],     # Inside Bar: candle lookback, sub-type
    "7:2": ["3", "1"],     # Bearish Inside Bar
    "7:3": ["0.8", "3"],   # Confluence: %, type (3=Any)
    "7:6": ["1"],          # BBands squeeze type (1=TTM Buy)
    "7:9": ["1"],          # MA signal type (1=Support)
}


def _normalize_scan_code(option_value: str):
    raw = str(option_value or "").strip()
    if not raw:
        return ""

    x_pos = raw.upper().find("X:")
    if x_pos >= 0:
        raw = raw[x_pos:]
    if not raw.upper().startswith("X:"):
        return ""

    parts = ["X"]
    for token in raw.split(":")[1:]:
        tok = str(token or "").strip()
        if not tok:
            continue
        tok = tok.rstrip(">").strip()
        if not tok or tok in {">", "<", "|", "~"}:
            continue
        if re.match(r"^i\s+\d+[mhdw]$", tok, flags=re.IGNORECASE):
            parts.append(tok.lower())
            continue
        if re.match(r"^(?:\d+|\d*\.\d+)$", tok):
            parts.append(tok)
            continue
        # Accept enabled menu-key style tokens (e.g., S, N, L) for guided flows.
        if re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", tok):
            parts.append(tok.upper() if len(tok) == 1 else tok)
            continue
        break

    if len(parts) < 3:
        return ""
    return ":".join(parts)


def _canonicalize_scan_option(option_value: str) -> str:
    """
    PKScreener interactive menu uses universe 12 for Nifty (All Stocks).
    Treat legacy/older universe token 0 as 12 for stable automation.
    """
    normalized = _normalize_scan_code(option_value)
    if not normalized:
        return ""

    parts = normalized.split(":")
    # X:<index>:<scan>[:...]
    if len(parts) >= 3 and parts[1] == "0":
        parts[1] = "12"
    return ":".join(parts)


def _option_intraday_timeframe(option_value: str):
    normalized = _normalize_scan_code(option_value)
    if not normalized:
        return ""
    for token in normalized.split(":")[1:]:
        tok = str(token or "").strip().lower()
        if re.match(r"^i\s+\d+[mhdw]$", tok):
            return tok.split(None, 1)[1]
    return ""


def _inputs_for_option(option_value: str) -> list:
    """
    Return the complete ordered stdin token list for a PKScreener option string.

    For option  "X:12:6:4:50" the tokens are already fully specified, so we
    just return them as-is.  For "X:12:6" we pad with the defaults for scan 6
    so PKScreener gets [12, 6, 3] and never hangs on a sub-menu prompt.
    """
    raw = _normalize_scan_code(option_value)
    if not raw.upper().startswith("X:"):
        return []

    all_parts = [p for p in raw.split(":")[1:] if str(p).strip() != ""]
    parts = [p for p in all_parts if not re.match(r"^i\s+\d+[mhdw]$", str(p).strip(), flags=re.IGNORECASE)]
    # parts[0] = index, parts[1] = scan, parts[2..] = L3/L4/…

    if len(parts) < 2:
        return parts  # malformed – return what we have

    scan = parts[1]
    supplied_extra = parts[2:]  # L3 and beyond as supplied by the option string

    # Look up a composite key (scan:L3) first for sub-type dependent defaults
    l3 = supplied_extra[0] if supplied_extra else None
    composite_key = f"{scan}:{l3}" if l3 is not None else None

    if composite_key and composite_key in _SCAN_INPUT_DEFAULTS:
        defaults = _SCAN_INPUT_DEFAULTS[composite_key]
    elif scan in _SCAN_INPUT_DEFAULTS:
        defaults = _SCAN_INPUT_DEFAULTS[scan]
    else:
        defaults = []  # scan is fully self-contained

    # Merge: use supplied values first, then fill remaining with defaults
    merged_extra = list(supplied_extra)
    for i, d in enumerate(defaults):
        if i >= len(merged_extra):
            merged_extra.append(d)

    return parts[:2] + merged_extra


def _extract_scan_codes_from_monitor_string(raw_text: str):
    if not raw_text:
        return []
    codes = []
    seen = set()
    for chunk in re.split(r"[~|]", str(raw_text)):
        text = chunk.strip()
        if not text:
            continue
        x_pos = text.find("X:")
        if x_pos < 0:
            continue
        code = _normalize_scan_code(text[x_pos:].strip())
        if not code:
            continue
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _sanitize_monitor_option_fields_in_ini():
    """Normalize scanner option fields in pkscreener.ini and persist cleanup."""
    if not CONFIG_FILE.exists():
        return

    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        _debug_log(f"could not read config for sanitization: {e}")
        return

    if not raw.strip():
        return

    keys_to_sanitize = {
        "defaultmonitoroptions",
        "soundalertformonitoroptions",
        "mymonitoroptions",
    }

    changed = False
    out_lines = []

    for line in raw.splitlines():
        stripped = line.strip()
        if "=" not in line or stripped.startswith("#") or stripped.startswith(";"):
            out_lines.append(line)
            continue

        left, right = line.split("=", 1)
        key = left.strip().lower()
        if key not in keys_to_sanitize:
            out_lines.append(line)
            continue

        value = right.strip()
        cleaned_codes = _extract_scan_codes_from_monitor_string(value)
        if not cleaned_codes:
            out_lines.append(line)
            continue

        normalized_value = "~".join(cleaned_codes)
        normalized_line = f"{left.rstrip()} = {normalized_value}"
        if normalized_line != line:
            changed = True
        out_lines.append(normalized_line)

    if not changed:
        return

    try:
        CONFIG_FILE.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        _app_log("[config] monitor option strings sanitized and normalized")
    except Exception as e:
        _debug_log(f"could not write sanitized config: {e}")


def _ensure_venv_python_runtime():
    """Relaunch backend with .venv python if started from another interpreter."""
    try:
        current_py = Path(sys.executable).resolve()
    except Exception:
        current_py = Path(sys.executable)

    try:
        current_prefix = Path(sys.prefix).resolve()
    except Exception:
        current_prefix = Path(sys.prefix)

    if not EXPECTED_VENV_PY.exists():
        _debug_log(f"venv python not found at {EXPECTED_VENV_PY}; continuing with {current_py}")
        return

    try:
        expected_py = EXPECTED_VENV_PY.resolve()
    except Exception:
        expected_py = EXPECTED_VENV_PY

    # Some Windows launch paths can report a base sys.prefix even when the
    # executable is already the desired venv python. In that case, do not relaunch.
    if str(current_py).lower() == str(expected_py).lower():
        return

    # Use sys.prefix for robust venv detection. sys.executable can resolve to
    # base interpreter path on some Windows redirector setups.
    if str(current_prefix).lower() == str(EXPECTED_VENV_PREFIX).lower():
        return

    if os.getenv(_VENV_RELAUNCH_FLAG, "") == "1":
        _app_log(
            f"[startup] WARNING: relaunch attempted but still not on venv python "
            f"(exe={current_py}, prefix={current_prefix})"
        )
        return

    relaunch_env = os.environ.copy()
    relaunch_env[_VENV_RELAUNCH_FLAG] = "1"
    relaunch_cmd = [str(EXPECTED_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]]

    _app_log(f"[startup] relaunching backend with venv python: {EXPECTED_VENV_PY}")
    try:
        subprocess.Popen(relaunch_cmd, cwd=str(ROOT), env=relaunch_env)
        raise SystemExit(0)
    except Exception as e:
        _app_log(f"[startup] venv relaunch failed ({e}); continuing with current interpreter")


def _scan_code_from_action_filename(file_name: str):
    if not file_name.lower().endswith(".txt"):
        return ""
    base = file_name[:-4]
    parts = base.split("_")
    if len(parts) < 3 or parts[0] != "X":
        return ""

    scan_parts = ["X"]
    for token in parts[1:]:
        token = token.strip()
        if not token:
            break
        # File names often end with the date, e.g. _2026-03-27.
        if re.match(r"^\d{4}-\d{2}-\d{2}$", token):
            break
        if token.isdigit() and len(token) == 4 and 1900 <= int(token) <= 2100:
            break
        if not re.match(r"^[0-9]+(\.[0-9]+)?$", token):
            break
        scan_parts.append(token)

    if len(scan_parts) < 3:
        return ""
    return _normalize_scan_code(":".join(scan_parts))


def _scan_id_from_code(option_code: str):
    m = re.match(r"^X:[^:]+:(\d+)", option_code)
    if m:
        return m.group(1)
    return ""


def _scan_label_from_code(option_code: str):
    scan_id = _scan_id_from_code(option_code)
    if scan_id:
        return SCAN_LABELS.get(scan_id, f"SCAN-{scan_id}")
    return "CUSTOM"


def _build_scanner_catalog():
    scanners = []
    seen = set()

    # Add options from INI monitor defaults first (highest confidence for this setup).
    raw_ini = _load_ini_raw()
    parsed = _parse_ini(raw_ini)
    monitor_text = parsed.get("config", {}).get("defaultmonitoroptions", "")
    for option in _extract_scan_codes_from_monitor_string(monitor_text):
        option = _canonicalize_scan_option(option)
        if not option:
            continue
        if option in seen:
            continue
        scan_id = _scan_id_from_code(option)
        label = _scan_label_from_code(option)
        scanners.append(
            {
                "id": scan_id or option,
                "scan_id": scan_id,
                "label": f"{label} ({option})",
                "option": option,
                "source": "config",
                "description": _scan_description_from_code(option, source="config"),
            }
        )
        seen.add(option)

    # Add options observed from action scan files next.
    if ACTION_SCAN_DIR.exists():
        for p in ACTION_SCAN_DIR.glob("X_*.txt"):
            option = _scan_code_from_action_filename(p.name)
            option = _canonicalize_scan_option(option)
            if not option or option in seen:
                continue
            scan_id = _scan_id_from_code(option)
            label = _scan_label_from_code(option)
            scanners.append(
                {
                    "id": scan_id or option,
                    "scan_id": scan_id,
                    "label": f"{label} ({option})",
                    "option": option,
                    "source": "actions-data-scan",
                    "description": _scan_description_from_code(option, source="actions-data-scan"),
                }
            )
            seen.add(option)

    # Include curated built-in defaults as fallback choices.
    for scan_id, label in SCAN_LABELS.items():
        option = f"X:12:{scan_id}"
        if option in seen:
            continue
        item = {
            "id": scan_id,
            "scan_id": scan_id,
            "label": f"{label} ({option})",
            "option": option,
            "source": "built-in",
            "description": _scan_description_from_code(option, source="built-in"),
        }
        scanners.append(item)
        seen.add(option)

    return scanners


def _default_scanner_option(scanners):
    if not scanners:
        return "X:12:1"

    for preferred_source in ("actions-data-scan", "config", "built-in"):
        for item in scanners:
            if item.get("source") == preferred_source and item.get("option"):
                return str(item.get("option"))

    first = scanners[0].get("option")
    return str(first) if first else "X:12:1"


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        _ok(self)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path != "/config":
            _fail(self, "Not found", code=404)
            return

        if not _is_authorized(self, qs):
            _fail(self, "Unauthorized", code=401)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="ignore")
            payload = json.loads(raw_body or "{}")
            ini_raw = str(payload.get("raw", ""))
            if not ini_raw.strip():
                _fail(self, "INI content is empty", code=400)
                return

            # Validate INI before writing.
            _parse_ini(ini_raw)
            backup_file = CONFIG_FILE.with_suffix(".ini.bak")
            if CONFIG_FILE.exists():
                backup_file.write_text(_load_ini_raw(), encoding="utf-8")
            CONFIG_FILE.write_text(ini_raw.strip() + "\n", encoding="utf-8")
            _ok(
                self,
                data={
                    "path": str(CONFIG_FILE),
                    "backup": str(backup_file) if backup_file.exists() else "",
                },
                message="Config saved",
            )
        except configparser.Error as e:
            _fail(self, f"Invalid INI: {e}", code=400)
        except json.JSONDecodeError:
            _fail(self, "Invalid JSON body", code=400)
        except Exception as e:
            _fail(self, str(e), code=500)

    def do_GET(self):
        global LAST_SCAN_COMMAND, LAST_SCAN_OPTION, LAST_SCAN_AT, SCAN_RUNNING, SCAN_STARTED_AT, SCAN_START_TS, SCAN_ACTIVE_LABEL
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/health":
            _ok(
                self,
                data={
                    "time": datetime.utcnow().isoformat() + "Z",
                    "features": {"connection_live": True, "scanners_catalog": True, "scan_option": True},
                    "python_executable": sys.executable,
                    "python_prefix": sys.prefix,
                    "python_base_prefix": getattr(sys, "base_prefix", sys.prefix),
                    "scan_timeout_sec": SCAN_TIMEOUT_SEC,
                    "scan_inactivity_timeout_sec": SCAN_INACTIVITY_TIMEOUT_SEC,
                    "scan_running": SCAN_RUNNING,
                    "last_scan_command": LAST_SCAN_COMMAND,
                    "last_scan_at": LAST_SCAN_AT,
                    "market": _market_snapshot(),
                },
            )
            return

        if path == "/runtime":
            _ok(
                self,
                data={
                    "pid": os.getpid(),
                    "python_executable": sys.executable,
                    "python_prefix": sys.prefix,
                    "python_base_prefix": getattr(sys, "base_prefix", sys.prefix),
                    "time": datetime.utcnow().isoformat() + "Z",
                    "scan_running": SCAN_RUNNING,
                    "scan_started_at": SCAN_STARTED_AT,
                    "scan_elapsed_sec": _elapsed_seconds(SCAN_STARTED_AT),
                    "scan_timeout_sec": SCAN_TIMEOUT_SEC,
                    "scan_inactivity_timeout_sec": SCAN_INACTIVITY_TIMEOUT_SEC,
                    "last_scan_command": LAST_SCAN_COMMAND,
                    "last_scan_at": LAST_SCAN_AT,
                    "market": _market_snapshot(),
                },
                message="runtime",
            )
            return

        if path == "/terminal-output":
            payload = {
                "lines": list(APP_TERMINAL_LINES),
                "joined": "\n".join(APP_TERMINAL_LINES),
            }
            _ok(self, data=payload, message="terminal")
            return

        if path == "/scan-progress":
            csv_file = _latest_csv(SCAN_START_TS) if SCAN_START_TS > 0 else None
            stocks = []
            csv_path = ""
            if csv_file is not None:
                csv_path = str(csv_file)
                try:
                    universe_symbols = _effective_universe_for_report(csv_file, LAST_SCAN_OPTION)
                    stocks = _parse_csv_to_stocks(csv_file, SCAN_ACTIVE_LABEL or "LIVE", universe_symbols=universe_symbols)
                except Exception as e:
                    _debug_log(f"scan-progress parse error: {e}")
                    stocks = []

            _ok(
                self,
                data={
                    "scan_running": SCAN_RUNNING,
                    "scan_started_at": SCAN_STARTED_AT,
                    "scan_elapsed_sec": _elapsed_seconds(SCAN_STARTED_AT),
                    "total": len(stocks),
                    "stocks": stocks,
                    "csv_path": csv_path,
                    "market": _market_snapshot(),
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                },
                message="scan-progress",
            )
            return

        if path == "/connection/live":
            server_host = getattr(self.server, "server_address", ("127.0.0.1", 5000))[0]
            server_port = int(getattr(self.server, "server_address", ("127.0.0.1", 5000))[1])
            if server_host in {"", "0.0.0.0"}:
                server_host = "127.0.0.1"

            default_target = f"http://127.0.0.1:{server_port}/health"
            target_url = (qs.get("target", [default_target])[0] or default_target).strip()
            target_allowed = _is_local_url(target_url)

            backend_tcp = _tcp_probe(server_host, server_port)
            target_probe = {
                "url": target_url,
                "allowed": target_allowed,
                "result": (
                    _http_probe_local_url(target_url)
                    if target_allowed
                    else {
                        "ok": False,
                        "status": 0,
                        "latency_ms": 0.0,
                        "preview": "",
                        "error": "Only localhost/127.0.0.1 URLs are allowed",
                    }
                ),
            }

            payload = {
                "time": datetime.utcnow().isoformat() + "Z",
                "backend": {
                    "host": server_host,
                    "port": server_port,
                    "tcp": backend_tcp,
                },
                "target": target_probe,
            }
            _ok(self, data=payload, message="Live connection snapshot")
            return

        if path == "/logs":
            logs = _tail_log_lines()
            payload = {
                "count": len(logs),
                "logs": [
                    {"timestamp": datetime.utcnow().isoformat() + "Z", "level": "INFO", "message": line}
                    for line in logs
                ],
            }
            _ok(self, data=payload)
            return

        if path == "/config":
            if not _is_authorized(self, qs):
                _fail(self, "Unauthorized", code=401)
                return
            raw_ini = _load_ini_raw()
            payload = {
                "raw": raw_ini,
                "parsed": _parse_ini(raw_ini),
                "auth_required": bool(CONFIG_AUTH_TOKEN),
            }
            _ok(self, data=payload)
            return

        if path == "/scanners":
            scanners = _build_scanner_catalog()
            payload = {
                "count": len(scanners),
                "default_option": _default_scanner_option(scanners),
                "scanners": scanners,
            }
            _ok(self, data=payload)
            return

        if path == "/menu-options":
            menu_path = (qs.get("path", [""])[0] or "").strip()
            payload = _children_for_menu_path(menu_path)
            _ok(self, data=payload)
            return

        if path != "/scan":
            _fail(self, "Not found", code=404)
            return

        _app_log(f"[scan-request] {datetime.utcnow().isoformat()}Z {self.path}")

        if not SCAN_LOCK.acquire(blocking=False):
            _app_log("[scan-request] rejected because another scan is already running")
            _fail(
                self,
                "A scan is already running. Please wait for it to complete.",
                code=409,
                data={
                    "scan_running": True,
                    "last_scan_command": LAST_SCAN_COMMAND,
                    "last_scan_at": LAST_SCAN_AT,
                },
            )
            return

        try:
            option = (qs.get("option", [""])[0] or "").strip()
            idx = (qs.get("index", ["12"])[0] or "12").strip()
            scn = (qs.get("scan", ["7"])[0] or "7").strip()

            if option:
                normalized_option = _normalize_scan_code(option)
                if not normalized_option.startswith("X:"):
                    _app_log("[scan-request] rejected invalid option format")
                    _fail(self, "Invalid option value: must start with X:", code=400)
                    return
                option_value = _canonicalize_scan_option(normalized_option)
                scan_label = _scan_label_from_code(option_value)
            else:
                if not idx.isdigit() and idx != "0":
                    _fail(self, "Invalid index value", code=400)
                    return
                if not scn.isdigit():
                    _fail(self, "Invalid scan value", code=400)
                    return
                canonical_idx = "12" if idx == "0" else idx
                option_value = f"X:{canonical_idx}:{scn}"
                scan_label = SCAN_LABELS.get(scn, "CUSTOM")

            # Build a complete, padded-with-defaults input sequence so that
            # every PKScreener prompt (L1 index → L2 scan → L3/L4 sub-filters)
            # gets the right value in the right order.
            scripted_inputs = _inputs_for_option(option_value) if option_value.upper().startswith("X:") else []
            intraday_timeframe = _option_intraday_timeframe(option_value)

            # ── Universe restriction ─────────────────────────────────────────
            # PKScreener's critical branch (globals.py):
            #   savedOrDownloadedKeys = listStockCodes if "," in userArgs.options
            #                           else list(stockDictPrimary.keys())
            # When no comma is present it falls back to all 2438 cached stocks.
            # Fix: append ":," to a fully-explicit option string so a comma is
            # present.  PKScreener then uses listStockCodes, which was set by
            # prepareStocksForScreening(indexOption=N) to the correct universe
            # (e.g. 50 Nifty stocks for N=1).  The trailing bare comma is
            # intentionally invalid so all L3/L4 handlers see it at options[-1]
            # and safely ignore it (they read options[3], options[4], etc. which
            # still hold the real sub-option values).
            _idx_key = option_value.split(":")[1] if len(option_value.split(":")) > 1 else "12"
            _universe = _get_universe_symbols(_idx_key)
            option_for_cmd = option_value
            if _universe:
                # Build a fully-specified option string from scripted_inputs so
                # every sub-option slot already has a valid value, then append :,
                if scripted_inputs:
                    option_for_cmd = "X:" + ":".join(str(x) for x in scripted_inputs) + ":,"
                else:
                    option_for_cmd = option_value + ":,"
                # scripted_inputs is intentionally NOT modified — the index token
                # (e.g. '1' for Nifty 50) must stay so PKScreener calls
                # prepareStocksForScreening(indexOption=1) → fetchStockCodes(1)
                # → exactly the 50 Nifty symbols.
                _debug_log(f"universe rewrite: {option_value} → {option_for_cmd} ({len(_universe)} restricted)")

            started = datetime.utcnow()
            start_ts = datetime.now().timestamp()

            cmd = [
                sys.executable,
                "-m",
                "pkscreener.pkscreenercli",
                "-a",
                "Y",
                "-o",
                option_for_cmd,
                "-e",
                "-l",
            ]
            if intraday_timeframe:
                cmd.extend(["-i", intraday_timeframe])

            LAST_SCAN_COMMAND = " ".join(cmd)
            LAST_SCAN_OPTION = option_for_cmd
            LAST_SCAN_AT = datetime.utcnow().isoformat() + "Z"
            SCAN_STARTED_AT = LAST_SCAN_AT
            SCAN_START_TS = start_ts
            SCAN_ACTIVE_LABEL = scan_label
            SCAN_RUNNING = True
            _app_log(f"[scan] {LAST_SCAN_AT} {LAST_SCAN_COMMAND}")
            _app_log(f"[scan] scripted_inputs sequence: {scripted_inputs}")
            scan_t0 = time.perf_counter()
            _debug_log(f"scan request received path={self.path}")

            try:
                stream_result = _stream_scan_process(cmd, scripted_inputs=scripted_inputs)
                elapsed = round(time.perf_counter() - scan_t0, 2)
                _debug_log(
                    f"scan subprocess completed rc={stream_result.get('returncode')} elapsed_sec={elapsed}"
                )
            except Exception as e:
                _fail(self, f"Scan execution failed: {e}", code=500)
                _debug_log(f"scan execution exception: {e}")
                return

            if stream_result.get("timeout"):
                _fail(
                    self,
                    f"Scan timed out after {SCAN_TIMEOUT_SEC} seconds",
                    code=504,
                    data={
                        "total": 0,
                        "stocks": [],
                        "scan_summary": [{"label": scan_label, "count": 0}],
                        "errors": [{"level": "ERROR", "message": "Scanner execution timeout"}],
                    },
                )
                elapsed = round(time.perf_counter() - scan_t0, 2)
                _debug_log(f"scan timeout after {elapsed}s")
                return

            if stream_result.get("inactivity"):
                _fail(
                    self,
                    f"Scan stopped due to no output for {SCAN_INACTIVITY_TIMEOUT_SEC} seconds",
                    code=504,
                    data={
                        "total": 0,
                        "stocks": [],
                        "scan_summary": [{"label": scan_label, "count": 0}],
                        "errors": [
                            {
                                "level": "ERROR",
                                "message": f"Scanner appears stuck (no output for {SCAN_INACTIVITY_TIMEOUT_SEC}s)",
                            }
                        ],
                        "debug_output": ((stream_result.get("stdout") or "") + "\n" + (stream_result.get("stderr") or "")).strip()[:4000],
                    },
                )
                _debug_log("scan terminated due to inactivity")
                return

            stdout_text = (stream_result.get("stdout") or "").strip()
            stderr_text = (stream_result.get("stderr") or "").strip()
            debug_output = (stdout_text + "\n" + stderr_text).strip()[:4000]
            if DEBUG_MODE:
                if stdout_text:
                    _debug_log(f"stdout tail: {stdout_text[-500:]}")
                if stderr_text:
                    _debug_log(f"stderr tail: {stderr_text[-500:]}")

            csv_file = _latest_csv(start_ts)
            if csv_file is None:
                payload = {
                    "total": 0,
                    "date": datetime.now().strftime("%d-%b-%Y"),
                    "stocks": [],
                    "scan_summary": [{"label": scan_label, "count": 0}],
                    "errors": [
                        {
                            "level": "ERROR",
                            "message": "No report CSV generated. Check PKScreener logs/output.",
                        }
                    ],
                    "debug_output": debug_output,
                    "all_logs": [
                        {
                            "timestamp": started.isoformat() + "Z",
                            "level": "INFO",
                            "message": "Command executed",
                            "details": " ".join(cmd),
                        },
                        {
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                            "level": "ERROR",
                            "message": "No CSV report found after run",
                            "details": stderr_text[:300],
                        },
                    ],
                }
                _fail(
                    self,
                    "No report CSV generated. Check PKScreener logs/output.",
                    code=200,
                    data=payload,
                )
                _debug_log("scan finished without CSV output")
                return

            _universe = _effective_universe_for_option(option_value)
            stocks = _parse_csv_to_stocks(csv_file, scan_label, universe_symbols=_universe)
            if not stocks:
                # PKScreener occasionally prints a full table to stdout but writes
                # header-only CSV/XLSX. Fall back to parsing symbols from stdout so
                # UI reflects what users see in terminal.
                stdout_fallback = _parse_stdout_table_stocks(stdout_text, scan_label, universe_symbols=_universe)
                if stdout_fallback:
                    stocks = stdout_fallback
                    _persist_fallback_reports(stocks, csv_file)
                    _debug_log(f"stdout fallback used: {len(stocks)} rows")
            payload = {
                "total": len(stocks),
                "date": datetime.now().strftime("%d-%b-%Y"),
                "stocks": stocks,
                "scan_summary": [{"label": scan_label, "count": len(stocks)}],
                "market": _market_snapshot(),
                "errors": [] if int(stream_result.get("returncode") or 0) == 0 else [{"level": "WARN", "message": "Scanner exited with non-zero code"}],
                "debug_output": debug_output,
                "all_logs": [
                    {
                        "timestamp": started.isoformat() + "Z",
                        "level": "INFO",
                        "message": "Command executed",
                        "details": " ".join(cmd),
                    },
                    {
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "level": "INFO",
                        "message": "Latest CSV parsed",
                        "details": str(csv_file),
                    },
                ],
            }
            _debug_log(f"scan completed with {len(stocks)} rows")
            _ok(self, data=payload, message="Scan completed")
        finally:
            SCAN_RUNNING = False
            SCAN_LOCK.release()


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main():
    _ensure_venv_python_runtime()
    _sanitize_monitor_option_fields_in_ini()

    host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("DASHBOARD_PORT", "5050"))
    try:
        server = ReusableThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        _app_log(f"Failed to start local bridge on http://{host}:{port}: {e}")
        raise

    _app_log(f"Local bridge running on http://{host}:{port} (pid={os.getpid()})")
    server.serve_forever()


if __name__ == "__main__":
    main()
