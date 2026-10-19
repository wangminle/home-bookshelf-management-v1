"""CHK-151：路径配置损坏时 auth 命令仍给出可解析的失败结果。"""
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner
from bookshelf.main import app
from bookshelf.credentials import TokenFileError, resolve_token


@pytest.mark.parametrize('command', ['login', 'logout'])
@pytest.mark.parametrize('json_output', [True, False])
def test_invalid_path_config_has_clean_command_error(command, json_output):
    config = Path.home() / '.config/bookshelf/token-path.json'
    config.parent.mkdir(parents=True)
    config.write_text('{broken-config-secret')
    config.chmod(0o600)
    args = ['auth', command, '--json' if json_output else '--no-json']
    if command == 'login':
        args += ['--stdin', '--no-verify']
    result = CliRunner().invoke(app, args, input='private-token')
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert '路径配置' in result.output
    assert 'private-token' not in result.output and 'broken-config-secret' not in result.output
    if json_output:
        assert json.loads(result.output)['ok'] is False


@pytest.mark.skipif(os.name == 'nt', reason='POSIX 配置文件权限')
def test_path_config_can_be_readable_but_not_writable_by_others(tmp_path):
    selected = tmp_path / 'token'
    selected.write_text('private-token')
    selected.chmod(0o600)
    config = Path.home() / '.config/bookshelf/token-path.json'
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'token_file': str(selected)}))
    config.chmod(0o644)
    assert resolve_token().token == 'private-token'
    config.chmod(0o666)
    with pytest.raises(TokenFileError, match='路径配置文件') as error:
        resolve_token()
    assert 'Token 文件' not in str(error.value)


def test_explicit_login_repairs_broken_path_config(tmp_path):
    config = Path.home() / '.config/bookshelf/token-path.json'
    config.parent.mkdir(parents=True)
    config.write_text('broken')
    result = CliRunner().invoke(app, ['auth','login','--stdin','--no-verify','--file',str(tmp_path/'token')], input='private-token')
    assert result.exit_code == 0, result.output
    assert resolve_token().token == 'private-token'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX 配置文件权限')
@pytest.mark.parametrize('command', ['login', 'logout'])
@pytest.mark.parametrize('json_output', [True, False])
def test_writable_config_error_is_clean_and_identifies_config(command, json_output):
    config = Path.home()/'.config/bookshelf/token-path.json'
    config.parent.mkdir(parents=True)
    config.write_text('{}')
    config.chmod(0o666)
    args = ['auth', command, '--json' if json_output else '--no-json']
    if command == 'login':
        args += ['--stdin','--no-verify']
    result = CliRunner().invoke(app, args, input='private-token')
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert '路径配置文件' in result.output and 'Token 文件' not in result.output
    assert 'private-token' not in result.output
    if json_output:
        assert json.loads(result.output)['ok'] is False
