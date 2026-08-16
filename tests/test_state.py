import json
from pathlib import Path

from relay.engine.state import RunState, list_run_ids


def test_list_run_ids_empty_when_state_dir_missing(tmp_path: Path):
    assert list_run_ids(tmp_path / "does-not-exist") == []


def test_list_run_ids_empty_when_no_runs(tmp_path: Path):
    assert list_run_ids(tmp_path) == []


def test_list_run_ids_returns_sorted_run_ids(tmp_path: Path):
    RunState("2026-08-16-run2", state_dir=tmp_path).start_iteration()
    RunState("2026-08-15-run1", state_dir=tmp_path).start_iteration()

    assert list_run_ids(tmp_path) == ["2026-08-15-run1", "2026-08-16-run2"]


def test_list_run_ids_default_state_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RELAY_HOME", str(tmp_path))
    RunState("r1", state_dir=tmp_path / "runs").start_iteration()

    assert list_run_ids() == ["r1"]


def test_summary_line_includes_spec_file_when_set(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path, spec_file="docs/SPEC.md")

    assert "spec=docs/SPEC.md" in s.summary_line()


def test_summary_line_shows_dash_when_spec_file_unset(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path)

    assert "spec=-" in s.summary_line()


def test_summary_line_reflects_gate_status(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path, gate_severities=("HIGH",))
    s.record_finding("f1", "HIGH", "still open")

    assert "gate=NOT CLEAN" in s.summary_line()

    s.update_finding_status("f1", "fixed")

    assert "gate=CLEAN" in s.summary_line()


def test_spec_file_persists_across_reload(tmp_path: Path):
    s1 = RunState("r1", state_dir=tmp_path, spec_file="docs/SPEC.md")
    s1.start_iteration()

    s2 = RunState("r1", state_dir=tmp_path)
    assert s2.spec_file == "docs/SPEC.md"


def test_spec_file_defaults_to_none(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path)

    assert s.spec_file is None


def test_spec_file_backward_compat_with_old_state_json(tmp_path: Path):
    state_path = tmp_path / "r1" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "run_id": "r1",
        "max_iterations": 3,
        "gate_severities": ["HIGH"],
        "started_at": 0,
        "iteration": 0,
        "phase": "find",
        "findings": [],
    }))

    s = RunState("r1", state_dir=tmp_path)

    assert s.spec_file is None


def test_render_includes_spec_file_line_when_set(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path, spec_file="docs/SPEC.md")

    assert "Spec: docs/SPEC.md" in s.render()


def test_render_omits_spec_file_line_when_unset(tmp_path: Path):
    s = RunState("r1", state_dir=tmp_path)

    assert "Spec:" not in s.render()


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
