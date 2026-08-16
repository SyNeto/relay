"""Rate/quota tracking for model-provider requests.

Persists request timestamps to one log per PROVIDER under the user's home
directory (~/.relay/rpm_log.<provider>.jsonl), not per-run or per-project —
the account-level quota a provider enforces is shared across every
run_id, every project, and every process that hits that provider's API
key, so scoping the log any narrower would undercount true usage and miss
exactly the kind of longer-window (hourly/daily) quota a 429 can come
from even when the 60s rpm never got close to the cap.

Scoped PER PROVIDER, not globally across all providers: two different
providers are two independent accounts with two independent quotas —
logging both into one file would conflate them and make neither number
meaningful.

Two independent things this tracks:
- rpm (60s window): our own self-imposed throttle, cap/target enforced
  by wait_if_needed() before every request.
- longer windows (1h/24h by default): NOT enforced/throttled — most
  providers don't publish this quota, so there's nothing to pace against.
  This is purely observability: report() surfaces the counts so a 429 can
  be cross-checked against "were we actually near a plausible hourly/daily
  ceiling" instead of just guessing after the fact.
"""
import json
import os
import time
from pathlib import Path

CAP_RPM = 40
TARGET_RPM = 35
RPM_WINDOW_SECONDS = 60

REPORT_WINDOWS = (
    ("rpm (60s)", 60),
    ("last hour", 3600),
    ("last 24h", 86400),
)


def default_log_path(provider: str) -> Path:
    home = Path(os.environ.get("RELAY_HOME", Path.home() / ".relay"))
    return home / f"rpm_log.{provider}.jsonl"


class RpmLimiter:
    def __init__(
        self,
        run_id: str,
        provider: str,
        cap: int = CAP_RPM,
        target: int = TARGET_RPM,
        path: Path | None = None,
    ):
        self.run_id = run_id  # kept for the record, not used to scope the log
        self.provider = provider
        self.path = path or default_log_path(provider)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cap = cap
        self.target = target

    def _timestamps_since(self, seconds: float) -> list:
        if not self.path.exists():
            return []
        cutoff = time.time() - seconds
        out = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            ts = json.loads(line)["ts"]
            if ts >= cutoff:
                out.append(ts)
        return out

    def current_rpm(self) -> int:
        return len(self._timestamps_since(RPM_WINDOW_SECONDS))

    def wait_if_needed(self, poll_seconds: float = 1.0) -> float:
        """Blocks until under target rpm (hard-stops under cap regardless).
        Only paces the 60s window — there's no known hourly/daily limit to
        pace against, only to report on after the fact. Returns total
        seconds waited, for logging."""
        waited = 0.0
        while self.current_rpm() >= self.target:
            time.sleep(poll_seconds)
            waited += poll_seconds
        while self.current_rpm() >= self.cap:
            time.sleep(poll_seconds)
            waited += poll_seconds
        return waited

    def record(self):
        with self.path.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "run_id": self.run_id}) + "\n")

    def report(self) -> str:
        lines = [f"{self.provider} — request volume (all runs) — {self.path}"]
        for label, seconds in REPORT_WINDOWS:
            n = len(self._timestamps_since(seconds))
            lines.append(f"  {label:<10} {n} requests")
        return "\n".join(lines)
