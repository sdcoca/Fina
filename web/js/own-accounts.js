// web/js/own-accounts.js
//
// WP-22b/WP-23 (R-3.8): reads and writes the account-names file's JSON -- the names (aliases)
// the user gave their accounts. Pure functions, no DOM and no storage: `app.js` loads the
// stored file(s) through `storage.js`, updates them here, and stores the result back. No
// financial logic: the engine (`fina`) validates the file and otherwise ignores it.
//
// Shape (`src/fina/adapters/own_accounts_json.py` is the authority and rejects anything else):
//   {"format": "fina-own-accounts", "version": 3,
//    "aliases": {"<IBAN, or institution for an account without one>": "<name>"}}
// Versions 1 and 2 (the retired "Mine / Not mine" confirmation file) are still read: only
// their names are kept, and the next change writes version 3 without the old decisions.

export const FORMAT = "fina-own-accounts";
export const VERSION = 3;

/**
 * The names stored in one file's bytes; none if it cannot be read -- a file the engine itself
 * would reject is then simply replaced by the next change.
 *
 * @param {Uint8Array} bytes
 * @returns {{aliases: Object<string, string>}}
 */
export function parseDocument(bytes) {
  const aliases = {};
  try {
    const doc = JSON.parse(new TextDecoder("utf-8").decode(bytes));
    if (doc && doc.format === FORMAT && doc.aliases && typeof doc.aliases === "object") {
      for (const [key, name] of Object.entries(doc.aliases)) {
        if (typeof name === "string" && name.trim() && key.trim()) {
          aliases[key] = name;
        }
      }
    }
  } catch {
    // fall through: no names
  }
  return { aliases };
}

/**
 * Several documents into one (only more than one after a backup restore): each account keeps
 * the name from the first document that has one (the documents are passed current-first).
 *
 * @param {Array<{aliases: Object<string, string>}>} docs
 * @returns {{aliases: Object<string, string>}}
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
  return { aliases };
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
 * @param {{aliases: Object<string, string>}} doc
 * @returns {Uint8Array} the file's bytes.
 */
export function serializeDocument(doc) {
  const out = { format: FORMAT, version: VERSION, aliases: doc.aliases };
  return new TextEncoder().encode(`${JSON.stringify(out, null, 2)}\n`);
}
