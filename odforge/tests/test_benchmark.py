from __future__ import annotations

from odforge import benchmark
from odforge.ir import Outline, PageRole, Presentation, Slide
from odforge.preview import PreviewUnavailable


def _outline() -> Outline:
    return Outline(
        pages=[
            PageRole(role="title", title="封面", gist="破題"),
            PageRole(role="process", title="方法", gist="三步驟"),
            PageRole(role="closing", title="結語", gist="收束"),
        ]
    )


def _strong_deck() -> Presentation:
    return Presentation(
        title="強",
        slides=[
            Slide(layout="title", title="封面", notes="開場"),
            Slide(
                layout="process",
                title="方法",
                steps=[
                    {"title": "理解", "detail": "定義問題"},
                    {"title": "製作", "detail": "完成原型"},
                    {"title": "驗證", "detail": "測試改善"},
                ],
                notes="說明步驟",
                sources=[{"label": "研究", "url": "https://example.com"}],
            ),
            Slide(layout="closing", title="結語", notes="行動呼籲"),
        ],
    )


def _weak_deck() -> Presentation:
    return Presentation(
        title="弱",
        slides=[
            Slide(layout="title", title="封面"),
            Slide(layout="title-content", title="內容", bullets=["甲", "乙"]),
            Slide(layout="closing", title="結語"),
        ],
    )


def test_benchmark_keeps_artifacts_and_picks_structural_winner(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(benchmark, "generate_outline", lambda *args, **kwargs: _outline())
    monkeypatch.setattr(
        benchmark,
        "generate_slides",
        lambda outline, candidate: (
            _strong_deck() if candidate == "strong" else _weak_deck()
        ),
    )
    monkeypatch.setattr(
        benchmark,
        "render_pages",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PreviewUnavailable("no soffice")
        ),
    )

    report = benchmark.run_benchmark(
        "同一題",
        ["weak", "strong"],
        tmp_path / "bench",
        pages=3,
    )

    assert report.winner == "strong"
    assert report.entries[1].score > report.entries[0].score
    for candidate in ("weak", "strong"):
        root = tmp_path / "bench" / candidate
        assert (root / "outline.json").exists()
        assert (root / "presentation.json").exists()
        assert (root / "deck.odp").exists()
    assert (tmp_path / "bench" / "report.json").exists()
    assert "strong" in (tmp_path / "bench" / "report.md").read_text(
        encoding="utf-8"
    )


def test_benchmark_failure_does_not_stop_other_candidates(tmp_path, monkeypatch):
    def outline(prompt, candidate, pages=None):
        if candidate == "broken":
            raise RuntimeError("model unavailable")
        return _outline()

    monkeypatch.setattr(benchmark, "generate_outline", outline)
    monkeypatch.setattr(
        benchmark, "generate_slides", lambda *args, **kwargs: _weak_deck()
    )
    monkeypatch.setattr(
        benchmark,
        "render_pages",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PreviewUnavailable("no soffice")
        ),
    )

    report = benchmark.run_benchmark(
        "同一題", ["broken", "healthy"], tmp_path / "bench"
    )
    assert report.entries[0].success is False
    assert "model unavailable" in (report.entries[0].error or "")
    assert report.entries[1].success is True
