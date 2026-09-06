# eventnxt-backend: docs/HANDOFF.md
# EventNXT — Handoff & Working Notes
_Last updated: 2026-09-06 (the Stripe Connect / reserve / purchasing-agreement session)_

EventNXT is the casting/guest/ticketing app built for Tito's FashioNXT events
(events360.app). Two repos: `eventnxt-backend` (FastAPI + SQLAlchemy + Alembic
+ Postgres, deployed on Heroku with migrations in the release phase) and
`eventnxt-frontend` (React + Vite). Deploys happen by pasting whole files into
the GitHub web editor.

**The two iron deploy rules:** backend ALWAYS deploys before its frontend
(release-phase migrations must land first, and new response fields must exist
before a UI reads them — a frontend deployed early makes edits *silently
revert*, which has bitten twice: day-number edits and recipient-seating picks).
And every pasted file gets a line-count check against the packaged copy —
paste truncation is real.

---

## 1. The domain model, as it actually works now

**Events & settings.** `ticket_span` (`per_day` / `mixed` / `multi_day` /
whole-event) drives whether day machinery exists. `ticketing_mode: native`
means EventNXT sells and mints; anything else is external. `sales_source:
native` means no outside sales platform — the Sales-platform panel (Promo
tracking) and the CSV importer (Seating summary) HIDE behind a muted
"switch in Event settings" line for pure-native events, reappearing when the
setting changes OR imported rows already exist (data's source is never
hidden). `comp_delivery` governs comp code delivery.

**Pools (seating categories), sections, seats.** A pool has a
`sales_grain`: `ga`, `row`, `table`, or `seat`. Row/table pools carry
`ZoneSection` rows (label + capacity); seat pools carry actual `Seat` rows.
Per-day events clone pools per night via ticket-type **fan-out**; clones are
named `"<Base> (MM/DD)"` and form a **family**. The bare-named base pool serves
the FIRST night. `pool_for_day` maps a family member + a date to the sibling
serving that night — placement, hold counting, and the recipient override all
route through it.

**Ticket types** belong to pools, carry `valid_date` (null = pass/all-days),
`admits` (a $400 table admitting 4 counts 4 heads), and fan out per night.

