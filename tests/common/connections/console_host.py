from .base_console_conn import (
    CONSOLE_SSH,
    CONSOLE_SSH_CISCO_CONFIG,
    CONSOLE_SSH_MENU_PORTS,
    CONSOLE_TELNET,
    CONSOLE_SSH_DIGI_CONFIG,
    CONSOLE_SSH_SONIC_CONFIG,
    CONSOLE_CONSERVER
)
from .telnet_console_conn import TelnetConsoleConn
from .ssh_console_conn import SSHConsoleConn
from .conserver_console_conn import ConserverConsoleConn

ConsoleTypeMapper = {
    CONSOLE_TELNET: TelnetConsoleConn,
    CONSOLE_SSH: SSHConsoleConn,
    CONSOLE_SSH_MENU_PORTS: SSHConsoleConn,
    CONSOLE_SSH_DIGI_CONFIG: SSHConsoleConn,
    CONSOLE_SSH_SONIC_CONFIG: SSHConsoleConn,
    CONSOLE_SSH_CISCO_CONFIG: SSHConsoleConn,
    CONSOLE_CONSERVER: ConserverConsoleConn
}


def ConsoleHost(console_type,
                console_host,
                console_port,
                sonic_username,
                sonic_password,
                console_username=None,
                console_password=None,
                console_device=None,
                timeout_s=100,
                cancel_event=None):
    if console_type not in ConsoleTypeMapper:
        raise ValueError("console type {} is not supported yet".format(console_type))
    params = {
        "console_host": console_host,
        "console_port": console_port,
        "console_type": console_type,
        "sonic_username": sonic_username,
        "sonic_password": sonic_password,
        "console_username": console_username,
        "console_password": console_password,
        "console_device": console_device,
        "timeout": timeout_s
    }
    # Only SSHConsoleConn consumes cancel_event (it pops it before delegating to
    # netmiko). The other console classes forward **kwargs straight to netmiko,
    # which would raise "unexpected keyword argument 'cancel_event'" and break
    # console-log collection on those console servers. Gate the injection on the
    # resolved class so non-SSH console types are unaffected.
    if cancel_event is not None and issubclass(ConsoleTypeMapper[console_type], SSHConsoleConn):
        params["cancel_event"] = cancel_event

    return ConsoleTypeMapper[console_type](**params)
