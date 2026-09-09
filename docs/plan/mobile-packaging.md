# Mobile packaging: from installable PWA to a real Android (and later iOS) app

Research and design only. No application code is touched by this document.

**Scope boundary.** Two sibling work streams are handling (a) the Pyodide-in-browser research
(getting the existing verified Python core to run client-side) and (b) the PWA shell itself —
manifest, service worker, offline caching, IndexedDB-backed storage, and the file-import UX.
This document assumes both land and does not re-design either. Its own scope is narrower and
comes *after* both: given a working installable PWA, how does the same codebase become a real
installable app on Android today, and what should be done now so iOS is cheap to add later.
Where a recommendation here depends on a detail the shell/storage stream owns (e.g. response
headers, exact storage API used), that dependency is named explicitly rather than assumed.

**How to read this document.** Section numbers map to the four questions this research was
scoped to answer. Each section states the options considered, a recommendation marked
explicitly, and — where the choice is a genuine product decision rather than a technical one —
a pointer to the corresponding entry in `docs/plan/open-questions.md`.

---

## 1. Wrapping technology: Capacitor vs the alternatives

### 1.1 What "wrapping" means here

The PWA shell (sibling stream) already produces an installable web app: a manifest, a service
worker, and — on Android Chrome specifically — a genuine install prompt that creates a **WebAPK**,
not a bookmark. This is worth stating plainly because it changes the premise of "beyond just add
to home screen" in one respect: on Android, "add to home screen" for a manifest-compliant PWA
*is* a real, signed Android package (Chrome builds and signs it on Google's infrastructure), it
gets its own entry in Android Settings → Apps, its own icon, a standalone window with no browser
chrome, and can be uninstalled like any other app. It is not the same thing as iOS's historical
"add to home screen," which is just a bookmark shortcut with no package behind it. So on Android,
some of "real installable app" is already solved by the PWA shell alone, at zero extra packaging
cost. What a WebAPK does **not** give: access to any native API beyond what the mobile browser
itself exposes, a listing in the Play Store, or a store-signed artifact under the project owner's
own control. Those are what a wrapper is for.

### 1.2 Options considered

1. **Capacitor** (Ionic). Wraps an existing web bundle in a thin native Android/iOS project that
   hosts it in a native WebView, with a plugin bridge to native APIs (filesystem, secure storage,
   biometrics, share intents, etc.). No rewrite of the app; the web bundle is the app.
2. **Trusted Web Activity (TWA)**, typically generated via Bubblewrap or PWABuilder. Android-only.
   Renders the PWA inside Chrome's own rendering process (not a bundled WebView), so the resulting
   APK is very small (roughly 800 KB vs. Capacitor's ~4 MB, because Capacitor ships its own
   WebView runtime) and always matches the installed Chrome's capabilities. Requires the site to
   pass Google's PWA/service-worker checks and a Digital Asset Links file proving domain
   ownership.
3. **Full native rewrite** (Flutter, React Native, or platform-native Kotlin/Swift). Rejected
   outright at this stage: it forks the codebase the project explicitly wants to avoid forking,
   and throws away the "same PWA codebase" premise of this whole research task.
4. **No wrapper at all** — ship only the installable PWA (per §1.1) on Android, and the
   equivalent installed-PWA experience on iOS Safari once that lands. Zero extra tooling, but no
   path to any native API the browser doesn't expose, ever, on either platform.

### 1.3 Why Capacitor over TWA — the deciding factor is iOS

TWA is Android-only by construction: it is a way of hosting a PWA inside *Chrome*, and Apple does
not ship an equivalent mechanism for hosting a PWA inside Safari as an installable native
package. Choosing TWA now would mean solving the Android wrapping problem with a tool that has
no story whatsoever for "and later iOS" — a second, unrelated wrapping mechanism would have to be
adopted from scratch when iOS work starts, doubling the packaging surface for no benefit. Capacitor
is the one option in this list that is genuinely one codebase for both target platforms: the same
web bundle, the same plugin calls, two native project folders (`android/`, `ios/`) generated once
and maintained going forward, with platform differences isolated to the plugins that need them.
Given the project owner's explicit framing — "ready to add iOS too" — this is close to a decisive
factor, not a marginal one. TWA would also be the *cheaper* Android-only path, and if iOS support
were ever abandoned as a goal, TWA would deserve a second look; it is not being ruled out for any
Android-only weakness of its own, only because it does not extend to the platform this project
has already said it wants next.

### 1.4 Recommendation

**Capacitor**, adopted as the wrapper for whenever native APIs are actually needed (see §2 for
what "actually needed" resolves to this iteration). It wraps the existing bundle without a fork,
covers both target platforms with one mechanism, and every native capability this project can
plausibly want (§2) has a maintained Capacitor plugin. This is a technology choice, not a
decision to wrap *now* — §4 addresses timing.

### 1.5 A real Play Store risk worth naming up front

Google Play's developer policy explicitly rejects "thin wrapper around a website" apps. A
Capacitor app that does nothing a browser tab couldn't already do is exactly that description on
paper. This is not a reason to avoid Capacitor — it is a reason the wrapper should only be stood
up once it is actually carrying a native capability the browser can't provide (secure storage
outside browser eviction, biometric lock, a share-sheet import target — §2), at which point the
"thin wrapper" objection no longer applies on the merits. It only becomes a live risk *if* the
project ever pursues Play Store distribution (§3) with a wrapper that adds nothing functional;
for direct/sideloaded distribution (§3.4) this policy has no teeth at all, since Google Play
policy only governs what Google Play distributes.

