// web/js/accounts.js
//
// WP-22b/WP-23: the "Accounts" section (after the chart). One card per account:
//   1. Accounts with a statement, listed directly (no group of their own, owner's decision
//                             2026-09-27), each with its own files inside (each file's include
//                             checkbox lives here: the separate "Stored files" card is gone).
//                             Each can be given a name (alias, stored in the account-names file
//                             so it travels with the backup).
//   2. "Might be yours"    -- a collapsible group, R-3.8: accounts in the owner's name with no statement, each with
//                             an "Import its statement" button. Display only: they change no
//                             figure (owner's decision, 2026-09-27, replacing "Mine / Not
//                             mine" -- every transfer counts as money in or out, R-3.4).
//   3. "Other files"       -- a collapsible group: stored files not linked to an account yet (they have never been
//                             part of a successful run).
// Collapsed by default; after a file is loaded, the section and "Might be yours" open by
// themselves when it has accounts. Everything is built with createElement/textContent -- file
// names and holder names are never parsed as HTML.
//
// No financial logic: every amount is a display string from `web/py/bridge.py`, only
// regrouped with thousands separators here, never parsed as a JS number (CLAUDE.md rule 9).

const INSTITUTION_LABELS = {
  trade_republic: "Trade Republic",
  bank_es: "Bank",
};

const GROUPS = [
  {
    id: "maybe",
    label: "Might be yours",
    note:
      "Your statements show transfers to or from these accounts, under the same holder name " +
      "(probably you).",
  },
  {
    id: "unlinked",
    label: "Other files",
    note: "Linked to their account after the next successful run.",
  },
];

// Session state kept across re-renders: which groups the user opened, which account is being
// renamed.
const _openGroups = new Set();
let _editingKey = null;

