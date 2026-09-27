// web/js/accounts.js
//
// WP-22b: the "Accounts" section (after the chart). One card per account, in collapsible
// groups:
//   1. "With a statement"            -- accounts a statement declares, with their own files
//                                       inside (each file's include checkbox lives here now:
//                                       the separate "Stored files" card is gone).
//   2. "Needs your decision"         -- R-3.8 candidates still pending: "Mine" / "Not mine".
//   3. "Yours, without a statement"  -- confirmed accounts: estimated balance (R-3.9) and, when
//                                       money must have reached them from outside, a
//                                       "Missing data" note.
//   4. "Not yours"
//   5. "Other files"                 -- stored files not linked to an account yet (they have
//                                       never been part of a successful run).
// Every account can be given a name (alias, stored in the confirmation file so it travels with
// the backup). Collapsed by default; after a file is loaded, the section and the groups that
// need attention open by themselves (owner's decision, 2026-09-26). Everything is built with
// createElement/textContent -- file names and holder names are never parsed as HTML.
//
// No financial logic: every amount is a display string from `web/py/bridge.py`, only
// regrouped with thousands separators here, never parsed as a JS number (CLAUDE.md rule 9).

const INSTITUTION_LABELS = {
  trade_republic: "Trade Republic",
  bank_es: "Bank",
};

const GROUPS = [
  { id: "statement", label: "With a statement" },
  { id: "pending", label: "Needs your decision" },
  { id: "owned", label: "Yours, without a statement" },
  { id: "not_owned", label: "Not yours" },
  { id: "unlinked", label: "Other files" },
];

// Session state kept across re-renders: which groups the user opened, which decided accounts
// were reopened with "Change", which account is being renamed.
const _openGroups = new Set();
const _reopened = new Set();
let _editingKey = null;

