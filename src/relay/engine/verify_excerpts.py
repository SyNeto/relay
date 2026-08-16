"""Pre-flight check: for every open finding in a run, confirm target_excerpt
appears exactly once in its file — before spending a model call on it.
"""
from dataclasses import dataclass
from pathlib import Path

from relay.engine.state import RunState


@dataclass
class VerifyResult:
    finding_id: str
    file: str
    count: int

    @property
    def ok(self) -> bool:
        return self.count == 1


def verify(state: RunState, repo_root: Path) -> list[VerifyResult]:
    results = []
    for f in state.findings:
        if f["status"] != "open":
            continue
        text = (repo_root / f["file"]).read_text()
        count = text.count(f["target_excerpt"])
        results.append(VerifyResult(finding_id=f["id"], file=f["file"], count=count))
    return results
