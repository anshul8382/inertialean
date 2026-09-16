# Current Portfolio Reconstruction Flow & Algorithm

## Overview
Reconstruct historical portfolio state by rolling back transactions from current holdings.

---

## FLOW DIAGRAM

```
┌─────────────────────────────────────────────────────────────┐
│ START: get_portfolio_state_at_date(client_id, target_date) │
└─────────────────────────────────────────────────────────────┘
                            ↓
                ┌───────────────────────┐
                │ Get Current Holdings  │
                │ (from Holding table)  │
                └───────────────────────┘
                            ↓
            ┌──────────────────────────────────┐
            │ Get All Transactions After       │
            │ target_date (LIFO order)         │
            └──────────────────────────────────┘
                            ↓
        ┌────────────────────────────────────────────┐
        │ Identify Securities with Corporate Actions │
        │ (SPLIT/BONUS/MERGER after target_date)     │
        └────────────────────────────────────────────┘
                            ↓
                ┌───────────────────────┐
                │ FOR EACH TRANSACTION  │
                │ (newest to oldest)    │
                └───────────────────────┘
                            ↓
                    ┌───────────────┐
                    │ Has Corporate │
                    │ Action?       │
                    └───────────────┘
                     /            \
                   YES             NO
                    ↓              ↓
        ┌─────────────────────┐   ┌──────────────────┐
        │ Call Quantity       │   │ Simple Rollback  │
        │ Adjustment API      │   │ BUY → subtract   │
        │                     │   │ SELL → add       │
        └─────────────────────┘   └──────────────────┘
                    ↓              ↓
                    └──────┬───────┘
                           ↓
                ┌──────────────────────┐
                │ Update holding qty   │
                └──────────────────────┘
                           ↓
            ┌─────────────────────────────┐
            │ Get Historical Prices       │
            │ for target_date             │
            └─────────────────────────────┘
                           ↓
                ┌──────────────────────────┐
                │ Calculate Portfolio      │
                │ Value & Metrics          │
                └──────────────────────────┘
                           ↓
                ┌──────────────────────────┐
                │ Log Portfolio Snapshot   │
                │ (2 files: simple + audit)│
                └──────────────────────────┘
                           ↓
                    ┌──────────┐
                    │ RETURN   │
                    │ portfolio│
                    │ _state   │
                    └──────────┘
```

---

## ALGORITHM DETAILS

### Step 1: Initialize
```python
# Get current holdings
holdings_dict = {
    security_id: {
        'symbol': symbol,
        'quantity': current_quantity,
        'average_price': avg_price
    }
}

# Get all transactions after target_date (sorted newest first)
future_transactions = Transaction.query.filter(
    client_id == client_id,
    transaction_date > target_date
).order_by(desc(transaction_date)).all()

# Identify securities with corporate actions
securities_with_corp_actions = set()
for action in CorporateAction.query.filter(
    action_date > target_date,
    action_date <= today
):
    securities_with_corp_actions.add(action.security_id)
```

### Step 2: Process Each Transaction (LIFO)
```python
# Track per-security standing dates
security_standing_dates = {}  # security_id -> last_processed_date

for transaction in future_transactions:
    security_id = transaction.security_id
    trade_date = transaction.transaction_date
    trade_type = transaction.type  # BUY or SELL
    trade_quantity = transaction.quantity
    
    # Get holding for this security
    holding = holdings_dict[security_id]
    current_qty = holding['quantity']
    
    # Determine standing_date for THIS security
    if security_id not in security_standing_dates:
        # First time processing this security
        security_standing_date = today
    else:
        # Use last processed date for this security
        security_standing_date = security_standing_dates[security_id]
    
    # Process based on whether security has corporate actions
    if security_id in securities_with_corp_actions:
        # ★ USE QUANTITY ADJUSTMENT API ★
        new_quantity = adjust_quantity_for_portfolio_reconstruction(
            standing_date=security_standing_date,
            target_date=trade_date,
            security_id=security_id,
            current_quantity=current_qty,
            trade_type=trade_type,
            trade_quantity=trade_quantity
        )
    else:
        # Simple rollback (no corporate actions)
        if trade_type == 'BUY':
            new_quantity = current_qty - trade_quantity
        else:  # SELL
            new_quantity = current_qty + trade_quantity
    
    # Update holding
    holding['quantity'] = new_quantity
    
    # Update standing_date for this security
    security_standing_dates[security_id] = trade_date
```

### Step 3: Quantity Adjustment API (for securities with corporate actions)
```python
def adjust_quantity_for_portfolio_reconstruction(
    standing_date,    # Where we are in rollback for this security
    target_date,      # Transaction date we're processing
    security_id,      # Security
    current_quantity, # Quantity as of standing_date
    trade_type,       # BUY or SELL
    trade_quantity    # Trade quantity
):
    # Find corporate actions BETWEEN target_date and standing_date
    actions = CorporateAction.query.filter(
        security_id == security_id,
        action_date > target_date,
        action_date <= standing_date
    ).order_by(action_date.desc()).all()  # Newest first (going backward)
    
    adjusted_quantity = current_quantity
    
    # Reverse each corporate action (going backward in time)
    for action in actions:
        if action.type == 'SPLIT':
            # Reverse split: divide by ratio
            adjusted_quantity = adjusted_quantity / action.ratio
        elif action.type == 'BONUS':
            # Reverse bonus: remove bonus shares
            adjusted_quantity = adjusted_quantity / (1 + action.ratio)
        elif action.type == 'MERGER':
            # Reverse merger: subtract converted quantity
            adjusted_quantity = adjusted_quantity - (source_qty * conversion_ratio)
    
    # Process trade rollback
    if trade_type == 'BUY':
        final_quantity = adjusted_quantity - trade_quantity
    else:  # SELL
        final_quantity = adjusted_quantity + trade_quantity
    
    return final_quantity
```

