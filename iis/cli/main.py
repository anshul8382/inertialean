from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from iis.models.enums import GateId, SourceTier
from iis.orchestrator.orchestrator import Orchestrator
from iis.review.gates import gate_preview

app = typer.Typer(name="iis", help="Inertia Intelligence System — Indian equity research")
console = Console()
orch = Orchestrator()


def _tier_style(tier: SourceTier) -> str:
    return {
        SourceTier.APPROVED: "green",
        SourceTier.EXTRACTED: "yellow",
        SourceTier.AGENT: "cyan",
        SourceTier.SESSION: "blue",
        SourceTier.EXTERNAL: "red bold",
    }.get(tier, "white")


def _print_chat_response(response) -> None:
    tier_color = _tier_style(response.source_tier)
    console.print(Panel(Markdown(response.answer), title=f"[{tier_color}]Source: {response.source_tier.value}[/{tier_color}]"))
    if response.sources:
        console.print("\n[bold]Sources:[/bold]")
        for src in response.sources:
            console.print(f"  • {src}")
    if response.note:
        console.print(f"\n[yellow]Note:[/yellow] {response.note}")
    if response.handoff:
        console.print(f"\n[cyan]Action:[/cyan] {response.handoff}")


def _preview_gate(session, gate_id: GateId) -> None:
    preview = gate_preview(session, gate_id)
    console.print(Panel(f"Status: [bold]{preview['status']}[/bold]", title=f"Gate: {gate_id.value}"))

    if gate_id == GateId.EXTRACTED_FINANCIALS:
        table = Table(title="Key Metrics")
        table.add_column("Metric")
        table.add_column("Period")
        table.add_column("Value (INR mn)")
        table.add_column("Source")
        for m in preview.get("metrics", []):
            table.add_row(m["metric"], m["period"][:30], f"{m['value']:,.0f}" if m["value"] else "-", m["source"])
        console.print(table)
        if preview.get("segments"):
            seg_table = Table(title="Segments")
            seg_table.add_column("Segment")
            seg_table.add_column("Revenue (INR mn)")
            for s in preview["segments"]:
                seg_table.add_row(s["segment"], f"{s['revenue_mn']:,.0f}")
            console.print(seg_table)
        flag_rows = preview.get("data_flags") or []
        review_flags = [f for f in flag_rows if f.get("severity") == "review"]
        if review_flags:
            console.print(f"\n[bold yellow]Data flags to review ({len(review_flags)}):[/bold yellow]")
            for f in review_flags[:8]:
                console.print(f"  • {f.get('headline', 'Flag')}")
                console.print(f"    [dim]{f.get('explanation', '')[:120]}[/dim]")

    elif gate_id == GateId.TEXT_INFERENCES:
        for art in preview.get("artifacts", []):
            title = art.get("type", "artifact")
            if art.get("sections_read"):
                title += f" ({art['sections_read']} AR sections)"
            console.print(Panel(art.get("preview", ""), title=f"{title} (confidence: {art['confidence']})"))
        console.print(f"PDF files: {preview.get('pdf_files', [])}")

    elif gate_id == GateId.NORMALIZATION:
        if preview.get("ratios"):
            table = Table(title="Computed Ratios")
            table.add_column("Period")
            table.add_column("EBITDA Margin")
            table.add_column("Net Margin")
            for period, ratios in list(preview["ratios"].items())[-4:]:
                em = ratios.get("ebitda_margin")
                nm = ratios.get("net_margin")
                table.add_row(
                    period,
                    f"{em:.1%}" if em is not None else "-",
                    f"{nm:.1%}" if nm is not None else "-",
                )
            console.print(table)

    elif gate_id == GateId.VALUATION_ASSUMPTIONS:
        table = Table(title="Proposed Assumptions")
        table.add_column("Key")
        table.add_column("Label")
        table.add_column("Effective")
        table.add_column("Rationale")
        for a in preview.get("assumptions", []):
            val = a["effective"]
            if isinstance(val, float) and a.get("unit") == "decimal":
                display = f"{val:.1%}" if "margin" in a["key"] or "growth" in a["key"] or "rate" in a["key"] else str(val)
            else:
                display = str(val)
            table.add_row(a["key"], a["label"], display, a["rationale"][:50] + "...")
        console.print(table)
        vp = preview.get("valuation_preview")
        if vp and vp.get("intrinsic_value_per_share"):
            console.print(
                f"\n[dim]Preview (requires approval): ₹{vp['intrinsic_value_per_share']:,.0f}/share[/dim]"
            )


@app.command()
def new(
    company: str = typer.Option(None, help="Company name hint"),
    session_id_only: bool = typer.Option(False, "--session-id", help="Print only session ID"),
):
    """Create a new research session."""
    session = orch.create_session(company)
    if session_id_only:
        typer.echo(session.id)
    else:
        console.print(Panel(f"Session created: [bold]{session.id}[/bold]", title="IIS"))
        console.print(f"Upload files: iis upload {session.id} <files...>")


