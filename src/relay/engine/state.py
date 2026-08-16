"""Run state: the state model defined in CONTRACT.md.

Persists to <state_dir>/<run_id>/state.json so it survives across the many
separate process invocations that make up one run (each CLI subcommand is
its own short-lived process, not one long-running daemon).

State is anchored to where `relay` is invoked from (the target project),
not to relay's own install location — a shared central install must not
let one project's run state collide with another's. Default state_dir is
./.relay/runs, overridable via the RELAY_HOME env var or an explicit
state_dir argument (the CLI's --state-dir flag).
"""
import json
import os
import time
from pathlib import Path

DEFAULT_GATE_SEVERITIES = ("CRITICAL", "HIGH")
RESOLVED_STATUSES = ("fixed", "wontfix")  # wontfix = deliberately decided, not "still open"
SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
VALID_PHASES = ("find", "fix", "validate", "commit")


def default_state_dir() -> Path:
    if os.environ.get("RELAY_HOME"):
        return Path(os.environ["RELAY_HOME"]) / "runs"
    return Path.cwd() / ".relay" / "runs"


def list_run_ids(state_dir: Path | None = None) -> list[str]:
    """Every run_id with a state.json under state_dir, sorted. Since the
    convention is a date-prefixed run_id (YYYY-MM-DD-runN), alphabetical
    sort is also chronological for same-day runs."""
    state_dir = Path(state_dir) if state_dir else default_state_dir()
    if not state_dir.exists():
        return []
    return sorted(p.parent.name for p in state_dir.glob("*/state.json"))


def _bar(current: int, total: int, width: int = 10) -> str:
    filled = int(width * current / total) if total else 0
    filled = max(0, min(width, filled))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class RunState:
    def __init__(
        self,
        run_id: str,
        max_iterations: int = 3,
        gate_severities: tuple = DEFAULT_GATE_SEVERITIES,
        spec_file: str | None = None,
        state_dir: Path | None = None,
    ):
        state_dir = state_dir or default_state_dir()
        self.path = Path(state_dir) / run_id / "state.json"
        if self.path.exists():
            data = json.loads(self.path.read_text())
        else:
            data = {
                "run_id": run_id,
                "max_iterations": max_iterations,
                "gate_severities": list(gate_severities),
                "spec_file": spec_file,
                "started_at": time.time(),
                "iteration": 0,
                "phase": "find",
                "findings": [],  # {id, severity, summary, status, iteration}
            }
        self.__dict__.update(data)
        self.gate_severities = tuple(self.gate_severities)  # stored as list in JSON
        self.spec_file = getattr(self, "spec_file", None)  # back-compat: pre-spec_file state.json

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            k: v
            for k, v in self.__dict__.items()
            if k != "path"
        }
        self.path.write_text(json.dumps(state, indent=2))

    def start_iteration(self):
        self.iteration += 1
        self.phase = "find"
        self._save()

    def set_phase(self, phase: str):
        if phase not in VALID_PHASES:
            raise ValueError(f"phase must be one of {VALID_PHASES}, got {phase!r}")
        self.phase = phase
        self._save()

    def record_finding(self, finding_id: str, severity: str, summary: str, status: str = "open", **extra):
        """extra: file, section, failure_scenario, target_excerpt, reference —
        whatever build_prompt.build() needs later. Kept loose here since
        this module only cares about id/severity/status for rendering/gating."""
        record = {
            "id": finding_id,
            "severity": severity,
            "summary": summary,
            "status": status,
            "iteration": self.iteration,
        }
        record.update(extra)
        self.findings.append(record)
        self._save()

    def get_finding(self, finding_id: str) -> dict | None:
        for f in self.findings:
            if f["id"] == finding_id:
                return f
        return None

    def update_finding_status(self, finding_id: str, status: str):
        for f in self.findings:
            if f["id"] == finding_id:
                f["status"] = status
        self._save()

    def gate_clean(self) -> bool:
        return not any(
            f["severity"] in self.gate_severities and f["status"] not in RESOLVED_STATUSES
            for f in self.findings
        )

    def should_stop(self) -> bool:
        """Exit condition: max iterations reached, or a full pass is clean
        on the gated severities after at least one iteration ran."""
        if self.iteration >= self.max_iterations:
            return True
        return self.iteration > 0 and self.gate_clean()

    def summary_line(self) -> str:
        """One line per run, for `relay run list` — the condensed
        counterpart to render()'s full multi-line detail."""
        gate = "CLEAN" if self.gate_clean() else "NOT CLEAN"
        spec = self.spec_file or "-"
        return (
            f"{self.run_id:<24} iter {self.iteration}/{self.max_iterations}   "
            f"phase={self.phase:<8} gate={gate:<9} spec={spec}"
        )

    def render(self) -> str:
        elapsed = _fmt_elapsed(time.time() - self.started_at)
        lines = [
            f"relay — run {self.run_id}",
            f"Iteration {_bar(self.iteration, self.max_iterations)} "
            f"{self.iteration}/{self.max_iterations}   "
            f"Phase: {self.phase}   Elapsed: {elapsed}",
        ]
        if self.spec_file:
            lines.append(f"Spec: {self.spec_file}")
        lines += ["", "Findings"]
        by_sev = {}
        for f in self.findings:
            by_sev.setdefault(f["severity"], []).append(f)

        if not self.findings:
            lines.append("  (none recorded yet)")
        for sev in SEVERITY_ORDER:
            items = by_sev.get(sev, [])
            if not items:
                continue
            open_n = sum(1 for f in items if f["status"] == "open")
            fixed_n = sum(1 for f in items if f["status"] == "fixed")
            wontfix_n = sum(1 for f in items if f["status"] == "wontfix")
            gate = "  <- gates exit" if sev in self.gate_severities else ""
            extra = f" / {wontfix_n} wontfix" if wontfix_n else ""
            lines.append(f"  {sev:<8} {open_n} open / {fixed_n} fixed{extra} / {len(items)} total{gate}")

        lines.append("")
        gate_status = "CLEAN" if self.gate_clean() else "NOT CLEAN"
        lines.append(f"Exit gate ({'+'.join(self.gate_severities)}): {gate_status}")
        if self.should_stop():
            reason = "max iterations reached" if self.iteration >= self.max_iterations else "clean pass"
            lines.append(f"-> run will stop after this iteration ({reason})")
        return "\n".join(lines)
