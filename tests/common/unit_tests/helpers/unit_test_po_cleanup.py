"""Hardware-independent checks for the large-LAG cleanup backport."""

import ast
from contextlib import contextmanager
import logging
from pathlib import Path
import re
from unittest.mock import MagicMock, Mock, call

import pytest


TESTS = Path(__file__).resolve().parents[3]


def load_definitions(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names for target in node.targets
        ):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def assert_result(result, message):
    assert result, message


@pytest.mark.parametrize("large,fail_reload", [(False, False), (True, False), (True, True)])
def test_stress_cleanup_is_pid_scoped_and_precedes_large_lag_bgp(large, fail_reload):
    duthost = Mock()
    duthost.setup.return_value = {"ansible_facts": {"ansible_processor_vcpus": 2}}
    duthost.get_asic_ids.return_value = [None]
    names = {"PortChannel{}".format(i): "" for i in range(101, 166 if large else 105)}
    duthost.lag_facts.return_value = {"ansible_facts": {"lag_facts": {"names": names}}}
    pids = iter([101, 102, 201])
    duthost.shell.side_effect = lambda cmd, **kwargs: (
        {"stdout": str(next(pids))} if cmd.startswith("nohup") else {"stdout": ""}
    )
    duthost.get_bgp_neighbors_per_asic.return_value = {"": {}}
    reload = Mock(side_effect=RuntimeError("reload failed") if fail_reload else None)

    @contextmanager
    def drain_context(host, cleanup):
        try:
            yield
        finally:
            cleanup()

    namespace = load_definitions(
        TESTS / "pc/test_po_cleanup.py",
        {
            "LAG_LOG_STRICT_PER_PC_MAX", "LOG_EXPECT_PO_CLEANUP_RE",
            "LOG_EXPECT_PO_CLEANUP_SIGTERM_ANY", "lag_numeric_key",
            "stratified_lag_samples_for_syslog", "test_po_cleanup_after_reload",
        },
        {
            "logging": logging,
            "pytest_assert": assert_result,
            "collect_teamd_lag_pids": Mock(return_value={pc: i for i, pc in enumerate(names)}),
            "LogAnalyzer": Mock(return_value=MagicMock()),
            "DisableLogrotateAndWaitSyslogContext": drain_context,
            "config_reload": reload,
            "bgp_wait_seconds_after_config_reload": Mock(return_value=360),
            "wait_until": Mock(return_value=True),
            "pre_reload_teamd_pids_gone": Mock(),
            "all_lags_recovered": Mock(),
        },
    )

    def run():
        namespace["test_po_cleanup_after_reload"]({"dut": duthost}, "dut", {})

    if fail_reload:
        with pytest.raises(RuntimeError, match="reload failed"):
            run()
    else:
        run()

    kill_call = call.shell("kill 101 102 201 2>/dev/null || true", module_ignore_errors=True)
    assert duthost.mock_calls.count(kill_call) == 1
    assert all("killall" not in args.args[0] and "pkill" not in args.args[0]
               for args in duthost.shell.call_args_list)
    reload.assert_called_once_with(duthost, wait=240, safe_reload=True, wait_for_bgp=not large)
    if large and not fail_reload:
        assert duthost.mock_calls.index(kill_call) < duthost.mock_calls.index(
            call.get_bgp_neighbors_per_asic(state="all")
        )


@pytest.mark.parametrize("failure", [None, "process", "kernel", "old_pid"])
def test_all_lags_recovered_checks_each_namespace_and_replacement(failure):
    current = {"PortChannel1": 201, "PortChannel2": 202}
    if failure == "process":
        current.pop("PortChannel2")
    elif failure == "old_pid":
        current["PortChannel2"] = 102
    duthost = Mock()
    duthost.get_linux_ip_cmd_for_namespace.side_effect = lambda cmd, ns: "{} {}".format(cmd, ns)
    duthost.shell.side_effect = lambda cmd, **kwargs: {
        "rc": 0,
        "stdout": "" if failure == "kernel" and cmd.endswith("asic1") else (
            "PortChannel1" if cmd.endswith("asic0") else "PortChannel2"
        ),
    }
    namespace = load_definitions(
        TESTS / "pc/test_po_cleanup.py", {"all_lags_recovered"},
        {"re": re, "logging": logging, "collect_teamd_lag_pids": Mock(return_value=current)},
    )
    assert namespace["all_lags_recovered"](
        duthost, {"PortChannel1": "asic0", "PortChannel2": "asic1"},
        {"PortChannel1": 101, "PortChannel2": 102},
    ) is (failure is None)


@pytest.mark.parametrize("stat_fails", [False, True])
def test_log_drain_cleans_stress_first_and_always_restores_rotation(stat_fails):
    events = []
    duthost = Mock()

    def stat(*args, **kwargs):
        events.append("stat")
        if stat_fails:
            raise RuntimeError("stat failed")
        return {"rc": 0, "stdout": "256"}

    duthost.shell.side_effect = stat
    rotation = Mock()
    rotation.__enter__ = Mock(side_effect=lambda: events.append("disable"))
    rotation.__exit__ = Mock(side_effect=lambda *args: events.append("restore"))
    namespace = load_definitions(
        TESTS / "common/plugins/loganalyzer/loganalyzer.py",
        {"DisableLogrotateAndWaitSyslogContext"},
        {
            "logging": logging, "time": Mock(time=Mock(return_value=0)),
            "DisableLogrotateCronContext": Mock(return_value=rotation),
        },
    )
    context = namespace["DisableLogrotateAndWaitSyslogContext"](
        duthost, cleanup=lambda: events.append("cleanup")
    )
    if stat_fails:
        with pytest.raises(RuntimeError, match="stat failed"):
            with context:
                pass
    else:
        with context:
            pass
    assert events[:3] == ["disable", "cleanup", "stat"]
    assert events[-1] == "restore"
