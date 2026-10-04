// web/js/price-check.js
//
// The diagnostic behind web/price-check.html (owner's decision 2026-09-27: test option B, a
// free price provider the app can call directly). Not part of the app: it only reports, from
// the app's own web address, (1) which providers' answers a page may read (CORS), and (2) what
// a provider returns for one of the owner's holdings with their own free key. Nothing is
// stored; the key never appears in the copied results.

const PROBES = [
  ["marketstack v2", "https://api.marketstack.com/v2/eod?symbols=AAPL"],
  ["marketstack v1", "https://api.marketstack.com/v1/eod?symbols=AAPL"],
  ["FCS API", "https://api-v4.fcsapi.com/stock/search?search=AAPL"],
  ["Twelve Data", "https://api.twelvedata.com/symbol_search?symbol=AAPL"],
  ["Alpha Vantage", "https://www.alphavantage.co/query?function=TIME_SERIES_MONTHLY&symbol=IBM&apikey=demo"],
  ["FMP", "https://financialmodelingprep.com/stable/historical-price-eod/light?symbol=AAPL"],
  ["EODHD", "https://eodhd.com/api/eod/MCD.US?api_token=demo&fmt=json&period=m"],
  ["Finnhub", "https://finnhub.io/api/v1/quote?symbol=AAPL"],
  ["Yahoo Finance", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1y&interval=1mo"],
  ["Stooq", "https://stooq.com/q/d/l/?s=aapl.us&i=m"],
  ["Börse Frankfurt", "https://api.boerse-frankfurt.de/v1/data/price_history?isin=DE0007164600&mic=XETR"],
  ["justETF", "https://www.justetf.com/api/etfs/IE00B4L5Y983/quote?locale=en&currency=EUR"],
  ["Frankfurter (ECB FX)", "https://api.frankfurter.app/latest?from=USD&to=EUR"],
];

// {KEY} is the key, {Q} the typed query, {FROM} a date two years back.
const PROVIDERS = {
  "marketstack v2": {
    search: "https://api.marketstack.com/v2/tickerslist?access_key={KEY}&search={Q}",
    history: "https://api.marketstack.com/v2/eod?access_key={KEY}&symbols={Q}&date_from={FROM}&limit=1000",
  },
  "marketstack v1": {
    search: "https://api.marketstack.com/v1/tickers?access_key={KEY}&search={Q}",
    history: "https://api.marketstack.com/v1/eod?access_key={KEY}&symbols={Q}&date_from={FROM}&limit=1000",
  },
  // FCS API v4 (https://fcsapi.com/document/stock-api): "Public Key" is the key a page may
  // carry -- it only works from the domains allowed in the FCS dashboard (Security -> Allowed
  // Domains). The access key is the secret one; both work here for the test.
  "FCS API (Public Key)": {
    search: "https://api-v4.fcsapi.com/stock/search?search={Q}&_public_key={KEY}",
    history: "https://api-v4.fcsapi.com/stock/history?symbol={Q}&period=month&length=60&_public_key={KEY}",
  },
  "FCS API (access key)": {
    search: "https://api-v4.fcsapi.com/stock/search?search={Q}&access_key={KEY}",
    history: "https://api-v4.fcsapi.com/stock/history?symbol={Q}&period=month&length=60&access_key={KEY}",
  },
  "Twelve Data": {
    search: "https://api.twelvedata.com/symbol_search?symbol={Q}&apikey={KEY}",
    history: "https://api.twelvedata.com/time_series?symbol={Q}&interval=1month&outputsize=36&apikey={KEY}",
  },
  "Alpha Vantage": {
    search: "https://www.alphavantage.co/query?function=SYMBOL_SEARCH&keywords={Q}&apikey={KEY}",
    history: "https://www.alphavantage.co/query?function=TIME_SERIES_MONTHLY_ADJUSTED&symbol={Q}&apikey={KEY}",
  },
  FMP: {
    search: "https://financialmodelingprep.com/stable/search-isin?isin={Q}&apikey={KEY}",
    history: "https://financialmodelingprep.com/stable/historical-price-eod/light?symbol={Q}&from={FROM}&apikey={KEY}",
  },
  EODHD: {
    search: "https://eodhd.com/api/search/{Q}?api_token={KEY}&fmt=json",
    history: "https://eodhd.com/api/eod/{Q}?api_token={KEY}&fmt=json&period=m&from={FROM}",
  },
};

const $ = (id) => document.getElementById(id);
const _log = [];

function _twoYearsAgo() {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 2);
  return d.toISOString().slice(0, 10);
}

function _addResult(listId, ok, title, detail) {
  const li = document.createElement("li");
  li.className = ok ? "pc-ok" : "pc-bad";
  const strong = document.createElement("strong");
  strong.textContent = title;
  li.appendChild(strong);
  if (detail) {
    const raw = document.createElement("div");
    raw.className = "pc-raw";
    raw.textContent = detail;
    li.appendChild(raw);
  }
  $(listId).appendChild(li);
  _log.push(`${ok ? "OK " : "NO "} ${title}${detail ? `\n    ${detail.replace(/\n/g, "\n    ")}` : ""}`);
}

