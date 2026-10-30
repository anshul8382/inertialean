"""
Daily client returns digest (Anshul only).

Lifetime: lifetime XIRR + absolute % on net invested; trailing 1M–5Y absolute %
on period net invested (V0 + investments − withdrawals); Nifty cashflow-timing
sim absolute for the same windows; since-inception when tenure < horizon.
Duration = calendar years from first real trade.
Email: HTML summary + Excel attachment → anshul@equities4wealth.com only.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

ALLOWED_EMAIL = "anshul@equities4wealth.com"
BENCHMARK_ID = 1  # NIFTY 50

HORIZONS: Tuple[Tuple[str, relativedelta], ...] = (
    ("1M", relativedelta(months=1)),
    ("3M", relativedelta(months=3)),
    ("6M", relativedelta(months=6)),
    ("1Y", relativedelta(years=1)),
    ("3Y", relativedelta(years=3)),
    ("5Y", relativedelta(years=5)),
)


@dataclass
class _CF:
    date: date
    amount: float


@dataclass
class HorizonResult:
    key: str
    start_date: Optional[date]
    since_inception: bool
    portfolio_abs_pct: Optional[float]
    nifty_abs_pct: Optional[float]
    v0: Optional[float] = None
    period_net_invested: Optional[float] = None
    vend: Optional[float] = None
    error: Optional[str] = None


@dataclass
class ClientReturnsRow:
    client_id: int
    client_name: str
    is_active: bool
    first_trade: Optional[date]
    duration_years: Optional[float]
    current_value: float
    lifetime_xirr_pct: Optional[float]
    lifetime_abs_pct: Optional[float]
    lifetime_nifty_abs_pct: Optional[float]
    horizons: Dict[str, HorizonResult] = field(default_factory=dict)
    error: Optional[str] = None


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def _pct(end_value: float, period_net_invested: float) -> Optional[float]:
    if period_net_invested is None or period_net_invested <= 1e-6:
        return None
    return ((end_value - period_net_invested) / period_net_invested) * 100.0


def _period_net_invested(v0: float, window_cfs: Sequence[_CF]) -> float:
    invested = sum(abs(cf.amount) for cf in window_cfs if cf.amount < 0)
    withdrawn = sum(cf.amount for cf in window_cfs if cf.amount > 0)
    return float(v0) + invested - withdrawn


def _window_cashflows(
    cashflows: Sequence[_CF], start: date, end: date
) -> List[_CF]:
    """Cashflows in (start, end] — start balance already embeds flows on start."""
    out: List[_CF] = []
    for cf in cashflows:
        d = _as_date(cf.date)
        if d is None:
            continue
        if start < d <= end:
            out.append(cf)
    return out


def _nifty_price_lookup(
    start: date, end: date, benchmark_id: int = BENCHMARK_ID
) -> Dict[date, float]:
    from models import BenchmarkData

    extended = start - timedelta(days=45)
    rows = (
        BenchmarkData.query.filter_by(benchmark_id=benchmark_id)
        .filter(BenchmarkData.date >= extended)
        .filter(BenchmarkData.date <= end)
        .order_by(BenchmarkData.date)
        .all()
    )
    return { _as_date(r.date): float(r.price) for r in rows if _as_date(r.date) }


def _price_on_or_before(lookup: Dict[date, float], target: date) -> Optional[float]:
    if target in lookup:
        return lookup[target]
    prior = [d for d in lookup if d <= target]
    if not prior:
        return None
    return lookup[max(prior)]


def _simulate_nifty_absolute(
    *,
    v0: float,
    start: date,
    end: date,
    window_cfs: Sequence[_CF],
    price_lookup: Dict[date, float],
) -> Optional[float]:
    """Cashflow-timing Nifty sim: seed V0 at start, then window flows; absolute %."""
    from services.benchmark_service import BenchmarkService

    start_px = _price_on_or_before(price_lookup, start)
    end_px = _price_on_or_before(price_lookup, end)
    if not start_px or start_px <= 0 or not end_px or end_px <= 0:
        return None

    sim_cfs: List[_CF] = []
    if v0 and v0 > 1e-6:
        sim_cfs.append(_CF(date=start, amount=-float(v0)))
    for cf in window_cfs:
        sim_cfs.append(_CF(date=_as_date(cf.date) or start, amount=float(cf.amount)))

    if not sim_cfs:
        return None

    result = BenchmarkService._simulate_benchmark_investment(
        sim_cfs, price_lookup, float(end_px)
    )
    net = float(result.get("net_investment") or 0.0)
    end_val = float(result.get("current_value") or 0.0)
    return _pct(end_val, net)


def _first_trade_date(client_id: int) -> Optional[date]:
    from models import Transaction
    from services.cashflow_service import DUMMY_DATES
    from sqlalchemy import func

    row = (
        Transaction.query.filter(Transaction.client_id == client_id)
        .with_entities(func.min(Transaction.transaction_date))
        .scalar()
    )
    d = _as_date(row)
    if d and d not in DUMMY_DATES:
        return d

    # Exclude dummy dates explicitly when min hit a placeholder
    dates = (
        Transaction.query.filter(Transaction.client_id == client_id)
        .with_entities(Transaction.transaction_date)
        .all()
    )
    real = sorted(
        {
            x
            for x in (_as_date(r[0]) for r in dates)
            if x and x not in DUMMY_DATES
        }
    )
    return real[0] if real else None


def _load_client_cashflows(client_id: int) -> List[_CF]:
    from models import Cashflow

    rows = (
        Cashflow.query.filter_by(client_id=client_id)
        .order_by(Cashflow.date)
        .all()
    )
    out: List[_CF] = []
    for r in rows:
        d = _as_date(r.date)
        if d is None:
            continue
        try:
            amt = float(r.amount)
        except (TypeError, ValueError):
            continue
        out.append(_CF(date=d, amount=amt))
    return out


def compute_client_row(
    client,
    *,
    as_of: date,
    price_lookup: Dict[date, float],
) -> ClientReturnsRow:
    from api.v1.performance import calculate_xirr
    from services.forward_holding_calculation_service import (
        get_client_portfolio_values_at_dates,
    )
    from services.portfolio_valuation_service import PortfolioValuationService

    row = ClientReturnsRow(
        client_id=int(client.id),
        client_name=str(client.name or f"Client {client.id}"),
        is_active=bool(getattr(client, "is_active", True)),
        first_trade=None,
        duration_years=None,
        current_value=0.0,
        lifetime_xirr_pct=None,
        lifetime_abs_pct=None,
        lifetime_nifty_abs_pct=None,
    )
    try:
        first = _first_trade_date(client.id)
        cashflows = _load_client_cashflows(client.id)
        if not first and cashflows:
            from services.cashflow_service import DUMMY_DATES

            real_cf = [c.date for c in cashflows if c.date not in DUMMY_DATES]
            first = min(real_cf) if real_cf else cashflows[0].date
        row.first_trade = first
        if first:
            row.duration_years = round((as_of - first).days / 365.25, 2)

        vend = float(
            PortfolioValuationService.total_market_value_for_client(client.id)
        )
        row.current_value = vend

        if not cashflows:
            row.error = "no_cashflows"
            return row

        # Lifetime portfolio
        cf_tuples = [(c.date, c.amount) for c in cashflows]
        xirr, _ti, _tw, net_inv, abs_ret = calculate_xirr(
            cf_tuples, vend, end_date=as_of
        )
        row.lifetime_xirr_pct = (
            round(float(xirr) * 100.0, 2) if xirr is not None else None
        )
        row.lifetime_abs_pct = (
            round(float(abs_ret), 2) if abs_ret is not None else None
        )

        # Lifetime Nifty — same cashflow-timing sim as client details
        try:
            from services.benchmark_service import BenchmarkService

            nifty_life = BenchmarkService.calculate_benchmark_xirr(
                client.id, benchmark_id=BENCHMARK_ID
            )
            if nifty_life.get("success"):
                row.lifetime_nifty_abs_pct = round(
                    float(nifty_life.get("nifty_absolute_return") or 0.0), 2
                )
            else:
                lifetime_nifty = _simulate_nifty_absolute(
                    v0=0.0,
                    start=cashflows[0].date,
                    end=as_of,
                    window_cfs=cashflows,
                    price_lookup=price_lookup,
                )
                row.lifetime_nifty_abs_pct = (
                    round(lifetime_nifty, 2)
                    if lifetime_nifty is not None
                    else None
                )
        except Exception as nifty_exc:
            logger.warning(
                "lifetime nifty failed client=%s: %s", client.id, nifty_exc
            )
            row.lifetime_nifty_abs_pct = None

        # Horizon start dates needing valuation
        horizon_starts: Dict[str, date] = {}
        need_dates: List[date] = []
        for key, delta in HORIZONS:
            t0 = as_of - delta
            horizon_starts[key] = t0
            if first and first <= t0:
                need_dates.append(t0)

        values_at: Dict[date, float] = {}
        if need_dates:
            raw = get_client_portfolio_values_at_dates(client.id, need_dates)
            for d, payload in (raw or {}).items():
                dd = _as_date(d)
                if dd is None:
                    continue
                values_at[dd] = float((payload or {}).get("total_value") or 0.0)

        for key, delta in HORIZONS:
            t0 = horizon_starts[key]
            since_inception = bool(not first or first > t0)
            hr = HorizonResult(
                key=key,
                start_date=first if since_inception else t0,
                since_inception=since_inception,
                portfolio_abs_pct=None,
                nifty_abs_pct=None,
                vend=vend,
            )
            if since_inception:
                # Same as lifetime absolute / nifty absolute for this cell
                hr.portfolio_abs_pct = row.lifetime_abs_pct
                hr.nifty_abs_pct = row.lifetime_nifty_abs_pct
                hr.v0 = 0.0
                hr.period_net_invested = float(net_inv) if net_inv else None
            else:
                v0 = float(values_at.get(t0, 0.0))
                window = _window_cashflows(cashflows, t0, as_of)
                pni = _period_net_invested(v0, window)
                hr.v0 = v0
                hr.period_net_invested = pni
                hr.portfolio_abs_pct = (
                    round(_pct(vend, pni), 2)
                    if _pct(vend, pni) is not None
                    else None
                )
                nifty = _simulate_nifty_absolute(
                    v0=v0,
                    start=t0,
                    end=as_of,
                    window_cfs=window,
                    price_lookup=price_lookup,
                )
                hr.nifty_abs_pct = (
                    round(nifty, 2) if nifty is not None else None
                )
            row.horizons[key] = hr

    except Exception as exc:
        logger.exception("daily returns failed for client %s", client.id)
        row.error = str(exc)
    return row


def build_daily_client_returns(
    *,
    as_of: Optional[date] = None,
    active_only: bool = False,
    client_ids: Optional[Sequence[int]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Compute firm-wide returns matrix.

    active_only=False → all clients with cashflows (locked "all clients").
    """
    from models import Cashflow, Client
    from sqlalchemy import distinct

    as_of = as_of or date.today()
    q = Client.query
    if client_ids:
        q = q.filter(Client.id.in_(list(client_ids)))
    elif active_only:
        q = q.filter(Client.is_active.is_(True))

    # Prefer clients that have at least one cashflow
    cf_ids = {
        r[0]
        for r in Cashflow.query.with_entities(distinct(Cashflow.client_id)).all()
    }
    clients = [c for c in q.order_by(Client.name).all() if c.id in cf_ids]
    if limit:
        clients = clients[: int(limit)]

    # Benchmark lookup spanning earliest plausible first trade
    earliest = as_of - relativedelta(years=25)
    price_lookup = _nifty_price_lookup(earliest, as_of)

    rows: List[ClientReturnsRow] = []
    for i, client in enumerate(clients, 1):
        if i == 1 or i % 10 == 0 or i == len(clients):
            logger.info(
                "daily returns progress %s/%s client_id=%s",
                i,
                len(clients),
                client.id,
            )
        rows.append(
            compute_client_row(client, as_of=as_of, price_lookup=price_lookup)
        )

    return {
        "as_of": as_of.isoformat(),
        "client_count": len(rows),
        "ok_count": sum(1 for r in rows if not r.error),
        "error_count": sum(1 for r in rows if r.error),
        "rows": rows,
        "horizons": [h[0] for h in HORIZONS],
    }


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


