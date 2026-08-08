# DynamoDB Schema

Three tables, one Global Secondary Index. On-demand capacity throughout.

## `leave_config`

Leave types and their rules — read by `submitLeaveRequest` and `weeklyBalanceJob`, never written to by the application (HR would edit this table directly to change policy).

| Attribute | Type | Notes |
|---|---|---|
| `leave_type` (PK) | String | e.g. `sick`, `casual`, `earned`, `unpaid` |
| `annual_quota` | Number | Days allocated per year |
| `carry_forward_allowed` | Boolean | Whether unused days roll over |
| `max_carry_forward` | Number | Cap on days carried forward |

Example item:
```json
{
  "leave_type": "earned",
  "annual_quota": 15,
  "carry_forward_allowed": true,
  "max_carry_forward": 10
}
```

`unpaid` is seeded with `annual_quota: 0` and is treated as a special case in code that bypasses the balance check entirely, since unpaid leave isn't limited by an allocation.

## `leave_balances`

One item per employee, per leave type, per year.

| Attribute | Type | Notes |
|---|---|---|
| `employee_id` (PK) | String | |
| `leave_type_year` (SK) | String | Format: `{leave_type}#{year}`, e.g. `sick#2026` |
| `allocated` | Number | Total days for the year |
| `used` | Number | Days consumed so far |
| `remaining` | Number | `allocated - used` (maintained directly, not derived, for atomic decrement) |

## `leave_requests`

The core transactional table — one item per leave request, plus transient fields used only while a request is actively awaiting approval.

| Attribute | Type | Notes |
|---|---|---|
| `employee_id` (PK) | String | |
| `request_id` (SK) | String | `req_` + 10 hex chars |
| `leave_type` | String | |
| `start_date` / `end_date` | String (ISO date) | |
| `requested_days` | Number | Inclusive day count |
| `reason` | String | |
| `status` | String | `PENDING`, `MGR_APPROVED`, `HR_APPROVED`, `REJECTED`, `AUTO_REJECTED` |
| `rejection_reason` | String | Present only on `AUTO_REJECTED` items |
| `created_at` | String (ISO datetime) | Used for sorting and the 48h staleness check |
| `task_token` | String | **Transient.** The Step Functions task token for whichever review stage is currently active. Set by `notifyApprover`, cleared by `finalizeRequest`. |
| `current_approver_role` | String | **Transient.** `manager` or `hr` — which stage is currently pending. Set/cleared alongside `task_token`. |

### Global Secondary Index: `status-created_at-index`

| Attribute | Role |
|---|---|
| `status` (PK) | |
| `created_at` (SK) | |

Projection: **All**.

Used by:
- `listLeaveRequests` (`mode=pending_approvals`, `mode=all_approved`) — avoids scanning the full table to build the manager/HR dashboard
- `checkPendingApprovals` — queries `status = PENDING` with `created_at` older than the 48-hour cutoff, to find requests needing a reminder

**IAM note:** granting `AmazonDynamoDBFullAccess` to a Lambda's execution role does not automatically guarantee `Query` access on a GSI in every case encountered during this build — an explicit inline policy naming both the table ARN and the `table/*/index/*` ARN was required to resolve an `AccessDeniedException` on this index. Worth checking explicitly rather than assuming a "FullAccess" policy covers index queries.

## Status lifecycle

```
PENDING ──(balance/overlap check fails)──▶ AUTO_REJECTED
   │
   │ (Step Functions execution starts)
   ▼
ManagerReview ──(reject)──▶ REJECTED
   │
   │ (approve)
   ▼
requested_days > 5? ──(no)──▶ MGR_APPROVED
   │ (yes)
   ▼
HRReview ──(reject)──▶ REJECTED
   │ (approve)
   ▼
HR_APPROVED
```

Balance (`leave_balances.remaining`/`.used`) is only ever modified at the two terminal points — `MGR_APPROVED` and `HR_APPROVED` — never at submission and never on rejection.
