"""Extract tables / descriptions from chart & screenshot images.

Backends (local, downloadable):
  - ollama: granite3.2-vision (default) — general charts/tables/docs
  - deplot: google/deplot via Hugging Face — specialist chart→table
  - unichart: optional if installed (same niche as DePlot; not auto-pulled)

Disk note: on a 256GB Air prefer Ollama first (~2.4GB). DePlot needs
torch + transformers (~extra GBs) — install via `iis vision setup --deplot`.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from iis.config import settings

logger = logging.getLogger(__name__)

Backend = Literal["auto", "ollama", "deplot", "unichart"]

CHART_PROMPT = (
    "This image is from equity research (Capital IQ, broker note, annual report, "
    "or investor presentation). Extract ALL readable data.\n"
    "1) If it is a chart/plot: return a Markdown data table (categories × series) "
    "with numbers as shown.\n"
    "2) If it is a table screenshot: return the full Markdown table.\n"
    "3) Add 2–4 bullet key takeaways from the visual.\n"
    "Do not invent numbers that are not visible. If unreadable, say so."
)

DEPLOT_PROMPT = "Generate underlying data table of the figure below:"


@dataclass
class ChartExtractResult:
    ok: bool
    backend: str
    model: str
    source_file: str
    table_markdown: str = ""
    narrative: str = ""
    raw: str = ""
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def dossier_text(self) -> str:
        parts = [f"**Chart extract** (`{Path(self.source_file).name}` · {self.backend}/{self.model})", ""]
        if self.table_markdown.strip():
            parts.append(self.table_markdown.strip())
            parts.append("")
        if self.narrative.strip():
            parts.append(self.narrative.strip())
            parts.append("")
        elif self.raw.strip() and not self.table_markdown.strip():
            parts.append(self.raw.strip())
            parts.append("")
        return "\n".join(parts)


def vision_status() -> dict[str, Any]:
    """What is installed / downloadable on this machine."""
    ollama_ok = False
    ollama_has_vision = False
    vision_model = getattr(settings, "ollama_vision_model", "granite3.2-vision")
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
            if r.status_code == 200:
                ollama_ok = True
                names = [m.get("name", "") for m in (r.json().get("models") or [])]
                ollama_has_vision = any(
                    vision_model.split(":")[0] in n or "vision" in n.lower() for n in names
                )
    except Exception:
        pass

    deplot = False
    unichart = False
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401

        deplot = True  # deps present; weights may still need download
        unichart = True
    except Exception:
        pass

    cache = Path.home() / ".cache" / "huggingface" / "hub"
    deplot_cached = any(cache.glob("*deplot*")) if cache.exists() else False
    unichart_cached = any(cache.glob("*unichart*")) if cache.exists() else False

    return {
        "chart_vision_enabled": bool(getattr(settings, "chart_vision_enabled", True)),
        "preferred_backend": getattr(settings, "chart_vision_backend", "auto"),
        "ollama_ok": ollama_ok,
        "ollama_vision_model": vision_model,
        "ollama_has_vision_model": ollama_has_vision,
        "deplot_deps_installed": deplot,
        "deplot_weights_cached": deplot_cached,
        "unichart_deps_installed": unichart,
        "unichart_weights_cached": unichart_cached,
        "note": (
            "Primary: Ollama granite3.2-vision (~2.4GB). "
            "Optional specialist: DePlot via Hugging Face (needs torch). "
            "UniChart is optional — same job as DePlot; not auto-downloaded."
        ),
    }


def extract_chart_image(
    path: str | Path,
    *,
    backend: Backend | None = None,
) -> ChartExtractResult:
    path = Path(path)
    if not path.exists():
        return ChartExtractResult(
            ok=False, backend="none", model="", source_file=str(path), error="file not found"
        )
    if not getattr(settings, "chart_vision_enabled", True):
        return ChartExtractResult(
            ok=False, backend="disabled", model="", source_file=str(path), error="chart_vision_enabled=false"
        )

    choice = (backend or getattr(settings, "chart_vision_backend", "auto") or "auto").lower()
    order: list[str]
    if choice == "auto":
        # Ollama first on Air (always light). DePlot if HF deps+weights present.
        order = ["ollama", "deplot"]
        st = vision_status()
        if st.get("deplot_deps_installed") and st.get("deplot_weights_cached"):
            order = ["deplot", "ollama"]
    elif choice in ("ollama", "deplot", "unichart"):
        order = [choice]
    else:
        order = ["ollama"]

    last: ChartExtractResult | None = None
    for name in order:
        if name == "ollama":
            last = _extract_ollama(path)
        elif name == "deplot":
            last = _extract_deplot(path)
        elif name == "unichart":
            last = _extract_unichart(path)
        else:
            continue
        if last.ok and (last.table_markdown or last.raw or last.narrative):
            return last
    return last or ChartExtractResult(
        ok=False, backend="none", model="", source_file=str(path), error="no backend succeeded"
    )


def _extract_ollama(path: Path) -> ChartExtractResult:
    model = getattr(settings, "ollama_vision_model", "granite3.2-vision")
    try:
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception as e:
        return ChartExtractResult(
            ok=False, backend="ollama", model=model, source_file=str(path), error=str(e)
        )

    # Ollama chat with images (base64, no data-URI prefix)
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": CHART_PROMPT,
                "images": [b64],
            }
        ],
        "options": {"temperature": 0.1},
    }
    timeout = float(getattr(settings, "chart_vision_timeout_sec", 120.0) or 120.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{settings.ollama_base_url.rstrip('/')}/api/chat", json=payload)
            if resp.status_code == 404:
                return ChartExtractResult(
                    ok=False,
                    backend="ollama",
                    model=model,
                    source_file=str(path),
                    error=f"model not pulled — run: ollama pull {model}",
                )
            resp.raise_for_status()
            raw = (resp.json().get("message") or {}).get("content") or ""
    except Exception as e:
        return ChartExtractResult(
            ok=False, backend="ollama", model=model, source_file=str(path), error=str(e)
        )

    table, narrative = _split_table_and_notes(raw)
    return ChartExtractResult(
        ok=bool(raw.strip()),
        backend="ollama",
        model=model,
        source_file=str(path),
        table_markdown=table,
        narrative=narrative,
        raw=raw,
    )


_deplot_bundle: dict[str, Any] | None = None


def _load_deplot():
    global _deplot_bundle
    if _deplot_bundle is not None:
        return _deplot_bundle
    from transformers import Pix2StructForConditionalGeneration, Pix2StructProcessor

    model_id = getattr(settings, "deplot_model_id", "google/deplot")
    processor = Pix2StructProcessor.from_pretrained(model_id)
    model = Pix2StructForConditionalGeneration.from_pretrained(model_id)
    model.eval()
    _deplot_bundle = {"processor": processor, "model": model, "id": model_id}
    return _deplot_bundle


def _extract_deplot(path: Path) -> ChartExtractResult:
    model_id = getattr(settings, "deplot_model_id", "google/deplot")
    try:
        from PIL import Image
        import torch
    except Exception as e:
        return ChartExtractResult(
            ok=False,
            backend="deplot",
            model=model_id,
            source_file=str(path),
            error=f"deps missing ({e}) — pip install 'inertia-intelligence[vision]' then iis vision setup --deplot",
        )
    try:
        bundle = _load_deplot()
    except Exception as e:
        return ChartExtractResult(
            ok=False, backend="deplot", model=model_id, source_file=str(path), error=str(e)
        )

    try:
        image = Image.open(path).convert("RGB")
        processor, model = bundle["processor"], bundle["model"]
        inputs = processor(images=image, text=DEPLOT_PROMPT, return_tensors="pt")
        with torch.no_grad():
            predictions = model.generate(**inputs, max_new_tokens=512)
        raw = processor.decode(predictions[0], skip_special_tokens=True)
        # DePlot often returns "TITLE | ... <0x0A> ..." linearized table
        table = _deplot_to_markdown(raw)
        return ChartExtractResult(
            ok=bool(raw.strip()),
            backend="deplot",
            model=bundle["id"],
            source_file=str(path),
            table_markdown=table or raw,
            narrative="",
            raw=raw,
        )
    except Exception as e:
        return ChartExtractResult(
            ok=False, backend="deplot", model=model_id, source_file=str(path), error=str(e)
        )


def _extract_unichart(path: Path) -> ChartExtractResult:
    """Optional UniChart — only if user installed weights; not auto-downloaded."""
    model_id = getattr(settings, "unichart_model_id", "ahmed-masry/unichart-base-960")
    try:
        from PIL import Image
        from transformers import DonutProcessor, VisionEncoderDecoderModel
        import torch
    except Exception as e:
        return ChartExtractResult(
            ok=False, backend="unichart", model=model_id, source_file=str(path), error=str(e)
        )
    try:
        processor = DonutProcessor.from_pretrained(model_id)
        model = VisionEncoderDecoderModel.from_pretrained(model_id)
        model.eval()
        image = Image.open(path).convert("RGB")
        prompt = "<extract_data_table> <s_answer>"
        decoder_input_ids = processor.tokenizer(prompt, add_special_tokens=False, return_tensors="pt").input_ids
        pixel_values = processor(image, return_tensors="pt").pixel_values
        with torch.no_grad():
            outputs = model.generate(
                pixel_values,
                decoder_input_ids=decoder_input_ids,
                max_length=model.decoder.config.max_position_embeddings,
                early_stopping=True,
                pad_token_id=processor.tokenizer.pad_token_id,
                eos_token_id=processor.tokenizer.eos_token_id,
                use_cache=True,
                num_beams=2,
                return_dict_in_generate=True,
            )
        sequence = processor.batch_decode(outputs.sequences)[0]
        sequence = sequence.replace(processor.tokenizer.eos_token, "").replace(processor.tokenizer.pad_token, "")
        raw = sequence.strip()
        return ChartExtractResult(
            ok=bool(raw),
            backend="unichart",
            model=model_id,
            source_file=str(path),
            table_markdown=raw,
            raw=raw,
        )
    except Exception as e:
        return ChartExtractResult(
            ok=False, backend="unichart", model=model_id, source_file=str(path), error=str(e)
        )


def _split_table_and_notes(raw: str) -> tuple[str, str]:
    if not raw:
        return "", ""
    lines = raw.splitlines()
    table_lines: list[str] = []
    other: list[str] = []
    in_table = False
    for ln in lines:
        if ln.strip().startswith("|"):
            in_table = True
            table_lines.append(ln)
        elif in_table and not ln.strip():
            in_table = False
            other.append(ln)
        elif in_table:
            table_lines.append(ln)
        else:
            other.append(ln)
    return "\n".join(table_lines).strip(), "\n".join(other).strip()


def _deplot_to_markdown(raw: str) -> str:
    """Convert DePlot linearized output to a simple markdown table when possible."""
    if not raw:
        return ""
    # Common format uses <0x0A> or newlines between rows, | between cells
    text = raw.replace("<0x0A>", "\n").replace("\\n", "\n")
    rows = [r.strip() for r in text.split("\n") if r.strip()]
    if len(rows) < 2:
        return f"```\n{raw}\n```"
    md_rows = []
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row.split("|") if c.strip() or True]
        # keep empty cells for alignment
        cells = [c.strip() for c in row.split("|")]
        if not any(cells):
            continue
        md_rows.append("| " + " | ".join(cells) + " |")
        if i == 0:
            md_rows.append("| " + " | ".join("---" for _ in cells) + " |")
    return "\n".join(md_rows) if md_rows else raw


def setup_ollama_vision(model: str | None = None) -> dict[str, Any]:
    model = model or getattr(settings, "ollama_vision_model", "granite3.2-vision")
    try:
        proc = subprocess.run(
            ["ollama", "pull", model],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        return {
            "ok": proc.returncode == 0,
            "model": model,
            "stdout": (proc.stdout or "")[-500:],
            "stderr": (proc.stderr or "")[-500:],
        }
    except Exception as e:
        return {"ok": False, "model": model, "error": str(e)}


def setup_deplot_weights() -> dict[str, Any]:
    """Download google/deplot into HF cache (requires vision extras)."""
    try:
        from transformers import Pix2StructForConditionalGeneration, Pix2StructProcessor

        model_id = getattr(settings, "deplot_model_id", "google/deplot")
        Pix2StructProcessor.from_pretrained(model_id)
        Pix2StructForConditionalGeneration.from_pretrained(model_id)
        return {"ok": True, "model": model_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def store_chart_extract_on_session(session: Any, result: ChartExtractResult) -> None:
    """Append extract to session agent_artifacts for dossier / report."""
    if not result.ok:
        return
    from iis.models.artifacts import AgentArtifact
    from iis.models.enums import SourceTier

    existing = next(
        (a for a in session.agent_artifacts if a.artifact_type == "chart_extracts"),
        None,
    )
    entry = result.to_dict()
    if existing and isinstance(existing.payload, dict):
        items = list(existing.payload.get("items") or [])
        items = [x for x in items if x.get("source_file") != result.source_file]
        items.append(entry)
        existing.payload["items"] = items
    else:
        session.agent_artifacts = [
            a for a in session.agent_artifacts if a.artifact_type != "chart_extracts"
        ]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id="chart_vision",
                artifact_type="chart_extracts",
                payload={"items": [entry]},
                confidence=0.7,
                provenance=[result.source_file],
                source_tier=SourceTier.AGENT,
            )
        )


def write_sidecar_markdown(image_path: Path, result: ChartExtractResult) -> Path | None:
    if not result.ok:
        return None
    side = image_path.with_suffix(image_path.suffix + ".chart.md")
    try:
        side.write_text(result.dossier_text(), encoding="utf-8")
        return side
    except Exception:
        return None
