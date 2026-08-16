"""Same-prompt presentation benchmark with reproducible artifacts.

The score is a structural quality proxy, not a substitute for human or vision
review. Every candidate keeps its outline, final IR, native ODP and previews so
the numeric summary remains auditable.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import List, Mapping, Optional

from pydantic import BaseModel, Field

from odforge.ir import MediaAssetRef, Outline, Presentation
from odforge.llm import generate_outline, generate_slides
from odforge.media import AssetInput
from odforge.preview import PreviewUnavailable, render_pages
from odforge.render import render
from odforge.textmetrics import check_budget
from odforge.themes import resolve_design
from odforge.validate import validate_odf

_VISUAL_LAYOUTS = {
    "chart",
    "process",
    "timeline",
    "metrics",
    "cards",
    "diagram",
    "image-focus",
    "image-split",
}


class BenchmarkMetrics(BaseModel):
    slide_count: int = 0
    layout_variety: int = 0
    visual_slide_ratio: float = 0.0
    notes_coverage: float = 0.0
    source_coverage: float = 0.0
    budget_warning_count: int = 0
    image_placeholder_count: int = 0
    package_valid: bool = False
    previews_rendered: int = 0


class BenchmarkEntry(BaseModel):
    candidate: str
    success: bool
    score: float = Field(ge=0, le=100)
    elapsed_seconds: float
    metrics: BenchmarkMetrics = Field(default_factory=BenchmarkMetrics)
    artifact_dir: str
    error: Optional[str] = None


class BenchmarkReport(BaseModel):
    prompt: str
    pages: Optional[int] = None
    entries: List[BenchmarkEntry]
    winner: Optional[str] = None
    scoring_note: str = (
        "Score is a deterministic structural proxy; inspect previews and use "
        "vision/human review for visual quality."
    )


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower()
    return cleaned[:48] or "candidate"


def _placeholder_count(
    presentation: Presentation,
    assets: Mapping[str, AssetInput] | None,
    image_generation_available: bool,
) -> int:
    known = set((assets or {}).keys())
    count = 0
    for slide in presentation.slides:
        image = slide.image
        if image is None:
            continue
        if image.src.startswith("asset://"):
            if image.src.removeprefix("asset://") not in known:
                count += 1
        elif not image.src and image.prompt and not image_generation_available:
            count += 1
    return count


def measure_presentation(
    presentation: Presentation,
    *,
    assets: Mapping[str, AssetInput] | None = None,
    package_valid: bool = False,
    previews_rendered: int = 0,
    image_generation_available: bool = False,
) -> BenchmarkMetrics:
    """Compute deterministic metrics from a generated presentation."""

    slides = presentation.slides
    total = len(slides)
    theme = resolve_design(presentation)
    visual = sum(slide.layout in _VISUAL_LAYOUTS for slide in slides)
    notes = sum(bool(slide.notes.strip()) for slide in slides)
    sources = sum(bool(slide.sources) for slide in slides)
    warnings = sum(len(check_budget(slide, theme)) for slide in slides)
    return BenchmarkMetrics(
        slide_count=total,
        layout_variety=len({slide.layout for slide in slides}),
        visual_slide_ratio=visual / total if total else 0.0,
        notes_coverage=notes / total if total else 0.0,
        source_coverage=sources / total if total else 0.0,
        budget_warning_count=warnings,
        image_placeholder_count=_placeholder_count(
            presentation, assets, image_generation_available
        ),
        package_valid=package_valid,
        previews_rendered=previews_rendered,
    )


def score_presentation(
    presentation: Presentation, metrics: BenchmarkMetrics
) -> float:
    """Return a 0–100 structural quality score."""

    if not metrics.package_valid or not presentation.slides:
        return 0.0
    slides = presentation.slides
    score = 10.0  # valid native package
    score += min(20.0, metrics.layout_variety / min(len(slides), 8) * 20.0)
    score += min(25.0, metrics.visual_slide_ratio / 0.6 * 25.0)
    score += metrics.notes_coverage * 15.0
    score += min(10.0, metrics.source_coverage / 0.35 * 10.0)
    score += max(0.0, 15.0 - metrics.budget_warning_count * 3.0)
    if slides[0].layout == "title":
        score += 2.5
    if slides[-1].layout == "closing":
        score += 2.5
    score -= metrics.image_placeholder_count * 8.0
    return round(max(0.0, min(100.0, score)), 1)


def _markdown(report: BenchmarkReport) -> str:
    lines = [
        "# ODForge model benchmark",
        "",
        f"Prompt: {report.prompt}",
        "",
        "> The score is a deterministic structural proxy. Review the generated "
        "previews for actual visual quality.",
        "",
        "| Candidate | Status | Score | Time | Slides | Layouts | Visual | "
        "Budget warnings | Previews |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for entry in report.entries:
        metrics = entry.metrics
        candidate_link = (
            f"[{entry.candidate}]({Path(entry.artifact_dir).name}/)"
            if entry.success
            else entry.candidate
        )
        lines.append(
            f"| {candidate_link} | {'OK' if entry.success else 'FAIL'} | "
            f"{entry.score:.1f} | {entry.elapsed_seconds:.1f}s | "
            f"{metrics.slide_count} | {metrics.layout_variety} | "
            f"{metrics.visual_slide_ratio:.0%} | "
            f"{metrics.budget_warning_count} | {metrics.previews_rendered} |"
        )
        if entry.error:
            lines.extend(["", f"- **{entry.candidate} error:** {entry.error}"])
    if report.winner:
        lines.extend(["", f"Structural-score winner: **{report.winner}**"])
    return "\n".join(lines) + "\n"


def run_benchmark(
    prompt: str,
    candidates: List[str],
    out_dir: Path,
    *,
    pages: int | None = None,
    assets: Mapping[str, AssetInput] | None = None,
    asset_refs: List[MediaAssetRef] | None = None,
) -> BenchmarkReport:
    """Run the complete pipeline once per candidate and write JSON/Markdown."""

    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    unique_candidates = list(dict.fromkeys(candidates))
    image_generation_available = (
        os.getenv("ODFORGE_IMAGE_BACKEND", "off").lower() == "http"
        and bool(os.getenv("ODFORGE_IMAGE_ENDPOINT", "").strip())
    )
    entries: list[BenchmarkEntry] = []

    for candidate in unique_candidates:
        started = time.perf_counter()
        candidate_dir = root / _safe_name(candidate)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        metrics = BenchmarkMetrics()
        try:
            outline: Outline = generate_outline(prompt, candidate, pages=pages)
            outline = outline.model_copy(
                update={
                    "media_assets": asset_refs or [],
                    "image_generation_available": image_generation_available,
                }
            )
            (candidate_dir / "outline.json").write_text(
                outline.model_dump_json(indent=2), encoding="utf-8"
            )
            presentation = generate_slides(outline, candidate)
            (candidate_dir / "presentation.json").write_text(
                presentation.model_dump_json(indent=2), encoding="utf-8"
            )
            odp_path = candidate_dir / "deck.odp"
            render(presentation, odp_path, assets=assets)
            validation = validate_odf(odp_path)
            package_valid = validation.ok
            previews = 0
            try:
                previews = len(
                    render_pages(odp_path, candidate_dir / "preview")
                )
            except PreviewUnavailable:
                previews = 0
            except Exception:
                previews = 0
            metrics = measure_presentation(
                presentation,
                assets=assets,
                package_valid=package_valid,
                previews_rendered=previews,
                image_generation_available=image_generation_available,
            )
            score = score_presentation(presentation, metrics)
            success = package_valid
            error = None if success else "ODF validation failed"
        except Exception as exc:
            score = 0.0
            success = False
            error = str(exc)

        entries.append(
            BenchmarkEntry(
                candidate=candidate,
                success=success,
                score=score,
                elapsed_seconds=round(time.perf_counter() - started, 3),
                metrics=metrics,
                artifact_dir=str(candidate_dir.resolve()),
                error=error,
            )
        )

    successful = [entry for entry in entries if entry.success]
    winner = (
        max(successful, key=lambda entry: entry.score).candidate
        if successful
        else None
    )
    report = BenchmarkReport(
        prompt=prompt, pages=pages, entries=entries, winner=winner
    )
    (root / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    (root / "report.md").write_text(_markdown(report), encoding="utf-8")
    return report


__all__ = [
    "BenchmarkEntry",
    "BenchmarkMetrics",
    "BenchmarkReport",
    "measure_presentation",
    "run_benchmark",
    "score_presentation",
]