@app.command()
def upload(
    session_id: str,
    files: list[Path] = typer.Argument(..., help="Files to upload (XLS, PDF)"),
):
    """Upload financial, estimate, or PDF files to a session."""
    orch.upload_files(session_id, files)
    console.print(f"Uploaded {len(files)} file(s) to session {session_id}")
    console.print("Run: iis ingest " + session_id)


@app.command()
def ingest(session_id: str):
    """Parse uploaded files (Capital IQ XLS + PDF extraction)."""
    session = orch.ingest(session_id)
    company = session.company.name if session.company else "Unknown"
    console.print(Panel(
        f"Company: [bold]{company}[/bold]\n"
        f"Line items: {len(session.financial_line_items)}\n"
        f"Consensus metrics: {len(session.consensus_metrics)}\n"
        f"PDF chunks: {len(session.pdf_chunks)}",
        title="Ingestion Complete",
    ))
    console.print(f"\nReview: iis review {session_id} --gate extracted_financials --preview")


@app.command()
def review(
    session_id: str,
    gate: str = typer.Option(..., "--gate", help="Gate ID to review"),
    approve: bool = typer.Option(False, "--approve", help="Approve the gate"),
    reject: bool = typer.Option(False, "--reject", help="Reject the gate"),
    comment: str = typer.Option(None, "--comment", help="Reviewer comment"),
    preview: bool = typer.Option(False, "--preview", help="Preview gate content"),
    json_out: bool = typer.Option(False, "--json", help="Output preview as JSON"),
):
    """Review and approve/reject an approval gate."""
    session = orch.store.load(session_id)
    gate_id = GateId(gate)

    if preview or json_out or (not approve and not reject):
        data = gate_preview(session, gate_id)
        if json_out:
            typer.echo(json.dumps(data, indent=2, default=str))
        else:
            _preview_gate(session, gate_id)
        return

    session = orch.approve_gate(session_id, gate_id, approve=approve and not reject, comment=comment)
    status = session.gate(gate_id).status.value
    console.print(f"Gate [bold]{gate}[/bold] → [green]{status}[/green]")


@app.command()
def assume(
    session_id: str,
    key: str = typer.Argument(..., help="Assumption key (e.g. terminal_growth)"),
    value: float = typer.Argument(..., help="New value (use decimal, e.g. 0.05 for 5%)"),
):
    """Edit a valuation assumption (resets Gate 4 to pending)."""
    session = orch.update_assumption(session_id, key, value)
    console.print(f"Updated [bold]{key}[/bold] → {value}")
    console.print(f"Gate 4 reset to pending. Review: iis review {session_id} --gate valuation_assumptions --preview")


@app.command()
def analyze(session_id: str, force: bool = typer.Option(False, help="Unused (gates no longer block analysis)")):
    """Run analysis agents. Review numbers and tweak valuation in chat — no gate approve required."""
    try:
        session = orch.analyze(session_id, skip_gates=True)
    except PermissionError as e:
        console.print(f"[yellow]{e}[/yellow]")
        raise typer.Exit(1)

    console.print(Panel("Analysis complete.", title="Analyze"))
    console.print(f"Assumptions: {len(session.assumptions)}")
    console.print(f"Review: iis review {session_id} --gate valuation_assumptions --preview")


@app.command()
def chat(session_id: str, question: str = typer.Argument(None, help="Your question")):
    """Ask a question about the research session."""
    if not question:
        console.print("[yellow]Use `iis interactive {session_id}` for REPL mode[/yellow]")
        raise typer.Exit(1)
    response = orch.ask(session_id, question)
    _print_chat_response(response)


@app.command()
def interactive(session_id: str):
    """Interactive chat REPL for a research session."""
    session = orch.get_session(session_id)
    company = session.company.name if session.company else session_id[:8]
    console.print(Panel(f"Interactive chat — {company}\nType 'exit' to quit, 'note' to generate research note.", title="IIS Chat"))
    while True:
        try:
            question = console.input("[bold blue]You:[/bold blue] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye.")
            break
        if not question.strip():
            continue
        if question.strip().lower() in ("exit", "quit"):
            break
        if question.strip().lower() == "note":
            text = orch.generate_note(session_id)
            console.print(Markdown(text[:3000]))
            continue
        response = orch.ask(session_id, question)
        _print_chat_response(response)


@app.command("ask")
def ask_alias(session_id: str, question: str = typer.Argument(...)):
    """Alias for chat."""
    chat(session_id, question)


@app.command("pack")
def pack_cmd(
    session_id: str,
    show: bool = typer.Option(False, "--show", help="Print pack markdown to stdout"),
):
    """Regenerate offline analysis_pack.md from Capital IQ / session (no web, no LLM)."""
    from iis.finance.analysis_pack import load_analysis_pack, write_analysis_pack

    session = orch.get_session(session_id)
    path = write_analysis_pack(session)
    orch.store.save(session)
    if not path:
        console.print("[red]Could not write analysis pack (missing company key / path).[/red]")
        raise typer.Exit(1)
    console.print(f"Wrote [bold]{path}[/bold] ({path.stat().st_size:,} bytes)")
    if show:
        md = load_analysis_pack(session) or ""
        console.print(Markdown(md[:12000] + ("…" if len(md) > 12000 else "")))