---

## 2. Native APIs Capacitor would realistically be used for here

### 2.1 File / import access

The import UX itself is the sibling PWA stream's territory; this is only about what the
*wrapper* would add to it.

- On Android, a browser-based `<input type="file">` inside an installed PWA already opens the
  system file picker and reaches the Downloads folder without any wrapper — this is not a gap
  Capacitor closes, it is already solved today. What Capacitor adds on top is the ability to
  register as an **Android share-sheet target** (an `<intent-filter>` in the native manifest),
  so the bank's own app, or the Files app, can "Share → Fina" directly with an exported statement
  file, instead of the user opening Fina first and picking the file from inside it. That is a
  genuine, native-only convenience — a plain browser PWA cannot register a share target on
  Android without going through the separate, more limited Web Share Target API (§4.3).
- Practical read: this is a nice-to-have layered on an import flow that already works via the
  file picker, not a capability gap that blocks anything. Worth doing once the wrapper exists for
  another reason; not, on its own, a reason to stand the wrapper up.

### 2.2 Secure / persistent local storage

This is the strongest technical argument for the wrapper, and it connects directly to §5.2's iOS
storage-eviction risk. Capacitor's `Preferences`/secure-storage plugins write to the OS's own
keystore (Android Keystore-backed storage, iOS Keychain) rather than to the browser's site-data
partition. Data there is **not** subject to a browser's own storage-eviction policy (§5.2) at
all — it is governed by OS-level app-uninstall semantics instead, which is a materially different
and, for this project's rule 18 constraint (data lives only on-device), safer failure mode. On
Android this is a nice-to-have today (Chrome's own eviction policy is far less aggressive than
Safari's — see §5.2), but the same mechanism becomes close to necessary once iOS is a real
target, because it sidesteps WebKit's 7-day eviction risk entirely rather than merely mitigating
it.

### 2.3 Biometric app-lock

Capacitor's Biometrics plugin (Face ID/Touch ID on iOS, fingerprint/face unlock on Android) is
the natural way to gate the app behind a second factor once/if that is prioritized (§6). It is
listed here for completeness, but §6 recommends deferring app-lock for this iteration regardless
of wrapper — and when it is picked up, a **plain browser WebAuthn platform-authenticator call**
(no wrapper required — supported in Chrome on Android and Safari 16+ on iOS) gets most of the
same user-facing effect for free, before any Capacitor investment. Capacitor's own biometrics
plugin is the better long-term answer only once the wrapper exists for another reason and native
UI polish (a custom lock screen, retry/lockout handling) starts to matter.

### 2.4 Priority ordering, this iteration

Of these three, none is a hard blocker today: file import already works via `<input type="file">`
(§2.1); app-lock is explicitly deferred (§6); secure storage's main payoff is iOS-specific and iOS
isn't being shipped yet. This is the basis for §7's recommendation to defer standing up the
wrapper itself, while keeping Capacitor as the named target technology for whenever one of these
becomes a real requirement.

---

## 3. Play Store distribution vs. direct distribution

### 3.1 Play Store mechanics

1. **Developer account**: a one-time **US$25** fee, no recurring renewal, paid during Play
   Console sign-up.
2. **Signing**: Google's **Play App Signing** is effectively mandatory for new apps — Google
   holds the app-signing key and re-signs the app for distribution; the developer keeps only an
   upload key. This is standard, low-friction, and not itself a blocker.
3. **The closed-testing gate**: for personal developer accounts opened after November 2023 (this
   would be a new account, so this applies), Google requires a closed test with **at least 12
   distinct testers, each opted in for 14 continuous days**, before the app is allowed onto
   production. This is the practical blocker for a single-user tool: there is no legitimate way
   for one person to self-satisfy a 12-distinct-tester requirement. It is not a matter of waiting
   longer or paying more — the account genuinely needs other people using the app for two weeks
   before Google will let it go to production at all.
4. **Review time**: beyond the testing gate, store assets (listing text, screenshots, privacy
   policy, data-safety declaration) and the first review pass typically add another 2-4 weeks
   before an app is live, even once the tester gate is satisfied.
5. **Ongoing cost**: none beyond keeping the app's target API level current as Android versions
   advance — a maintenance obligation, not a one-time one, that only makes sense to take on if
   the app is actually being distributed to people who'd notice if it broke.

### 3.2 Direct distribution options (no store)

1. **The installable PWA itself** (§1.1) — already a real installed Android app once the shell
   lands, zero extra tooling, zero review, zero account.
2. **A self-signed Capacitor APK, sideloaded** — built locally, installed via Android's "install
   unknown apps" permission on the project owner's own device. No developer account, no review,
   no tester quota. The tradeoff is exactly what "sideloading" always means: no Play Protect
   store-review baseline, and every future update is a manual reinstall rather than a store's
   auto-update — both irrelevant frictions for a one-person tool the owner builds and installs
   themself, and both would need revisiting the moment this is shared with anyone else.

### 3.3 A financial-data-specific consideration

This app handles the project owner's real bank and brokerage data (CLAUDE.md rule 18: it never
leaves the device). A Play Store listing, even a private/unlisted one, adds a Google-mediated
distribution channel and a public developer identity to something that has so far been designed
specifically to avoid any external data path. That is a reason to be more conservative about
store distribution here than for a typical hobby app, independent of the tester-quota blocker in
§3.1.3 — one more argument for §3.4's recommendation, not a new one.

