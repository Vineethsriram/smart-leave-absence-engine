# Smart Leave & Absence Management Engine
[![Deploy submitLeaveRequest](https://github.com/Vineethsriram/smart-leave-absence-engine/actions/workflows/deploy-submitLeaveRequest.yml/badge.svg)](https://github.com/Vineethsriram/smart-leave-absence-engine/actions/workflows/deploy-submitLeaveRequest.yml)

A full-cycle, serverless leave management system built on AWS. Employees apply for leave, managers and HR approve through a structured multi-level workflow, quotas are enforced automatically, and stale/inactive requests get followed up on their own.

## Overview

- Employees submit leave requests through a web portal
- The system automatically checks leave balance and blocks overlapping approved leave
- Requests over 5 days require **both** manager and HR approval; shorter requests only need a manager
- Approvals happen via secure, signed links sent by email — no login required to approve
- A weekly job carries forward unused balance and emails a summary
- A recurring job reminds approvers if a request has sat pending for more than 48 hours
- Managers and HR get a dashboard showing pending approvals and a team absence overview, with CSV export

## Architecture

```
Employee (browser) ──▶ API Gateway ──▶ Lambda (submitLeaveRequest)
                                              │
                                              ▼
                                    validates balance + overlap
                                              │
                                              ▼
                                    Step Functions state machine
                                    (ManagerReview → optional HRReview)
                                              │
                              ┌───────────────┴───────────────┐
                              ▼                                ▼
                      SNS email notification          waitForTaskToken
                      (signed approve/reject link)     (paused until clicked)
                              │
                              ▼
                 API Gateway ──▶ Lambda (handleStepFunctionApproval)
                                       │
                                       ▼
                              SendTaskSuccess → resumes workflow
                                       │
                                       ▼
                         Lambda (finalizeRequest): updates
                         balance, sets final status, sends SES email
```

Two scheduled EventBridge jobs run independently of this flow:
- **weeklyBalanceJob** — every Monday, carries forward balance and emails a summary
- **checkPendingApprovals** — every 6 hours, reminds whoever is holding up a request pending more than 48 hours

## Tech stack

| Layer | Service |
|---|---|
| Data | DynamoDB (3 tables + 1 GSI) |
| Compute | AWS Lambda (Python 3.12) |
| Workflow orchestration | AWS Step Functions (Standard, `waitForTaskToken`) |
| API | API Gateway (REST, Lambda proxy integration) |
| Auth | Amazon Cognito (User Pool, 2 groups: Employee / ManagerHR) |
| Notifications | Amazon SNS (manager/HR alerts), Amazon SES (transactional emails) |
| Secrets | AWS Secrets Manager (HMAC signing key for approval links) |
| Scheduling | Amazon EventBridge Scheduler |
| Frontend hosting | Amazon S3 static website |

## Repository structure

```
lambdas/                   Source code for all 7 Lambda functions
state-machine/              Step Functions ASL (Amazon States Language) definition
frontend/                   employee.html and manager.html — static, Cognito-authenticated dashboards
docs/                       Balance logic walkthrough and edge-case report
dynamodb-schema.md          Full table/GSI schema and item shapes
```

## Lambda functions

| Function | Purpose |
|---|---|
| `submitLeaveRequest` | Validates balance/overlap, writes the request, starts the Step Functions execution |
| `notifyApprover` | Sends the approval email (manager or HR) with a signed link; stores the Step Functions task token |
| `handleStepFunctionApproval` | Verifies the clicked link's signed token, resumes the paused workflow via `SendTaskSuccess` |
| `finalizeRequest` | Decrements balance on approval, sets final status, sends the confirmation email |
| `weeklyBalanceJob` | Weekly carry-forward and balance summary email (EventBridge-scheduled) |
| `checkPendingApprovals` | Finds requests pending >48h and sends a reminder (EventBridge-scheduled) |
| `listLeaveRequests` | Read API backing the frontend dashboards (my requests / pending approvals / all approved) |

## Key design decisions

- **Balance is decremented only on final approval**, never at submission — prevents double-counting if a request is later rejected.
- **Leave type quotas live in a config table**, not hardcoded — HR can change rules without a code deployment.
- **Approval links are HMAC-signed and role-scoped** (manager vs HR) with a 48-hour expiry — a manager's link can't be replayed to approve an HR-stage decision.
- **`SendTaskSuccess` is used for both approve and reject** — rejection is a valid business outcome, not a workflow failure. `SendTaskFailure` is reserved for genuine errors.
- **The 48-hour job sends a reminder, not an auto-approval** — a deliberate choice to avoid the system silently making leave decisions without a human.
- A GSI on `status` + `created_at` avoids full table scans for both the pending-approvals list and the 48-hour check.

See `docs/balance-logic-walkthrough.md` for the full reasoning behind the balance and quota rules, and `docs/edge-case-report.md` for how each required edge case (insufficient balance, overlapping dates, manager inaction) is handled and was tested — plus a set of real operational issues hit during development and how they were diagnosed and fixed.

## Setup notes

This project was built entirely through the AWS Console (no IaC/Terraform/CDK) as a hands-on learning exercise. To reproduce it:
1. Create the 3 DynamoDB tables and 1 GSI per `dynamodb-schema.md`.
2. Deploy the 7 Lambda functions from `lambdas/`, attaching the IAM permissions noted in each function's header comment.
3. Create the Step Functions state machine from `state-machine/leave-approval-workflow.asl.json`.
4. Wire up API Gateway routes per the table below, with a Cognito Authorizer on the authenticated routes.
5. Create the Cognito User Pool with `Employee` and `ManagerHR` groups.
6. Host `frontend/employee.html` and `frontend/manager.html` on an S3 static website bucket, filling in your own User Pool ID, App Client ID, and API invoke URL at the top of each file's `<script>` block.

| Route | Method | Auth | Lambda |
|---|---|---|---|
| `/leave/apply` | POST | Cognito | `submitLeaveRequest` |
| `/leave/list` | GET | Cognito | `listLeaveRequests` |
| `/leave/sfn-action` | GET | None (HMAC token) | `handleStepFunctionApproval` |