@app.command("checklist")
def checklist_cmd(
    init: bool = typer.Option(False, "--init", help="Write default ~/.iis/checklist_profile.json"),
    show: bool = typer.Option(True, "--show/--no-show", help="Print current profile"),
    enable_scores: bool | None = typer.Option(
        None, "--enable-scores/--disable-scores", help="Toggle optional Piotroski-style scores"
    ),
):
    """View / initialize configurable analysis checklist (CIQ flags + pack sections)."""
    from iis.finance.checklist import (
        default_profile,
        load_checklist_profile,
        profile_path,
        profile_to_dict,
        save_checklist_profile,
    )

    path = profile_path()
    if init or not path.exists():
        p = default_profile()
        save_checklist_profile(p, path)
        console.print(f"Wrote default checklist → [bold]{path}[/bold]")
    else:
        p = load_checklist_profile(path)

    if enable_scores is not None:
        p.enable_quality_scores = enable_scores
        p.pack_sections["quality_scores"] = enable_scores
        save_checklist_profile(p, path)
        console.print(f"quality scores → {'on' if enable_scores else 'off'}")

    if show:
        import json

        console.print(json.dumps(profile_to_dict(p), indent=2))
        console.print(f"\nEdit [bold]{path}[/bold] then re-run `iis pack <session>`.")


@app.command()
def note(session_id: str, output: Path = typer.Option(None, "--output", "-o", help="Save report to file")):
    """Generate Layer 1 + Layer 2 research report (valuation is chat-editable; rating left blank)."""
    text = orch.generate_note(session_id)
    if output:
        output.write_text(text)
        console.print(f"Report saved to {output}")
    else:
        console.print(Markdown(text))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host"),
    port: int = typer.Option(8080, help="Port"),
    reload: bool = typer.Option(False, help="Auto-reload"),
):
    """Start the minimal web UI."""
    import uvicorn

    console.print(Panel(f"Open [bold]http://{host}:{port}[/bold]", title="IIS Web UI"))
    uvicorn.run("iis.api.app:app", host=host, port=port, reload=reload)


@app.command("list")
def list_sessions():
    """List all research sessions."""
    sessions = orch.list_sessions()
    if not sessions:
        console.print("No sessions found.")
        return

    table = Table(title="Research Sessions")
    table.add_column("ID")
    table.add_column("Company")
    table.add_column("Status")
    table.add_column("Files")

    for s in sessions:
        table.add_row(s.id[:8] + "...", s.company.name if s.company else "-", s.status.value, str(len(s.uploaded_files)))
    console.print(table)


@app.command()
def status(session_id: str):
    """Show session status and gate states."""
    session = orch.get_session(session_id)
    company = session.company.name if session.company else "Unknown"
    console.print(Panel(
        f"Company: {company}\nStatus: {session.status.value}\n"
        f"Line items: {len(session.financial_line_items)}\n"
        f"PDF chunks: {len(session.pdf_chunks)}\n"
        f"Events: {len(session.events)}",
        title=f"Session {session_id[:8]}...",
    ))

    table = Table(title="Approval Gates")
    table.add_column("Gate")
    table.add_column("Status")
    for gate_id in GateId:
        g = session.gate(gate_id)
        table.add_row(gate_id.value, g.status.value)
    console.print(table)

    if session.market_quote:
        q = session.market_quote
        console.print(
            f"\nLive price (Ollama): ₹{q.price:,.2f} ({q.exchange}, confidence: {q.confidence})"
        )
        if q.disclaimer:
            console.print(f"[dim]{q.disclaimer}[/dim]")

    if session.pdf_documents:
        console.print("\nPDF documents:")
        for d in session.pdf_documents:
            console.print(f"  • {d.file}: {d.doc_type} ({d.qa_pairs} Q&A pairs)")


auth_app = typer.Typer(help="User authentication")
app.add_typer(auth_app, name="auth")


@auth_app.command("register")
def auth_register(
    email: str = typer.Argument(...),
    password: str = typer.Argument(...),
    name: str = typer.Option("Analyst", "--name"),
):
    """Register a new IIS user."""
    from iis.auth.store import UserStore

    store = UserStore()
    try:
        user = store.register(email, password, name)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    from iis.cli.auth_util import save_token

    save_token(user.api_token, user.email)
    console.print(Panel(f"Registered [bold]{user.name}[/bold]\nAPI token saved to ~/.iis/credentials.json", title="Auth"))


@auth_app.command("login")
def auth_login(email: str = typer.Argument(...), password: str = typer.Argument(...)):
    """Login and save API token."""
    from iis.auth.store import UserStore
    from iis.cli.auth_util import save_token

    store = UserStore()
    user = store.authenticate(email, password)
    if not user:
        console.print("[red]Invalid credentials[/red]")
        raise typer.Exit(1)
    save_token(user.api_token, user.email)
    console.print(f"Logged in as {user.name}. Token saved.")


