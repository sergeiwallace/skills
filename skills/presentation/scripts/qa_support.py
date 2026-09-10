"""Immutable visual-critic fragments and collision-safe QA report assembly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from .presentation import PresentationError
    from .render_support import atomic_json, digest_path, digest_render_artifacts
except ImportError:  # Direct execution from the installed skill's scripts directory.
    from presentation import PresentationError
    from render_support import atomic_json, digest_path, digest_render_artifacts


class QAAssemblyError(PresentationError):
    """QA fragments cannot safely describe one candidate render."""


REQUIRED_RUBRIC_ITEMS = (
    "hierarchy",
    "composition_variety",
    "information_density",
    "narrative_continuity",
    "audience_fit",
    "accessibility",
    "rehearsal_readiness",
)
COMPARATIVE_RUBRIC_ITEMS = (
    "hierarchy",
    "pacing",
    "examples",
    "narrative_continuity",
    "actionability",
    "source_rigor",
)


def _judgment_coverage(
    values: object,
    required: tuple[str, ...],
    label: str,
    *,
    passes_only: bool = False,
) -> None:
    if not isinstance(values, list):
        raise QAAssemblyError(f"critic lacks complete {label} rubric coverage")
    items = {
        item.get("item")
        for item in values
        if isinstance(item, dict)
        and item.get("judgment") in ({"pass"} if passes_only else {"pass", "fail"})
        and isinstance(item.get("notes"), str)
        and item["notes"].strip()
    }
    if items != set(required) or len(values) != len(required):
        raise QAAssemblyError(f"critic lacks complete {label} rubric coverage")


def validate_critic_coverage(
    critic: dict[str, Any], expected_render_digest: str
) -> None:
    """Require explicit, digest-bound deck and representative-slide judgments."""
    _judgment_coverage(critic.get("deck"), REQUIRED_RUBRIC_ITEMS, "deck")
    reviewer = critic.get("reviewer")
    if (
        not isinstance(reviewer, dict)
        or reviewer.get("type") not in {"agent", "human"}
        or not reviewer.get("name")
    ):
        raise QAAssemblyError("critic lacks reviewer identity")
    if not isinstance(critic.get("findings"), list):
        raise QAAssemblyError("critic findings must be a JSON list")
    representatives = critic.get("representative_slides")
    if not isinstance(representatives, list) or not representatives:
        raise QAAssemblyError("critic lacks representative-slide rubric coverage")
    for slide in representatives:
        if not isinstance(slide, dict) or not slide.get("slide_id"):
            raise QAAssemblyError("critic lacks representative-slide rubric coverage")
        _judgment_coverage(
            slide.get("judgments"), REQUIRED_RUBRIC_ITEMS, "representative-slide"
        )
    if not critic["findings"] and (
        any(item["judgment"] != "pass" for item in critic["deck"])
        or any(
            item["judgment"] != "pass"
            for slide in representatives
            for item in slide["judgments"]
        )
    ):
        raise QAAssemblyError("zero findings requires passing rubric coverage")
    if critic.get("reviewed_artifact_digest") != expected_render_digest:
        raise QAAssemblyError("critic reviewed artifact digest does not match render")
    if critic.get("comparison_target") or critic.get("comparative") is not None:
        if reviewer.get("type") != "human" or not critic.get("comparison_target"):
            raise QAAssemblyError("comparative review requires an identified human")
        _judgment_coverage(
            critic.get("comparative"),
            COMPARATIVE_RUBRIC_ITEMS,
            "comparative",
            passes_only=True,
        )


def commit_critic_fragment(
    run: Path,
    input_digest: str,
    render_digest: str,
    review: dict[str, Any] | list[dict[str, Any]],
) -> Path:
    """Commit the critic's sole immutable output; unknown imagery is deck-level."""
    output = run / "qa" / "critic.fragment.json"
    if output.exists():
        raise QAAssemblyError("critic fragment is immutable and already exists")
    supplied = {"findings": review} if isinstance(review, list) else dict(review)
    findings = supplied.get("findings")
    if not isinstance(findings, list):
        raise QAAssemblyError("critic findings must be a JSON list")
    normalized = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise QAAssemblyError("critic findings must contain JSON objects")
        item = dict(finding)
        if not item.get("slide_id"):
            item.pop("slide_id", None)
            item["scope"] = "deck"
        else:
            item["scope"] = "slide"
        item.setdefault("action", "review")
        normalized.append(item)
    supplied["findings"] = normalized
    supplied.update(
        {
            "producer": "critic",
            "run_id": run.name,
            "input_digest": input_digest,
            "render_digest": render_digest,
        }
    )
    atomic_json(output, supplied)
    return output


