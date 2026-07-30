"""Run several real discovery interviews against the configured LLM backend.

This is intentionally a live smoke test, not part of pytest: it spends provider
quota and requires the project's ``.env``. It prints only prompts and generated
questions; API keys are never read into the report.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

from odforge.llm import DiscoveryPlan, generate_discovery_questions


CASES = [
    {
        "id": "graduation_vague",
        "prompt": "向系上老師提案的畢業專題進度報告，重點放系統架構與時程",
        "context": "講述型態：講者型；目標頁數：8 頁",
    },
    {
        "id": "startup_vague",
        "prompt": "替新創公司做一份募資簡報",
        "context": "講述型態：講者型",
    },
    {
        "id": "teaching_partial",
        "prompt": "給大一新生的資料結構第三章教學簡報，12 頁，語氣親切",
        "context": "講述型態：講者型；目標頁數：12 頁",
    },
    {
        "id": "hospital_specific",
        "prompt": (
            "向醫院主管提案候診流程 90 天改善試行，需說明目前等待拆解、"
            "跨職類交接、三項 KPI、風險與下一步"
        ),
        "context": "講述型態：講者型；目標頁數：6 頁；可用圖片素材：跨職類工作坊",
    },
    {
        "id": "already_detailed",
        "prompt": (
            "為資訊工程系專題評審製作 10 頁講者型進度簡報。目標是取得模型部署方案核准。"
            "專題為校園餐廳推薦系統；前端與資料庫已完成，目前卡在推薦模型延遲。"
            "必須包含三層式架構、已完成／未完成、8 月底前的四個里程碑、延遲風險、"
            "Demo 截圖，以及最後請老師確認雲端部署預算。不得虛構效能數據。"
        ),
        "context": "講述型態：講者型；目標頁數：10 頁；可用圖片素材：Demo 截圖",
    },
]


def _structural_issues(plan: DiscoveryPlan) -> list[str]:
    issues: list[str] = []
    if not 2 <= len(plan.questions) <= 5:
        issues.append("question_count")
    ids = [question.id for question in plan.questions]
    if len(ids) != len(set(ids)):
        issues.append("duplicate_ids")
    for question in plan.questions:
        if not 2 <= len(question.options) <= 4:
            issues.append(f"{question.id}:option_count")
        if len(question.options) != len(set(question.options)):
            issues.append(f"{question.id}:duplicate_options")
        if any(
            option.strip().startswith(("其他", "其它"))
            for option in question.options
        ):
            issues.append(f"{question.id}:other_option")
    return issues


def _run(case: dict[str, str]) -> dict:
    plan = generate_discovery_questions(
        case["prompt"],
        "custom",
        context=case["context"],
    )
    return {
        "id": case["id"],
        "prompt": case["prompt"],
        "summary": plan.summary,
        "known_context": plan.known_context,
        "completeness": plan.completeness,
        "questions": [
            {
                "id": question.id,
                "question": question.question,
                "why": question.why,
                "options": question.options,
            }
            for question in plan.questions
        ],
        "structural_issues": _structural_issues(plan),
    }


def main() -> None:
    load_dotenv()
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        future_cases = {pool.submit(_run, case): case for case in CASES}
        for future in as_completed(future_cases):
            case = future_cases[future]
            try:
                results[case["id"]] = future.result()
            except Exception as exc:  # live provider failures belong in the report
                results[case["id"]] = {
                    "id": case["id"],
                    "prompt": case["prompt"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
    ordered = [results[case["id"]] for case in CASES]
    print(json.dumps(ordered, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