@app.command()
def price(session_id: str):
    """Fetch live market price via Ollama (no BSE/NSE APIs)."""
    session = orch.refresh_market_price(session_id)
    if session.market_quote:
        q = session.market_quote
        body = (
            f"**{q.symbol}** on {q.exchange}\n"
            f"Price: [bold]₹{q.price:,.2f}[/bold]\n"
            f"Confidence: {q.confidence}\n"
            f"Source: {q.source}\n"
            f"Fetched: {q.fetched_at.isoformat()}"
        )
        if q.disclaimer:
            body += f"\n\n[dim]{q.disclaimer}[/dim]"
        console.print(Panel(body, title="Market Price (Ollama)"))
    else:
        console.print(
            "[yellow]Could not fetch price via Ollama. Ensure Ollama is running and company ticker is set.[/yellow]"
        )


@app.command("init-db")
def init_db():
    """Create MySQL/SQLite tables for IIS."""
    from iis.config import settings
    from iis.db.engine import database_available, init_database

    init_database()
    if database_available():
        console.print(Panel(
            f"Database ready.\nURL: [dim]{settings.resolved_database_url.split('@')[-1]}[/dim]",
            title="IIS Database",
        ))
    else:
        console.print("[red]Could not connect to database. Start MySQL: docker compose up -d[/red]")
        raise typer.Exit(1)


@app.command("migrate-json-sessions")
def migrate_json_sessions():
    """Import legacy JSON sessions from ~/.iis/sessions into MySQL."""
    from iis.db.engine import init_database
    from iis.storage.session_store import DatabaseSessionStore, JsonSessionStore

    init_database()
    src = JsonSessionStore()
    dst = DatabaseSessionStore()
    sessions = src.list_sessions()
    if not sessions:
        console.print("No JSON sessions to migrate.")
        return
    for s in sessions:
        dst.save(s)
        console.print(f"Migrated {s.id[:8]}... ({s.company.name if s.company else 'unknown'})")
    console.print(f"[green]Migrated {len(sessions)} session(s).[/green]")


@app.command("setup-finance-model")
def setup_finance_model():
    """Create the IIS finance-specialized Ollama model (iis-finance)."""
    import subprocess
    from pathlib import Path

    modelfile = Path(__file__).resolve().parent.parent / "llm" / "Modelfile"
    if not modelfile.exists():
        console.print(f"[red]Modelfile not found at {modelfile}[/red]")
        raise typer.Exit(1)

    console.print(f"Creating [bold]iis-finance[/bold] from {modelfile}...")
    result = subprocess.run(["ollama", "create", "iis-finance", "-f", str(modelfile)], capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]{result.stderr or result.stdout}[/red]")
        raise typer.Exit(1)
    console.print(Panel(
        "Finance model [bold]iis-finance[/bold] created.\n\n"
        "Annual report reading will use this model automatically.\n"
        "Set IIS_OLLAMA_FINANCE_MODEL=iis-finance in .env if needed.",
        title="Finance Model Ready",
    ))


bench_app = typer.Typer(name="bench", help="Batch benchmarks and training runs")
app.add_typer(bench_app, name="bench")

drive_app = typer.Typer(name="drive", help="Google Shared Drive sync (company folders + notes)")
app.add_typer(drive_app, name="drive")


@drive_app.command("install-creds")
def drive_install_creds(
    path: str = typer.Argument(..., help="Path to the OAuth client JSON you downloaded from Google Cloud"),
):
    """Copy your downloaded OAuth client JSON into ~/.iis/google/credentials.json."""
    import json
    import shutil
    from pathlib import Path

    from iis.config import settings

    src = Path(path).expanduser().resolve()
    if not src.exists():
        console.print(f"[red]File not found:[/red] {src}")
        raise typer.Exit(1)
    try:
        data = json.loads(src.read_text())
    except json.JSONDecodeError as e:
        console.print(f"[red]Not valid JSON:[/red] {e}")
        raise typer.Exit(1)
    if "installed" not in data and "web" not in data:
        console.print(
            "[red]Wrong file.[/red] Use an OAuth 2.0 Client ID JSON "
            "(Desktop app preferred — should contain an \"installed\" key)."
        )
        raise typer.Exit(1)
    if "installed" not in data and "web" in data:
        console.print(
            "[yellow]This looks like a Web client.[/yellow] "
            "Prefer creating a Desktop OAuth client in Google Cloud Console."
        )
    dest = settings.google_drive_credentials_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    # Force re-auth with the new client
    if settings.google_drive_token_path.exists():
        settings.google_drive_token_path.unlink()
        console.print("Removed old token — you must re-auth.")
    console.print(f"[green]Installed credentials →[/green] {dest}")
    console.print("Next: [bold]iis drive auth[/bold]")


@drive_app.command("auth")
def drive_auth():
    """One-time Google OAuth — opens a browser, then saves token under ~/.iis/google/."""
    from iis.config import settings
    from iis.integrations.gdrive import run_oauth_setup

    console.print(Panel(
        f"OAuth client JSON expected at:\n[bold]{settings.google_drive_credentials_path}[/bold]\n\n"
        f"Shared Drive folder ID: [bold]{settings.google_drive_folder_id}[/bold]\n"
        "Your Google account must have write access to that Shared Drive.\n\n"
        "If you just downloaded the JSON, run first:\n"
        "[bold]iis drive install-creds ~/Downloads/client_secret_….json[/bold]",
        title="Google Drive setup",
    ))
    if not settings.google_drive_credentials_path.exists():
        console.print("[red]credentials.json missing.[/red] Run: iis drive install-creds /path/to/downloaded.json")
        raise typer.Exit(1)
    try:
        token = run_oauth_setup()
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Authenticated.[/green] Token saved to {token}")
    console.print("Then sync files: [bold]iis drive push[/bold]  (or click Sync to Drive in the UI)")