def _fragment(path: Path, expected_run: str) -> dict[str, Any]:
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QAAssemblyError(f"missing or invalid QA fragment: {path.name}") from exc
    if item.get("run_id") != expected_run:
        raise QAAssemblyError("cross-run QA fragment")
    if not item.get("input_digest") or not item.get("render_digest"):
        raise QAAssemblyError("QA fragment lacks digest binding")
    return item


def _render_binding(run: Path) -> tuple[str, str]:
    """Return digests independently derived from the committed render artifact."""
    result_path = run / "render" / "render-result.json"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QAAssemblyError("missing or invalid committed render result") from exc
    if result.get("run_id") != run.name or not result.get("input_digest"):
        raise QAAssemblyError("render result lacks digest binding")
    actual_render = digest_render_artifacts(run / "render")
    if result.get("render_digest") != actual_render:
        raise QAAssemblyError("committed render digest does not match artifact bytes")
    return result["input_digest"], actual_render


def validate_final_review(report: Path, expected_run: str) -> dict[str, Any]:
    """Reject placeholders: a final review must be a substantive assembled QA report."""
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QAAssemblyError("final QA report is missing or invalid") from exc
    required = {"run_id", "input_digest", "render_digest", "deterministic", "critics"}
    if not required <= value.keys() or value["run_id"] != expected_run:
        raise QAAssemblyError("final QA report lacks required bindings")
    deterministic = value["deterministic"]
    critics = value["critics"]
    if (
        not isinstance(deterministic, dict)
        or deterministic.get("producer") != "lint"
        or not isinstance(deterministic.get("checks"), list)
        or not deterministic["checks"]
        or not isinstance(critics, list)
        or not critics
        or any(
            not isinstance(critic, dict)
            or critic.get("producer") != "critic"
            or not isinstance(critic.get("findings"), list)
            for critic in critics
        )
    ):
        raise QAAssemblyError(
            "final QA report is not a substantive lint and critic review"
        )
    for critic in critics:
        validate_critic_coverage(critic, value["render_digest"])
    return value


def assemble_qa(run: Path, fragments: list[Path] | None = None) -> Path:
    """Atomically publish a report only after all fragments agree; never replace it."""
    report = run / "qa" / "report.json"
    if report.exists():
        raise QAAssemblyError("immutable QA report already exists")
    paths = fragments or [
        run / "qa/lint.fragment.json",
        run / "qa/critic.fragment.json",
    ]
    if not paths:
        raise QAAssemblyError("missing QA fragments")
    records = [_fragment(path, run.name) for path in paths]
    producers = [item.get("producer") for item in records]
    if len(producers) != len(set(producers)) or any(not item for item in producers):
        raise QAAssemblyError("duplicate-producer QA fragments")
    deterministic = next((item for item in records if item["producer"] == "lint"), None)
    if deterministic is None:
        raise QAAssemblyError("missing deterministic lint fragment")
    critic = next((item for item in records if item["producer"] == "critic"), None)
    if critic is None:
        raise QAAssemblyError("missing visual critic fragment")
    expected_binding = _render_binding(run)
    bindings = {(item["input_digest"], item["render_digest"]) for item in records}
    if bindings != {expected_binding}:
        raise QAAssemblyError("conflicting QA fragment digests")
    input_digest, render_digest = expected_binding
    validate_critic_coverage(critic, render_digest)
    atomic_json(
        report,
        {
            "run_id": run.name,
            "input_digest": input_digest,
            "render_digest": render_digest,
            "deterministic": deterministic,
            "critics": [critic],
            "report_digest_basis": digest_path(run / "qa"),
        },
    )
    return report
