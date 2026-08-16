"""Renders the fix prompt (system + user) for a single finding, ready to
send to a configured model provider. See CONTRACT.md's "Finding schema"
for the expected shape of `finding`.
"""
from importlib import resources

REQUIRED_FIELDS = ("file", "severity", "summary", "failure_scenario", "target_excerpt")


def _load_prompt(name: str) -> str:
    return resources.files("relay.prompts").joinpath(name).read_text()


def build(finding: dict) -> dict:
    missing = [f for f in REQUIRED_FIELDS if not finding.get(f)]
    if missing:
        raise ValueError(f"finding {finding.get('id', '?')} missing required fields: {missing}")

    section_suffix = f" — section: {finding['section']}" if finding.get("section") else ""
    reference_block = ""
    if finding.get("reference"):
        reference_block = f"\n## Reference (must be consistent with this)\n{finding['reference']}\n"

    template = _load_prompt("fix_prompt.md")
    user_prompt = template.format(
        file=finding["file"],
        section_suffix=section_suffix,
        severity=finding["severity"],
        summary=finding["summary"],
        failure_scenario=finding["failure_scenario"],
        reference_block=reference_block,
        target_excerpt=finding["target_excerpt"],
    )
    return {"system": _load_prompt("fix_system.md"), "user": user_prompt}
