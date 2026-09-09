# Database regression scenarios — prepared, not executed

Run only against an isolated disposable Supabase test database after all five
migrations have been explained and applied. These are acceptance scenarios for
database/backend integration tests, not results. Use deterministic fake providers
and Redis fixtures; never send real email as an incidental test.

Fixtures: two workspaces W1/W2, one active Owner each, active Admin/Manager/Member/
Viewer, a revoked member and a nonmember. Use separate connections for races,
barriers instead of sleeps, explicit READ COMMITTED transactions, and realistic
runtime login identities that cannot assume other capabilities. Assert SQLSTATE,
visible rows, affected-row counts and durable invariants after commit/rollback.

| ID | Scenario / action | Expected result |
|---|---|---|
| SEC-01 | Repeat every API SELECT/INSERT/UPDATE/DELETE with absent user/workspace, malformed UUID, nonmember, revoked member and W2 resource | Deny or zero visible rows; malformed context must not grant access; no changes |
| SEC-02 | Viewer mutates contacts/templates/drafts; Member executes campaign/manages mailbox; Manager edits workspace/rate or releases block; Admin changes team | Denied at policy/column/command boundary according to USER_ROLES |
| SEC-03 | All roles edit own preferences and own notification read state; target another user's profile/notification | Self allowed; other-user denied; revoked membership denies notification access |
| SEC-04 | Revoke membership between requests and between controlled authorization creation/dispatch | Subsequent statement/request uses current membership; dispatch command rejects revoked/insufficient requester |
| SEC-05 | Commit, rollback, raise exception and cancel transactions on a pooled connection; reuse for another user/workspace | No old context/role survives; every new transaction establishes fresh context |
| SEC-06 | Browser anon/authenticated/service_role reads tables/calls helper functions; general worker reads credentials/OAuth; relay updates messages; send worker changes global recovery/config | Denied; each dedicated login cannot SET ROLE to an unrelated capability/helper |
| SEC-07 | Use protected connection role with revoked Viewer/Member or another actor's OAuth flow | Denied; Manager+ current actor can access verifier and perform fenced callback |
| SEC-08 | Forge audit USER actor, SYSTEM actor through app_api, request effect or release target | Forged actor denied; distinct effects per request allowed; duplicate effect conflicts; unrelated audit cannot release block |
| OWN-01 | Create workspace without active Owner; add second active Owner; revoke last Owner | Cannot commit |
| OWN-02 | Race two ownership transfers and membership revocation using workspace-first locks | Exactly one active Owner at every successful commit; losing command conflicts/retries without partial demotion |
| OWN-03 | Invite OWNER or accept expired/revoked invitation; reuse digest for another actor/email | Invalid role/time shape rejected; acceptance command verifies authenticated recipient and token once |
| FK-01 | For every tenant FK, substitute W2 parent; for each strengthened FK substitute wrong W1 template/campaign/sequence/audience/lead/address/mailbox | Rejected, including template current version, planning sequence, enrollment capture identity, controlled message and attempt mailbox |
| FK-02 | Put NULL campaign/enrollment on a link to a campaign outbound; mix a controlled outbound with an unrelated enrollment | Rejected by reply attribution guard despite nullable composite-FK semantics |
| FK-03 | Put notification recipient A with membership B; unsubscribe token recipient A with originating message B | Rejected |
| NORM-01 | Case/outer spaces, IDNA domain, preserved dots/plus tags, Unicode local part, display-name syntax, CRLF, malformed domain, version 2 | Backend emits only supported v1 ASCII canonical identity; SQL rejects invalid storage shape/version; originals retained |
| NORM-02 | Concurrent creation of same canonical safety identity; edit a referenced identity | One unique identity; retry obtains same row; updates cannot rewrite recipient identity |
| IMP-01 | Repeat same row/import after worker crash; ON CONFLICT list add; duplicate canonical input within and across files | One result/effect per row; correct accepted/duplicate/rejected counts; no artificial membership revision increment |
| IMP-02 | Replace storage object, wrong digest/version, malformed mapping, oversized row/file, wrong result kind or missing rejection reason | Backend rejects unsafe input; DB rejects incoherent result; safe resumable failure recorded |
| IMP-03 | Crash after row mutation before cursor/result commit, and after commit before ack | First rolls back; second replay observes durable result; no duplicate lead/list/suppression effect |
| CAP-01 | Race list insertion/deletion with capture gate acquisition | Parent lock determines winner; captured revision matches; mutation during held gate rejected |
| CAP-02 | Crash capture midway, reclaim expired lease, change list selection in recovery | Persisted versioned selection/revisions/generation reused; immutable source selection cannot change |
| CAP-03 | Complete/fail/abandon capture without releasing every source; re-acquire a released gate | Cannot commit; successful recovery releases exact counters once |
| CAP-04 | Insert/update/delete step while freezing; attempt unfreeze; insert member into READY audience; update completed audience | Competing parent locks serialize; frozen/READY snapshots remain immutable |
| CAP-05 | Capture stale lead revision/address; enroll wrong audience/variables/destination/sequence | Rejected; later lead edits cannot alter successful frozen snapshots |
| CAM-01 | Member changes DRAFT versus PAUSED/RUNNING campaign; Member inserts paused settings; activate incomplete snapshots | Only draft changes allowed; execution requires Manager+; activation requires own frozen sequence/READY audience/settings |
| CAM-02 | Edit activated content/selection/sender assignment; edit running settings; directly archive running campaign; reopen completed | Rejected; pause before settings/archive, duplicate terminal outreach |
| CAM-03 | Two activation commands with same/different request keys and expected version | One coherent activation/enrollment plan; idempotent same-key replay; stale version conflict |
| SAFE-01 | Race absent-address suppression insertion against send authorization | Both acquire canonical recipient gate; committed block before authorization prevents invocation |
| SAFE-02 | Manual + unsubscribe + bounce reasons coexist; release MANUAL; observe MANUAL again | Other reasons remain active; release requires Admin/Owner and matching audit; reactivation retains prior sources/audit |
| SAFE-03 | Member/Manager/worker releases any block; Admin releases nonmanual/platform block | Denied |
| SAFE-04 | Receive duplicate safety webhook; crash before effects; retry/resolve hold twice | Receipt/hold identity deduplicates; counters reflect active holds; effects + release atomic; unresolved hold prevents sending |
| OAUTH-01 | Expired/replayed state, wrong workspace/actor, concurrent claims, callback failure | No verifier leakage; one fenced callback completion; claim expiry is recoverable |
| OAUTH-02 | Race refresh and disconnect; late refresh finishes after disconnect; reconnect another workspace to active account | Stale generation cannot be promoted; old secrets revoked/destroyed; active-account uniqueness rejects second workspace |
| OAUTH-03 | Destroy ciphertext; try restore envelope/reuse generation/rewrite account identity | Secret destruction leaves historical generation; old history cannot be resurrected or reassigned |
| SEND-01 | Create incomplete PLANNED; move to scheduled/queued without full render; mutate frozen sender/body/destination after render | PLANNED accepted, dispatch progression rejected until complete; frozen content cannot change |
| SEND-02 | Controlled command requests mailbox/address A but inserts message B; authorization expires or is revoked before claim | DB rejects wrong target; send command rechecks live authorization and original requester |
| SEND-03 | Concurrent scheduler claims; expired claim; stale generation delivery; duplicate worker invocation | One live unresolved attempt; conditional updates fence stale jobs; retries do not invoke twice |
| SEND-04 | Provider accepts then socket times out; worker crashes after call before result commit | UNKNOWN preserved, capacity conservatively consumed; reconciliation appends evidence; no blind resend |
| SEND-05 | Definite not-invoked, rejection, acceptance, unsupported contradictory evidence; retry budget exhausted | Evidence/state/reason/due shape enforced; terminal evidence retained; retries only when safety proof permits |
| SEND-06 | Pause/block/disconnect/reply between planning, queuing and final authorization | Final ordered command rechecks state; planned work is not sending authority |
| EVT-01 | Two event types at same aggregate version; repeat same type/key; duplicate outbox publication and consumer delivery | Distinct types coexist; duplicate semantic effects rejected; effect+consumer receipt commit once |
| EVT-02 | Crash relay before publish, after publish before DB update, and PUBLISHED without consumer completion | Lease recovery/redelivery works; publication never treated as effect completion |
| SYNC-01 | Same IMAP UID in different folders/UIDVALIDITY; repeated provider message; expired generation cursor | Correct identities coexist/deduplicate; stale generation cannot advance current sync |
| SYNC-02 | Ambiguous reply, auto-reply, complaint/bounce evidence, wrong mailbox attribution | Conservative matching; only confirmed documented outcome stops enrollment; no unrelated campaign outcome |
| RATE-01 | Missing rate singleton, RECOVERING, missing watermark or Redis generation mismatch | No provider invocation/capacity authorization |
| RATE-02 | Multiple units/windows + workspace/campaign/mailbox/provider/account ceilings; concurrent authorizations | Every applicable limit is checked atomically; narrow setting never relaxes a broader ceiling |
| RATE-03 | Redis loss during unknown sends; rebuild with changed policy; historical scopes reference old version | Conservative retained debits reconstruct all windows; old policy snapshots retained; READY only after validated watermark |
| RATE-04 | Calendar-day DST 23/25-hour boundaries, rolling interval boundaries, spacing/cooldown, clock skew | Correct local calendar boundaries; no assumption that every local day is 86400 elapsed seconds |
| NOT-01 | Duplicate event fanout, revoked recipient, preferences disabled, duplicate delivery | Unique notification per event/type/user; no unauthorized read or unwanted external send |
| NOT-02 | Transactional provider timeout after acceptance; expired SENDING lease | UNKNOWN, never fabricated SENT or automatic safe retry; reconcile with provider evidence |
| HIST-01 | Runtime delete/update of event, source, template/settings version, capture member, attempt evidence or debit scope | Append-only grants deny mutation; parent RESTRICT preserves referenced history |
| OPS-01 | Apply five files fresh using actual Supabase migration identity; inspect all grants/functions/default privileges | Entire chain executes, role ownership/revocations match review; no browser access introduced |
| OPS-02 | Analyze actual tenant cardinalities and due/expired lease/recipient fanout/inbox queries | Explain plans use appropriate prefixes; contention and p95 targets measured before production |

Follow these with backend unit/API/provider contract tests and documented production
release criteria. None of these runtime scenarios were executed during the static
migration correction task.
