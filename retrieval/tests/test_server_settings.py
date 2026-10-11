from __future__ import annotations

import pytest

from retrieval.config import SERVICE_DEFAULT_HOST, SERVICE_DEFAULT_PORT
from retrieval.server_settings import (
    InvalidServerSettingError,
    ServerSettings,
    read_server_settings,
)


def test_a_local_run_listens_only_on_this_machine():
    settings = read_server_settings({})

    assert settings == ServerSettings(host=SERVICE_DEFAULT_HOST, port=SERVICE_DEFAULT_PORT)
    assert settings.bind_address == "127.0.0.1:5001"


def test_empty_values_count_as_unset():
    settings = read_server_settings({"HOST": "", "PORT": ""})

    assert settings.bind_address == "127.0.0.1:5001"


def test_the_deployment_chooses_the_address_and_port():
    settings = read_server_settings({"HOST": "0.0.0.0", "PORT": "10000"})

    assert settings.bind_address == "0.0.0.0:10000"


def test_an_ipv6_address_is_bracketed():
    settings = read_server_settings({"HOST": "::", "PORT": "8080"})

    assert settings.bind_address == "[::]:8080"


@pytest.mark.parametrize("port_text", ["1", "65535"])
def test_the_lowest_and_highest_ports_are_accepted(port_text):
    assert read_server_settings({"PORT": port_text}).port == int(port_text)


@pytest.mark.parametrize(
    "port_text", ["0", "65536", "99999", "-1", "5001.0", "5e3", " 5001", "abc", "٥٠٠١"]
)
def test_a_bad_port_stops_start_up_naming_the_variable(port_text):
    with pytest.raises(InvalidServerSettingError) as raised:
        read_server_settings({"PORT": port_text})

    assert raised.value.variable_name == "PORT"
    assert str(raised.value) == "PORT must be a whole number from 1 to 65535"


@pytest.mark.parametrize("host", [" 0.0.0.0", "0.0.0.0 ", "local host"])
def test_a_host_with_spaces_is_refused(host):
    with pytest.raises(InvalidServerSettingError) as raised:
        read_server_settings({"HOST": host})

    assert raised.value.variable_name == "HOST"