### 3.4 Recommendation

**Skip the Play Store for now; use direct distribution.** The tester-quota gate in §3.1.3 is not
a close call for a single-user personal tool — it cannot be satisfied honestly by one person, so
pursuing it now would mean either recruiting testers for an app that has none, or misrepresenting
the testing. Combined with §3.3's data-exposure consideration, there is no case for Play Store
listing at this stage. Revisit if and when the app is ever shared with other people — at that
point the 12-tester gate stops being an obstacle and becomes something the app already has
naturally, and the ongoing target-API maintenance cost becomes worth paying because other people
would be affected by neglecting it.

---

## 4. iOS-readiness, concretely

This section is about what to account for **now**, in a design that is currently being built and
tested only against Android, so that adding iOS later is cheap rather than a second research
project. Nothing here is a request to build iOS support this iteration.

### 4.1 Pyodide / WASM in WebKit

Safari's WebAssembly support is mature and this should work. Two concrete caveats, both
platform-specific rather than Pyodide-specific:

1. **Memory limits.** iOS Safari enforces materially tighter per-page WASM memory ceilings than
   desktop browsers, more so on older devices. Pyodide's own memory footprint (the CPython
   runtime plus whatever packages this project's core needs) should be sized against this before
   assuming parity with the Android/desktop experience — this is a question for the Pyodide
   research stream to close out with an actual on-device measurement on an older iPhone, not
   something resolvable from documentation alone.