### Step 4: Update Prices & Calculate Values
```python
for holding in holdings_dict.values():
    # Get historical price for target_date
    historical_price = get_historical_price(security_id, target_date)
    
    # Calculate values
    holding['current_price'] = historical_price
    holding['current_value'] = holding['quantity'] * historical_price
    holding['unrealized_pnl'] = current_value - cost_basis
```

### Step 5: Log Portfolio Snapshot
Creates 2 files:
1. `/logs/portfolio_snapshots/client_{id}_{date}.txt` - Simple snapshot with holdings table
2. `/logs/audit_trails/reconstruction_client_{id}_{date}.txt` - Detailed audit trail

---

## KEY DESIGN DECISIONS

### 1. **Per-Security Standing Dates**
- **Why**: Securities with corporate actions need to track when the last transaction for THAT security was processed
- **Implementation**: `security_standing_dates` dictionary tracks last_processed_date per security_id
- **Benefit**: Ensures API receives correct date range to find corporate actions

### 2. **LIFO Transaction Processing**
- **Why**: Natural backward reconstruction from current state
- **Order**: Newest transactions first (2025-09-02 → 2025-08-04 → 2025-04-02)
- **Benefit**: Matches mental model of "undoing" recent transactions

### 3. **API-Only Logic for Corporate Actions**
- **Why**: Centralized logic, maintainable, no duplication
- **Implementation**: All corporate action handling in `quantity_adjustment_service.py`
- **Benefit**: Single source of truth, easier testing

### 4. **Simple Rollback for Non-Corporate Action Securities**
- **Why**: Performance optimization - don't call API unnecessarily
- **Implementation**: Direct BUY/SELL reversal in main loop
- **Benefit**: Faster processing for majority of securities

---

## EXAMPLE: NAUKRI Reconstruction

### Current State (2025-10-20)
- Holdings: 250 shares
- Transactions to reverse:
  1. 2025-09-02: BUY 10
  2. 2025-08-04: BUY 15
  3. 2025-04-02: BUY 2
- Corporate Action: 2025-05-07 SPLIT 5:1

### Execution Trace

#### Transaction 1: Sep 2 (BUY 10)
```
security_standing_date = 2025-10-20 (first time, so = today)
API Call:
  standing_date: 2025-10-20
  target_date: 2025-09-02
  current_quantity: 250
  trade: BUY 10
  
API finds: 0 corporate actions between 2025-09-02 and 2025-10-20
API returns: 250 - 10 = 240

security_standing_dates[NAUKRI] = 2025-09-02
```

#### Transaction 2: Aug 4 (BUY 15)
```
security_standing_date = 2025-09-02 (last processed for NAUKRI)
API Call:
  standing_date: 2025-09-02
  target_date: 2025-08-04
  current_quantity: 240
  trade: BUY 15
  
API finds: 0 corporate actions between 2025-08-04 and 2025-09-02
API returns: 240 - 15 = 225

security_standing_dates[NAUKRI] = 2025-08-04
```

#### Transaction 3: Apr 2 (BUY 2)
```
security_standing_date = 2025-08-04 (last processed for NAUKRI)
API Call:
  standing_date: 2025-08-04
  target_date: 2025-04-02
  current_quantity: 225
  trade: BUY 2
  
API finds: 1 corporate action between 2025-04-02 and 2025-08-04
  → SPLIT 5:1 on 2025-05-07
  
API logic:
  1. Reverse SPLIT: 225 ÷ 5 = 45
  2. Process BUY rollback: 45 - 2 = 43
  
API returns: 43 ✓

security_standing_dates[NAUKRI] = 2025-04-02
```

#### Result
- NAUKRI holdings as of 2025-03-22: **43 shares** ✓

---

## FILES INVOLVED

1. **`/api/v1/portfolio_reconstruction.py`**
   - Main reconstruction logic
   - Transaction processing loop
   - Per-security standing date tracking

2. **`/services/quantity_adjustment_service.py`**
   - `adjust_quantity_for_portfolio_reconstruction()` API
   - Corporate action reversal logic
   - SPLIT, BONUS, MERGER handling

3. **`/models.py`**
   - Holding, Transaction, CorporateAction models
   - Database schema

---

## CURRENT STATUS

✅ Algorithm designed and implemented
✅ Per-security standing dates tracking added
✅ Quantity adjustment API created
✅ Audit trail logging in place
⏳ Testing in progress - verifying NAUKRI reconstruction


