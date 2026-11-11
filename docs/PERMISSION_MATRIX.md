# Role permission matrix (M01-S04)

Source of truth for Phase 2 RBAC. Enforce on **backend** (`require_role_unified`, `get_managed_branch_ids_for_user`, ownership helpers). Frontend menu `permissionId` is UX only.

| Capability | Super Admin | Branch Manager | Coach | Student / Account | Visitor |
|---|---|---|---|---|---|
| Platform CMS / masters | Yes | No | No | No | No |
| Branch CRUD (all) | Yes | No | No | No | No |
| Own managed branches | Yes | Yes | No | No | No |
| Student list/detail (scoped) | All | Managed branches | Assigned only | Self / linked | No |
| Admin plan renew | Yes | Managed branches | No | Self renew path only | No |
| Payments / invoices admin | Yes | Managed branches | Limited | Own | No |
| Biometric devices | Yes | Managed branches | No | No | No |
| KPI config | Yes | Limited | Assess where allowed | View own | No |
| Partner CMS | Yes | Assigned partner branch only | No | No | Public landing |
| Leads / callbacks | Yes | Managed branches | Assigned leads | No | Submit public forms |
| Reports / exports | Yes | Managed branch scope | Limited | No | No |
| Website visitor OTP gate | N/A | N/A | N/A | N/A | Mandatory when enabled |

## Branch Manager rules

- Resolve allowed branches via `get_managed_branch_ids_for_user`.
- Reject cross-branch student renew, partner CMS, financial, and export requests server-side.
- Collaboration partner BM: only CMS for assigned collaboration branch(es).

## Student / account rules

- Resolve active student via `active_student_id` / linked-students APIs.
- Never trust client-sent student IDs without ownership check.

## Visitor rules

- Mandatory popup: no Skip; OTP verify required before browsing when popup enabled.
- Verified marker: `rock_visitor_otp_verified_v2` only after successful OTP + lead submit.