def build_excel_bytes(payload: Dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Daily returns"

    headers = [
        "Client ID",
        "Client",
        "Active",
        "First trade",
        "Duration (y)",
        "Current value",
        "Lifetime XIRR %",
        "Lifetime Abs %",
        "Lifetime Nifty Abs %",
    ]
    for key in payload.get("horizons") or [h[0] for h in HORIZONS]:
        headers.append(f"{key} Abs %")
        headers.append(f"{key} Nifty Abs %")
        headers.append(f"{key} since inception")
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True)

    for row in payload.get("rows") or []:
        line = [
            row.client_id,
            row.client_name,
            "Y" if row.is_active else "N",
            row.first_trade.isoformat() if row.first_trade else "",
            row.duration_years if row.duration_years is not None else "",
            round(row.current_value, 2),
            row.lifetime_xirr_pct if row.lifetime_xirr_pct is not None else "",
            row.lifetime_abs_pct if row.lifetime_abs_pct is not None else "",
            row.lifetime_nifty_abs_pct
            if row.lifetime_nifty_abs_pct is not None
            else "",
        ]
        for key in payload.get("horizons") or [h[0] for h in HORIZONS]:
            hr = (row.horizons or {}).get(key)
            if not hr:
                line.extend(["", "", ""])
                continue
            line.append(
                hr.portfolio_abs_pct if hr.portfolio_abs_pct is not None else ""
            )
            line.append(hr.nifty_abs_pct if hr.nifty_abs_pct is not None else "")
            line.append("Y" if hr.since_inception else "N")
        if row.error:
            line.append(row.error)
        ws.append(line)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def render_email_html(payload: Dict[str, Any], *, max_rows: int = 40) -> str:
    import html as html_mod

    as_of = payload.get("as_of")
    rows: List[ClientReturnsRow] = list(payload.get("rows") or [])
    # Sort: longest duration first for the email preview
    rows_sorted = sorted(
        rows,
        key=lambda r: (r.duration_years is not None, r.duration_years or 0),
        reverse=True,
    )
    preview = rows_sorted[:max_rows]
    horizon_keys = payload.get("horizons") or [h[0] for h in HORIZONS]

    th = [
        "Client",
        "Dur (y)",
        "XIRR",
        "Life Abs",
        "Life Nifty",
    ] + [f"{k}" for k in horizon_keys] + [f"{k} Nifty" for k in horizon_keys]

    body_rows = []
    for r in preview:
        cells = [
            html_mod.escape(r.client_name),
            f"{r.duration_years:.2f}" if r.duration_years is not None else "—",
            _fmt_pct(r.lifetime_xirr_pct),
            _fmt_pct(r.lifetime_abs_pct),
            _fmt_pct(r.lifetime_nifty_abs_pct),
        ]
        for k in horizon_keys:
            hr = (r.horizons or {}).get(k)
            label = _fmt_pct(hr.portfolio_abs_pct if hr else None)
            if hr and hr.since_inception and hr.portfolio_abs_pct is not None:
                label += "*"
            cells.append(label)
        for k in horizon_keys:
            hr = (r.horizons or {}).get(k)
            label = _fmt_pct(hr.nifty_abs_pct if hr else None)
            if hr and hr.since_inception and hr.nifty_abs_pct is not None:
                label += "*"
            cells.append(label)
        body_rows.append(
            "<tr>"
            + "".join(f"<td style='padding:4px 6px;border:1px solid #ddd'>{c}</td>" for c in cells)
            + "</tr>"
        )

    table = (
        "<table style='border-collapse:collapse;font-family:Arial,sans-serif;"
        "font-size:11px;width:100%'>"
        "<thead><tr style='background:#f0f0f0'>"
        + "".join(
            f"<th style='padding:4px 6px;border:1px solid #ddd;text-align:left'>{h}</th>"
            for h in th
        )
        + "</tr></thead><tbody>"
        + "\n".join(body_rows)
        + "</tbody></table>"
    )

    return f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;padding:16px">