/** `"-25000.5"` -> `"-25,000.5 €"`: regroups an already-rounded display string. */
export function formatEur(display) {
  const negative = display.startsWith("-");
  const [whole, cents] = display.replace(/^-/, "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}${cents !== undefined ? `.${cents}` : ""} €`;
}

export function groupIban(iban) {
  return iban.replace(/(.{4})(?=.)/g, "$1 ");
}

function _formatSize(bytes) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function _el(tag, className, text) {
  const el = document.createElement(tag);
  if (className) {
    el.className = className;
  }
  if (text !== undefined) {
    el.textContent = text;
  }
  return el;
}

function _button(label, className, onClick) {
  const button = _el("button", `acc-button ${className}`, label);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

function _disableAll(root) {
  for (const b of root.querySelectorAll("button, input")) {
    b.disabled = true;
  }
}

/** The account's name: its alias, else a default built from what the statement says. */
function _title(aliases, key, fallback) {
  return aliases[key] || fallback;
}

function _titleBlock(root, ctx, key, fallback) {
  const block = _el("div", "account-title");
  if (_editingKey === key) {
    const form = _el("form", "alias-edit");
    const input = _el("input", "alias-input");
    input.type = "text";
    input.value = ctx.model.aliases[key] || "";
    input.placeholder = fallback;
    input.setAttribute("aria-label", "Account name");
    input.maxLength = 60;
    form.appendChild(input);
    form.appendChild(_button("Save", "acc-button--primary acc-button--small", () => {}));
    form.lastChild.type = "submit";
    form.appendChild(
      _button("Cancel", "acc-button--small", () => {
        _editingKey = null;
        ctx.rerender();
      })
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      _editingKey = null;
      _disableAll(root);
      ctx.handlers.onRename(key, input.value).catch(ctx.handlers.onError);
    });
    block.appendChild(form);
    queueMicrotask(() => input.focus());
  } else {
    block.appendChild(_el("p", "account-alias", _title(ctx.model.aliases, key, fallback)));
  }
  return block;
}

function _renameLink(ctx, key) {
  const link = _button("Rename", "acc-rename", () => {
    _editingKey = key;
    ctx.rerender();
  });
  return link;
}

function _badge(text, kind) {
  return _el("span", `badge badge--${kind}`, text);
}

function _fileList(ctx, files) {
  const list = _el("ul", "files");
  const sorted = [...files].sort((a, b) => a.filename.localeCompare(b.filename));
  for (const file of sorted) {
    const li = _el("li", "stored-file");
    li.dataset.fileId = file.id;
    if (!file.active) {
      li.classList.add("stored-file-inactive");
    }
    const label = _el("label", "stored-file-row");
    const checkbox = _el("input", "stored-file-checkbox");
    checkbox.type = "checkbox";
    checkbox.checked = file.active;
    checkbox.setAttribute("aria-label", `Include ${file.filename} in the next run`);
    checkbox.addEventListener("change", () => {
      ctx.handlers.onToggleFile(file.id, checkbox.checked).catch(ctx.handlers.onError);
    });
    const info = _el("div", "stored-file-info");
    info.appendChild(_el("span", "stored-file-name", file.filename));
    const note = file.active ? "" : " · not included";
    info.appendChild(
      _el(
        "span",
        "stored-file-meta",
        `${file.recognizedAs ?? "unknown"} · ${_formatSize(file.size)}${note}`
      )
    );
    label.appendChild(checkbox);
    label.appendChild(info);
    li.appendChild(label);
    list.appendChild(li);
  }
  return list;
}

function _statementCard(root, ctx, account) {
  const li = _el("li", "account account--statement");
  li.dataset.accountKey = account.key;
  const institution = INSTITUTION_LABELS[account.institution] || account.institution;
  const head = _el("div", "account-head");
  const title = _titleBlock(root, ctx, account.key, institution);
  title.appendChild(_el("p", "account-sub", `${institution} · ${account.holder_name}`));
  head.appendChild(title);
  head.appendChild(_badge("Statement", "statement"));
  li.appendChild(head);
  if (account.iban) {
    li.appendChild(_el("p", "iban", groupIban(account.iban)));
  }
  if (_editingKey !== account.key) {
    li.appendChild(_renameLink(ctx, account.key));
  }
  if (account.balance) {
    li.appendChild(_el("p", "balance", `Balance: ${formatEur(account.balance)}`));
  }
  li.appendChild(_fileList(ctx, account.files));
  return li;
}

function _candidateCard(ctx, candidate) {
  const li = _el("li", "account own-account");
  li.dataset.iban = candidate.iban;
  const holder = candidate.holder_names.join(" · ");
  const head = _el("div", "account-head");
  const title = _el("div", "account-title");
  title.appendChild(_el("p", "account-alias", holder));
  head.appendChild(title);
  head.appendChild(_badge("No statement", "maybe"));
  li.appendChild(head);
  li.appendChild(_el("p", "iban", groupIban(candidate.iban)));
  const count = candidate.transfers === 1 ? "1 transfer" : `${candidate.transfers} transfers`;
  const period =
    candidate.first_date === candidate.last_date
      ? candidate.first_date
      : `${candidate.first_date} – ${candidate.last_date}`;
  const meta = _el(
    "p",
    "own-account-meta",
    `${count} · in ${formatEur(candidate.total_in)} · out ${formatEur(candidate.total_out)} · `
  );
  meta.appendChild(_el("span", "nowrap", period)); // a date never breaks in the middle
  li.appendChild(meta);
  const actions = _el("div", "own-account-actions");
  actions.appendChild(
    _button("Import its statement", "acc-button--small own-account-import", () =>
      ctx.handlers.onImport()
    )
  );
  li.appendChild(actions);
  return li;
}

/**
 * Renders the whole section into `section` (the `<details id="accounts-section">`).
 *
 * @param {HTMLDetailsElement} section
 * @param {{statementAccounts: Array<object>, candidates: Array<object>,
 *   unlinkedFiles: Array<object>, aliases: Object<string, string>}} model
 * @param {{onToggleFile: Function, onRename: Function, onImport: Function,
 *   onError: Function}} handlers
 * @param {{expandAttention?: boolean}} options -- true right after a file was loaded: open
 *   the section and the "Might be yours" group when it has accounts.
 */
export function renderAccounts(section, model, handlers, { expandAttention = false } = {}) {
  const groupsEl = section.querySelector("#accounts-groups");
  const emptyEl = section.querySelector("#accounts-empty");
  const ctx = {
    model,
    handlers,
    rerender: () => renderAccounts(section, model, handlers),
  };

  if (expandAttention && model.candidates.length > 0) {
    section.open = true;
    _openGroups.add("maybe");
  }

  const cardsByGroup = {
    maybe: model.candidates.map((c) => () => _candidateCard(ctx, c)),
    unlinked: [],
  };
  const empty = model.statementAccounts.length === 0 &&
    model.candidates.length === 0 &&
    model.unlinkedFiles.length === 0;
  emptyEl.hidden = !empty;
  if (empty) {
    section.open = true; // nothing else on the page yet: show how to start
  }

  groupsEl.replaceChildren();
  // Accounts with a statement are the list itself (owner's decision, 2026-09-27): no group.
  if (model.statementAccounts.length > 0) {
    const list = _el("ul", "accounts-list accounts-list--statements");
    for (const account of model.statementAccounts) {
      list.appendChild(_statementCard(groupsEl, ctx, account));
    }
    groupsEl.appendChild(list);
  }
  for (const group of GROUPS) {
    const isUnlinked = group.id === "unlinked";
    const count = isUnlinked ? model.unlinkedFiles.length : cardsByGroup[group.id].length;
    if (count === 0) {
      continue;
    }
    const details = _el("details", "acc-group");
    details.dataset.group = group.id;
    details.open = _openGroups.has(group.id);
    details.addEventListener("toggle", () => {
      if (details.open) {
        _openGroups.add(group.id);
      } else {
        _openGroups.delete(group.id);
      }
    });
    const summary = _el("summary", "group-summary");
    summary.appendChild(_el("span", "group-label", group.label));
    summary.appendChild(_el("span", "count", String(count)));
    details.appendChild(summary);
    if (group.note) {
      details.appendChild(_el("p", "group-note", group.note));
    }
    if (isUnlinked) {
      details.appendChild(_fileList(ctx, model.unlinkedFiles));
    } else {
      const list = _el("ul", "accounts-list");
      for (const build of cardsByGroup[group.id]) {
        list.appendChild(build(groupsEl));
      }
      details.appendChild(list);
    }
    groupsEl.appendChild(details);
  }
}

/** Forgets session-only UI state (tests reset between phases through a reload anyway). */
export function resetAccountsState() {
  _openGroups.clear();
  _editingKey = null;
}
