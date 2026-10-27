from parley.config import get_settings
from parley.evals.scenarios import load_scenarios


def test_every_scenario_file_loads():
    scenarios = load_scenarios(get_settings().scenarios_dir)
    assert len(scenarios) >= 10
    assert any("smoke" in s.tags for s in scenarios)