<h2>Daily client returns — {html_mod.escape(str(as_of))}</h2>
<p style="color:#555">
  Clients: {payload.get('client_count', 0)}
  (ok={payload.get('ok_count', 0)}, errors={payload.get('error_count', 0)}).
  Absolute % on period net invested; Nifty = cashflow-timing sim.
  * = since inception (tenure shorter than horizon). Full matrix in Excel attachment.
</p>
{table}
<p style="color:#888;font-size:12px">Anshul-only digest · generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</p>
</body></html>"""


def write_preview_artifacts(
    payload: Dict[str, Any],
    *,
    out_dir: Optional[str] = None,
) -> Dict[str, str]:
    root = out_dir or os.path.join(
        os.environ.get("INERTIA_APP_DIR")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "var",
        "daily_returns",
    )
    os.makedirs(root, exist_ok=True)
    as_of = payload.get("as_of") or date.today().isoformat()
    xlsx_path = os.path.join(root, f"daily_client_returns_{as_of}.xlsx")
    html_path = os.path.join(root, f"daily_client_returns_{as_of}.html")
    txt_path = os.path.join(root, f"daily_client_returns_{as_of}_preview.txt")

    with open(xlsx_path, "wb") as f:
        f.write(build_excel_bytes(payload))
    html = render_email_html(payload)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    lines = [
        f"as_of={as_of} clients={payload.get('client_count')} "
        f"ok={payload.get('ok_count')} errors={payload.get('error_count')}",
        "",
    ]
    for r in sorted(
        payload.get("rows") or [],
        key=lambda x: (x.duration_years is not None, x.duration_years or 0),
        reverse=True,
    )[:25]:
        lines.append(
            f"{r.client_id:>4} {r.client_name[:28]:<28} "
            f"dur={r.duration_years or 0:>5.2f}y "
            f"xirr={_fmt_pct(r.lifetime_xirr_pct):>9} "
            f"abs={_fmt_pct(r.lifetime_abs_pct):>9} "
            f"nifty={_fmt_pct(r.lifetime_nifty_abs_pct):>9} "
            f"1Y={_fmt_pct((r.horizons.get('1Y').portfolio_abs_pct if r.horizons.get('1Y') else None)):>9}"
        )
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return {"xlsx": xlsx_path, "html": html_path, "preview": txt_path}


def send_daily_client_returns_digest(
    *,
    dry_run: bool = False,
    as_of: Optional[date] = None,
    active_only: bool = False,
    client_ids: Optional[Sequence[int]] = None,
    limit: Optional[int] = None,
    out_dir: Optional[str] = None,
) -> Dict[str, Any]:
    payload = build_daily_client_returns(
        as_of=as_of,
        active_only=active_only,
        client_ids=client_ids,
        limit=limit,
    )
    artifacts = write_preview_artifacts(payload, out_dir=out_dir)
    html = render_email_html(payload)
    xlsx = build_excel_bytes(payload)
    subject = (
        f"Daily client returns — {payload.get('as_of')} "
        f"({payload.get('client_count', 0)} clients)"
    )
    result: Dict[str, Any] = {
        "dry_run": dry_run,
        "as_of": payload.get("as_of"),
        "client_count": payload.get("client_count"),
        "ok_count": payload.get("ok_count"),
        "error_count": payload.get("error_count"),
        "subject": subject,
        "recipients": [ALLOWED_EMAIL],
        "artifacts": artifacts,
        "sent": False,
    }
    if dry_run:
        return result

    try:
        from flask import current_app
        from flask_mail import Message
        from extensions import mail

        msg = Message(
            subject=subject,
            recipients=[ALLOWED_EMAIL],
            html=html,
            sender=current_app.config.get("MAIL_DEFAULT_SENDER")
            or current_app.config.get("MAIL_USERNAME"),
        )
        fname = f"daily_client_returns_{payload.get('as_of')}.xlsx"
        msg.attach(
            filename=fname,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            data=xlsx,
        )
        mail.send(msg)
        result["sent"] = True
        logger.info("Daily client returns digest sent to %s", ALLOWED_EMAIL)
    except Exception as exc:
        logger.exception("Daily client returns email failed: %s", exc)
        result["error"] = str(exc)
    return result
