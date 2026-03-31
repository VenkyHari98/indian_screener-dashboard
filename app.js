const screenerData = [
  {
    title: "Volume Gainer",
    code: "X:12:9",
    description: "Finds stocks with unusual volume expansion.",
    ideal: "Best for active sessions and pre-breakout tracking.",
    pairing: "Pair with ATR Cross (X:12:27) to reduce weak breakouts.",
    tip: "Ideal in liquid counters. Avoid very low-volume symbols."
  },
  {
    title: "Fair Value Opportunities",
    code: "X:12:21:8",
    description: "Screens for value dislocations against fair-value model.",
    ideal: "Use for swing watchlists where valuation matters.",
    pairing: "Pair with trend filters to avoid cheap but weak setups.",
    tip: "Ideal for medium-term entries, not rapid intraday moves."
  },
  {
    title: "Deals Scanner",
    code: "X:12:23",
    description: "Highlights block, bulk, and short-deal activity patterns.",
    ideal: "Use when you want institutional activity context.",
    pairing: "Pair with volume and momentum for confirmation.",
    tip: "Deal activity can be noisy; use as confirmation not standalone."
  },
  {
    title: "ATR Cross",
    code: "X:12:27",
    description: "Captures directional strength through ATR-based break behavior.",
    ideal: "Good for momentum continuation filters.",
    pairing: "Pair with volume gainer and stage-2 filter.",
    tip: "High ATR names need wider stop planning."
  },
  {
    title: "High Momentum",
    code: "X:12:31",
    description: "Uses RSI/MFI/CCI strength to isolate fast movers.",
    ideal: "Useful for short to medium term momentum baskets.",
    pairing: "Pair with minimum volume and max display controls.",
    tip: "Momentum can reverse sharply, so avoid crowded late entries."
  },
  {
    title: "VCP Pattern",
    code: "X:12:7:4",
    description: "Finds contraction and setup structures inspired by VCP behavior.",
    ideal: "Strong for positional entries with structure-based risk.",
    pairing: "Pair with bull-cross and anchored VWAP filters.",
    tip: "Best in stage-2 trends with rising relative strength."
  }
];

const configMeta = {
  alwaysexporttoexcel: {
    label: "Always Export To Excel",
    purpose: "Auto-saves scanner results to spreadsheet files.",
    ideal: "y for automated workflows",
    helper: "Set y to avoid manual prompt every run.",
    type: "toggle"
  },
  minprice: {
    label: "Minimum Price",
    purpose: "Excludes low-priced symbols.",
    ideal: "20 to 100 for cleaner liquidity",
    helper: "Raise this if you want fewer speculative names."
  },
  maxprice: {
    label: "Maximum Price",
    purpose: "Upper price ceiling for selected stocks.",
    ideal: "2000 to 10000 for manageable ticket sizes",
    helper: "Keep broad unless your strategy has a strict cap."
  },
  minimumvolume: {
    label: "Minimum Volume",
    purpose: "Filters out illiquid symbols.",
    ideal: "100000 plus for reliable execution",
    helper: "Higher volume usually means cleaner breakouts."
  },
  volumeratio: {
    label: "Volume Ratio",
    purpose: "Required multiple of volume compared with baseline.",
    ideal: "1.8 to 2.8",
    helper: "Higher values catch stronger participation."
  },
  daystolookback: {
    label: "Days To Lookback",
    purpose: "Lookback window for trend and signal context.",
    ideal: "22 for monthly trend context",
    helper: "Lower for faster strategy, higher for smoother trend."
  },
  onlystagetwostocks: {
    label: "Only Stage Two Stocks",
    purpose: "Focuses on stocks in stronger trend phase.",
    ideal: "y for trend-following setups",
    helper: "Disable only when hunting deep reversals.",
    type: "toggle"
  },
  enableadditionalvcpfilters: {
    label: "Enable Additional VCP Filters",
    purpose: "Adds extra tightening checks for VCP style setups.",
    ideal: "y for quality over quantity",
    helper: "Can reduce noise significantly in VCP scans.",
    type: "toggle"
  },
  period: {
    label: "Historical Period",
    purpose: "Data window used for calculations.",
    ideal: "1y for balanced context",
    helper: "Increase for long-term setups, reduce for tactical scans."
  },
  duration: {
    label: "Candle Duration",
    purpose: "Primary candle timeframe for analytics.",
    ideal: "1d for swing, intraday values for short term",
    helper: "Match this to your holding horizon."
  },
  maxdisplayresults: {
    label: "Max Display Results",
    purpose: "Controls number of rows shown and saved.",
    ideal: "50 to 150",
    helper: "Smaller list is easier to review deeply."
  },
  superconfluenceemaperiods: {
    label: "Super Confluence EMA Periods",
    purpose: "EMA set used by super-confluence checks.",
    ideal: "8,21,55",
    helper: "Keep default unless strategy explicitly changes this."
  },
  telegramsamplenumberrows: {
    label: "Telegram Sample Rows",
    purpose: "How many rows are shown in summary messages.",
    ideal: "5 to 12",
    helper: "Higher values increase message size."
  }
};

