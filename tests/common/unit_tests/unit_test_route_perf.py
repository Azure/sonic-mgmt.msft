"""Exercise route-performance ALPM checks without testbed dependencies."""

import ast
from pathlib import Path
from unittest.mock import Mock

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "route" / "test_route_perf.py"


def _pytest_assert(condition, message):
    assert condition, message


@pytest.fixture
def check_config():
    tree = ast.parse(MODULE_PATH.read_text())
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("check_config", "get_l3_alpm_template_from_config_bcm")
    ]
    for function in functions:
        function.decorator_list = []
    namespace = {"logging": Mock(), "logger": Mock(), "pytest_assert": _pytest_assert}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(MODULE_PATH), "exec"), namespace)
    return namespace["check_config"]


@pytest.mark.parametrize("platform", [
    "x86_64-micas_m2-w6940-128x1-fr4-r0",
    "x86_64-arista_7060x6_64pe_b",
])
@pytest.mark.parametrize("template", [1, 2])
def test_th5_checks_alpm_template_for_each_platform(check_config, platform, template):
    """All TH5 platforms use config.bcm and still reject non-combined ALPM mode."""
    duthost = Mock(facts={"asic_type": "broadcom", "platform": platform}, is_multi_asic=False)
    duthost.get_asic_name.return_value = "th5"
    duthost.shell.side_effect = [
        {"stdout": "SAI_INIT_CONFIG_FILE=/etc/sai.d/config.bcm"},
        {"stdout": "l3_alpm_template: {}".format(template)},
    ]
    duthost.command.return_value = {
        "stdout_lines": ["", "", "Command not supported for this device"],
    }

    if template == 1:
        check_config({"dut": duthost}, "dut", None, {"topo": {"type": "ft2"}})
    else:
        with pytest.raises(AssertionError, match="l3_alpm_template is not set for route scaling"):
            check_config({"dut": duthost}, "dut", None, {"topo": {"type": "ft2"}})

    duthost.command.assert_not_called()
    assert duthost.shell.call_count == 2


@pytest.mark.parametrize("asic_index,command", [
    (None, 'bcmcmd "conf show l3_alpm_enable"'),
    (1, 'bcmcmd -n 1 "conf show l3_alpm_enable"'),
])
def test_legacy_broadcom_keeps_alpm_enable_check(check_config, asic_index, command):
    """Non-TH5 devices retain the single-ASIC and namespaced bcmcmd checks."""
    duthost = Mock(
        facts={"asic_type": "broadcom", "platform": "legacy-broadcom"},
        is_multi_asic=asic_index is not None,
    )
    duthost.get_asic_name.return_value = "th4"
    duthost.command.return_value = {"stdout_lines": ["", "", "l3_alpm_enable=2"]}

    check_config({"dut": duthost}, "dut", asic_index, {"topo": {"type": "t1"}})

    duthost.command.assert_called_once_with(command)
    duthost.shell.assert_not_called()
