# eventnxt-backend: docs/HANDOFF.md
# EventNXT — Handoff & Working Notes
_Last updated: 2026-09-06 (the external-events session: slices 1–5, migrations 0049–0051)_

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
paste truncation is real. Delivered files carry their repo path both as the
first-line comment AND encoded in the filename
(`eventnxt-backend__app__services__sales.py`), so four files named `sales.py`
can never be confused again.

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

**External events — the parity principle (2026-09-06, slices 1–4).**
Non-native = native minus exactly two things: tickets aren't minted/shipped
(the organizer flips per-guest "✓ Tickets sent" markers, 0038), and sold
inventory arrives by CSV/Excel import instead of live orders. Everything
else — rooms, comps, priorities, day machinery, promo/referral tracking,
reconciliation — is the SAME system fed by imported rows. Concretely:

- *Room building*: Seats Setup renders the SAME composer for non-native
  events minus the selling fields (price, max/order, admits, native day
  fan-out) — "Add an area" creates bare structured pools (no ticket types),
  listed in a "Your room" panel with the shared sections editor and the seat
  reserve/block picker (seat-grain pools). Baked-in guidance: **one area per
  product the outside platform sells** ("Row 2 Preferred", "Standing Room")
  so imports reconcile per product. Ticket Tomato is the live case: one
  sub-event per night, per-ticket exports (Last/First Name, Ticket Type,
  Discounts coupon cells, Barcode).
- *Day families without ticket types*: `pool_for_day` falls back to
  POOL-NAME families when no dated ticket type exists — the same
  `"<Base> (MM/DD)"` convention, normalized-name matched, bare base = FIRST
  night. A pool-level fan-out endpoint
  (`POST /seating-categories/{id}/fan-out`) clones a bare pool to every
  event day (sections + fresh seats, holds NOT copied, idempotent, refuses
  suffixed clones and pools sold by a ticket type). Wired to "Create for
  every day" in the builder and an "Every day" row action. **The bare base =
  first night is load-bearing convention**: hand-renaming a clone's suffix
  drops it from its family and dated guests silently route to the base.