@drive_app.command("status")
def drive_status():
    """Show whether Drive sync is configured and reachable."""
    from iis.config import settings
    from iis.integrations.gdrive import drive_configured, get_drive_service

    console.print(f"Enabled: {settings.google_drive_enabled}")
    console.print(f"Folder ID: {settings.google_drive_folder_id}")
    console.print(f"Credentials: {settings.google_drive_credentials_path} "
                  f"({'ok' if settings.google_drive_credentials_path.exists() else 'missing'})")
    console.print(f"Token: {settings.google_drive_token_path} "
                  f"({'ok' if settings.google_drive_token_path.exists() else 'missing'})")
    console.print(f"Configured: {drive_configured()}")
    if drive_configured():
        svc = get_drive_service()
        if not svc:
            console.print("[yellow]Could not build Drive client — re-run: iis drive auth[/yellow]")
            raise typer.Exit(1)
        fid = settings.google_drive_folder_id
        try:
            meta = svc.files().get(
                fileId=fid,
                fields="id, name, mimeType, driveId",
                supportsAllDrives=True,
            ).execute()
            console.print(f"[green]Reachable folder:[/green] {meta.get('name')} ({meta.get('id')})")
        except Exception:
            try:
                drive = svc.drives().get(driveId=fid, fields="id, name").execute()
                console.print(f"[green]Reachable Shared Drive:[/green] {drive.get('name')} ({drive.get('id')})")
            except Exception as e:
                console.print(f"[red]Lookup failed:[/red] {e}")
                console.print("[yellow]Tip:[/yellow] delete ~/.iis/google/token.json and re-run: iis drive auth")
                raise typer.Exit(1)


@drive_app.command("ensure-folder")
def drive_ensure_folder(company: str = typer.Argument(..., help="Company folder name")):
    """Create (or find) a company directory on the Shared Drive."""
    from iis.integrations.gdrive import ensure_company_folder

    folder_id = ensure_company_folder(company)
    if not folder_id:
        console.print("[red]Failed — check: iis drive status[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Folder ready:[/green] {company} → {folder_id}")


@drive_app.command("push")
def drive_push(
    company: str = typer.Argument(
        None,
        help="Company key (e.g. bse-502355) or leave blank to push all",
    ),
    name: str = typer.Option(None, "--name", "-n", help="Override Drive folder display name"),
):
    """Upload local company library files to Shared Drive (after auth)."""
    from iis.integrations.gdrive import drive_configured, push_company_library_to_drive

    if not drive_configured():
        console.print("[red]Not authenticated.[/red] Run: [bold]iis drive auth[/bold]")
        raise typer.Exit(1)
    console.print(Panel("Pushing local library → Shared Drive…"))
    result = push_company_library_to_drive(company_key=company, company_name=name)
    for row in result.get("companies") or []:
        console.print(
            f"  {row['company']}: [green]{row.get('uploaded', 0)}[/green] uploaded"
            + (f", [cyan]{row.get('skipped', 0)}[/cyan] already on Drive" if row.get("skipped") else "")
            + (f", [red]{row.get('failed', 0)} failed[/red]" if row.get("failed") else "")
        )
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)
    console.print(
        f"\n[green]Done.[/green] {result.get('uploaded', 0)} uploaded"
        + f" · {result.get('skipped', 0)} already present"
        + (f" · {result.get('failed', 0)} failed" if result.get("failed") else "")
    )


telegram_app = typer.Typer(
    name="telegram",
    help="Telegram group PDFs → Shared Drive inbox (personal Telethon session)",
)
app.add_typer(telegram_app, name="telegram")