**Guests** are one table wearing hats via `guest_mode`: `invite` (they RSVP
for themselves — the Invites page), `distribute` (they hand tickets out — the
Allotments page), and legacy `select` (kill-path spec'd, see §8). Additionally
`is_referrer_only` (migration 0043) marks typeless Guest rows created purely
as referral people — fenced out of EVERY attendee flow (door roster,
single/bulk invite emails, allotment portal sender, Invites grid). A referrer
whose email matches an existing attendee attaches codes to that row instead —
one human, one identity. A guest's granted days live in explicit per-day rows
(`ticket_allotment`), with `ticket_allotment_overridden` marking that they've
stopped inheriting their type. `spend_total` is the across-days cap.
**Chooser-is-data:** whenever a guest's Total is lower than the sum of their
day amounts, their RSVP page automatically becomes "place your N across these
caps." No mode selects this — the numbers do.

**Guest types are OFFERS.** The unified editor (Guest types page) is: Name ·
Invite/Allotment · one cell per event night · Total (cap) · Pull · Save, then
seating priorities. Saving converts legacy shape scopes (`all`/`choose` +
count) into plain day rows and retires the shape (`day_scope`/`default_ticket_count`
null). Type defaults stamp onto guests at add (`default_spend_total`
inheritance = migration **0040**); day defaults ghost into the grids.
Archetypes: Celebrity 2/2/2 no Total; Volunteer 2/2/2 Total 2 (chooser);
Performer 0/1/0; Sponsor = Allotment with budgets.

**Allotments & recipients.** A distribute-mode guest gets per-day BUDGETS (same
rows) plus a portal link. Recipients they enter become child guests
(`allocated_by_guest_id`). **Recipients do NOT RSVP** — the sponsor entering
them is the vouch: they're CONFIRMED at distribution, seated immediately, and
on native ticketing their comp codes mint and email on the spot. A stray click
on their old link is a no-op (guard in `/respond`). Sponsors can REMOVE a
recipient: codes void (REFUNDED), ticket rows detach (`guest_id` nullable for
exactly this lineage), seat and budget free — the one lock is a recipient who
has already checked in.

**Recipient seating override (migration 0041).** `guests.recipient_seating_category_id`
/ `recipient_section_label` hold ORGANIZER INTENT for where an allotment's
recipients land, ahead of type priorities. The holder's own
`seating_category_id` could NOT carry this (guest-create auto-resolves a pool
onto confirmed holders; keying on it hijacked every allotment). Resolution
order on distribute: parent override (day-mapped via `pool_for_day`) → type
priorities → soft-land `needs_seating`. Inside one distribute call, each
placement is flushed so the next sibling's room math sees it.

**Promos & referrals (migrations 0042–0044).** `promo_codes.guest_id` is
nullable: NULL = a SELF promo (org marketing code like EARLYBIRD) which
refuses all reward machinery and skips bonus-tier inheritance; non-null = a
referral code held by a person. One person can hold SEVERAL codes with
different reward splits — one profile, one portal, per-code deals. Email is
the identity key (two emails = two people). `referral_contacts` (0044) holds
per-recipient outreach tokens; `referral_contact_id` on orders and sales
stamps who brought the buyer. Bonus tiers resolve via
`bonuses.effective_bonus_tiers` (code override if `bonus_tiers_overridden`,
else event default) — the SAME resolution the award machinery runs, and the
same one shown to referrers in the portal.

**Payments & money (migrations 0045–0048).** Stripe Connect, DESTINATION
charges: EventNXT's platform account is merchant of record; buyers pay
face value; the organizer's share transfers atomically to their
connected account. `payment_accounts` is ORG-scoped (one Express account
per Events360 org — acct id + three status booleans mirrored ONLY by the
`account.updated` Connect webhook at `/webhooks/stripe-connect`, its own
`STRIPE_CONNECT_WEBHOOK_SECRET`, fail-closed). Express accounts are
created with a 7-DAY payout delay. The application fee sent to Stripe =
`platform_fee_cents` (3% + 75¢ policy, snapshotted) **+ `reserve_cents`**
(≈2.9% + 30¢, the estimated processing cost, 0047) — the reserve cash
never leaves platform custody. `orders.stripe_destination_account`
snapshots where the money settled (NULL = platform-account fallback);
`STRIPE_REQUIRE_CONNECTED_ACCOUNT=false` (the go-live switch) lets
unconnected orgs still charge into the platform account, `=true` 409s
them pre-persist ($0 orders exempt).

**Refunds & the reserve — one line: RELEASE = SUM(reserve) OVER
STILL-PAID ORDERS.** A refund reverses the transfer; with an UNRELEASED
reserve the withheld app fee is KEPT (`refund_application_fee=False`) —
the fee slice funds the buyer's 100%, the reserve slice covers Stripe's
never-returned processing cost, and the organizer bears it because that
order's reserve never releases (collected by NON-PAYMENT, never a debit
that can fail — mass event cancellation nets the platform zero).
Released/zero-reserve (legacy) orders fall back to
`refund_application_fee=True` = platform eats the cost. The release
endpoint (`/payments/release-reserve`) is gated STRICTLY after the
event's last day (Events360 dates, fails closed when unknown), locks
rows FOR UPDATE, pays ONE transfer with a deterministic idempotency key
(`event+sum+count`), transfer-first stamps-after. Refunds also DELETE
the order's native Sale rows (referral tallies net down) but crossed
volume bonuses stay — final and non-revocable, per the purchasing
agreement.

**Purchasing agreement (0048).** `terms_accepted` is REQUIRED at
checkout — enforced server-side FIRST, before any inventory work, both
paid and $0 paths; acceptance stamps `orders.terms_accepted_at` (null =
pre-agreement order). `marketing_opt_in` is the optional §7 consent —
default false, backfilled false (unknown consent = no consent), shown to
organizers as the "✓ marketing OK" tag on Orders. The agreement text
lives at the frontend's static public `/terms/purchase`
(`PurchaseTermsPage.jsx`) — THE single buyer-facing copy; attorney
revisions get pasted there. Comp/RSVP guests are never asked (no
checkout) — RSVP-side consent is an offered, unbuilt slice.

