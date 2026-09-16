# Corrected Portfolio Reconstruction Flow

## Key Insight: Process by DATE, then SECURITIES

### Wrong Approach (old):
- Loop through all transactions one by one
- Track standing_date per security
- Mix dates and securities

### Correct Approach (new):
- **Outer Loop**: Process by DATE (newest to oldest)
- **Inner Loop**: Process all securities for that date
- Create portfolio snapshot after each date is complete
- Update `as_of_date` only when moving to a new date

---

## Algorithm

```python
# Step 1: Group transactions by date
transactions_by_date = group_by_date(future_transactions)
# Result: {
#   '2025-09-02': [list of transactions],
#   '2025-08-04': [list of transactions],
#   '2025-04-02': [list of transactions],
#   ...
# }

# Step 2: Initialize
as_of_date = today  # Current date
holdings_dict = current_holdings  # Start with current holdings

# Step 3: Process each date (newest to oldest)
for trade_date in sorted(transactions_by_date.keys(), reverse=True):
    target_date = trade_date
    transactions_for_this_date = transactions_by_date[trade_date]
    
    # Process all transactions for this date
    for transaction in transactions_for_this_date:
        security_id = transaction.security_id
        
        if security has corporate actions:
            # Call API with as_of_date and target_date
            new_quantity = adjust_quantity_for_portfolio_reconstruction(
                standing_date=as_of_date,
                target_date=target_date,
                security_id=security_id,
                current_quantity=holdings_dict[security_id]['quantity'],
                trade_type=transaction.type,
                trade_quantity=transaction.quantity
            )
            holdings_dict[security_id]['quantity'] = new_quantity
        else:
            # Simple rollback
            if transaction.type == 'BUY':
                holdings_dict[security_id]['quantity'] -= transaction.quantity
            else:
                holdings_dict[security_id]['quantity'] += transaction.quantity
    
    # All transactions for this date processed
    # Now create snapshot for as_of_date (BEFORE moving to new date)
    create_portfolio_snapshot(
        holdings=holdings_dict,
        snapshot_date=as_of_date,
        audit_message=f"After processing all {trade_date} transactions"
    )
    
    # Update as_of_date for next iteration
    as_of_date = target_date
```

---

## Example: NAUKRI

### Transactions by Date:
- **2025-09-02**: NAUKRI BUY 10, APLAPOLLO BUY 10, TCS BUY 10, ...
- **2025-08-04**: NAUKRI BUY 15, HNGSNGBEES BUY 50, ...
- **2025-04-02**: NAUKRI BUY 2, INFY BUY 10, ...

### Execution:

#### Iteration 1: Process 2025-09-02
```
as_of_date = 2025-10-20
target_date = 2025-09-02

Process NAUKRI BUY 10:
  API(standing_date=2025-10-20, target_date=2025-09-02, qty=250)
  → Returns: 240

Process APLAPOLLO BUY 10:
  API(standing_date=2025-10-20, target_date=2025-09-02, qty=...)
  → Returns: ...

... (process all Sep 2 transactions)

Create snapshot for as_of_date (2025-10-20):
  - Shows holdings AFTER reversing Sep 2 transactions
  - Audit: "After processing all 2025-09-02 transactions"

Update: as_of_date = 2025-09-02
```

#### Iteration 2: Process 2025-08-04
```
as_of_date = 2025-09-02  ← Updated from previous iteration
target_date = 2025-08-04

Process NAUKRI BUY 15:
  API(standing_date=2025-09-02, target_date=2025-08-04, qty=240)
  → Returns: 225

Process HNGSNGBEES BUY 50:
  API(standing_date=2025-09-02, target_date=2025-08-04, qty=...)
  → Returns: ...

... (process all Aug 4 transactions)

Create snapshot for as_of_date (2025-09-02):
  - Shows holdings AFTER reversing Aug 4 transactions
  - Audit: "After processing all 2025-08-04 transactions"

Update: as_of_date = 2025-08-04
```

#### Iteration 3: Process 2025-04-02
```
as_of_date = 2025-08-04  ← Updated from previous iteration
target_date = 2025-04-02

Process NAUKRI BUY 2:
  API(standing_date=2025-08-04, target_date=2025-04-02, qty=225)
  API finds: SPLIT 5:1 on 2025-05-07 (between dates) ✓
  API logic: (225 ÷ 5) - 2 = 45 - 2 = 43
  → Returns: 43 ✓

Process INFY BUY 10:
  API(standing_date=2025-08-04, target_date=2025-04-02, qty=...)
  → Returns: ...

... (process all Apr 2 transactions)

Create snapshot for as_of_date (2025-08-04):
  - Shows holdings AFTER reversing Apr 2 transactions
  - Audit: "After processing all 2025-04-02 transactions"

Update: as_of_date = 2025-04-02
```

---

## Benefits of This Approach

✅ **Clearer logic**: One as_of_date and target_date for all securities in same date
✅ **Snapshots at natural points**: After processing each date completely
✅ **Easier debugging**: Can see portfolio state after each date
✅ **Matches mental model**: "What did portfolio look like on this date?"
✅ **No per-security tracking needed**: as_of_date is global, not per-security

---

## Audit Trail Structure

```
=== PORTFOLIO SNAPSHOT: 2025-10-20 ===
After processing all 2025-09-02 transactions
Holdings:
  NAUKRI: 240 shares @ ₹... = ₹...
  APLAPOLLO: ... shares @ ₹... = ₹...
  ...
Total Value: ₹...

=== PORTFOLIO SNAPSHOT: 2025-09-02 ===
After processing all 2025-08-04 transactions
Holdings:
  NAUKRI: 225 shares @ ₹... = ₹...
  ...
Total Value: ₹...

=== PORTFOLIO SNAPSHOT: 2025-08-04 ===
After processing all 2025-04-02 transactions
Holdings:
  NAUKRI: 43 shares @ ₹... = ₹...
  ...
Total Value: ₹...
```

