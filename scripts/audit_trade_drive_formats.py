#!/usr/bin/env python3
"""Audit / optionally download trade files from a client's Trade details Drive folder.

  python3 scripts/audit_trade_drive_formats.py
  python3 scripts/audit_trade_drive_formats.py --client-id 99 --download 25
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", type=int, default=99)
    ap.add_argument("--download", type=int, default=0, help="Save N files under fixtures")
    ap.add_argument(
        "--folder-id",
        default="",
        help="Override Drive folder (default: trade_drive_links.json)",
    )
    args = ap.parse_args()

    from main import create_app
    from services.reconciliation.broker_trade_evidence import load_trade_drive_link
    from services.client_google_drive_service import walk_folder_tree, download_file_bytes
    from services.reconciliation.trade_parsers import detect_and_parse_trade

    app = create_app()
    with app.app_context():
        link = load_trade_drive_link(args.client_id)
        folder_id = (args.folder_id or link.get("folder_id") or "").strip()
        passwords = list(link.get("pdf_passwords") or [])
        if not folder_id:
            print("No trade folder linked for client", args.client_id)
            return 1
        walked = walk_folder_tree(folder_id, max_depth=3, max_nodes=300)
        files = list(walked.get("files") or [])
        print(f"folder={folder_id} files={len(files)} passwords={len(passwords)}")

        fmts = Counter()
        fills_n = 0
        for f in files:
            name = f.get("name") or ""
            dl = download_file_bytes(f["id"])
            if not dl.get("ok"):
                print("FAIL download", name, dl.get("error"))
                continue
            data = dl.get("content") or b""
            parsed = detect_and_parse_trade(
                data, filename=name, passwords=passwords
            )
            fmts[parsed.format_id] += 1
            fills_n += len(parsed.fills)
            warn = (parsed.warnings or [""])[0][:80]
            print(
                f"  {parsed.format_id:28s} fills={len(parsed.fills):3d}  {name[:55]:55s} {warn}"
            )

        print("format_counts", dict(fmts), "total_fills", fills_n)

        if args.download > 0:
            out = ROOT / "tests/fixtures/trade_statements/_raw"
            out.mkdir(parents=True, exist_ok=True)
            manifest = []
            for i, f in enumerate(files[: args.download], 1):
                name = f.get("name") or f"file_{i}"
                safe = re.sub(r"[^\w.\- ]+", "_", name)[:100].strip() or f"file_{i}"
                dl = download_file_bytes(f["id"])
                if not dl.get("ok"):
                    continue
                data = dl.get("content") or b""
                path = out / f"{i:02d}_{safe}"
                path.write_bytes(data)
                parsed = detect_and_parse_trade(
                    data, filename=name, passwords=passwords
                )
                manifest.append(
                    {
                        "i": i,
                        "name": name,
                        "path": str(path.relative_to(ROOT)),
                        "format_id": parsed.format_id,
                        "fills": len(parsed.fills),
                        "bytes": len(data),
                    }
                )
            man = ROOT / "tests/fixtures/trade_statements/MANIFEST.json"
            man.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print("wrote", len(manifest), "→", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
