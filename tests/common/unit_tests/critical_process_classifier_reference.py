"""Test-only pure classifier reference from sonic-net/sonic-mgmt #29375's tree.

202608 has no host_failure_utils.py. Retain the upstream classifier controls
without adding #26803 suite-stop hooks, cache policy or runtime imports.
Only AST-selected pure constants/functions are evaluated by the gated tests.
Source: 81695866060f2885c04d6f93f4ce22fb3c03bafb, helpers/host_failure_utils.py.
This reference does not enable testbed-unreachable stopping on this branch.
"""

MAX_INSPECTED_MESSAGE_LENGTH = 8192


UNREACHABLE_MARKERS = (
    "unreachable in the inventory",
    "unreachable in the extra inventory",
)


CONNECTIVITY_FAILURE_MARKERS = (
    "unable to connect to port 22",
    "timeout when waiting for",
    "no route to host",
)


def _iter_exception_chain(exc):
    """Yield an exception and every exception it is chained to."""
    pending = [exc]
    seen = set()

    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current

        pending.append(getattr(current, "__cause__", None))
        # __context__ is attached to any exception raised while another one was
        # being handled, so honor an explicit "raise ... from None".
        if not getattr(current, "__suppress_context__", False):
            pending.append(getattr(current, "__context__", None))
        # Exceptions collected by an ExceptionGroup, used by parallel runs.
        nested = getattr(current, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            pending.extend(nested)


def _get_dark_hosts(exc):
    """Return the hosts an ansible connection failure could not reach.

    "dark" is the exception attribute pytest-ansible populates directly from
    its result callback: AnsibleConnectionFailure(msg, dark=callback.unreachable).
    The callback itself is a local of ModuleDispatcherV213._run and AdHocResult
    only carries the contacted hosts, so this attribute is the only supported
    way to read callback.unreachable, and it is exact rather than text matched.
    """
    dark = getattr(exc, "dark", None)
    if isinstance(dark, dict):
        return sorted(dark.keys())
    return []


def _get_message(exc):
    try:
        message = str(exc)
    except Exception:
        return ""
    return message[:MAX_INSPECTED_MESSAGE_LENGTH].lower()


def _is_unreachable_failure(exc, connection_failure_types):
    # A connection-failure type alone is not conclusive: require the unreachable
    # hosts that pytest-ansible supplies in its "dark" attribute.
    if (connection_failure_types and
            isinstance(exc, connection_failure_types) and
            _get_dark_hosts(exc)):
        return True

    message = _get_message(exc)
    if any(marker in message for marker in UNREACHABLE_MARKERS):
        return True

    # A DUT that never came back from a reboot always leaves the testbed unusable.
    if "did not startup after reboot" in message:
        return True

    if not any(marker in message
               for marker in CONNECTIVITY_FAILURE_MARKERS):
        return False

    # A bare "did not startup" is also used by functional assertions, it only
    # means an unusable testbed when the DUT is unreachable as well.
    reboot_failure = (
        "dut not start" in message or
        "did not startup" in message
    )
    sanity_recovery_failure = "recovery of sanity check failed" in message
    return reboot_failure or sanity_recovery_failure


def is_testbed_unreachable_exception(exc, connection_failure_types=None):
    """Return whether an exception means the assigned testbed is unusable."""
    return any(
        _is_unreachable_failure(current, connection_failure_types)
        for current in _iter_exception_chain(exc)
    )
