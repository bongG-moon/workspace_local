import io
import json
import socket
import ssl
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from local_app import llm_usage_config as config
from local_app.llm_usage import UsageError, UsageService, query_usage, resolve_token


def settled(service):
    deadline = time.monotonic() + 3
    while service.loading and time.monotonic() < deadline:
        time.sleep(.005)
    assert not service.loading
    return service.snapshot()


@pytest.mark.parametrize('url', ['https://user:secret@example.test/mcp', 'https://example.test/mcp?key=secret',
    'https://example.test/#secret', 'file:///C:/secret', 'https://example.test/%2e%2e/mcp',
    'https://example.test:99999/mcp', 'https://example.test/\nsecret'])
def test_reject_credential_or_unsafe_urls(url):
    with pytest.raises(ValueError) as error:
        config.checked_url(url)
    assert 'secret' not in str(error.value)


def test_address_only_schema():
    value = {'schema': 1, 'mcpUrl': 'http://USAGE.example.test/mcp'}
    assert config.from_bytes(config.encoded(value))['mcpUrl'] == 'http://usage.example.test/mcp'
    for raw in (b'{"schema":1,"schema":1,"mcpUrl":"http://example.test"}', b'x'*8193,
                json.dumps({**value, 'token': 'secret'}).encode()):
        with pytest.raises(ValueError):
            config.from_bytes(raw)


def test_config_pins_deployment_destination_and_production_cannot_edit(tmp_path):
    path = tmp_path/config.CONFIG_NAME
    path.write_bytes(config.encoded({'schema':1, 'mcpUrl':'http://site.example.test/mcp'}))
    service = UsageService(config_path=path, env={'LLMOPS_MCP_URL':'http://other.example.test/mcp'})
    assert service.connection() == ('http://site.example.test/mcp', 'deployment')
    with pytest.raises(ValueError):
        service.configure_test({'mcpUrl':'http://change.example.test/mcp'})
    assert service.test_settings is None
    path.write_text('{broken')
    with pytest.raises(ValueError):
        service.connection()  # Never silently send the user's KEY to an environment fallback.


def test_test_address_persists_and_resets_old_endpoint_result(tmp_path):
    calls = []
    def query(**kwargs):
        calls.append(kwargs)
        return {'spend':1, 'max':20}
    path = tmp_path/'test/settings.json'
    service = UsageService(test_settings=path, config_path=tmp_path/'missing', query=query, env={})
    service.result = {'spend':99}; service.team = 'old-team'
    service.configure_test({'mcpUrl':'http://local.example.test/mcp'})
    result = settled(service)
    assert result['usage']['spend'] == 1 and calls[0]['team'] == ''
    assert calls[0]['env']['LLMOPS_MCP_URL'] == 'http://local.example.test/mcp'
    assert result['connection'] == {'editable':True,'mcpUrl':'http://local.example.test/mcp','source':'test','networkMode':'system'}
    assert UsageService(test_settings=path, env={}).connection() == ('http://local.example.test/mcp', 'test')
    assert set(json.loads(path.read_text())) == {'schema', 'mcpUrl', 'networkMode'}
    service.loading = True
    with pytest.raises(ValueError):
        service.configure_test({'mcpUrl':'http://next.example.test/mcp'})
    assert 'local.example.test' in path.read_text()


def test_config_dir_and_settings_api_key(tmp_path):
    custom = tmp_path/'custom'; custom.mkdir()
    (custom/'settings.json').write_text('{"env":{"ANTHROPIC_API_KEY":"fixture-only"}}')
    assert resolve_token({'CLAUDE_CONFIG_DIR':str(custom)}, tmp_path) == 'fixture-only'


@pytest.mark.parametrize('status,code', [(401,'key_required'), (403,'key_required'),
    (407,'proxy_auth_required'), (429,'rate_limited'), (500,'http_error')])
def test_http_diagnostics_without_server_body(status, code):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.HTTPError('http://example.test',status,'secret-reflection',{},io.BytesIO(b'secret'))
    with pytest.raises(UsageError) as error:
        query_usage({'LLMOPS_MCP_URL':'http://example.test/mcp','ANTHROPIC_AUTH_TOKEN':'secret'}, opener=Opener())
    assert str(error.value) == code
    assert error.value.public() == {'stage':'initialize', 'httpStatus':status}
    assert 'secret' not in json.dumps(error.value.public())


@pytest.mark.parametrize('failure,code', [(socket.gaierror(11001,'private-host'),'dns_error'),
    (TimeoutError('secret'),'timeout'), (ssl.SSLError('secret'),'tls_error'),
    (ConnectionRefusedError('secret'),'connection_error')])
def test_network_diagnostics(failure, code):
    class Opener:
        def open(self, *_args, **_kwargs): raise urllib.error.URLError(failure)
    with pytest.raises(UsageError) as error:
        query_usage({'LLMOPS_MCP_URL':'http://example.test/mcp','ANTHROPIC_AUTH_TOKEN':'secret'}, opener=Opener())
    assert str(error.value) == code and error.value.public() == {'stage':'initialize'}


def test_tool_diagnostic_is_scoped_and_safe(tmp_path):
    def query(**_): raise UsageError('server_error',stage='get_team_budget_usage')
    service = UsageService(query=query, env={'LLMOPS_MCP_URL':'http://example.test/mcp'}, config_path=tmp_path/'missing')
    service.snapshot()
    result = settled(service)
    assert result['error'] == 'server_error'
    assert result['diagnostic'] == {'stage':'get_team_budget_usage'}
    assert result['connection'] == {'editable':False}
