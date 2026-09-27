// web/js/own-accounts.js
//
// WP-19/WP-22b (R-3.8): reads and writes the own-accounts confirmation file's JSON -- the
// user's "mine / not mine" decision per account, and the names (aliases) they gave their
// accounts. Pure functions, no DOM and no storage: `app.js` loads the stored file(s) through
// `storage.js`, updates them here, and stores the result back. No financial logic: the file
// only records decisions and names; the engine (`fina`) does the rest.
//
// Shape (`src/fina/adapters/own_accounts_json.py` is the authority and rejects anything else):
//   {"format": "fina-own-accounts", "version": 2,
//    "accounts": [{"iban", "holder_name", "owned": true|false, "decided_on": "YYYY-MM-DD"}],
//    "aliases": {"<IBAN, or institution for an account without one>": "<name>"}}

export const FORMAT = "fina-own-accounts";
export const VERSION = 2;

/**
 * The decisions and names stored in one confirmation file's bytes; an empty document if it
 * cannot be read -- a file the engine itself would reject is then simply replaced by the next
 * change.
 *
 * @param {Uint8Array} bytes
 * @returns {{accounts: Array<object>, aliases: Object<string, string>}}
 */
export function parseDocument(bytes) {
  try {
    const doc = JSON.parse(new TextDecoder("utf-8").decode(bytes));
    if (doc && doc.format === FORMAT && Array.isArray(doc.accounts)) {
      const aliases = {};
      if (doc.aliases && typeof doc.aliases === "object" && !Array.isArray(doc.aliases)) {
        for (const [key, name] of Object.entries(doc.aliases)) {
          if (typeof name === "string" && name.trim() && key.trim()) {
            aliases[key] = name;
          }
        }
      }
      return {
        accounts: doc.accounts.filter((a) => a && typeof a.iban === "string"),
        aliases,
      };
    }
  } catch {
    // fall through
  }
  return { accounts: [], aliases: {} };
}

/**
 * One decision per IBAN out of several files' decisions (only more than one file after a
 * backup restore): the latest `decided_on` wins; on a tie, the earlier list wins.
 *
 * @param {Array<Array<object>>} lists
 * @returns {Array<object>} in first-seen order.
 */
export function mergeDecisions(lists) {
  const byIban = new Map();
  for (const list of lists) {
    for (const decision of list) {
      const current = byIban.get(decision.iban);
      if (current === undefined || decision.decided_on > current.decided_on) {
        byIban.set(decision.iban, decision);
      }
    }
  }
  return [...byIban.values()];
}

/**
 * Several documents into one: decisions per `mergeDecisions`; names from the first document
 * that has one for each account (the documents are passed current-first).
 *
 * @param {Array<{accounts: Array<object>, aliases: Object<string, string>}>} docs
 */
export function mergeDocuments(docs) {
  const aliases = {};
  for (const doc of docs) {
    for (const [key, name] of Object.entries(doc.aliases)) {
      if (!(key in aliases)) {
        aliases[key] = name;
      }
    }
  }
  return { accounts: mergeDecisions(docs.map((d) => d.accounts)), aliases };
}

/**
 * `decisions` with `decision` recorded: replaces the one for the same IBAN, else appends.
 *
 * @param {Array<object>} decisions
 * @param {{iban: string, holder_name: string, owned: boolean, decided_on: string}} decision
 * @returns {Array<object>}
 */
export function withDecision(decisions, decision) {
  const out = decisions.filter((d) => d.iban !== decision.iban);
  const index = decisions.findIndex((d) => d.iban === decision.iban);
  out.splice(index === -1 ? out.length : index, 0, decision);
  return out;
}

/**
 * `aliases` with `key` named `name`; a blank name removes it (back to the default label).
 *
 * @param {Object<string, string>} aliases
 * @param {string} key
 * @param {string} name
 * @returns {Object<string, string>}
 */
export function withAlias(aliases, key, name) {
  const out = { ...aliases };
  const trimmed = name.trim();
  if (trimmed) {
    out[key] = trimmed;
  } else {
    delete out[key];
  }
  return out;
}

/**
 * @param {{accounts: Array<object>, aliases: Object<string, string>}} doc
 * @returns {Uint8Array} the confirmation file's bytes.
 */
export function serializeDocument(doc) {
  const out = { format: FORMAT, version: VERSION, accounts: doc.accounts, aliases: doc.aliases };
  return new TextEncoder().encode(`${JSON.stringify(out, null, 2)}\n`);
}

/** Today's local date as `YYYY-MM-DD`. */
export function todayIso(now = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
