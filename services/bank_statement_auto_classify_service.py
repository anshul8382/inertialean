"""
Auto-classify bank statement lines using patterns from all historically classified data
(system-wide, not per user). Exact normalized description match first, then token Jaccard.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import joinedload

from models import AccountHead, BankStatementTransaction

# Minimum Jaccard similarity vs a known pattern (same credit/debit flow).
JACCARD_MIN = 0.55
# Require this gap between the best and second-best pattern similarity to reduce ties.
JACCARD_GAP_MIN = 0.04
# At least this many tokens (after filtering) for fuzzy matching.
MIN_TOKENS_FUZZY = 2
# Exact match: assign only if the top head vote beats the runner-up by this margin (votes).
EXACT_VOTE_MARGIN = 1
# Ignore very short normalized strings for fuzzy path.
MIN_NORM_LEN_FUZZY = 6

# Default heads (created if missing). Credit = money into bank; debit = money out / expense.
DEFAULT_ACCOUNT_HEADS: Tuple[Tuple[str, str, str], ...] = (
    (
        "Withdrawal from FD",
        "credit",
        "Maturity / premature withdrawal of fixed deposit credited to the bank account.",
    ),
    (
        "Investment in FD",
        "debit",
        "Amount transferred from the bank account into a fixed deposit.",
    ),
    (
        "Cloud Expenses",
        "debit",
        "Cloud / SaaS infra billing (AWS, GCP, Azure, hosting, etc.).",
    ),
)
# Backward-compatible alias
DEFAULT_FD_HEADS = DEFAULT_ACCOUNT_HEADS

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "from",
        "by",
        "and",
        "or",
        "of",
        "in",
        "on",
        "for",
        "dr",
        "cr",
    }
)


def _head_flow(head_type: Optional[str]) -> Optional[str]:
    ht = (head_type or "").lower()
    if ht in ("credit", "income"):
        return "credit"
    if ht in ("debit", "expense"):
        return "debit"
    return None


def _txn_flow(txn: BankStatementTransaction) -> Optional[str]:
    dep = float(txn.deposit_amount or 0)
    wdr = float(txn.withdrawal_amount or 0)
    if dep > 0 and wdr <= 0:
        return "credit"
    if wdr > 0 and dep <= 0:
        return "debit"
    return None


def _normalize_text(s: Optional[str]) -> str:
    if not s or not isinstance(s, str):
        return ""
    t = s.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def _tokenize(norm: str) -> Set[str]:
    if not norm:
        return set()
    parts = re.findall(r"[a-z0-9]+", norm.lower())
    return {p for p in parts if len(p) > 1 and p not in _STOPWORDS}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _majority_with_margin(counter: Counter, margin: int) -> Optional[int]:
    if not counter:
        return None
    top = counter.most_common(2)
    best_id, best_n = top[0]
    if len(top) == 1:
        return int(best_id)
    second_n = top[1][1]
    if best_n - second_n >= margin:
        return int(best_id)
    return None


def ensure_default_account_heads(session, created_by: int) -> Tuple[List[AccountHead], bool]:
    """
    Ensure default credit/debit heads exist (FD, Cloud Expenses, etc.).
    Re-activates if previously soft-deleted.
    Returns (heads, changed). Caller should commit when changed is True.
    """
    ensured: List[AccountHead] = []
    changed = False
    for name, head_type, description in DEFAULT_ACCOUNT_HEADS:
        head = (
            session.query(AccountHead)
            .filter(AccountHead.name == name, AccountHead.head_type == head_type)
            .first()
        )
        if head:
            if not head.is_active:
                head.is_active = True
                changed = True
            if not head.description:
                head.description = description
                changed = True
            ensured.append(head)
            continue
        head = AccountHead(
            name=name,
            head_type=head_type,
            description=description,
            created_by=created_by,
            is_active=True,
            sort_order=0,
        )
        session.add(head)
        ensured.append(head)
        changed = True
    if changed:
        session.flush()
    return ensured, changed


def ensure_default_account_heads_committed(session, created_by: int) -> List[AccountHead]:
    """Ensure default heads and commit only if anything was created or updated."""
    ensured, changed = ensure_default_account_heads(session, created_by)
    if changed:
        session.commit()
    return ensured


# Backward-compatible aliases
ensure_fd_account_heads = ensure_default_account_heads
ensure_fd_account_heads_committed = ensure_default_account_heads_committed


def _load_training_rows(session) -> List[BankStatementTransaction]:
    """Classified lines with active heads (global)."""
    q = (
        session.query(BankStatementTransaction)
        .join(AccountHead, BankStatementTransaction.income_expense_head_id == AccountHead.id)
        .filter(
            BankStatementTransaction.income_expense_head_id.isnot(None),
            AccountHead.is_active.is_(True),
        )
        .options(joinedload(BankStatementTransaction.income_expense_head))
    )
    return q.all()


def _build_exact_and_patterns(
    training: List[BankStatementTransaction],
) -> Tuple[Dict[Tuple[str, str], Counter], List[Dict[str, Any]]]:
    """
    exact_counts[(flow, norm)] -> Counter(head_id)
    patterns: one entry per distinct (flow, norm) with resolved head_id and tokens.
    """
    exact_counts: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    for txn in training:
        hf = _head_flow(txn.income_expense_head.head_type if txn.income_expense_head else None)
        tf = _txn_flow(txn)
        if hf is None or tf is None or hf != tf:
            continue
        norm = _normalize_text(txn.description_sanitized or txn.description or "")
        if not norm:
            continue
        exact_counts[(tf, norm)][txn.income_expense_head_id] += 1

    patterns: List[Dict[str, Any]] = []
    for (flow, norm), ctr in exact_counts.items():
        head_id = _majority_with_margin(ctr, EXACT_VOTE_MARGIN)
        if head_id is None:
            continue
        tok = _tokenize(norm)
        patterns.append(
            {
                "flow": flow,
                "norm": norm,
                "tokens": tok,
                "head_id": head_id,
                "support": ctr[head_id],
            }
        )
    return exact_counts, patterns


def _resolve_head_id(
    txn: BankStatementTransaction,
    exact_counts: Dict[Tuple[str, str], Counter],
    by_flow: Dict[str, List[Dict[str, Any]]],
    active_head_types: Dict[int, str],
    *,
    mutate_sanitize: bool = False,
) -> Tuple[Optional[int], str]:
    """
    Resolve a suggested/assigned head for one txn from historical patterns.
    Returns (head_id or None, skip_reason). skip_reason is '' on success.
    """
    from services.bank_statement_service import sanitize_description

    flow = _txn_flow(txn)
    if flow is None:
        return None, "skipped_no_flow"

    raw = txn.description_sanitized or txn.description or ""
    norm = _normalize_text(raw)
    if not norm:
        san = sanitize_description(txn.description)
        norm = _normalize_text(san)
        if mutate_sanitize and san and not txn.description_sanitized:
            txn.description_sanitized = san
    if not norm:
        return None, "skipped_no_text"

    key = (flow, norm)
    if key in exact_counts:
        head_id = _majority_with_margin(exact_counts[key], EXACT_VOTE_MARGIN)
        if head_id is not None:
            hf = _head_flow(active_head_types.get(head_id))
            if hf == flow:
                return head_id, ""
        else:
            return None, "skipped_ambiguous_exact"

    tokens = _tokenize(norm)
    if len(tokens) < MIN_TOKENS_FUZZY or len(norm) < MIN_NORM_LEN_FUZZY:
        return None, "skipped_no_match"

    best_j = -1.0
    second_j = -1.0
    best_head: Optional[int] = None
    second_head: Optional[int] = None

    for p in by_flow.get(flow, []):
        if not p["tokens"]:
            continue
        j = _jaccard(tokens, p["tokens"])
        if j > best_j:
            second_j, second_head = best_j, best_head
            best_j, best_head = j, p["head_id"]
        elif j > second_j:
            second_j, second_head = j, p["head_id"]

    if best_j < JACCARD_MIN or best_head is None:
        return None, "skipped_no_match"
    if second_j >= 0 and (best_j - second_j) < JACCARD_GAP_MIN and best_head != second_head:
        return None, "skipped_ambiguous_fuzzy"

    hf = _head_flow(active_head_types.get(best_head))
    if hf != flow:
        return None, "skipped_no_match"
    return best_head, ""


def _prepare_matcher(session) -> Tuple[Dict[Tuple[str, str], Counter], Dict[str, List[Dict[str, Any]]], Dict[int, str]]:
    active_head_types = {
        h.id: h.head_type
        for h in session.query(AccountHead).filter(AccountHead.is_active.is_(True)).all()
    }
    training = _load_training_rows(session)
    exact_counts, patterns = _build_exact_and_patterns(training)
    by_flow: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in patterns:
        by_flow[p["flow"]].append(p)
    return exact_counts, by_flow, active_head_types


def suggest_heads_for_transactions(
    session, transactions: List[BankStatementTransaction]
) -> Dict[int, int]:
    """
    Map unclassified txn id -> suggested head_id from historical patterns.
    Does not write to the DB. Already-classified rows are omitted.
    """
    unclassified = [t for t in transactions if t.income_expense_head_id is None]
    if not unclassified:
        return {}
    exact_counts, by_flow, active_head_types = _prepare_matcher(session)
    suggestions: Dict[int, int] = {}
    for txn in unclassified:
        head_id, reason = _resolve_head_id(
            txn, exact_counts, by_flow, active_head_types, mutate_sanitize=False
        )
        if head_id is not None and reason == "":
            suggestions[txn.id] = head_id
    return suggestions


def run_auto_classify(
    session,
    *,
    statement_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Set income_expense_head_id on unclassified transactions when a global pattern
    matches (exact or fuzzy). Optionally limit to one statement.
    Caller should commit the session.
    """
    q = session.query(BankStatementTransaction).filter(
        BankStatementTransaction.income_expense_head_id.is_(None)
    )
    if statement_id is not None:
        q = q.filter(BankStatementTransaction.bank_statement_id == statement_id)
    unclassified = q.all()

    stats = {
        "candidates": len(unclassified),
        "classified": 0,
        "skipped_no_flow": 0,
        "skipped_no_text": 0,
        "skipped_no_match": 0,
        "skipped_ambiguous_exact": 0,
        "skipped_ambiguous_fuzzy": 0,
    }
    if not unclassified:
        return stats

    exact_counts, by_flow, active_head_types = _prepare_matcher(session)
    for txn in unclassified:
        head_id, reason = _resolve_head_id(
            txn, exact_counts, by_flow, active_head_types, mutate_sanitize=True
        )
        if head_id is not None and reason == "":
            txn.income_expense_head_id = head_id
            stats["classified"] += 1
        elif reason:
            stats[reason] = stats.get(reason, 0) + 1
        else:
            stats["skipped_no_match"] += 1
    return stats


def run_global_auto_classify(session) -> Dict[str, Any]:
    """Classify all unclassified lines system-wide. Caller should commit."""
    return run_auto_classify(session, statement_id=None)