@telegram_app.command("auth")
def telegram_auth(
    method: str = typer.Option(
        "phone",
        "--method",
        "-m",
        help="phone = login code from Telegram chat 777000 (recommended); qr = scan Preview image",
    ),
    timeout: int = typer.Option(300, "--timeout", help="Total seconds for QR mode"),
    phone: str = typer.Option(None, "--phone", "-p", help="Phone with country code, e.g. +9198xxxxxxxx"),
    reset: bool = typer.Option(False, "--reset", help="Delete broken session files first"),
):
    """One-time Telegram login. Prefer phone code from in-app chat Telegram/777000."""
    from iis.config import settings
    from iis.integrations.telegram_inbox import (
        clear_telegram_session,
        run_telegram_auth,
        telegram_credentials_ok,
    )

    console.print(Panel(
        "Telegram is already on this Mac — the login code usually appears [bold]here[/bold],\n"
        "in Mac Telegram → chat [bold]Telegram / 777000[/bold] — [bold]not[/bold] as SMS.\n\n"
        "1. Run auth with your [bold]+91…[/bold] number\n"
        "2. Switch to the Telegram app (it may open automatically)\n"
        "3. Open the newest message from [bold]Telegram[/bold] and paste only the digits\n"
        "4. If still nothing: at the prompt type [bold]sms[/bold] or [bold]call[/bold]\n\n"
        f"Session: [bold]{settings.telegram_session_path}[/bold]",
        title="Telegram auth",
    ))
    if not telegram_credentials_ok():
        console.print("[red]Missing API credentials.[/red] Set IIS_TELEGRAM_API_ID and IIS_TELEGRAM_API_HASH.")
        raise typer.Exit(1)
    if reset:
        clear_telegram_session()
        console.print("[yellow]Cleared old session files.[/yellow]")
    method = (method or "phone").lower().strip()
    if method not in ("qr", "phone"):
        console.print("[red]--method must be phone or qr[/red]")
        raise typer.Exit(1)
    try:
        info = run_telegram_auth(method=method, timeout=timeout, phone=phone or settings.telegram_phone)
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        console.print("[yellow]Tip:[/yellow] iis telegram auth --reset --method phone --phone +91XXXXXXXXXX")
        raise typer.Exit(1)
    console.print(
        f"[green]Authenticated.[/green] method={info.get('method')} "
        f"user={info.get('username') or info.get('user_id')} "
        f"session={info.get('session')}"
    )
    console.print("Next: [bold]iis telegram status[/bold] then [bold]iis telegram sync[/bold]")


@telegram_app.command("status")
def telegram_status_cmd():
    """Show Telegram session, group reachability, Drive inbox, and report index."""
    from iis.integrations.telegram_inbox import telegram_status
    from iis.integrations.telegram_report_index import index_stats

    st = telegram_status()
    console.print(f"Enabled: {st.get('enabled')}")
    console.print(f"API credentials: {'ok' if st.get('credentials_ok') else 'missing'}")
    console.print(f"Session: {'ok' if st.get('session_ok') else 'missing — run iis telegram auth'}")
    console.print(f"Group setting: {st.get('group') or '(not set)'}")
    console.print(f"Group reachable: {st.get('group_ok')} {st.get('group_title') or ''}")
    console.print(f"Drive configured: {st.get('drive_configured')}")
    console.print(f"Drive inbox: {st.get('drive_inbox_name')} → {st.get('drive_inbox_id') or 'n/a'}")
    idx = index_stats()
    console.print(
        f"Report index: {idx.get('entry_count', 0)} entries · "
        f"updated {idx.get('updated_at') or 'never'} · {idx.get('path')}"
    )
    if st.get("error"):
        console.print(f"[yellow]{st['error']}[/yellow]")
        raise typer.Exit(1)


@telegram_app.command("sync")
def telegram_sync(
    limit: int = typer.Option(50, "--limit", "-n", help="Max recent messages to scan (inbox mode)"),
    company: str = typer.Option(
        None,
        "--company",
        "-c",
        help="Search group for this company and ingest PDFs into its library (analysis)",
    ),
):
    """Download PDFs from the Telegram group.

    Default: recent PDFs → Shared Drive 'Telegram PDFs' inbox.
    With --company: search + download into that company's brokerage_reports and ingest for analysis.
    """
    from iis.integrations.telegram_inbox import (
        sync_pdfs_for_company,
        sync_pdfs_to_drive,
        telegram_credentials_ok,
        telegram_session_exists,
    )
    from iis.config import settings

    if not telegram_credentials_ok():
        console.print("[red]Set IIS_TELEGRAM_API_ID and IIS_TELEGRAM_API_HASH first.[/red]")
        raise typer.Exit(1)
    if not telegram_session_exists():
        console.print("[red]No session.[/red] Run: [bold]iis telegram auth[/bold]")
        raise typer.Exit(1)
    if not settings.telegram_group:
        console.print("[red]Set IIS_TELEGRAM_GROUP=@group or chat id.[/red]")
        raise typer.Exit(1)

    if company:
        console.print(Panel(
            f"Pulling reports for [bold]{company}[/bold] from [bold]{settings.telegram_group}[/bold]\n"
            "Uses daily report index first, then live search fallback\n"
            "→ brokerage_reports → Drive → library ingest",
            title="Telegram company pull",
        ))
        result = sync_pdfs_for_company(company, search_limit=max(limit, 100), max_files=40, ingest=True)
        console.print(
            f"Source: {result.get('source')} · index hits {result.get('index_hits', 0)} · "
            f"matched {result.get('matched', 0)} · downloaded {result.get('downloaded', 0)} · "
            f"uploaded {result.get('uploaded', 0)} · ingested {result.get('ingested', 0)} · "
            f"skipped {result.get('skipped_seen', 0)}"
        )
        console.print(f"Terms: {', '.join(result.get('terms') or [])}")
        console.print(f"Library key: {result.get('company_key')}")
        for f in (result.get("files") or [])[:25]:
            meta = " · ".join(
                x
                for x in [
                    f.get("broker"),
                    f.get("report_type"),
                    f.get("rating"),
                    f.get("report_date"),
                ]
                if x
            )
            suffix = f"  ({meta})" if meta else ""
            console.print(f"  · {f.get('name')}{suffix}")
        for err in result.get("errors") or []:
            console.print(f"[yellow]{err}[/yellow]")
        if result.get("errors") and not result.get("downloaded") and not result.get("ingested"):
            raise typer.Exit(1)
        return

    console.print(Panel(
        f"Scanning last [bold]{limit}[/bold] messages in [bold]{settings.telegram_group}[/bold]\n"
        f"Local: {settings.telegram_local_inbox}\n"
        f"Drive folder: {settings.telegram_drive_inbox_name}",
        title="Telegram PDF sync",
    ))
    result = sync_pdfs_to_drive(limit=limit)
    console.print(
        f"Scanned {result.get('scanned', 0)} · PDFs {result.get('pdfs_found', 0)} · "
        f"downloaded {result.get('downloaded', 0)} · uploaded {result.get('uploaded', 0)} · "
        f"skipped {result.get('skipped_seen', 0)}"
    )
    if result.get("drive_inbox_id"):
        console.print(f"Drive inbox id: {result['drive_inbox_id']}")
    for f in (result.get("files") or [])[:20]:
        console.print(f"  · {f.get('name')} → {f.get('drive_id') or 'local only'}")
    for err in result.get("errors") or []:
        console.print(f"[yellow]{err}[/yellow]")
    if result.get("errors") and not result.get("downloaded"):
        raise typer.Exit(1)


