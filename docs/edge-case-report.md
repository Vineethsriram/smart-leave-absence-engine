# Edge Case Report

This document covers the three required edge cases — how each is detected, handled, and was tested — plus a set of real operational issues encountered during development, included because they reflect genuine system-design and debugging lessons rather than just the happy path.

---

## 1. Insufficient balance

**Detection:** At submission, `submitLeaveRequest` looks up the employee's `leave_balances` item for the requested leave type and year, and compares `remaining` against `requested_days`.

**Handling:** If `remaining < requested_days` (or no balance record exists at all), the request is written to `leave_requests` with:
```json
{"status": "AUTO_REJECTED", "rejection_reason": "Insufficient balance: 10 remaining, 16 requested"}
```
The request never reaches a manager — there's nothing to approve if the days aren't available. The API returns `400` with the same reason in the response body, so the frontend can show it immediately.

**Tested:** Verified via direct Lambda test events and via the live frontend — submitting a request for more days than available produces an immediate, clearly-worded rejection with the exact numbers involved. Also verified that `unpaid` leave correctly bypasses this check entirely, since it has no meaningful quota to check against.

---

## 2. Overlapping dates

**Detection:** After the balance check passes, `submitLeaveRequest` queries all of the employee's existing requests and checks the new date range against every request currently in `MGR_APPROVED` or `HR_APPROVED` status, using a standard interval-overlap comparison (`not (new_end < existing_start or new_start > existing_end)`).

**Handling:** On a match, the request is `AUTO_REJECTED` with a reason naming the specific conflicting date range, e.g. `"Overlaps with existing approved leave from 2026-08-20 to 2026-08-21"`.

**Known limitation:** overlap is only checked against *approved* leave, not other *pending* requests, and only at the moment of submission. If an employee submits two overlapping requests while both are still pending, neither is rejected at submission time; the conflict would only surface if the second one is later approved after the first (since by then the first would be `MGR_APPROVED`/`HR_APPROVED` and would be caught by any *new* submission — but the two originally-simultaneous pending requests are not re-checked against each other automatically). This is a known simplification, documented here rather than treated as a hidden bug; the fix would be to also check pending requests, and decide product-level how to handle two pending requests that can't both be approved.

**Tested:** Verified by approving a request for a date range, then submitting a second request for overlapping dates — confirmed `AUTO_REJECTED` with the correct conflict message.

---

## 3. Manager inaction after 48 hours

**Detection:** `checkPendingApprovals` runs every 6 hours via EventBridge Scheduler and queries the `status-created_at-index` GSI for all `status = PENDING` items with `created_at` older than 48 hours.

**Handling:** For each stale request, an email reminder is sent to whichever approver is currently holding it up (read from `current_approver_role` on the request item — `manager` or `hr`), referencing the original approval email.

**Design decision — reminder, not auto-escalation:** the job sends a reminder rather than automatically forcing the workflow forward (e.g. by calling `SendTaskSuccess` with a synthetic "escalate" action after 48 hours). Silently making a leave decision without a human actually approving it is a bigger, riskier design choice than most real systems would make by default — a genuine auto-escalation policy would need explicit product sign-off and is left as a documented, considered-but-rejected alternative rather than implemented here.

**Known limitation:** the reminder fires on every 6-hour run for as long as a request remains stale — there's no "already reminded" tracking, so a request pending for a week would generate multiple reminder emails rather than one. A production version would likely add a `last_reminded_at` field and only remind again after some additional interval.

**Tested:** Verified with zero false positives against fresh test data (nothing incorrectly flagged as stale), then verified detection worked correctly after manually backdating a test request's `created_at` past the 48-hour threshold — confirmed the reminder email arrived with the correct hours-pending figure.

---

## 4. Real operational issues encountered during development

These aren't part of the required edge cases, but reflect genuine debugging work across the build and are included as evidence of what was actually involved in getting this system production-shaped, not just demo-shaped.

**Stale SNS subscriptions.** Multiple times during development, an SNS email subscription would silently revert to an unsubscribed state shortly after being confirmed — the console would show the subscription ID as `"Deleted"` while still displaying status `"Confirmed"`, a misleading combination. This happened across two different topics and two different email addresses. The most likely cause is an email security scanner or link-prefetcher automatically visiting the unsubscribe link that SNS includes in every notification email — a real, if unusual, interaction between transactional email delivery and automated email scanning. **Takeaway for a production version:** avoid SNS's built-in subscription-management emails for transactional, security-sensitive links like leave approvals; send these via SES direct-send instead, where there's no automatic unsubscribe mechanism to be triggered.

**Email link corruption on direct click.** Clicking an approval link directly from within Gmail sometimes resulted in a corrupted or truncated token, causing valid approval links to fail verification. This was resolved by copying the link address and pasting it into a fresh browser tab rather than clicking directly — strongly suggesting Gmail's link-wrapping/safe-browsing redirect was altering the URL's query string in some cases.

**IAM FullAccess policy not covering GSI Query.** A Lambda with `AmazonDynamoDBFullAccess` attached still received `AccessDeniedException: not authorized to perform dynamodb:Query on resource: .../index/...` when querying a GSI. Resolved with an explicit inline policy naming both the table ARN and its index ARN. This is a useful reminder that "FullAccess" managed policies aren't always as broad as their name implies for every less obvious operation, and that GSI access specifically is worth verifying rather than assuming.

**Cognito Authorizer pointed at the wrong user pool.** After adding a Cognito Authorizer to protect the API, every valid login token was rejected with `401 Unauthorized`. Diagnosed using the Authorizer's built-in test tool (which showed the rejection directly, without needing to reproduce it through the full frontend), and traced to the authorizer's **Cognito pool** field being set to an unrelated leftover user pool from earlier testing, rather than the pool the frontend's users actually belonged to. A reminder that authorization configuration should be verified against the actual identity source in use, not just that some value was selected.

**String-based date comparison sensitivity.** While manually backdating a test row's `created_at` field to test the 48-hour job, a single-character typo (`22026-08-01` instead of `2026-08-01`) caused the staleness query to silently return zero matches instead of erroring — because DynamoDB's GSI sort-key comparison is a plain string comparison, and the malformed string sorted *after* the current date rather than before it. A good illustration of why date fields stored as strings are sensitive to exact formatting, and why a "zero results" response should be treated with the same suspicion as an error when the expected outcome was "some results."
