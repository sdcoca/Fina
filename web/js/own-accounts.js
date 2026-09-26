// web/js/own-accounts.js
//
// WP-19 (R-3.8): reads and writes the own-accounts confirmation file's JSON -- the user's
// "mine / not mine" decision per account. Pure functions, no DOM and no storage: `app.js`
// loads the stored file(s) through `storage.js`, updates them here, and stores the result back.
// No financial logic: the file only records decisions; the engine (`fina`) does the rest.
//
// Shape (`src/fina/adapters/own_accounts_json.py` is the authority and rejects anything else):
//   {"format": "fina-own-accounts", "version": 1,
//    "accounts": [{"iban", "holder_name", "owned": true|false, "decided_on": "YYYY-MM-DD"}]}

export const FORMAT = "fina-own-accounts";
export const VERSION = 1;

/**
 * The decisions stored in one confirmation file's bytes, or `[]` if it cannot be read -- a
 * file the engine itself would reject is then simply replaced by the next decision.
 *
 * @param {Uint8Array} bytes
 * @returns {Array<{iban: string, holder_name: string, owned: boolean, decided_on: string}>}
 */
export function parseDecisions(bytes) {
  try {
    const doc = JSON.parse(new TextDecoder("utf-8").decode(bytes));
    if (doc && doc.format === FORMAT && Array.isArray(doc.accounts)) {
      return doc.accounts.filter((a) => a && typeof a.iban === "string");
    }
  } catch {
    // fall through
  }
  return [];
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
 * @param {Array<object>} decisions
 * @returns {Uint8Array} the confirmation file's bytes.
 */
export function serializeDecisions(decisions) {
  const doc = { format: FORMAT, version: VERSION, accounts: decisions };
  return new TextEncoder().encode(`${JSON.stringify(doc, null, 2)}\n`);
}

/** Today's local date as `YYYY-MM-DD`. */
export function todayIso(now = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
