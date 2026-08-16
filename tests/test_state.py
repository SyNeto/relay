from pathlib import Path

from relay.engine.state import RunState


def test_persists_across_reload(tmp_path: Path):
    s1 = RunState("r1", state_dir=tmp_path, gate_severities=("HIGH",))
    s1.start_iteration()
    s1.record_finding("f1", "HIGH", "summary", file="a.md", target_excerpt="x")

    s2 = RunState("r1", state_dir=tmp_path)
    assert s2.iteration == 1
    assert s2.gate_severities == ("HIGH",)
    assert s2.get_finding("f1")["summary"] == "summary"


def test_gate_clean_requires_gated_severities_resolved(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path, gate_severities=("CRITICAL", "HIGH"))
    s.record_finding("f1", "CRITICAL", "x")
    s.record_finding("f2", "MEDIUM", "y")  # not gated — stays open, shouldn't block the gate

    assert not s.gate_clean()

    s.update_finding_status("f1", "fixed")

    assert s.gate_clean()


def test_wontfix_counts_as_resolved(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path, gate_severities=("HIGH",))
    s.record_finding("f1", "HIGH", "superseded by f2")
    s.update_finding_status("f1", "wontfix")

    assert s.gate_clean()


def test_should_stop_on_max_iterations(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path, max_iterations=2, gate_severities=("HIGH",))
    s.record_finding("f1", "HIGH", "still open")
    s.start_iteration()
    s.start_iteration()

    assert s.iteration == 2
    assert s.should_stop()  # max reached, even though not clean


def test_should_stop_on_clean_pass_before_max(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path, max_iterations=3, gate_severities=("HIGH",))
    s.start_iteration()
    s.record_finding("f1", "HIGH", "resolved")
    s.update_finding_status("f1", "fixed")

    assert s.iteration == 1
    assert s.should_stop()


def test_should_not_stop_before_first_iteration_even_if_clean(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path, max_iterations=3, gate_severities=("HIGH",))

    assert s.iteration == 0
    assert not s.should_stop()  # no findings, gate is technically clean, but no iteration ran yet