@telegram_app.command("index")
def telegram_index_cmd(
    full: bool = typer.Option(
        False,
        "--full",
        help="Rescan many messages (ignore last_message_id). Use once, then daily incremental.",
    ),
    limit: int = typer.Option(
        None,
        "--limit",
        "-n",
        help="Max messages to scan (default: 400 incremental / 5000 full)",
    ),
    company: str = typer.Option(
        None,
        "--company",
        "-c",
        help="Only print index hits for this company (no Telegram scan)",
    ),
):
    """Build/update the daily PDF cross-ref index (~/.iis/telegram/report_index.json).

    Entries: file name, broker, company, report type, date, rating, keywords.
    Company research reads this file first to pick relevant reports.
    """
    from iis.integrations.telegram_report_index import (
        find_reports_for_company,
        index_stats,
        update_report_index,
    )

    if company:
        hits = find_reports_for_company(company)
        console.print(Panel(
            f"Index hits for [bold]{company}[/bold]: {len(hits)}",
            title="Report index lookup",
        ))
        for h in hits[:40]:
            console.print(
                f"  · [{h.get('_score')}] {h.get('file_name')}\n"
                f"    {h.get('broker') or '—'} · {h.get('report_type')} · "
                f"{h.get('rating') or '—'} · {h.get('report_date') or '—'} · "
                f"company={h.get('company') or '—'}"
            )
        st = index_stats()
        console.print(f"Index file: {st.get('path')} ({st.get('entry_count')} entries)")
        return

    console.print(Panel(
        f"{'Full' if full else 'Incremental'} index update → "
        f"[bold]~/.iis/telegram/report_index.json[/bold]",
        title="Telegram report index",
    ))
    result = update_report_index(limit=limit, full=full)
    if result.get("ok"):
        console.print(
            f"[green]OK[/green] scanned {result.get('scanned', 0)} · "
            f"PDFs {result.get('pdfs', 0)} · upserted {result.get('upserted', 0)} · "
            f"total entries {result.get('entry_count', 0)}"
        )
        console.print(f"Path: {result.get('path')}")
        console.print(f"last_message_id: {result.get('last_message_id')}")
    else:
        for err in result.get("errors") or ["index update failed"]:
            console.print(f"[red]{err}[/red]")
        raise typer.Exit(1)


