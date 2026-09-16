# NAUKRI Portfolio Reconstruction Analysis

## Starting Point
- **Date**: 2025-10-20 (today)
- **Current Holding**: 250 shares

## Timeline
```
2025-03-22 ← TARGET DATE (we want to know holdings on this date)
2025-04-02   BUY 2 shares
2025-05-07   ★★★ SPLIT 5:1 ★★★
2025-08-04   BUY 15 shares
2025-09-02   BUY 10 shares
2025-10-20   Current: 250 shares
```

## Manual Calculation (Backward Reconstruction)

### Step 1: Reverse Sep 2 transaction
- **Before**: 250 shares (as of Oct 20)
- **Transaction**: BUY 10 shares on Sep 2
- **Rollback**: 250 - 10 = 240 shares
- **Result**: 240 shares as of Sep 2
- **Corporate actions between Oct 20 and Sep 2**: NONE ✓

### Step 2: Reverse Aug 4 transaction
- **Before**: 240 shares (as of Sep 2)
- **Transaction**: BUY 15 shares on Aug 4
- **Rollback**: 240 - 15 = 225 shares
- **Result**: 225 shares as of Aug 4
- **Corporate actions between Sep 2 and Aug 4**: NONE ✓

### Step 3: Reverse Apr 2 transaction
- **Before**: 225 shares (as of Aug 4)
- **Transaction**: BUY 2 shares on Apr 2
- **Corporate actions between Aug 4 and Apr 2**: ★★★ SPLIT 5:1 on May 7 ★★★
  
  **THIS IS THE KEY STEP!**
  
  The 225 shares we have "as of Aug 4" are POST-SPLIT shares.
  Before the split (as of Apr 2), these would have been: 225 ÷ 5 = 45 shares
  
- **After split reversal**: 45 shares (pre-split quantity)
- **Rollback the Apr 2 BUY**: 45 - 2 = 43 shares
- **Result**: 43 shares as of Apr 2 ✓

### Step 4: No more transactions
- **Result**: 43 shares as of March 22, 2025 ✓

## What the API Should Do

### API Call for Step 3 (Apr 2 transaction):
```
Input:
  - standing_date: 2025-08-04 (where we are now in the rollback)
  - target_date: 2025-04-02 (the transaction we're processing)
  - security_id: 1264 (NAUKRI)
  - current_quantity: 225.0 (quantity as of Aug 4)
  - trade_type: BUY
  - trade_quantity: 2.0

Algorithm:
  1. Find corporate actions between 2025-04-02 and 2025-08-04
     → Found: SPLIT 5:1 on 2025-05-07
  
  2. Reverse the SPLIT: 225 ÷ 5 = 45
  
  3. Process the BUY rollback: 45 - 2 = 43

Output:
  - 43.0 shares
```

## Current Problem

When we process the Apr 2 transaction, the API is being called with:
```
standing_date: 2025-04-16  ← WRONG! Should be 2025-08-04
target_date: 2025-04-02
current_quantity: 225.0
```

Because standing_date is 2025-04-16, the API looks for corporate actions between Apr 2 and Apr 16, and finds NONE (the split is on May 7, which is after Apr 16).

## Solution

We need to track **per-security standing dates**, not a global standing date.

For NAUKRI specifically:
- After processing Sep 2 transaction → NAUKRI standing_date = 2025-09-02
- After processing Aug 4 transaction → NAUKRI standing_date = 2025-08-04
- When processing Apr 2 transaction → Use NAUKRI standing_date = 2025-08-04

This way, the API will correctly find the SPLIT between Apr 2 and Aug 4!

