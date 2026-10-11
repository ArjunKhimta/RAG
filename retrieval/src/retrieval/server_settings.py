"""Where the production server listens, read from `HOST` and `PORT`.

The rules match the Node API's: the service listens on 127.0.0.1 unless `HOST` says otherwise,
so a local run is not reachable from other machines on the network, and the deployment sets
`HOST` explicitly. `PORT` must be a whole number from 1 to 65535; hosting providers such as
Render choose it and pass it in. A bad value stops start-up naming the variable, never echoing
the value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from retrieval.config import SERVICE_DEFAULT_HOST, SERVICE_DEFAULT_PORT, SERVICE_HIGHEST_PORT

HOST_VARIABLE = "HOST"

PORT_VARIABLE = "PORT"


class InvalidServerSettingError(ValueError):
    """Raised when `HOST` or `PORT` cannot be used; names the variable, never its value."""

    def __init__(self, variable_name: str, rule: str) -> None:
        super().__init__(f"{variable_name} {rule}")
        self.variable_name = variable_name


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int

    @property
    def bind_address(self) -> str:
        if ":" in self.host:
            return f"[{self.host}]:{self.port}"
        return f"{self.host}:{self.port}"


def read_server_settings(environment: Mapping[str, str]) -> ServerSettings:
    return ServerSettings(host=_read_host(environment), port=_read_port(environment))


def _read_host(environment: Mapping[str, str]) -> str:
    host = environment.get(HOST_VARIABLE, "")
    if not host:
        return SERVICE_DEFAULT_HOST
    if host != host.strip() or " " in host:
        raise InvalidServerSettingError(HOST_VARIABLE, "must be an address with no spaces")
    return host


def _read_port(environment: Mapping[str, str]) -> int:
    text = environment.get(PORT_VARIABLE, "")
    if not text:
        return SERVICE_DEFAULT_PORT
    if not text.isascii() or not text.isdigit():
        raise _invalid_port()
    port = int(text)
    if port < 1 or port > SERVICE_HIGHEST_PORT:
        raise _invalid_port()
    return port


def _invalid_port() -> InvalidServerSettingError:
    return InvalidServerSettingError(
        PORT_VARIABLE, f"must be a whole number from 1 to {SERVICE_HIGHEST_PORT}"
    )