2. **`SharedArrayBuffer` / cross-origin isolation.** If Pyodide's build relies on
   `SharedArrayBuffer` (used by some threading/worker configurations), WebKit requires the page to
   be served with `Cross-Origin-Opener-Policy` and `Cross-Origin-Embedder-Policy` headers set
   correctly — a **PWA-shell hosting concern**, not a Pyodide one. Flagging this as a dependency
   the shell stream should confirm either way (does the chosen Pyodide build path need it?) before
   iOS work starts, since getting these headers wrong silently breaks only on WebKit and would be
   an unpleasant surprise late.

### 4.2 IndexedDB storage eviction — the one iOS gap that is genuinely serious here

1. **The policy.** Since Safari 13.1 / iOS 13.4, WebKit evicts "script-writable storage" —
   IndexedDB, LocalStorage, Service Worker registrations — for any origin the user has not
   interacted with for **7 days**, on a least-recently-used basis. `navigator.storage.persist()`
   exists to request an exemption, but on iOS it is not guaranteed to be honored, and behavior for
   home-screen-installed PWAs specifically (vs. a page merely open in a Safari tab) has shifted
   across WebKit releases (WebKit's own storage-policy write-ups describe ongoing refinement of
   this exact distinction, most recently as of Safari 17's fuller Storage API support) — treat the
   exemption as "may help, do not rely on it" rather than as a guarantee, and re-verify against
   whatever WebKit version is current when iOS work actually starts, not against this document's
   snapshot of it.
2. **Why this is data loss, not inconvenience, specifically for this project.** CLAUDE.md rule 18
   is unambiguous: personal financial data lives only on-device, never in this repository, and (by
   the project's own design) nowhere else either — there is no server copy to fall back to.
   Combined with the 7-day eviction policy, a user who doesn't open the app for a week on iOS
   risks the browser silently deleting the entire ledger with no server-side recovery path at all.
   This is a materially different risk profile from the same eviction policy hitting, say, a
   cached shopping cart.
3. **Mitigation to propose now, even though it is out of scope for this iteration's code:** an
   explicit, user-triggered **"export a backup file"** feature — a single button that serializes
   the current ledger/state to a file the user saves wherever they choose (Files app, iCloud
   Drive, a downloads folder, email to themselves, whatever the OS file-save sheet offers). This
   is the one mitigation that is fully within the app's own control regardless of what WebKit's
   eviction policy does or how it changes across iOS releases, and it is cheap: it needs no native
   plugin, works identically via the browser's own file-save mechanism on Android and iOS, and (if
   the Capacitor wrapper from §1 ever exists) works even better via native `Filesystem` writes.
   **This is flagged as a requirement for the PWA-shell/storage stream to pick up before iOS is
   ever relied on for anything real** — not merely a nice-to-have, precisely because rule 18 turns
   "eviction" into "data loss" for this specific project in a way it wouldn't be for a typical
   app. Capacitor's secure-storage plugin (§2.2) is a second, complementary mitigation once/if the
   wrapper exists, since OS-keystore-backed storage sits outside this eviction policy entirely —
   but the export feature is the one mitigation available immediately, in the browser-only PWA,
   with no packaging decision required first.

### 4.3 File picker / share-sheet differences

1. **No File System Access API on iOS.** Safari ships only the sandboxed Origin Private File
   System (since Safari 15.2), never the local-disk picker methods (`showOpenFilePicker` etc.) —
   Apple has not committed to shipping them on any timeline. This is a hard platform gap, not a
   temporary one. It does not affect this project either way, since nothing here should be
   designed around that API — see next point.
2. **`<input type="file">` is the one import mechanism that already works identically today** on
   Android Chrome (installed PWA or plain tab), iOS Safari (installed PWA or plain tab), and
   inside a Capacitor WebView on both platforms. This is already presumably how the sibling
   import-UX stream is building it, given rule 18 and the file-based (bank/broker export) nature
   of every input this system ingests; this document's contribution is simply to confirm it is
   also the right choice for iOS parity and to recommend it stay the primary path rather than
   being treated as an Android-only fallback.
3. **Web Share Target API (registering the PWA as a share-sheet destination) is Android-only.**
   iOS Safari has no equivalent for a plain installed PWA. On iOS, the only way to get "share a
   bank statement PDF into Fina" from another app is a native **Share Extension**, which requires
   the Capacitor wrapper (§1) and materially more iOS-specific native code than Android's
   manifest `<intent-filter>` (§2.1) — this is the one asymmetry in this whole document where
   Android's native-wrapper story is meaningfully simpler than iOS's. Recommendation: never make
   share-sheet import a requirement on either platform; treat it as an optional enhancement, and
   accept that it lands on Android meaningfully before iOS if it is ever built at all.

### 4.4 Net iOS-readiness conclusion

Nothing observed above requires changing today's Android-only work. Three things are worth
carrying forward as explicit requirements on the sibling streams, restated here for traceability:
(a) keep `<input type="file">` as the one import path, never something Android-specific; (b) get
an explicit backup-export feature onto the roadmap before iOS is depended on for real data,
regardless of whose iteration it lands in; (c) confirm with the Pyodide-research stream whether
its chosen build needs `SharedArrayBuffer`/COOP-COEP headers, so the shell stream can serve them
correctly from day one rather than retrofitting them when iOS surfaces a WebKit-only failure.

---

## 5. App-lock / access control

### 5.1 Is it in scope for this iteration

**Recommendation: explicitly deferred.** No PIN or biometric app-lock this iteration. The app
relies entirely on the phone's own OS-level lock screen as the only access gate.

### 5.2 The interim risk, stated so it is a documented tradeoff and not a silent gap

Anyone who has already unlocked the phone — which, by definition, is anyone the owner's own
device passcode/biometric has already let past — can open the installed app (or the browser tab)
and see the full ledger: every account, every transaction, real balances. There is currently no
second factor specific to this app. For a tool that CLAUDE.md itself flags as handling "real bank
data," this is a real gap, not a hypothetical one, and it should be named as such rather than
assumed obviously fine because "it's just my own phone" — a shared or borrowed phone, a lost
phone with the screen already unlocked, or a household member with the passcode all defeat it
identically.

### 5.3 Why defer rather than build now

1. Building it properly needs either (a) a plain-browser **WebAuthn platform-authenticator**
   call — Face ID/Touch ID/fingerprint via the browser's own API, no native wrapper required,
   already supported today on Chrome/Android and Safari 16+/iOS — or (b) Capacitor's native
   Biometrics plugin (§2.3), which requires the wrapper to exist first.
2. Since this iteration's minimum packaging (§6) does not require standing up the Capacitor
   wrapper at all, option (a) is the cheaper near-term path *if and when* app-lock becomes a
   priority — it needs no packaging decision made first, and would work identically on Android
   and (already, today) iOS Safari.
3. Building it now, before it's been prioritized against the sibling streams' own remaining work,
   would be scope creep on a request that was specifically about packaging, not about
   authentication design — flagging it here (per CLAUDE.md rule 7) rather than including it by
   default.

### 5.4 Recommendation, restated

Ship without app-lock this iteration; the risk in §5.2 is real and now documented rather than
silent. When app-lock is picked up, default to a WebAuthn platform-authenticator prompt on app
open as the first, cheapest version, independent of whether Capacitor has been adopted by then;
revisit Capacitor's native biometrics plugin only if the WebAuthn version turns out to be
insufficient (e.g. inconsistent behavior across devices) once actually tried.

---

## 6. Concrete recommendation for this iteration

### 6.1 The three tiers, restated with what each buys

1. **Installable PWA only** (no native wrapper). Once the shell/storage stream lands: a real
   installed Android app (§1.1 — a genuine WebAPK, own icon, standalone window, uninstallable
   like any app) and, later, an installed iOS Safari PWA. Zero extra tooling. No native APIs
   beyond what the browser exposes on each platform (§4 covers exactly which those are and are
   not).
2. **PWA + Capacitor-wrapped Android APK, sideloaded.** Adds: OS-keystore-backed secure storage
   (§2.2), a share-sheet import target (§2.1), and a ready path to native biometrics (§2.3) if
   §5's deferred app-lock is ever picked up via that route instead of WebAuthn. Costs: standing up
   an Android project, a signing keystore the owner manages themself, and ongoing manual
   reinstall-to-update (no store auto-update) — all one-time or low-recurring costs for a
   single-device install.
3. **Play Store listing.** Adds: discoverability and auto-update for other users, if any ever
   exist. Costs: the practically-blocking 12-tester/14-day gate for a brand-new personal account
   (§3.1.3), the $25 fee, ongoing target-API maintenance, and a public distribution channel for an
   app whose entire design point (rule 18) is to avoid external data paths (§3.3).

### 6.2 Recommendation

**Minimum viable packaging for this iteration: Tier 1, the installable PWA alone.** None of
Tier 2's capabilities are load-bearing yet: file import already works via `<input type="file">`
without any wrapper (§2.1, §4.3), app-lock is explicitly deferred (§5), and secure storage's main
payoff (§2.2) is specifically about mitigating iOS's eviction policy (§4.2) — which doesn't apply
yet because iOS isn't being shipped. Standing up a Capacitor project now would be real, maintained
infrastructure (a keystore, a native build, a second project folder) built ahead of any concrete
need for what it provides, which is the opposite of this project's stated preference for
traceable, justified work over speculative infrastructure.

**Explicitly deferred, with the trigger condition for revisiting each:**

1. **Tier 2 (Capacitor Android wrapper)** — take up once any one of these becomes concretely
   true: (a) iOS support is actually being built and the §4.2 eviction risk needs the
   OS-keystore-storage mitigation rather than just the backup-export one; (b) app-lock (§5) is
   prioritized and WebAuthn alone (§5.3) proves insufficient; (c) the share-sheet import
   convenience (§2.1) is specifically requested. Do not build it "in case."
2. **Tier 3 (Play Store)** — take up only if and when this tool is ever shared with people other
   than the project owner. Until then, §3.1.3's tester-quota gate makes it impractical on its own
   merits, independent of the data-exposure argument in §3.3.
3. **App-lock itself (§5)**, independent of tier — deferred until explicitly prioritized; interim
   risk documented at §5.2.
4. **Backup/export feature (§4.2)** — not this document's or this iteration's code to write, but
   flagged as a requirement the PWA-shell/storage stream should schedule before iOS is ever relied
   on for real data, given rule 18 turns eviction into data loss specifically for this project.

---

## 7. Sources

- [Bubblewrap: publishing a PWA to Google Play](https://www.thinktecture.com/en/pwa/twa-bubblewrap/) — TWA mechanics, size, Digital Asset Links requirement.
- [TWA vs Capacitor: which Android wrapper wins in 2026?](https://saastostore.com/blog/twa-vs-capacitor) — size/runtime comparison (~800 KB TWA vs. ~4 MB Capacitor) and the "thin wrapper" Play policy risk.
- [Google Play Console price: the $25 developer account fee explained](https://consolemint.com/google-play-console-price/) — one-time fee, no renewal.
- [Google Play developer fee 2026: $25 + 12-tester rule](https://www.iconikai.com/blog/google-play-developer-account-fee-2026) — closed-testing gate for personal accounts opened after Nov 2023.
- [WebKit: Updates to Storage Policy](https://webkit.org/blog/14403/updates-to-storage-policy/) — the 7-day script-writable-storage eviction policy and its evolution.
- [Apple Developer Forums: Safari iOS PWA data persistence beyond 7 days](https://developer.apple.com/forums/thread/710157) — practical reports on the eviction policy's behavior for installed PWAs.
- [The State of WebAssembly – 2025 and 2026](https://platform.uno/blog/the-state-of-webassembly-2025-2026/) and [WebAssembly browser support 2026](https://reintech.io/blog/webassembly-browser-support-2026-compatibility-guide) — Safari/iOS WASM memory limits, `SharedArrayBuffer`/COOP-COEP requirement.
- [WebKit: The File System Access API with Origin Private File System](https://webkit.org/blog/12257/the-file-system-access-api-with-origin-private-file-system/) — confirms no local-disk picker on iOS Safari.
- [web.dev: OS Integration for PWAs](https://web.dev/learn/pwa/os-integration) — Web Share Target API's Android-only availability for installed PWAs.
- [Capawesome: Capacitor Biometrics plugin](https://capawesome.io/docs/sdks/capacitor/biometrics/) — native biometric app-lock and WebAuthn/passkey support surface.