/** One request; `readable` is false when the browser refused to hand over the answer. */
async function _request(url) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    const text = await response.text();
    return { readable: true, status: response.status, text };
  } catch (err) {
    return { readable: false, error: String(err && err.message ? err.message : err) };
  }
}

const _DATE_KEYS = ["date", "datetime", "Date", "time", "tm"];
const _CLOSE_KEYS = ["close", "adj_close", "adjClose", "c", "Close", "price"];

/** Finds the longest array of {date, close}-like rows anywhere in `json`. */
function _findSeries(json) {
  let best = null;
  const visit = (node) => {
    if (Array.isArray(node)) {
      const rows = node.filter(
        (r) => r && typeof r === "object" && _DATE_KEYS.some((k) => k in r) && _CLOSE_KEYS.some((k) => k in r)
      );
      if (rows.length && (!best || rows.length > best.length)) {
        best = rows.map((r) => ({
          date: String(r[_DATE_KEYS.find((k) => k in r)]).slice(0, 10),
          close: r[_CLOSE_KEYS.find((k) => k in r)],
        }));
      }
      node.forEach(visit);
    } else if (node && typeof node === "object") {
      // FCS shape: {"1722519000": {"c": 218.36, "tm": "2024-08-01 13:30:00"}, ...}
      const values = Object.values(node);
      if (values.length > 1 && values.every((v) => v && typeof v === "object" && "tm" in v && "c" in v)) {
        const rows = values.map((v) => ({ date: String(v.tm).slice(0, 10), close: v.c }));
        if (!best || rows.length > best.length) {
          best = rows;
        }
      }
      // Alpha Vantage shape: {"2026-08-29": {"4. close": "..."}, ...}
      const dated = Object.keys(node).filter((k) => /^\d{4}-\d{2}-\d{2}/.test(k));
      if (dated.length > 1) {
        const rows = dated.map((k) => {
          const v = node[k] || {};
          const closeKey = Object.keys(v).find((c) => /close/i.test(c));
          return { date: k.slice(0, 10), close: closeKey ? v[closeKey] : undefined };
        });
        if (!best || rows.length > best.length) {
          best = rows;
        }
      }
      Object.values(node).forEach(visit);
    }
  };
  visit(json);
  return best;
}

function _summarize(text) {
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    return `not JSON: ${text.slice(0, 300)}`;
  }
  const series = _findSeries(json);
  if (series && series.length) {
    const sorted = [...series].sort((a, b) => (a.date < b.date ? -1 : 1));
    const first = sorted[0];
    const last = sorted[sorted.length - 1];
    return (
      `${series.length} prices from ${first.date} to ${last.date}; last close ${last.close}\n` +
      text.slice(0, 400)
    );
  }
  return text.slice(0, 700);
}

async function runProbes() {
  $("probe-results").replaceChildren();
  $("probe-button").disabled = true;
  _log.push("== 1. Which providers answer this page?");
  // All at once, listed in a fixed order whatever order they answer in.
  const results = await Promise.all(PROBES.map(([, url]) => _request(url)));
  PROBES.forEach(([name], i) => {
    const result = results[i];
    if (result.readable) {
      _addResult("probe-results", true, `${name}: answered (HTTP ${result.status})`, result.text.slice(0, 160));
    } else {
      _addResult("probe-results", false, `${name}: blocked by the browser, or unreachable`, "");
    }
  });
  $("probe-button").disabled = false;
}

function _url(kind) {
  const provider = PROVIDERS[$("provider").value];
  const key = $("api-key").value.trim();
  const query = $("query").value.trim();
  return {
    url: provider[kind]
      .replace("{KEY}", encodeURIComponent(key))
      .replace("{Q}", encodeURIComponent(query))
      .replace("{FROM}", _twoYearsAgo()),
    key,
  };
}

async function runKeyed(kind) {
  const { url, key } = _url(kind);
  const shown = key ? url.split(encodeURIComponent(key)).join("***") : url;
  const label = `${$("provider").value} ${kind} "${$("query").value.trim()}"`;
  _log.push(`== 2. ${label}\n    ${shown}`);
  const result = await _request(url);
  if (!result.readable) {
    _addResult("key-results", false, `${label}: blocked by the browser, or unreachable`, shown);
    return;
  }
  const summary = _summarize(result.text);
  _addResult(
    "key-results",
    result.status < 400,
    `${label}: HTTP ${result.status}`,
    key ? summary.split(key).join("***") : summary
  );
}

async function copyResults() {
  const key = $("api-key").value.trim();
  let text = `Fina price check, ${new Date().toISOString()}\n${_log.join("\n")}`;
  if (key) {
    text = text.split(key).join("***");
  }
  try {
    await navigator.clipboard.writeText(text);
    $("copy-status").textContent = "Copied. Paste it in the chat.";
  } catch {
    $("copy-status").textContent = "Could not copy; select the results above instead.";
  }
  $("copy-status").hidden = false;
}

for (const name of Object.keys(PROVIDERS)) {
  const option = document.createElement("option");
  option.value = name;
  option.textContent = name;
  $("provider").appendChild(option);
}
$("probe-button").addEventListener("click", runProbes);
$("search-button").addEventListener("click", () => runKeyed("search"));
$("history-button").addEventListener("click", () => runKeyed("history"));
$("copy-button").addEventListener("click", copyResults);
window.__finaPriceCheckReady = true;