const recommendedDefaults = {
  alwaysexporttoexcel: "y",
  minprice: "20.0",
  maxprice: "50000.0",
  minimumvolume: "100000",
  volumeratio: "2.5",
  daystolookback: "22",
  onlystagetwostocks: "y",
  enableadditionalvcpfilters: "y",
  period: "1y",
  duration: "1d",
  maxdisplayresults: "100",
  superconfluenceemaperiods: "8,21,55",
  telegramsamplenumberrows: "5"
};

const state = {
  rawText: "",
  parsed: {
    config: {},
    filters: {}
  }
};

function initTabs() {
  const tabButtons = document.querySelectorAll(".tab");
  const tabPanels = document.querySelectorAll(".tab-panel");
  tabButtons.forEach((button) => {
    button.addEventListener("click", () => {
      tabButtons.forEach((b) => b.classList.remove("active"));
      tabPanels.forEach((p) => p.classList.remove("active"));
      button.classList.add("active");
      document.getElementById(button.dataset.tab).classList.add("active");
    });
  });
}

function renderScreeners() {
  const grid = document.getElementById("screener-grid");
  const template = document.getElementById("screener-card-template");
  grid.innerHTML = "";
  screenerData.forEach((item) => {
    const node = template.content.cloneNode(true);
    node.querySelector("h3").textContent = item.title;
    node.querySelector(".code").textContent = item.code;
    node.querySelector(".desc").textContent = "Purpose: " + item.description;
    node.querySelector(".ideal").textContent = "Ideal: " + item.ideal;
    node.querySelector(".pair").textContent = "Best paired with: " + item.pairing;
    node.querySelector(".tip").textContent = "Tooltip: " + item.tip;
    grid.appendChild(node);
  });
}

function parseIni(ini) {
  const parsed = { config: {}, filters: {} };
  let section = "";
  ini.split(/\r?\n/).forEach((line) => {
    const clean = line.trim();
    if (!clean || clean.startsWith(";")) return;
    if (clean.startsWith("[") && clean.endsWith("]")) {
      section = clean.slice(1, -1).toLowerCase();
      return;
    }
    const idx = clean.indexOf("=");
    if (idx < 0) return;
    const key = clean.slice(0, idx).trim().toLowerCase();
    const value = clean.slice(idx + 1).trim();
    if (section === "config") parsed.config[key] = value;
    if (section === "filters") parsed.filters[key] = value;
  });
  return parsed;
}

function buildIni(parsed) {
  const lines = [];
  lines.push("[config]");
  Object.entries(parsed.config).forEach(([k, v]) => lines.push(`${k} = ${v}`));
  lines.push("");
  lines.push("[filters]");
  Object.entries(parsed.filters).forEach(([k, v]) => lines.push(`${k} = ${v}`));
  lines.push("");
  return lines.join("\n");
}

function renderConfigFields() {
  const host = document.getElementById("config-grid");
  const template = document.getElementById("config-item-template");
  host.innerHTML = "";

  Object.entries(configMeta).forEach(([key, meta]) => {
    const node = template.content.cloneNode(true);
    const label = node.querySelector(".label-text");
    const input = node.querySelector("input");
    const mini = node.querySelector(".mini");
    const tooltip = node.querySelector(".tooltip-box");

    label.textContent = meta.label;
    input.value = state.parsed.config[key] ?? state.parsed.filters[key] ?? "";
    input.dataset.key = key;
    mini.textContent = meta.helper;
    tooltip.textContent = `Purpose: ${meta.purpose} | Ideal: ${meta.ideal}`;

    input.addEventListener("input", (ev) => {
      const val = ev.target.value;
      if (key in state.parsed.config) state.parsed.config[key] = val;
      else if (key in state.parsed.filters) state.parsed.filters[key] = val;
      else if (["minprice", "maxprice", "minimumvolume", "volumeratio"].includes(key)) state.parsed.filters[key] = val;
      else state.parsed.config[key] = val;
      document.getElementById("raw-preview").value = buildIni(state.parsed);
    });

    host.appendChild(node);
  });
  document.getElementById("raw-preview").value = buildIni(state.parsed);
}

async function loadIniFromRepo() {
  try {
    const res = await fetch("pkscreener.ini", { cache: "no-store" });
    if (!res.ok) throw new Error("Could not load pkscreener.ini");
    state.rawText = await res.text();
    state.parsed = parseIni(state.rawText);
  } catch {
    state.parsed = {
      config: { ...recommendedDefaults },
      filters: {
        minprice: recommendedDefaults.minprice,
        maxprice: recommendedDefaults.maxprice,
        minimumvolume: recommendedDefaults.minimumvolume,
        volumeratio: recommendedDefaults.volumeratio
      }
    };
  }
  renderConfigFields();
}

function resetRecommended() {
  Object.entries(recommendedDefaults).forEach(([key, value]) => {
    if (key in state.parsed.filters) state.parsed.filters[key] = value;
    else state.parsed.config[key] = value;
  });
  renderConfigFields();
}

function downloadIniFile() {
  const text = buildIni(state.parsed);
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "pkscreener.ini";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function wireActions() {
  document.getElementById("load-ini").addEventListener("click", loadIniFromRepo);
  document.getElementById("reset-defaults").addEventListener("click", resetRecommended);
  document.getElementById("download-ini").addEventListener("click", downloadIniFile);
}

function init() {
  initTabs();
  renderScreeners();
  wireActions();
  loadIniFromRepo();
}

init();