@bench_app.command("valuation")
def bench_valuation(
    limit: int = typer.Option(50, help="Number of companies to run"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip Ollama synthesis"),
    ticker: list[str] = typer.Option(None, "--ticker", "-t", help="Specific tickers (repeatable)"),
):
    """Run DCF + valuation review on many companies via Yahoo Finance."""
    from iis.bench.valuation_batch import BENCHMARK_TICKERS, run_batch

    tickers = ticker if ticker else BENCHMARK_TICKERS[:limit]
    console.print(Panel(f"Running valuation benchmark on [bold]{len(tickers)}[/bold] companies (Yahoo Finance)..."))
    payload = run_batch(tickers, use_llm=not no_llm)
    s = payload["summary"]
    console.print(f"\n[green]Done.[/green] OK: {s['ok']}/{s['total']} · flags: {s['companies_needing_input']} companies need input")
    console.print(f"JSON: {payload['json_path']}")
    console.print(f"Report: {payload['markdown_path']}")
    llm = payload.get("llm_recommendations") or {}
    if llm.get("executive_summary"):
        console.print(Panel(Markdown(llm["executive_summary"]), title="Ollama — executive summary"))
    for adj in (llm.get("system_adjustments") or [])[:5]:
        console.print(f"  [{adj.get('priority', '?')}] {adj.get('area')}: {adj.get('proposed_change', '')[:120]}")


overnight_app = typer.Typer(
    name="overnight",
    help="Queue up to 10 companies; run full analysis+report one-by-one overnight",
)
app.add_typer(overnight_app, name="overnight")


@overnight_app.command("status")
def overnight_status_cmd():
    from iis.jobs.overnight_batch import get_overnight_batch

    st = get_overnight_batch().status()
    console.print(Panel(f"Batch status: [bold]{st.get('status')}[/bold] · worker={st.get('worker_alive')}"))
    c = st.get("counts") or {}
    console.print(
        f"pending={c.get('pending')} running={c.get('running')} "
        f"complete={c.get('complete')} failed={c.get('failed')} skipped={c.get('skipped')}"
    )
    for it in st.get("items") or []:
        console.print(
            f"  [{it.get('status')}] {it.get('company')} — {it.get('message') or ''}"
            + (f" · session {str(it.get('session_id') or '')[:8]}" if it.get("session_id") else "")
        )


@overnight_app.command("add")
def overnight_add_cmd(
    companies: list[str] = typer.Argument(..., help="Company names (space-separated or quoted)"),
):
    from iis.jobs.overnight_batch import get_overnight_batch

    get_overnight_batch().add_companies(companies)
    overnight_status_cmd()


@overnight_app.command("start")
def overnight_start_cmd():
    from iis.jobs.overnight_batch import get_overnight_batch

    try:
        get_overnight_batch().start()
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print("[green]Overnight batch started[/green] — leave the app running.")
    overnight_status_cmd()


@overnight_app.command("stop")
def overnight_stop_cmd():
    from iis.jobs.overnight_batch import get_overnight_batch

    get_overnight_batch().stop()
    console.print("Stop requested.")
    overnight_status_cmd()


vision_app = typer.Typer(
    name="vision",
    help="Local chart/screenshot vision (Ollama granite3.2-vision + optional DePlot)",
)
app.add_typer(vision_app, name="vision")


@vision_app.command("status")
def vision_status_cmd():
    """Show which vision backends are installed / downloaded."""
    from iis.vision.chart_extract import vision_status

    st = vision_status()
    table = Table(title="Chart vision")
    table.add_column("Key")
    table.add_column("Value")
    for k, v in st.items():
        table.add_row(str(k), str(v))
    console.print(table)


@vision_app.command("setup")
def vision_setup(
    deplot: bool = typer.Option(
        False,
        "--deplot",
        help="Also download DePlot weights (needs: pip install '.[vision]')",
    ),
    unichart: bool = typer.Option(
        False,
        "--unichart",
        help="Also try UniChart weights (optional; large — same niche as DePlot)",
    ),
    skip_ollama: bool = typer.Option(False, "--skip-ollama", help="Skip ollama pull"),
):
    """Download local vision models. Default: Ollama granite3.2-vision (~2.4GB)."""
    from iis.config import settings
    from iis.vision.chart_extract import setup_deplot_weights, setup_ollama_vision

    if not skip_ollama:
        model = settings.ollama_vision_model
        console.print(f"Pulling Ollama vision model [bold]{model}[/bold] …")
        res = setup_ollama_vision(model)
        if res.get("ok"):
            console.print(f"[green]Ollama ready:[/green] {model}")
        else:
            console.print(f"[red]Ollama pull failed:[/red] {res}")
            raise typer.Exit(1)

    if deplot:
        console.print("Downloading DePlot (google/deplot) into Hugging Face cache …")
        res = setup_deplot_weights()
        if res.get("ok"):
            console.print(f"[green]DePlot ready:[/green] {res.get('model')}")
        else:
            console.print(
                "[red]DePlot failed.[/red] Install deps first:\n"
                "  pip install -e '.[vision]'\n"
                f"Then retry. Error: {res.get('error')}"
            )
            raise typer.Exit(1)

    if unichart:
        console.print(
            "[yellow]UniChart[/yellow] is optional (overlaps DePlot). "
            "Install vision extras, then in Python:\n"
            "  from transformers import DonutProcessor, VisionEncoderDecoderModel\n"
            f"  DonutProcessor.from_pretrained('{settings.unichart_model_id}')\n"
            f"  VisionEncoderDecoderModel.from_pretrained('{settings.unichart_model_id}')"
        )

    console.print(Panel("Vision setup done. Upload screenshots in the app — charts are extracted automatically."))


@vision_app.command("extract")
def vision_extract(
    image: Path = typer.Argument(..., exists=True, help="PNG/JPG chart or table screenshot"),
    backend: str = typer.Option("auto", help="auto | ollama | deplot | unichart"),
):
    """Run chart/table extraction on one image."""
    from iis.vision.chart_extract import extract_chart_image

    result = extract_chart_image(image, backend=backend)  # type: ignore[arg-type]
    if not result.ok:
        console.print(f"[red]Failed ({result.backend}):[/red] {result.error}")
        raise typer.Exit(1)
    console.print(Panel(Markdown(result.dossier_text()), title=f"{result.backend} · {result.model}"))


if __name__ == "__main__":
    app()
