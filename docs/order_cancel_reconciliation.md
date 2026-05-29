# Order Cancel and Fill Reconciliation

This note documents the safe handling rule for KIS domestic stock orders when a cancel request races with an execution.

## Core Rule

A cancel request does not prove that an order had no fill.

The bot must update `lots` and `positions` only after a `fills` row is inserted successfully. If an order is canceled, partially canceled, or cancel-rejected, the bot still has to check executions for that `order_id`.

## Status Meaning

| status | meaning |
| --- | --- |
| `REQUESTED` | Order was accepted and is waiting for fill/cancel reconciliation. |
| `PARTIAL` | Some quantity filled and remaining quantity is still open. |
| `CANCEL_REJECTED` | Cancel request failed or was rejected; the order remains a reconciliation candidate. |
| `CANCELED_NO_FILL` | Cancel was confirmed and post-cancel execution check found no fill. |
| `CANCELED_AFTER_PARTIAL_FILL` | Some quantity filled, then remaining quantity was canceled. |
| `FILLED_AFTER_CANCEL_REQUEST` | Fill was found after a cancel request or after a canceled-looking state. |
| `FILLED` | Full quantity filled normally. |

## Stored Cancel Metadata

The `orders` table records:

- `cancel_requested`
- `cancel_confirmed`
- `cancel_rejected`
- `filled_after_cancel_request`
- `cancel_response_code`
- `cancel_response_message`
- `cancel_checked_at`
- `post_cancel_execution_checked_at`

The UI Orders table also shows `filled_quantity`, `remaining_quantity`, `fill_count`, `post_cancel_execution_checked`, and `order_sync_warning`.

## Reconciliation Flow

1. Submit limit order.
2. Poll executions during the configured timeout.
3. If not filled, request cancel.
4. Regardless of cancel success, query executions again for the same order.
5. Insert any new fill first.
6. Apply the fill to `lots` and `positions` only after `record_fill()` succeeds.
7. If KIS balance quantity and DB open LOT quantity differ, mark the symbol `SYNC_REQUIRED` and block new orders.

## Operator Procedure for MTS/DB Mismatch

For a case like NEXTEEL where MTS shows 23 shares and DB open LOTs show 17:

1. Keep the symbol in `SYNC_REQUIRED`; do not resume new orders for it.
2. Confirm the suspected order number in MTS/KIS executions.
3. If the missing execution row exists, recover by inserting a real fill through the reconciliation path, then apply the fill to create the missing LOT.
4. If only balance proves the extra shares and no execution row is available, do not edit `positions` alone. Use a manual reconciliation/audit procedure that creates an explicit repair fill or review record.
5. After repair, verify DB open LOT quantity equals KIS balance quantity before clearing `SYNC_REQUIRED`.

## Safety

This reconciliation design is read/query based. UI views must not call KIS order, revise, or cancel APIs. DB reset is not part of mismatch recovery.
