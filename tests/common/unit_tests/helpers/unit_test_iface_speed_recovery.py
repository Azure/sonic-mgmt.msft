"""Hardware-independent coverage for temporary TH6 speed-change recovery."""

import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest


SOURCE = Path(__file__).resolve().parents[3] / "iface_namingmode" / "test_iface_namingmode.py"


def assert_result(result, message):
    assert result, message


def load_recovery():
    tree = ast.parse(SOURCE.read_text())
    recovery = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "restore_interface_speed")
    test_class = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == "TestConfigInterface")
    speed_test = next(node for node in test_class.body
                      if isinstance(node, ast.FunctionDef) and node.name == "test_config_interface_speed")
    namespace = {
        "contextmanager": contextmanager,
        "logger": Mock(),
        "wait_until": Mock(side_effect=lambda timeout, interval, delay, check, *args: check(*args)),
        "pytest_assert": assert_result,
        "pytest": pytest,
        "PORT_TOGGLE_TIMEOUT": 30,
        "ESTABLISH_LLDP_NEIGHBOR_TIMEOUT": 90,
    }
    exec(compile(ast.Module(body=[recovery, speed_test], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def make_hosts(asic_name="th6", autoneg="on", namespace=""):
    config = {
        "Ethernet0": {"admin_status": "up", "autoneg": autoneg},
        "Ethernet4": {"admin_status": "up", "autoneg": autoneg},
        "Ethernet8": {"admin_status": "up", "autoneg": "off"},
        "Ethernet12": {"admin_status": "down", "autoneg": "on"},
        "Ethernet16": {"admin_status": "up", "autoneg": "on"},
    }
    baseline = {
        port: {"oper_state": "up" if port in ["Ethernet0", "Ethernet4", "Ethernet8"] else "down"}
        for port in config
    }
    down = {port: {"oper_state": "down"} for port in config}
    down["Ethernet8"] = {"oper_state": "up"}
    asic = Mock()
    asic.config_facts.return_value = {"ansible_facts": {"PORT": config}}
    asic.show_interface.side_effect = [
        {"ansible_facts": {"int_status": status}}
        for status in [baseline, down, down, baseline]
    ]
    host = Mock()
    host.get_asic_name.return_value = asic_name
    host.asic_instance.return_value = asic
    host.shell_cmds.return_value = {"failed": False}
    host.facts = {"hwsku": "test"}
    host.get_supported_speeds.return_value = ["100000", "400000"]
    guest = Mock()
    guest.shell.return_value = {"rc": 0}
    sample = {
        "asic_index": 0, "cli_ns_option": namespace, "native_speed": "400000",
        "default": "Ethernet0", "alias": "etp1a",
    }
    return host, guest, asic, sample, (guest, "alias", "alias")


@pytest.mark.parametrize("namespace", ["", "-n asic0"])
@pytest.mark.parametrize("autoneg", ["on", "off"])
def test_recovers_only_previously_up_failed_ports_and_preserves_an(namespace, autoneg):
    loaded = load_recovery()
    host, guest, _, sample, mode = make_hosts(autoneg=autoneg, namespace=namespace)
    with loaded["restore_interface_speed"](host, sample, mode):
        pass

    commands = host.shell_cmds.call_args.kwargs
    assert commands["continue_on_fail"] is True
    assert commands["module_ignore_errors"] is True
    prefix = "config interface {}".format(namespace)
    for port in ["Ethernet0", "Ethernet4"]:
        assert "{} shutdown {}".format(prefix, port) in commands["cmds"]
        assert "{} startup {}".format(prefix, port) in commands["cmds"]
        an_commands = [cmd for cmd in commands["cmds"] if "autoneg {} ".format(port) in cmd]
        assert an_commands == ([
            "{} autoneg {} disabled".format(prefix, port),
            "{} autoneg {} enabled".format(prefix, port),
        ] if autoneg == "on" else [])
    assert not any(port in cmd for cmd in commands["cmds"]
                   for port in ["Ethernet8", "Ethernet12", "Ethernet16"])
    guest.shell.assert_called_once_with(
        "SONIC_CLI_IFACE_MODE=alias sudo config interface {} speed etp1a 400000".format(namespace),
        module_ignore_errors=True)


@pytest.mark.parametrize("asic_name", ["th6", "th5"])
def test_does_not_bounce_healthy_ports_or_other_asics(asic_name):
    loaded = load_recovery()
    host, _, asic, sample, mode = make_hosts(asic_name=asic_name)
    asic.show_interface.side_effect = None
    asic.show_interface.return_value = {
        "ansible_facts": {"int_status": {"Ethernet0": {"oper_state": "up"}}}
    }
    with loaded["restore_interface_speed"](host, sample, mode):
        pass
    host.shell_cmds.assert_not_called()
    if asic_name != "th6":
        asic.config_facts.assert_not_called()
        asic.show_interface.assert_not_called()


def test_speed_and_ports_are_restored_when_intermediate_speed_assertion_fails():
    loaded = load_recovery()
    host, guest, _, sample, mode = make_hosts()
    guest.shell.side_effect = lambda cmd, **kwargs: (
        {"rc": 0, "stdout": "wrong-speed"} if "CONFIG_DB HGET" in cmd else {"rc": 0}
    )
    with pytest.raises(AssertionError, match="Interface speed mismatch after configuration"):
        loaded["test_config_interface_speed"](
            SimpleNamespace(check_speed_change=lambda *args: True),
            mode, sample, {"dut": host}, "dut", Mock())
    assert guest.shell.call_args == call(
        "SONIC_CLI_IFACE_MODE=alias sudo config interface  speed etp1a 400000",
        module_ignore_errors=True)
    host.shell_cmds.assert_called_once()


def test_recovery_command_failures_are_reported_after_attempting_all_ports():
    loaded = load_recovery()
    host, _, _, sample, mode = make_hosts()
    host.shell_cmds.return_value = {"failed": True}
    with pytest.raises(AssertionError, match="Port recovery commands failed"):
        with loaded["restore_interface_speed"](host, sample, mode):
            pass
    commands = host.shell_cmds.call_args.kwargs
    assert commands["continue_on_fail"] is True
    assert "config interface  startup Ethernet0" in commands["cmds"]
    assert "config interface  startup Ethernet4" in commands["cmds"]