---

## 2. Inventory math — the definitions (all code-verified)

- **box_office**: heads bought with money — paid native orders × `admits`,
  plus CSV-imported sales matched to the pool by name.
- **allotted**: every comp-side promise — `guest_hold_heads` in "offered" mode
  (confirmed guests PLUS pending guests with Pull "Now"), + guest-assigned
  seats, + labeled blocked-seat holds. "If every offer lands."
- **committed**: the sure subset — confirmed heads + assigned seats. Labeled
  blocks are exposure, not commitment.
- **confirmed_avail** = capacity − committed − box_office.
- **estimated_avail** = capacity − allotted − box_office.
- **Ticket-type availability** = quantity − sold − held − pass-taken −
  **comp_held** (day/family-aware; checkout reads the same function — closes
  the bare-GA oversell). Surfaced as the Comps column on Seats Setup.
- **Section availability** (`/seating-categories/section-summary`): per section
  — capacity, Sold (box-office heads incl. pass claims), Comps (placed comp
  heads; on seat pools: blocked seats + seatless confirmed), Avail. **Avail is
  computed by the SAME function checkout and placement enforce**
  (`section_room_for_comps` decomposed). `test_section_summary` proves it by
  filling a section until the display says 0 and asserting the next recipient
  really overflows.
- **Sale aggregation is shared**: `services/sales.sale_aggregates_by_code`
  feeds BOTH the organizer's `/promo-stats` AND the referrer portal, so the
  org and the referrer always see one number (pinned by a portal==promo-stats
  agreement check). Native and CSV sales count identically via the shared
  Sale table.
- **Given/Comps counts pull-now pendings** — a pull-now guest blocks buyers
  the moment they're saved, RSVP or not.

---

## 3. The pages and their shared grammar