/** `"-25000.5"` -> `"-25,000.5 €"`: regroups an already-rounded display string. */
export function formatEur(display) {
  const negative = display.startsWith("-");
  const [whole, cents] = display.replace(/^-/, "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}${cents !== undefined ? `.${cents}` : ""} €`;
}

function _isZero(display) {
  return /^-?0*(\.0*)?$/.test(display);
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

function _candidateCard(root, ctx, candidate) {
  const li = _el("li", `account own-account own-account--${candidate.status}`);
  li.dataset.iban = candidate.iban;
  const missingData = candidate.status === "owned" && !_isZero(candidate.unseen_income);
  if (missingData) {
    li.classList.add("own-account--missing-data");
  }
  const holder = candidate.holder_names.length ? candidate.holder_names.join(" · ") : "—";
  const head = _el("div", "account-head");
  const title =
    candidate.status === "owned"
      ? _titleBlock(root, ctx, candidate.iban, holder)
      : (() => {
          const block = _el("div", "account-title");
          block.appendChild(_el("p", "account-alias", holder));
          return block;
        })();
  if (candidate.status === "owned" && ctx.model.aliases[candidate.iban]) {
    title.appendChild(_el("p", "account-sub", holder));
  }
  head.appendChild(title);
  const badges = {
    pending: ["Is it yours?", "pending"],
    owned: ["Estimated", "estimated"],
    not_owned: ["Not yours", "not-yours"],
  };
  head.appendChild(_badge(...badges[candidate.status]));
  li.appendChild(head);
  li.appendChild(_el("p", "iban", groupIban(candidate.iban)));
  if (candidate.status === "owned" && _editingKey !== candidate.iban) {
    li.appendChild(_renameLink(ctx, candidate.iban));
  }
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
  if (candidate.status === "owned") {
    li.appendChild(
      _el(
        "p",
        "own-account-balance",
        `Estimated balance: ${formatEur(candidate.estimated_balance)}`
      )
    );
  }
  if (missingData) {
    li.appendChild(
      _el(
        "p",
        "own-account-missing",
        `Missing data: at least ${formatEur(candidate.unseen_income)} reached this account ` +
          "from outside. Import its statement."
      )
    );
  }

  const actions = _el("div", "own-account-actions");
  const decide = (owned) => () => {
    _reopened.delete(candidate.iban);
    _openGroups.add(owned ? "owned" : "not_owned"); // keep the card just decided in view
    _disableAll(root);
    ctx.handlers.onDecide(candidate, owned).catch(ctx.handlers.onError);
  };
  if (candidate.status === "pending" || _reopened.has(candidate.iban)) {
    actions.appendChild(_button("Mine", "acc-button--primary own-account-button--mine", decide(true)));
    actions.appendChild(_button("Not mine", "own-account-button--not-mine", decide(false)));
  } else {
    const status =
      candidate.status === "owned" ? "Marked as yours" : "Counted as money from outside";
    actions.appendChild(_el("span", "own-account-status", status));
    if (candidate.status === "owned") {
      actions.appendChild(
        _button("Import its statement", "acc-button--small own-account-import", () =>
          ctx.handlers.onImport()
        )
      );
    }
    actions.appendChild(
      _button("Change", "acc-button--small own-account-button--change", () => {
        _reopened.add(candidate.iban);
        ctx.rerender();
      })
    );
  }
  li.appendChild(actions);
  return li;
}

/** How many cards in each group need the user: a decision, or a statement to fill a gap. */
function _attention(model) {
  return {
    pending: model.candidates.filter((c) => c.status === "pending").length,
    owned: model.candidates.filter((c) => c.status === "owned" && !_isZero(c.unseen_income))
      .length,
  };
}

/**
 * Renders the whole section into `section` (the `<details id="accounts-section">`).
 *
 * @param {HTMLDetailsElement} section
 * @param {{statementAccounts: Array<object>, candidates: Array<object>,
 *   unlinkedFiles: Array<object>, aliases: Object<string, string>}} model
 * @param {{onToggleFile: Function, onDecide: Function, onRename: Function,
 *   onImport: Function, onError: Function}} handlers
 * @param {{expandAttention?: boolean}} options -- true right after a file was loaded: open
 *   the section and every group that needs attention.
 */
export function renderAccounts(section, model, handlers, { expandAttention = false } = {}) {
  const groupsEl = section.querySelector("#accounts-groups");
  const emptyEl = section.querySelector("#accounts-empty");
  const badgeEl = section.querySelector("#accounts-attention");
  const attention = _attention(model);
  const attentionTotal = attention.pending + attention.owned;
  const ctx = {
    model,
    handlers,
    rerender: () => renderAccounts(section, model, handlers),
  };

  if (expandAttention) {
    if (attentionTotal > 0) {
      section.open = true;
    }
    for (const id of ["pending", "owned"]) {
      if (attention[id] > 0) {
        _openGroups.add(id);
      }
    }
  }

  badgeEl.hidden = attentionTotal === 0;
  badgeEl.textContent =
    attentionTotal === 1 ? "1 needs attention" : `${attentionTotal} need attention`;

  const cardsByGroup = {
    statement: model.statementAccounts.map((a) => (root) => _statementCard(root, ctx, a)),
    pending: [],
    owned: [],
    not_owned: [],
    unlinked: [],
  };
  for (const c of model.candidates) {
    cardsByGroup[c.status].push((root) => _candidateCard(root, ctx, c));
  }
  const empty = GROUPS.every((g) => cardsByGroup[g.id].length === 0) &&
    model.unlinkedFiles.length === 0;
  emptyEl.hidden = !empty;
  if (empty) {
    section.open = true; // nothing else on the page yet: show how to start
  }

  groupsEl.replaceChildren();
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
    if (attention[group.id] > 0) {
      const dot = _el("span", "dot");
      dot.setAttribute("aria-label", "needs attention");
      summary.appendChild(dot);
    }
    details.appendChild(summary);
    if (isUnlinked) {
      details.appendChild(
        _el("p", "group-note", "Linked to their account after the next successful run.")
      );
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
  _reopened.clear();
  _editingKey = null;
}
