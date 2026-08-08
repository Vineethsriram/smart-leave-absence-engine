# Balance Logic Walkthrough

This document explains how leave balances are enforced, decremented, and replenished — and the reasoning behind each rule.

## 1. Where quotas come from

Leave types and their annual quotas are **not hardcoded** anywhere in application code. They live in the `leave_config` DynamoDB table:

```json
{"leave_type": "sick", "annual_quota": 12, "carry_forward_allowed": true, "max_carry_forward": 5}
```

`submitLeaveRequest` and `weeklyBalanceJob` both read this table at runtime. This means HR can change a quota, or add an entirely new leave type, by editing a DynamoDB item — no code deployment required.

`unpaid` leave is seeded with `annual_quota: 0` and is treated as a special case in `submitLeaveRequest`: the balance check is skipped entirely for `unpaid` requests, since unpaid leave isn't meant to be limited by an allocation in the first place.

## 2. Balance check at submission

When a request comes in, `submitLeaveRequest`:
1. Computes `requested_days` from `start_date`/`end_date`.
2. Looks up `leave_balances` for `{employee_id, leave_type#year}`.
3. If `remaining < requested_days`, the request is written with `status: AUTO_REJECTED` and a `rejection_reason` explaining exactly how many days were available versus requested. The request is rejected **before** it ever reaches a manager — there's no point notifying anyone about a request that can't be fulfilled.

## 3. Why balance is NOT decremented at submission

This is the most important design decision in the whole system: **`remaining` is only ever decremented once a request reaches a terminal approved state** (`MGR_APPROVED` or `HR_APPROVED`), inside `finalizeRequest`. It is never touched at submission, and never touched on rejection.

The reasoning: a request sits in `PENDING` for anywhere from minutes to days while it waits on human approval. If balance were decremented at submission "optimistically," and the request were later rejected, that balance would need to be **credited back** — a step that's easy to forget, and which opens a window where the balance briefly shows an incorrect (over-decremented) value to the employee. By deferring the decrement to the single terminal point where a request is actually approved, there is no rollback logic needed at all, and the balance is always accurate to what's actually been approved.

## 4. Overlap detection

Independently of the balance check, `submitLeaveRequest` also queries the employee's existing requests and checks whether the new date range overlaps with any request that is already `MGR_APPROVED` or `HR_APPROVED`. If so, the new request is auto-rejected with a `rejection_reason` naming the conflicting dates. This check only considers *approved* leave, not other pending requests — an employee submitting two overlapping requests that are both still pending is allowed (the first one approved will then cause the second to fail the overlap check, if it's still pending at that point and gets checked again — in the current implementation this is enforced at submission time only, not re-checked when a pending request is later approved; see the edge-case report for more on this).

## 5. Multi-level approval and where the balance actually gets touched

Requests of 5 days or fewer only need manager approval. Requests longer than 5 days require **both** manager and HR approval, modeled as a Step Functions state machine using the `waitForTaskToken` pattern so the workflow genuinely pauses between stages rather than polling.

The balance is decremented in exactly one place: the `UpdateBalance` state, which calls `finalizeRequest` with `outcome: "approved"`. This state is only reached after every required approval stage has said yes. `finalizeRequest` decrements `leave_balances.remaining` and increments `.used` by `requested_days`, sets the request's final `status`, and sends the confirmation email — all in one Lambda invocation, so there's no window where balance and status could get out of sync with each other.

## 6. Weekly carry-forward (simplification, documented deliberately)

`weeklyBalanceJob` runs every Monday and applies a **simplified** carry-forward rule: for any leave type where `carry_forward_allowed` is true, it tops up `remaining` toward the allocation, capped at `max_carry_forward`. This is intentionally not a true fiscal-year-end rollover calculation — the current data model doesn't have a "leave year end date" concept, which a production carry-forward system would need (to know when to reset `used` to zero and roll over only what's left at that specific point in time, rather than topping up on an ongoing weekly basis). This simplification is called out explicitly here rather than left to look like an oversight; extending the `leave_config` table with a fiscal-year boundary would be the natural next step.

## 7. Summary of the rule in one sentence

**A leave day only ever leaves an employee's balance once every required human approver has said yes — never before, and never speculatively.**
