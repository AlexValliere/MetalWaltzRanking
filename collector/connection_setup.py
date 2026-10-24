"""Reviewed pre-login socket attempts with fixed, identity-free diagnostics."""

from collections import Counter
import errno
import socket
import time

from .timing import CollectionDeadline, remaining_seconds


CONNECT_FAILURE_FIELDS = {
    "refused": "connectRefused",
    "unreachable": "connectUnreachable",
    "timeout": "connectTimeouts",
    "name_resolution": "connectNameResolutionFailures",
    "reset": "connectResets",
    "other": "connectOtherFailures",
}
CONNECT_FAILURE_OPERATIONS = frozenset(("connect", "socket_setup", "session_initialization"))


def connect_failure_reason(error):
    """Classify only reviewed exception types/numbers, never exception text."""
    number, windows = error.errno, getattr(error, "winerror", None)
    if isinstance(error, socket.gaierror):
        return "name_resolution"
    if isinstance(error, TimeoutError) or number == errno.ETIMEDOUT or windows == 10060:
        return "timeout"
    if isinstance(error, ConnectionRefusedError) or number == errno.ECONNREFUSED or windows == 10061:
        return "refused"
    if number in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN) or windows in (10050, 10051, 10065):
        return "unreachable"
    if isinstance(error, ConnectionResetError) or number == errno.ECONNRESET or windows == 10054:
        return "reset"
    return "other"


def safe_setup_diagnostics(supplied):
    """Recheck setup fields at the log boundary; raw values are never forwarded."""
    if not isinstance(supplied, dict):
        return {}
    result = {}
    for key in ("connectAttempts", *CONNECT_FAILURE_FIELDS.values()):
        value = supplied.get(key)
        if type(value) is int and value >= 0:
            result[key] = value
    value = supplied.get("connectLastFailureEndpointOrdinal")
    if type(value) is int and value > 0:
        result["connectLastFailureEndpointOrdinal"] = value
    value = supplied.get("connectFailureReason")
    if isinstance(value, str) and value in CONNECT_FAILURE_FIELDS:
        result["connectFailureReason"] = value
    value = supplied.get("connectFailureOperation")
    if isinstance(value, str) and value in CONNECT_FAILURE_OPERATIONS:
        result["connectFailureOperation"] = value
    return result


class ConnectionSetupDiagnostics:
    """Counts only; the ordinal follows the existing reviewed route/port order."""

    def __init__(self):
        self.attempts = 0
        self.failures = Counter()
        self.last_failure_endpoint = self.last_failure_reason = self.last_failure_operation = None

    def record_failure(self, error, operation):
        reason = connect_failure_reason(error)
        self.failures[reason] += 1
        self.last_failure_endpoint = self.attempts
        self.last_failure_reason, self.last_failure_operation = reason, operation

    def annotate(self, error, operation=None):
        if not self.attempts:
            return
        if (operation is not None and isinstance(error, OSError) and not isinstance(error, CollectionDeadline)
                and getattr(error, "_collector_setup_state", None) is not self):
            self.record_failure(error, operation)
        result = {"connectAttempts": self.attempts}
        result.update((field, self.failures[reason]) for reason, field in CONNECT_FAILURE_FIELDS.items())
        if self.last_failure_reason is not None:
            result.update(connectLastFailureEndpointOrdinal=self.last_failure_endpoint,
                          connectFailureReason=self.last_failure_reason,
                          connectFailureOperation=self.last_failure_operation)
        error._collector_setup_state = self
        error.collector_diagnostics = result


def open_reviewed_socket(server, *, connector, deadline=None, clock=time.monotonic, diagnostics=None):
    """Keep the existing once-per-endpoint policy; stop after any TCP success."""
    state = diagnostics if diagnostics is not None else ConnectionSetupDiagnostics()
    try:
        for route in (server, *server.get("alternateRoutes", [])):
            for port in route["ports"]:
                remaining = remaining_seconds(deadline, clock)
                state.attempts += 1
                try:
                    sock = connector((route["address"], port),
                                     timeout=min(5, remaining) if remaining is not None else 5)
                except CollectionDeadline:
                    raise
                except OSError as error:
                    state.record_failure(error, "connect")
                    remaining_seconds(deadline, clock)
                    continue
                try:
                    remaining_seconds(deadline, clock)
                except CollectionDeadline:
                    # A connected socket must not escape its shared budget.
                    try:
                        sock.close()
                    except OSError:
                        pass
                    raise
                return sock
    except CollectionDeadline as error:
        state.annotate(error)
        raise
    error = ConnectionError("No reviewed endpoint could be reached")
    state.annotate(error)
    raise error
