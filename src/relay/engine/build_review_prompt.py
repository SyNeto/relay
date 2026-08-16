"""Renders the review prompt (system + user) for the Review capability. See
CONTRACT.md's "Review (independent critique)" section for the expected
shape of `review_request`.
"""
from importlib import resources

REQUIRED_FIELDS = ("decision", "context")


def _load_prompt(name: str) -> str:
    return resources.files("relay.prompts").joinpath(name).read_text()


def build(review_request: dict) -> dict:
    missing = [f for f in REQUIRED_FIELDS if not review_request.get(f)]
    if missing:
        raise ValueError(f"review_request missing required fields: {missing}")

    for item in review_request["context"]:
        if not item.get("source") or not item.get("content"):
            raise ValueError("every context item needs a non-empty 'source' and 'content'")

    context_blocks = "\n\n".join(
        f"### Source: {item['source']}\n{item['content']}" for item in review_request["context"]
    )

    template = _load_prompt("review_prompt.md")
    user_prompt = template.format(
        decision=review_request["decision"],
        context_blocks=context_blocks,
    )
    return {"system": _load_prompt("review_system.md"), "user": user_prompt}
