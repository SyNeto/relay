"""Renders the spec-draft prompt (system + user) for the Discover/Generate
capability. See CONTRACT.md's "Discover & Generate (spec authorship)"
section for the expected shape of `spec_request`.
"""
from importlib import resources

REQUIRED_FIELDS = ("change_request", "context")


def _load_prompt(name: str) -> str:
    return resources.files("relay.prompts").joinpath(name).read_text()


def build(spec_request: dict) -> dict:
    missing = [f for f in REQUIRED_FIELDS if not spec_request.get(f)]
    if missing:
        raise ValueError(f"spec_request missing required fields: {missing}")

    for item in spec_request["context"]:
        if not item.get("source") or not item.get("content"):
            raise ValueError("every context item needs a non-empty 'source' and 'content'")

    context_blocks = "\n\n".join(
        f"### Source: {item['source']}\n{item['content']}" for item in spec_request["context"]
    )

    template = _load_prompt("spec_prompt.md")
    user_prompt = template.format(
        change_request=spec_request["change_request"],
        context_blocks=context_blocks,
    )
    return {"system": _load_prompt("spec_system.md"), "user": user_prompt}