- *Basis vocabulary*: "Room spaces (multi-ticket units)" is a composer basis
  writing the SAME `table` sales_grain with selling-appropriate words
  ("Spaces per section", "Tickets / space", native sold-by "Whole space") —
  a room space IS a table structurally (a purchasable unit of N heads); no
  new grain exists (modes derivable from data shouldn't).
- *Assigned seats on external events*: the seat grid is the COMP-side
  instrument (holds, hand-placements). Which specific seats outside buyers
  took lives on the outside platform; pool-level counts reconcile.
  Recipe for FashioNXT: Row 1 products = row basis + assigned (one section
  per product is fine); everything else = Named area, one per product.

**Imported-sales matching (0049).** THE match runs ONCE at import time
(`services/sale_matching.py`) and stamps the Sale row
(`seating_category_id` + `event_day` + `is_admission`); seating math reads
the stamp — rename-proof, and display can never drift from enforcement.
Resolution: saved mapping on the full normalized label → mapping on the
day-stripped label ("THURSDAY – Row 3 Preferred" → "Row 3 Preferred") →
pool-name match (normalized). The show day = a day token in the ticket name
(weekday or MM/DD resolved against the event's REAL days; an ambiguous
weekday never guesses) else the file-level day confirmed in staging; a
purchase-date column NEVER routes days. The matched base pool day-routes
through the same `pool_for_day` comps use. `sale_type_mappings` is the
organizer's saved label→area answers (normalized key, unique per event) with
optional `face_value_cents` (fills a missing amount = face − parsed coupon,
so percentage rewards compute from price-less exports) and `is_admission`
false for drink coupons/merch (imported + attributable, never in room math).
Coupon cells ("Coupon VANESSA2510: -$6.50") attribute the referral code when
no promo-code column exists. Barcode → `external_transaction_id` dedup, so
re-uploading the growing export lands only new rows.

**All-days packages (0050).** A "Weekend Pass" sold outside consumes a head
EVERY night: the mapping's `all_days` flag ("Every night" in staging) stamps
such rows to the family's BASE pool with NO single day, and
`imported_heads_for_pool` counts them into every member of that pool's name
family. Day tokens and the file-day selector are deliberately ignored for
these rows. Design decision (confirmed): the package is an import mapping,
NOT a product object in Seats Setup — the product catalog lives on the
outside platform, and a second EventNXT copy would be the duplicate-inventory
trap passes exist to avoid. (An optional pre-declare/display panel is a
possible later polish, same table.) Comp-side weekend guests never needed
any of this — all-days guest types worked already.

**Pools (seating categories), sections, seats.** A pool has a
`sales_grain`: `ga`, `row`, `table`, or `seat`. Row/table pools carry
`ZoneSection` rows (label + capacity); seat pools carry actual `Seat` rows.
Per-day events clone pools per night via ticket-type **fan-out** (native) or
pool fan-out (external); clones are named `"<Base> (MM/DD)"` and form a
**family**. The bare-named base pool serves the FIRST night. `pool_for_day`
maps a family member + a date to the sibling serving that night — placement,
hold counting, the recipient override, and now import stamping all route
through it.

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

**Referrer policies (0051, the program's legal surface).** Two policies, one
canonical attorney-editable page at the frontend's public `/terms/referral`
(`ReferralTermsPage.jsx` — the `/terms/purchase` pattern; PLACEHOLDER copy
pending attorney review; §10-style support address still a placeholder):

- *Payout terms* — a DISCLOSURE, three places: the portal-link email's
  "About your payout" block, the portal progress tab above the code cards,
  and the full page. The commitments: rewards accrue at the terms in effect
  when each sale happens; the organizer may adjust terms for FUTURE sales so
  later payouts won't necessarily match initial terms; **the portal always
  shows current effective terms** (literally true — the cards render
  `effective_bonus_tiers` through the award machinery's own resolution);
  crossed volume bonuses final; settled post-event net of refunds; cash
  rewards are the ORGANIZER's obligation (EventNXT provides the accounting —
  the clause keeping EventNXT out of the payment-promise chain).
- *Outreach Policy* — a GATE. `guests.outreach_terms_accepted_at`; `/refer`
  enforces acceptance FIRST (before publish/code checks), 400s without it,
  stamps once — and the stamp is COMMITTED at the gate, so a failed send
  can't silently un-accept. Portal UX: a summary box + checkbox gating the
  Send button on first send, collapsing to "accepted {date} · view" after.
  Substance: known-contacts only, no purchased/harvested lists, truthful
  event-related content, the tracked link + "sent through EventNXT" footer
  may never be circumvented (appended server-side regardless), stop on
  request, CAN-SPAM compliance, enforcement (suspend sending, void rewards).

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
  plus imported heads via the ONE shared `sales.imported_heads_for_pool`
  (0049/0050): stamped rows by their `seating_category_id`; all-days
  package rows counted into EVERY member of the pool's name family;
  unstamped (pre-0049 / unmapped) rows by NORMALIZED-name fallback (the old
  exact `ilike` silently dropped "Row 2 " with a trailing space — fixed);
  non-admission rows (drink coupons) never counted anywhere.
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
- **Pool-level comp placement is deliberately OPTIMISTIC** (pinned as such,
  2026-09-06): direct guest-type priority placement at pool level checks only
  confirmed comp heads — comp-vs-buyer exposure at pool level is reconciled
  on Seating summary, not enforced. What DOES honor imported/box-office heads
  through `_pool_room_components`: the sectionless section-summary display
  and the allotment-recipient room check. Whether external events should
  hard-enforce at pool level is an OPEN policy question (§8).
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
last-column `col-flex` slack. External events: Pull column hidden, per-row
"✓ Tickets sent / Not sent" toggles + sent/not-sent filter instead.

**Seats Setup**: NATIVE = type composer + type list + comp-only areas +
reservations, day chips filtering both lists. NON-NATIVE = the same composer
as "Add an area" (no price/max/admits/native-fan-out; "Create for every day"
via pool fan-out) + the "Your room" pool list (structure line, day pill,
sections editor, seats reserve picker on seat grain, "Every day" action on
bare pools, Delete) — the old GA-only comp quick-form is replaced there.
The sections editor and seats picker are ONE shared implementation serving
both lists (extracted 2026-09-06 — the two surfaces can't drift).

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
pure-native events (see §1 settings gating). The importer recognizes
box-office export headers as-is (Last/First Name joined, Barcode → dedup id,
Discounts → coupon parsing) and staging opens with **"Where do these land?"**:
one row per distinct ticket-type label (with counts) → Area picker (bare
pools first, single-night copies grouped, "Not admission" option) · optional
Face value · "Every night" checkbox (multi-day) — answers saved to
`sale_type_mappings` so next month's upload maps itself — plus the
file-level day selector ("a day inside a ticket name always wins").
Save-mappings-then-import is one button.

**The Promote group** (2026-09-05 redesign; `SalesReferralsTab.jsx` is dead):
- **Promos** — org creates SELF promo discount codes (EarlyBird etc.), no
  referrer attached, no reward machinery.
- **Promo tracking** — code-first: one row per promo (Promo Code · Amount ·
  Qty · Last updated via shared aggregator), each expanding to that code's
  individual sales; a muted expandable "No promo code" row holds organic
  sales. Sales-platform panel below (settings-gated).
- **Referral setup** — add referral people from scratch (quick-add auto-emails
  their portal link, best-effort — the deal survives a dead SMTP; the email
  now ends with the payout-terms disclosure + `/terms/referral` link),
  per-code deal machinery (reward type/value, points rates, redemption
  options, bonus overrides), plus the event-wide Redemption tiers and Default
  bonus tiers panels. Same-email quick-add resolves to the existing person;
  "Add code" attaches more deals to one profile.
- **Referral payouts** — per referrer/per code: tickets, $, reward accrued in
  the deal's own unit (unspent points shown), volume bonuses crossed, cash
  payout queue with mark-paid. Deliberately NO net-owed column (points/ticket
  rewards don't net into dollars — deferred to the Stripe Connect era).

**The referrer portal** (`/referrer/<token>`, public): two tabs.
- *Your progress*: the payout-terms disclosure line (current-effective-terms,
  future-may-differ, this-page-is-where, bonuses-final, net-of-refunds, full
  terms link) above one card per code opening with the payout agreement in a
  bold sentence in the deal's own unit ("You earn $2 per ticket sold" / "15%
  of each sale" / points rate table), effective bonus tiers beneath, then
  tickets sold · $ sold · link clicks · estimated payout, and at the bottom
  the invited-people table (invited/clicked/bought per person).
- *Refer people*: name/email rows, code picker when holding several, editable
  Subject (≤150) and Message (≤2000) prefilled from the code's
  `referral_message_draft` (organizer's draft = the template referrers start
  from — good drafts get sent nearly untouched). First send is gated by the
  Outreach Policy box (checkbox + link; Send disabled until ticked; collapses
  to "accepted {date}" after). The tracked link, discount line, and "sent
  through EventNXT on behalf of" footer are appended SERVER-SIDE no matter
  what — referrers customize the words, never the mechanism; caps are
  anti-abuse (referrer text rides the platform's SMTP). Tracked emails link
  `/e/<slug>?ref=CODE&r=token`; idempotent re-sends reuse the token; requires
  a published event page.

**Public terms pages** (static, no auth, no fetch — can never fail to load):
`/terms/purchase` (buyer agreement) and `/terms/referral` (referral payout
terms + outreach policy). Each is THE single canonical copy of its document;
attorney edits go there; both carry the same support-address placeholder.

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

**Backend**: 32 standalone suites in `test/` — run each with
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

External-events suites (2026-09-06): `test_external_rooms` (pool fan-out
naming/structure/holds-not-copied/idempotency/refusals + the pool-name-family
day routing with ZERO ticket types + lone-pool shared-room behavior),
`test_import_matching` (mapping endpoints, coupon parse → attribution,
face-value amount fill → percentage reward, day-token-beats-file-day, drink
coupons out of room math, barcode dedup, per-night summary heads, legacy
normalized-name fallback, the pool-level-optimism pin, and 0050 package rows
counting into every night of a family and never into other families).
`test_outreach` additionally pins the 0051 gate: refer without acceptance →
400 naming the policy, nothing sent; acceptance stamped once and COMMITTED
(survives a same-request failure like unpublished-event).

Connect-era suites: `test_connect` (account lifecycle + webhook
idempotency + fail-closed), `test_destination_charges` (routing, the
gate, fee+reserve param, refund flags, sub-dollar fee cap against the
REAL gateway), `test_reserve` (THE cancellation ledger + release
gating/idempotency key + used-vs-released accounting), `test_earnings`
(money-state sums), `test_purchase_terms` (agreement enforcement +
consent storage/exposure). Fakes in these suites are uuid-suffixed —
fixed fake session/acct ids collide with rows previous runs left behind
(unique constraints are the referee here too — this bit AGAIN on
2026-09-06 in test_import_matching: fixed fake transaction ids + a
non-event-scoped query read a previous run's row; event-scope every
cross-run-able assertion).

Promote-era suites: `test_self_promos` (0042 self-promo guards),
`test_referral_setup` (referrer fencing, same-email identity, portal payload,
portal==promo-stats agreement), `test_outreach` (THE attribution policy +
custom subject/message + payout-agreement payload + the 0051 gate).
Long-standing anchors: `test_placement`, `test_portal`,
`test_comp_inventory`, `test_allotment_seating`, `test_section_summary`,
`test_type_defaults`, `test_compday`, `test_capacity_drift`.

**Frontend**: `crash_hunt.cjs` — jsdom mounts the REAL Dashboard over baked
fixtures and walks all 13 tabs (fixtures describe a PURE-NATIVE event, so the
settings-gated hidden states are exercised and the muted redirect lines are
pinned as needles); console errors fail the run. Run:
`npx vite build && node crash_hunt.cjs`. STATUS RESOLVED 2026-09-06: the repo
copy at HEAD was verified WORKING (the repaired 09-05 copy did get pasted
after all — that worry is retired). `multiday_smoke.cjs` is still LOST and
still needs a fresh rebuild against current code (standing offer). Note the
fixtures never exercise the NON-NATIVE branches — external-mode behavior was
probe-verified per slice; an external-fixture crash_hunt pass is a candidate
future addition.

**The probe pattern — a HOUSE RULE for public-page changes:** before
packaging any change to `PublicEventPage`/`PublicRSVPPage`/portal pages,
esbuild-bundle the single component into jsdom under a MemoryRouter with a
fetch mock and assert the changed behavior. Probes are throwaway (deleted
after use); only behaviors worth keeping get pinned into permanent harnesses.
Write probes FRESH. Probe-environment bug classes catalogued 2026-09-06
(reproduce before explaining — all three were the probe, not the app):
captured DOM nodes go stale across React re-renders (re-query live at call
time); `tr.textContent` includes every `<option>` label, so text-matching a
row can match the dropdown's own options (match on the label CELL);
controlled checkboxes need a CLICK, not a value-set + change event.

**The pinning convention**: every accepted behavior gets a permanent check the
same day it ships. When behavior deliberately changes, the pin is REWRITTEN to
the new contract, never deleted. Corollary learned 2026-09-06: pin what IS,
not what you assumed — a planned pin ("pool-level placement blocks past
imported heads") turned out to contradict a documented deliberate design;
the pin was rewritten to state the optimism explicitly.

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
file. 2026-09-06 additions to the same family: scripted region-replacement
by index left a dangling fragment (the import check caught it — always
import/build immediately after scripted edits); a heredoc quoting collision
killed a whole edit script BEFORE it wrote (verify per-edit with asserts, and
know that assert-then-write-at-end means a thrown assert leaves the file
untouched); and anchoring a schema field on a line that exists in TWO Pydantic
classes put it in the wrong one — the response serializer silently DROPPED the
unknown field (key absent, not null; grep which class actually got it).

**Hooks live above early returns.** A `useEffect` planted below a component's
loading early-returns changes hook order between renders — invisible to the
build, fatal at runtime. The public pages early-return around line ~170; new
hooks go at the top with their siblings, self-contained on raw state.

**Reproduce before explaining.** Every "it doesn't work" has a mechanism
findable in one repro — and the repro also distinguishes app bugs from
probe-environment bugs (now FIVE times the "failure" was the probe).

**Display must equal enforcement, structurally.** Numbers shown and numbers
enforced come from the same function, never parallel math
(`availability_for`, `section_room_for_comps`, `sale_aggregates_by_code`,
`effective_bonus_tiers`, `imported_heads_for_pool`). Parallel math WILL
drift. Extension (0049): when a MATCH can be computed once and stamped,
stamp it — re-running name-guessing inside every query is parallel math in
time.

**Intent needs its own column.** When a value can be written by both
automation and a human choice, the choice gets a dedicated field (0041).

**Modes that can be derived from data shouldn't exist.** Chooser-ness falls
out of the numbers; every mode removed is a class of stale combinations gone.
Corollary (0050): "room spaces" vs "tables" is vocabulary, not structure —
one grain, two labels.

**Acceptance is its own fact.** A terms/consent stamp must COMMIT at the
gate, not flush — otherwise a later failure in the same request silently
un-accepts what the user accepted (found live in the 0051 outreach gate).

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
- **0049** `sales.seating_category_id`/`event_day`/`is_admission` (SET NULL
  on pool delete — sales outlive rooms) + `sale_type_mappings` table — the
  import match, stamped once, mapped once.
- **0050** `sales.all_days` + `sale_type_mappings.all_days` — weekend
  packages counting into every night of a pool's name family.
- **0051** `guests.outreach_terms_accepted_at` — the Outreach Policy gate
  on `/refer` (committed at the gate).

---

## 7. Demolition ledger (updated 2026-09-06)

- **DELETED this session**: both exact-`ilike` imported-sales matching sites
  (services/seating.py + routers/seating_categories.py — replaced by the one
  `imported_heads_for_pool`; the router's dead `Sale` import went with them);
  the native seats/sections expander JSX duplicates in `TicketsSeatingTab`
  (extracted into shared render helpers serving both lists) and the
  now-unused `applySeats` wrapper; the GA-only comp quick-form for
  NON-NATIVE events (replaced by the full room builder — it remains for
  native events as the press-row/holds panel).
- **NEEDS-MIGRATION-FIRST — the `select` mode kill (spec'd, awaiting go)**:
  migration **0052+** (renumbered a FOURTH time: 0045→0047→0048→0049-0051
  got used) converts `guest_mode='select'` → `'invite'`; then delete the
  mode from `comp_tickets.py` (GUEST_MODES + ~5 branches), the
  `schemas/guest.py` literals, the RSVP chooser fallback
  (`schemas/rsvp.py` + `PublicRSVPPage.jsx` line ~54), and the legacy option
  in `EventWorkspaceTab.jsx`; REWRITE the pins in `test_invites.py` and
  `test_rsvp_grid.py`. Consequence to accept: converted guests keep totals
  but day-CHOICE survives only where choose-within-caps data exists.
- **LOAD-BEARING, deliberately kept**: the shape-derivation paths (dead only
  once every legacy guest type is re-saved in the unified editor — a one-time
  re-save sweep script would force the trigger); the `tickets`→`partySize`
  importer alias (it's the alias table doing its job — and 0049's
  `sale_type_mappings` is the same pattern promoted to a real table);
  pool-level placement optimism (documented design, see §2 — changing it is
  a policy decision, not cleanup).

---

## 8. Open items & standing offers

- **FashioNXT dry run** on the deployed app with real casting data —
  STRONGEST recommendation before any new feature work; it now
  rehearses the ENTIRE money path (connect → sell → refund → release),
  PDFs (`requirements.txt` must be deployed for the PDF deps), AND the
  external path (build the Ticket Tomato-mirroring room, fan out days,
  import a real export, watch per-night reconciliation + referral
  attribution).
- **Real Ticket Tomato export verification** — the importer was built
  against the dashboard's known shape (screenshot-verified columns);
  first real file may need one header alias at most. Confirm whether
  the export carries a check-in column (future: reconcile external
  check-ins into Guest list).
- **Pool-level comp enforcement policy** (from §2): should external
  events hard-refuse comp placement past imported heads at pool level?
  One deliberate line, but it would tighten native identically. Awaiting
  Joshua's call.
- **Packages pre-declare panel** (optional polish, declined-for-now):
  list all-days mappings in Seats Setup before any import
  ("Weekend Pass → rides Row 2 Preferred, every night, sold: N") — same
  table, display + pre-declare only.
- **Attorney review of `/terms/referral`** (placeholder copy) alongside
  the purchasing agreement; BOTH terms pages share the §10 support-address
  placeholder — paste the real address in both when it exists.
- **Sandbox eyes-on money check** (load-bearing, still owed): refund one
  reserved order and verify the three balances — buyer +100%,
  organizer's balance down exactly their transfer, platform balance
  UNCHANGED by the refund.
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
- **`select`-mode kill** — spec above (§7), now 0052+, needs Joshua's
  explicit go.
- **Rebuild `multiday_smoke.cjs`** against current code (original lost);
  an external-fixture crash_hunt pass is a related candidate.
- **Comp minting for type-less pools** — never explicitly verified;
  check before relying on it for FashioNXT's press rows.
- **Per-seat import fidelity** — Ticket Tomato's lounge rows carry
  Table/Seat numbers; a future slice could mark externally-sold seats
  seat-by-seat (today: pool-level truth, seat grid = comp instrument).
- **Role-gated sidebar** — blocked on Tito's Events360 role values.
- **Add-ons page** — needs a design conversation; no backend exists
  (drink-coupon import labels preview the space).
- **Seat-adjacency automation**; "Unsectioned" row on Seating summary;
  the silent-email logging patch (nice-to-haves / revisit on demand).
- **Eyes-on-deploy queue**: external room builder on a real event (one GA
  + one assigned-row area; reserve two seats; fan out a multi-day area
  and check the chips); "Room spaces" wording end-to-end incl. the
  native "Whole space" sold-by note; a real export through staging
  (mapping panel on mobile too) then a re-upload watching dupes skip; a
  fake weekend-pass row moving all nights' Sold at once; `/terms/referral`
  once; one referrer through first-send acceptance on mobile; the
  portal-link email's payout block in a real inbox; plus the standing
  items: checkout agree/marketing spacing on mobile, `/terms/purchase`,
  a fresh order's "✓ marketing OK" tag, Earnings/reserve strip wrapping
  on mobile, tickets tab on the real FashioNXT event, referrer portal on
  mobile.

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
(MANDATORY for public-page changes — §4) → full regression (all 32 suites +
`crash_hunt`) → package COMPLETE files with the repo path as a first-line
comment AND encoded in the filename
(`repo__folder__folder__file.ext`), line counts stated. Backend before
frontend, always. Plain-language summaries of what changed and why, honest
confessions when a wobble happened mid-slice, and one operational note when a
change has a sharp edge. Every accepted behavior pinned the day it ships —
and pins state what IS, including deliberate optimism.