**Invites and Allotments are twins** — "truly the same thing except the person
inputted isn't the final recipient." Both have two views ("Set up & send" /
"Track sent"), two-row blocks (offer row + lifecycle strip), stacked day
headers ("Thu" over "12/24"), ghost truthfulness (type defaults ghost into
empty cells only while the guest still inherits; editing any day materializes
the ghosted others into the save; overridden guests' ghosts go silent), and
last-column `col-flex` slack.

**Seats Setup**: type composer + type list + comp-only areas + reservations.
Day chips (All · each night · All-days) filter both lists.

**Event settings** gained the org-scoped **Payouts card** (status pill,
Connect/Finish button → Stripe-hosted Express onboarding via one-time
Account Links — always mint fresh, they die in minutes; "Manage payouts"
login link once `details_submitted`); returning from Stripe lands on
`/?payments=return`, which the Dashboard reads to open straight onto
Event settings. **Orders** opens with the **Earnings panel**: event
gross/fees/net/refunded from order snapshots (native money only), the
reserve strip (Held / Released / Used-on-refunds + the post-event
"Release reserve ($X)" button when releasable), and the org-wide live
Stripe balance + recent payouts when connected. The public checkout card
carries the required agree-box (links `/terms/purchase`, pay button
disabled until checked — AND enforced server-side) and the optional
marketing box.

**Seating summary** (Manage): every pool as a block with per-section
Capacity · Sold · Comps · Avail, same day chips, frozen header. The box-office
**sales upload** (CSV/Excel with staging) lives at the bottom — hidden for
pure-native events (see §1 settings gating).

**The Promote group** (2026-09-05 redesign; `SalesReferralsTab.jsx` is dead):
- **Promos** — org creates SELF promo discount codes (EarlyBird etc.), no
  referrer attached, no reward machinery.
- **Promo tracking** — code-first: one row per promo (Promo Code · Amount ·
  Qty · Last updated via shared aggregator), each expanding to that code's
  individual sales; a muted expandable "No promo code" row holds organic
  sales. Sales-platform panel below (settings-gated).
- **Referral setup** — add referral people from scratch (quick-add auto-emails
  their portal link, best-effort — the deal survives a dead SMTP), per-code
  deal machinery (reward type/value, points rates, redemption options, bonus
  overrides), plus the event-wide Redemption tiers and Default bonus tiers
  panels. Same-email quick-add resolves to the existing person; "Add code"
  attaches more deals to one profile.
- **Referral payouts** — per referrer/per code: tickets, $, reward accrued in
  the deal's own unit (unspent points shown), volume bonuses crossed, cash
  payout queue with mark-paid. Deliberately NO net-owed column (points/ticket
  rewards don't net into dollars — deferred to the Stripe Connect era).

**The referrer portal** (`/referrer/<token>`, public): two tabs.
- *Your progress*: one card per code opening with the payout agreement in a
  bold sentence in the deal's own unit ("You earn $2 per ticket sold" / "15%
  of each sale" / points rate table), effective bonus tiers beneath, then
  tickets sold · $ sold · link clicks · estimated payout, and at the bottom
  the invited-people table (invited/clicked/bought per person).
- *Refer people*: name/email rows, code picker when holding several, editable
  Subject (≤150) and Message (≤2000) prefilled from the code's
  `referral_message_draft` (organizer's draft = the template referrers start
  from — good drafts get sent nearly untouched). The tracked link, discount
  line, and "sent through EventNXT on behalf of" footer are appended
  SERVER-SIDE no matter what — referrers customize the words, never the
  mechanism; caps are anti-abuse (referrer text rides the platform's SMTP).
  Tracked emails link `/e/<slug>?ref=CODE&r=token`; idempotent re-sends reuse
  the token; requires a published event page.

**The public event page** is TWO URL-addressable views served by one component
(so `?ref`/`?r` capture works wherever a buyer lands): `/e/<slug>` = About
(banner, title/dates/description, About Us, schedules, photos, contacts, Get
Tickets CTA) and `/e/<slug>/tickets` = Tickets (picker, buyer form,
find-my-tickets, venue map, external CTA), with an About/Tickets tab bar under
the title. **Day chips on Tickets**: "All days" = the all-days/pass PRODUCTS
(default when they exist), then one chip per night — there is NO
show-everything view; the chips ARE the sections (in-list day headings
suppressed). The filter is display-only: "Also in your order: N tickets from
other days" appears when filtered-out selections exist, and the total always
spans everything. Ticket rows read as columns: fixed-width name block, uniform
Section selects (150px) and Seat selects (110px), assigned-seating
Section/Seat/Add-seat INLINE in the row (picked chips below), stepper hugging
the right edge, mobile wrap at 560px. The buyer form is a bounded "Your
details" **checkout card** (labeled Name/Email grid, full-width optional
Referral code, accent focus ring, autoComplete hints) styled entirely from
existing tokens so organizer theming carries through.

**Attribution policy (LOCKED 2026-09-05; permanent home: `test/test_outreach.py`):**
last-click wins; a typed code beats the remembered link (and drops the person
stamp if codes differ); at paid-time, a device-switch buyer (clicked on phone,
bought on laptop) credits the sender only when EXACTLY ONE referral contact
matches the buyer's email — ambiguous double-invites stay unattributed for the
org to decide; the same single-match rule covers codeless CSV rows. One shared
service (`services/referrals.py`: `single_match_contact` + `stamp_click`)
across native checkout, the $0 path, and the importer. External ticket links
carry UTM (`utm_campaign=code`, `utm_content=token`). Salesforce parked in
favor of UTM.

---

## 4. Verification harness (the actual safety net)

**Backend**: 30 standalone suites in `test/` — run each with
`python3 test/test_X.py` with `DATABASE_URL` set. Local Postgres 16 lives at
`/tmp/pgdata`, port 5433, socket `/tmp`, db `eventnxt_test`; it dies between
container sessions — restart:

    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /tmp/pgdata \
      -o \"-p 5433 -k /tmp -c listen_addresses=''\" -l /tmp/pg.log start"

**WARNING earned the hard way:** only `rm /tmp/pgdata/postmaster.pid` when the
server is genuinely DOWN (stale pidfile). Removing it on a RUNNING server
triggers immediate shutdown. Postgres also dies BETWEEN bash commands
constantly in the container — restart before regressions, and wrap one-off
runs with a restart fallback.

Connect-era suites: `test_connect` (account lifecycle + webhook
idempotency + fail-closed), `test_destination_charges` (routing, the
gate, fee+reserve param, refund flags, sub-dollar fee cap against the
REAL gateway), `test_reserve` (THE cancellation ledger + release
gating/idempotency key + used-vs-released accounting), `test_earnings`
(money-state sums), `test_purchase_terms` (agreement enforcement +
consent storage/exposure). Fakes in these suites are uuid-suffixed —
fixed fake session/acct ids collide with rows previous runs left behind
(unique constraints are the referee here too).

Promote-era suites: `test_self_promos` (0042 self-promo guards),
`test_referral_setup` (referrer fencing, same-email identity, portal payload,
portal==promo-stats agreement), `test_outreach` (THE attribution policy +
custom subject/message + payout-agreement payload). Long-standing anchors:
`test_placement`, `test_portal`, `test_comp_inventory`,
`test_allotment_seating`, `test_section_summary`, `test_type_defaults`.

**Frontend**: `crash_hunt.cjs` — jsdom mounts the REAL Dashboard over baked
fixtures and walks all 13 tabs (fixtures describe a PURE-NATIVE event, so the
settings-gated hidden states are exercised and the muted redirect lines are
pinned as needles); console errors fail the run. Run:
`npx vite build && node crash_hunt.cjs`. The repo copy was found BROKEN at
HEAD this session (a repaired 09-02 copy never got pasted) — the repaired +
extended version was re-delivered 2026-09-05 and MUST be pasted.
`multiday_smoke.cjs` (per-day rendering smoke) was built in a prior session,
never committed, and its only copy died with that session's workspace — it
needs a fresh rebuild against current code (standing offer).

**The probe pattern — now a HOUSE RULE for public-page changes:** before
packaging any change to `PublicEventPage`/`PublicRSVPPage`/portal pages,
esbuild-bundle the single component into jsdom under a MemoryRouter with a
fetch mock and assert the changed behavior. Probes are throwaway (deleted
after use); only behaviors worth keeping get pinned into permanent harnesses.
This pattern caught three REAL bugs in one day that `vite build` passed:
undefined vars from a silently-no-matching edit, a Rules-of-Hooks violation
below early returns, and a duplicated legacy block. Write probes FRESH — a
probe patched by stacked string-replaces inherits the same silent-no-match
failure class it exists to catch.

**The pinning convention**: every accepted behavior gets a permanent check the
same day it ships. When behavior deliberately changes, the pin is REWRITTEN to
the new contract, never deleted.

---

## 5. Lessons learned (worth re-reading)

**Leftover code is the dominant failure class in evolve-in-place work.** Say
**"replace, not add"** to condemn an old path; end every slice by answering
**"what did this just make dead?"**; grep for strays before packaging; run
periodic pure-demolition slices.

**Python str.replace silently no-matches.** Regions of real files have blank
lines between statements that a compact pattern won't match, and `.replace`
doesn't complain — uses can land while definitions don't, and the BUILD STILL
PASSES (the crash waits for render). Verify every edit actually landed (grep
the symbol after), and prefer exact-text edits taken from a fresh view of the
file.

**Hooks live above early returns.** A `useEffect` planted below a component's
loading early-returns changes hook order between renders — invisible to the
build, fatal at runtime. The public pages early-return around line ~170; new
hooks go at the top with their siblings, self-contained on raw state.

**Reproduce before explaining.** Every "it doesn't work" has a mechanism
findable in one repro — and the repro also distinguishes app bugs from
probe-environment bugs (twice the "failure" was the probe).

**Display must equal enforcement, structurally.** Numbers shown and numbers
enforced come from the same function, never parallel math
(`availability_for`, `section_room_for_comps`, `sale_aggregates_by_code`,
`effective_bonus_tiers`). Parallel math WILL drift.

**Intent needs its own column.** When a value can be written by both
automation and a human choice, the choice gets a dedicated field (0041).

**Modes that can be derived from data shouldn't exist.** Chooser-ness falls
out of the numbers; every mode removed is a class of stale combinations gone.

**Emails go through the module** (`email_service.send_email`), never a
direct import of the function — monkeypatching and provider swaps depend on
it.

**CSS traps**: `.field input { min-width }` crushes compact cells;
`width:100%` on selects inside auto-layout TABLES lets cells collapse
(content-size there; fixed widths are fine in FLEX rows — the ticket picker
uses them deliberately); `border-collapse: collapse` silently kills sticky
headers; table slack goes to the LAST column only.

**Never swallow an external API's error.** The first live Connect click
502'd behind a generic "try again" — the real Stripe message (the entire
diagnosis) was caught and discarded, costing a full diagnose-redeploy
round trip. Every Stripe catch in payments.py now routes through
`_stripe_502`, which logs the raw error AND appends Stripe's
`user_message` to the detail. The rule generalizes: a catch that
doesn't log the cause is a debugging debt.

**New Stripe sandboxes are v2-first.** Fresh Connect setups DISABLE
Accounts v1: `Account.create(type='express')` is rejected until the
"Accounts v1 support" feature is enabled (dashboard → Settings →
Account features). The webhook wizard similarly pushes `v2.core.*`
events with Thin payloads — the app needs classic `account.updated`
with SNAPSHOT payloads. The SAME v1 toggle must be enabled in LIVE mode
at the go-live swap or the first real organizer's connect click dies
identically.

**jsdom cannot see layout.** Anything touching geometry gets an explicit
"give it your eyes on deploy" flag.

**Copy is part of the data model.** Behavior changes end with a stale-copy
grep.

---

## 6. Migrations (recent)

- **0040** `guest_types.default_spend_total` — type-level Total inherited at
  guest add.
- **0041** `guests.recipient_seating_category_id` + `recipient_section_label`
  — per-allotment recipient seating intent.
- **0042** `promo_codes.guest_id` + `reward_type` nullable — self promos.
- **0043** `guests.is_referrer_only` + `guest_type_id` nullable — referral
  people as typeless, fenced Guest rows.
- **0044** `referral_contacts` table + `referral_contact_id` on orders and
  sales — per-recipient outreach tracking and attribution stamps.
- **0045** `payment_accounts` — org-scoped Stripe Express account (acct
  id + three webhook-mirrored status booleans; nothing sensitive).
- **0046** `orders.stripe_destination_account` — where the money settled
  at charge time; refunds reverse the transfer iff set.
- **0047** `orders.reserve_cents` + `reserve_released_at` — the
  refund-cost reserve (release = sum over still-paid orders).
- **0048** `orders.terms_accepted_at` + `marketing_opt_in` — purchasing
  agreement enforcement + §7 marketing consent.

---

## 7. Demolition ledger (inventory run 2026-09-05)

- **DELETED / delete-now**: `TicketsTab.jsx` (confirmed gone at HEAD),
  `SalesReferralsTab.jsx` (dissolved into the four Promote pages — delete the
  file in GitHub if not already done), `api.updateSeatingCategory` (zero
  references; removed from `api.js`).
- **NEEDS-MIGRATION-FIRST — the `select` mode kill (spec'd, awaiting go)**:
  migration 0049+ (renumbered thrice: 0045→0047→0048 got used) converts `guest_mode='select'` → `'invite'`; then delete the
  mode from `comp_tickets.py` (GUEST_MODES + ~5 branches), the
  `schemas/guest.py` literals, the RSVP chooser fallback
  (`schemas/rsvp.py` + `PublicRSVPPage.jsx` line ~54), and the legacy option
  in `EventWorkspaceTab.jsx`; REWRITE the pins in `test_invites.py` and
  `test_rsvp_grid.py`. Consequence to accept: converted guests keep totals
  but day-CHOICE survives only where choose-within-caps data exists.
- **LOAD-BEARING, deliberately kept**: the shape-derivation paths (dead only
  once every legacy guest type is re-saved in the unified editor — a one-time
  re-save sweep script would force the trigger); the `tickets`→`partySize`
  importer alias (reclassified: it's the alias table doing its job — deleting
  it breaks old organizer CSVs for one line of savings).

---

## 8. Open items & standing offers

- **FashioNXT dry run** on the deployed app with real casting data —
  STRONGEST recommendation before any new feature work; it now
  rehearses the ENTIRE money path (connect → sell → refund → release)
  plus PDFs (`requirements.txt` must be deployed for the PDF deps).
- **Sandbox eyes-on money check** (load-bearing, still owed): refund one
  reserved order and verify the three balances — buyer +100%,
  organizer's balance down exactly their transfer, platform balance
  UNCHANGED by the refund. That single observation validates the refund
  flag arithmetic the reserve design derives from docs.
- **Consent follow-ups (offered, unbuilt)**: an "emails with marketing
  consent" export/filter on Orders; RSVP-page opt-in stored on guests +
  Guest-list tag (comp people are never asked today).
- **Chargeback enforcement** — the agreement makes organizers liable;
  wiring `charge.dispute.*` webhooks into reserve/payout deduction is
  the enforcement slice (today it'd be manual).
- **Postponement ticket re-dating** — accepted onto the roadmap (terms
  promise tickets honored on rescheduled dates; dated codes make that
  manual today; Guest-list manual check-in is the stopgap).
- **Stripe Tax** — parked until the first event in a taxing state
  (Oregon home base = no sales tax); agreement's tax clause + a CPA
  hour at that milestone.
- **Support email** — §10 of the purchasing agreement (docx AND
  `PurchaseTermsPage.jsx`) carries a placeholder ending; paste the real
  address when it exists.
- **`select`-mode kill** — spec above (§7), needs Joshua's explicit go.
- **Rebuild `multiday_smoke.cjs`** against current code (original lost).
- **Comp minting for type-less pools** — never explicitly verified;
  check before relying on it for FashioNXT's press rows.
- **Role-gated sidebar** — blocked on Tito's Events360 role values.
- **Add-ons page** — needs a design conversation; no backend exists.
- **Seat-adjacency automation**; "Unsectioned" row on Seating summary;
  the silent-email logging patch (nice-to-haves / revisit on demand).
- **Eyes-on-deploy queue**: checkout agree/marketing checkbox spacing on
  mobile, `/terms/purchase` page once, a fresh order showing the
  "✓ marketing OK" tag, Earnings/reserve strip wrapping on mobile,
  tickets tab on the real FashioNXT event (mixed-span chips), referrer
  portal on mobile.

---

## 8a. Go-live checklist (sandbox → live), in order

1. Activate the LIVE platform account (choose the legal entity — this
   is the merchant of record), complete the live Connect platform
   profile, set statement descriptor + Connect branding (live settings
   are separate from sandbox).
2. Enable **Accounts v1 support** in LIVE mode (§5 lesson — new
   platforms ship with it off; the connect button dies without it).
3. Recreate BOTH webhook endpoints in live mode: `/webhooks/stripe`
   (your account: checkout.session.completed/expired) and
   `/webhooks/stripe-connect` (Connected accounts: `account.updated`,
   SNAPSHOT payload, classic event — not v2.core).
4. Configure Stripe's 1099 tax-reporting settings for Connect before
   the January cycle.
5. THE one-time config-var swap, all four TOGETHER:
   `STRIPE_SECRET_KEY` (sk_live), `STRIPE_WEBHOOK_SECRET`,
   `STRIPE_CONNECT_WEBHOOK_SECRET`, and
   `STRIPE_REQUIRE_CONNECTED_ACCOUNT=true`. (A live key with a test
   webhook secret = charged buyers with forever-pending orders.)
6. `DELETE FROM payment_accounts;` — sandbox rows point at test acct
   ids that don't exist in live mode; orgs re-onboard with real
   business/bank/identity details (live KYC: minutes to a day).
7. Verify with real money: one small live purchase (watch the transfer
   + application fee + 7-day payout date on the connected balance),
   then refund it (watch the reversal and the kept reserve). Cost:
   Stripe's ~2.9%+30¢, never returned.

---

## 9. Working agreements (how we build)

Baby steps: agree scope → build one slice with throwaway verification probes
(MANDATORY for public-page changes — §4) → full regression (all 25 suites +
`crash_hunt`) → package COMPLETE files with the repo path as a first-line
comment and line counts stated, mirroring the folder structure in outputs.
Backend before frontend, always. Plain-language summaries of what changed and
why, honest confessions when a wobble happened mid-slice, and one operational
note when a change has a sharp edge. Every accepted behavior pinned the day
it ships.