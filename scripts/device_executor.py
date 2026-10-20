"""On-demand Linux executor for a single YoMesh-managed server.

The SSH entry point accepts one bounded JSON request on stdin. Root-owned
target.json fixes every managed path and service name; clients cannot choose
commands or paths at call time. No external Python packages are needed.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

DEFAULT_POLICY = Path('/etc/yomesh-executor/target.json')
MAX_REQUEST = 24 * 1024 * 1024
MAX_FILES = 512
MAX_CONTENT = 16 * 1024 * 1024
JOB_ID = re.compile(r'release_[0-9a-f]{32}\Z')


class Rejected(Exception):
    pass


def _json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _atomic(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.yomesh-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _policy(path=DEFAULT_POLICY):
    value = _json(Path(path))
    if value.get('format') != 'yomesh-executor-v1' or value.get('manager') not in {'openrc', 'systemd'}:
        raise Rejected('设备执行器设置无效')
    for field in ('mihomo_home', 'mihomo_config', 'mihomo_binary', 'localdns_root', 'localdns_binary', 'state_dir'):
        raw = value.get(field)
        if not isinstance(raw, str) or not Path(raw).is_absolute() or '..' in Path(raw).parts:
            raise Rejected('设备执行器缺少安全的绝对路径')
    services = value.get('services')
    if not isinstance(services, dict) or set(services) != {'mihomo', 'localdns'} or any(not re.fullmatch(r'[a-zA-Z0-9_.@-]{1,80}', s) for s in services.values()):
        raise Rejected('设备服务名称无效')
    return value


def _name(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value or
            value.startswith('/') or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise Rejected('包内路径无效')
    parts = PurePosixPath(value).parts
    if any(part in ('', '.', '..') for part in value.split('/')) or not parts:
        raise Rejected('包内路径无效')
    return value


def _target(policy, name):
    name = _name(name)
    if name == 'mihomo.yaml':
        return Path(policy['mihomo_config'])
    if name.startswith('localdns/'):
        path = Path(policy['localdns_root']) / name.removeprefix('localdns/')
        allowed = Path(policy['localdns_root'])
    else:
        path = Path(policy['mihomo_home']) / name
        allowed = Path(policy['mihomo_home'])
    if not path.resolve().is_relative_to(allowed.resolve()):
        raise Rejected('文件超出设备允许目录')
    return path


def _read_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CONTENT:
        raise Rejected('设备配置文件缺失、过大或为符号链接')
    return path.read_bytes()


def _capture(policy, extra=()):
    if not isinstance(extra, (list, tuple)) or len(extra) > MAX_FILES:
        raise Rejected('设备引用文件列表无效或过多')
    files = {'mihomo.yaml': _read_file(Path(policy['mihomo_config']))}
    root = Path(policy['localdns_root'])
    if root.is_dir():
        for folder in ('config', 'rules', 'generated'):
            directory = root / folder
            if not directory.exists():
                continue
            if directory.is_symlink():
                raise Rejected('LocalDNS 目录不能是符号链接')
            for path in directory.rglob('*'):
                if path.is_symlink():
                    raise Rejected('LocalDNS 资产不能是符号链接')
                if path.is_file():
                    files['localdns/' + path.relative_to(root).as_posix()] = _read_file(path)
                    if len(files) > MAX_FILES:
                        raise Rejected('设备配置文件数量超过安全限制')
    for name in extra:
        if name.startswith('localdns/') or name == 'mihomo.yaml':
            continue
        files[_name(name)] = _read_file(_target(policy, name))
    if len(files) > MAX_FILES or sum(len(body) for body in files.values()) > MAX_CONTENT:
        raise Rejected('设备配置快照超过安全限制')
    return files


def _manifest(files):
    # Runtime-generated DNS tables evolve on their own and never gate a release.
    return {name: hashlib.sha256(body).hexdigest() for name, body in sorted(files.items())
            if not name.startswith('localdns/generated/')}


def _run(args, *, timeout=35, env=None):
    try:
        result = subprocess.run([str(item) for item in args], capture_output=True,
                                timeout=timeout, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Rejected('设备原生命令未完成') from None
    if result.returncode:
        # Native stderr can contain private subscription URLs or passwords.
        raise Rejected('设备原生命令校验失败（退出码 ' + str(result.returncode) + '）')
    return result


def _validate(policy, stage: Path, versions):
    results = {}
    if (stage / 'mihomo.yaml').exists():
        binary = policy['mihomo_binary']
        version = _run([binary, '-v'], timeout=8)
        if not isinstance(versions.get('mihomo'), str) or versions['mihomo'] not in (version.stdout + version.stderr).decode(errors='replace'):
            raise Rejected('设备 Mihomo 版本与配置包不符')
        _run([binary, '-t', '-d', stage, '-f', stage / 'mihomo.yaml'])
        results['mihomo'] = 'passed'
    if (stage / 'localdns/config/localdns.json').exists():
        binary = policy['localdns_binary']
        env = os.environ.copy()
        env.update(policy.get('validation_env', {}))
        version = _run([binary, '--version'], timeout=8, env=env)
        if not isinstance(versions.get('localdns'), str) or versions['localdns'] not in (version.stdout + version.stderr).decode(errors='replace'):
            raise Rejected('设备 LocalDNS 版本与配置包不符')
        _run([binary, '--root', stage / 'localdns', '--config', stage / 'localdns/config/localdns.json', '--check'], env=env)
        results['localdns'] = 'passed'
    if not results:
        raise Rejected('配置包没有 Mihomo 或 LocalDNS 主文件')
    return results


def _job_dir(policy, ident):
    if not isinstance(ident, str) or not JOB_ID.fullmatch(ident):
        raise Rejected('发布任务 ID 无效')
    return Path(policy['state_dir']) / ident


def _state(policy, ident):
    return _json(_job_dir(policy, ident) / 'state.json')


def _write_state(policy, ident, value):
    _atomic(_job_dir(policy, ident) / 'state.json', value)


def _lock(policy):
    root = Path(policy['state_dir'])
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    file = (root / '.apply.lock').open('a+b')
    fcntl.flock(file, fcntl.LOCK_EX)
    return file


def _service(policy, kind, action):
    name = policy['services'][kind]
    if policy['manager'] == 'openrc':
        _run(['rc-service', name, action], timeout=40)
    else:
        _run(['systemctl', action, name], timeout=40)


def _healthy(policy, kinds):
    for kind in kinds:
        _service(policy, kind, 'status' if policy['manager'] == 'openrc' else 'is-active')


def _restore(policy, ident, entry):
    directory = _job_dir(policy, ident)
    for name, metadata in reversed(list(entry.get('backup', {}).items())):
        target = _target(policy, name)
        if metadata['existed']:
            source = directory / 'backup' / name
            if not source.is_file():
                raise Rejected('回滚文件缺失，需要人工恢复')
            _replace(target, source.read_bytes(), metadata['mode'])
            os.chown(target, metadata['uid'], metadata['gid'])
        elif target.exists():
            target.unlink()
    for kind in entry.get('services', []):
        _service(policy, kind, 'restart')
    _healthy(policy, entry.get('services', []))


def _replace(target: Path, body: bytes, mode: int):
    if target.is_symlink() or target.parent.is_symlink():
        raise Rejected('目标文件是符号链接')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.yomesh-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, mode)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _apply(policy, ident):
    with _lock(policy):
        state = _state(policy, ident)
        if state['status'] != 'running':
            return
        directory = _job_dir(policy, ident)
        stage = directory / 'stage'
        writes = state['writes']
        deletes = state['deletes']
        changed = writes + deletes
        try:
            if _manifest(_capture(policy, state.get('refs', []))) != state['baseline']:
                state['status'] = 'conflict'
                _write_state(policy, ident, state)
                return
            if any(_target(policy, name).exists() for name in writes if name not in state['baseline']):
                state['status'] = 'conflict'
                _write_state(policy, ident, state)
                return
            for name in changed:
                target = _target(policy, name)
                previous = target.exists()
                stat = target.stat() if previous else None
                state.setdefault('backup', {})[name] = {'existed': previous,
                                                         'mode': stat.st_mode & 0o777 if stat else 0o644,
                                                         'uid': stat.st_uid if stat else os.getuid(),
                                                         'gid': stat.st_gid if stat else os.getgid()}
                if previous:
                    old = _read_file(target)
                    backup = directory / 'backup' / name
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(old)
                    os.chmod(backup, 0o600)
                _write_state(policy, ident, state)
                if name in writes:
                    _replace(target, (stage / name).read_bytes(), state['backup'][name]['mode'])
                    if previous:
                        os.chown(target, stat.st_uid, stat.st_gid)
                else:
                    target.unlink()
            for kind in state['services']:
                _service(policy, kind, 'restart')
            _healthy(policy, state['services'])
            state['applied'] = {name: hashlib.sha256(_read_file(_target(policy, name))).hexdigest()
                                for name in writes}
            state['applied'].update({name: None for name in deletes})
            state['status'] = 'succeeded'
        except Exception:  # noqa: BLE001 - every apply failure must attempt rollback
            if state.get('backup'):
                try:
                    _restore(policy, ident, state)
                    state['status'] = 'rolled_back'
                except Exception:  # noqa: BLE001 - preserve manual-recovery state
                    state['status'] = 'needs_manual_recovery'
            else:
                state['status'] = 'failed'
        _write_state(policy, ident, state)


def _watchdog(policy, ident):
    time.sleep(115)
    with _lock(policy):
        state = _state(policy, ident)
        if state['status'] != 'running':
            return
        if state.get('backup'):
            try:
                _restore(policy, ident, state)
                state['status'] = 'rolled_back'
            except Exception:  # noqa: BLE001 - watchdog must record recovery state
                state['status'] = 'needs_manual_recovery'
        else:
            state['status'] = 'failed'
        _write_state(policy, ident, state)


def _prepare(policy, payload):
    ident = payload.get('release_id')
    directory = _job_dir(policy, ident)
    expected = payload.get('baseline')
    if not isinstance(expected, dict) or len(expected) > MAX_FILES:
        raise Rejected('设备基准无效')
    for name, digest in expected.items():
        _name(name)
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise Rejected('设备基准无效')
    current = _manifest(_capture(policy, payload.get('refs', [])))
    if current != expected:
        raise Rejected('设备配置自上次读取后变化，请先重新读取并处理冲突')
    body = base64.b64decode(payload.get('bundle', ''), validate=True)
    if len(body) > MAX_CONTENT or hashlib.sha256(body).hexdigest() != payload.get('bundle_sha256'):
        raise Rejected('发布包摘要或大小无效')
    if directory.exists():
        previous = _state(policy, ident)
        if previous['bundle_sha256'] != payload['bundle_sha256']:
            raise Rejected('发布任务 ID 已用于另一份配置')
        return {'status': previous['status'], 'validation': previous.get('validation', {})}
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory, 0o700)
    try:
        stage = directory / 'stage'
        stage.mkdir()
        files = []
        total = 0
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            for item in archive.infolist():
                name = _name(item.filename)
                if item.is_dir() or item.external_attr >> 16 & 0o170000 == 0o120000 or item.file_size > MAX_CONTENT:
                    raise Rejected('配置包含不受支持的文件')
                total += item.file_size
                if total > MAX_CONTENT or len(files) >= MAX_FILES or name in files:
                    raise Rejected('配置包超过安全限制或包含重复文件')
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
                files.append(name)
        desired = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in files
                   if name not in {'README.txt', 'manifest.json'} and not name.startswith(('archive/', 'localdns/generated/'))}
        if ('mihomo.yaml' not in desired or
                ('localdns/config/localdns.json' in expected) != ('localdns/config/localdns.json' in desired)):
            raise Rejected('发布包不得改变此设备的代理或 DNS 内核类型')
        writes = [name for name in sorted(desired) if expected.get(name) != desired[name]]
        deletes = [name for name in sorted(expected) if name not in desired]
        if any(_target(policy, name).exists() for name in writes if name not in expected):
            raise Rejected('发布包含设备上已存在但未纳入基准的文件，请先重新读取')
        validation = _validate(policy, stage, payload.get('versions', {}))
        services = [kind for kind in ('mihomo', 'localdns') if kind + ('.yaml' if kind == 'mihomo' else '/config/localdns.json') in files]
        _write_state(policy, ident, {'status': 'prepared', 'bundle_sha256': payload['bundle_sha256'],
                                      'files': files, 'writes': writes, 'deletes': deletes,
                                      'validation': validation, 'services': services,
                                      'baseline': expected, 'refs': payload.get('refs', []), 'backup': {}})
        return {'status': 'prepared', 'validation': validation, 'files': len(files)}
    except Exception:  # Discard only this newly created, bounded staging directory.
        if directory.resolve().parent == Path(policy['state_dir']).resolve() and JOB_ID.fullmatch(directory.name):
            shutil.rmtree(directory)
        raise


def _dispatch(policy, operation, payload):
    if operation == 'probe':
        return {'executor': 1, 'manager': policy['manager'], 'mihomo': Path(policy['mihomo_binary']).is_file(),
                'localdns': Path(policy['localdns_binary']).is_file()}
    if operation == 'read':
        files = _capture(policy, payload.get('refs', []))
        return {'files': {name: base64.b64encode(body).decode() for name, body in files.items()},
                'manifest': _manifest(files), 'manager': policy['manager']}
    if operation == 'manifest':
        return {'manifest': _manifest(_capture(policy, payload.get('refs', [])))}
    if operation == 'prepare':
        return _prepare(policy, payload)
    if operation == 'commit':
        ident = payload.get('release_id')
        with _lock(policy):
            entry = _state(policy, ident)
            if entry['bundle_sha256'] != payload.get('bundle_sha256'):
                raise Rejected('发布摘要不一致')
            if entry['status'] == 'prepared':
                entry['status'] = 'running'
                _write_state(policy, ident, entry)
                command = [sys.executable, str(Path(__file__).resolve())]
                try:
                    subprocess.Popen([*command, '_watchdog', ident], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True, close_fds=True)
                    subprocess.Popen([*command, '_apply', ident], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True, close_fds=True)
                except OSError:
                    entry['status'] = 'failed'
                    _write_state(policy, ident, entry)
                    raise Rejected('设备发布任务未能启动') from None
            elif entry['status'] not in {'running', 'succeeded', 'rolled_back', 'needs_manual_recovery'}:
                raise Rejected('发布任务状态无效')
            return {'status': entry['status']}
    if operation == 'status':
        entry = _state(policy, payload.get('release_id'))
        return {'status': entry['status'], 'validation': entry.get('validation', {})}
    if operation == 'rollback':
        ident = payload.get('release_id')
        with _lock(policy):
            entry = _state(policy, ident)
            if entry['status'] == 'rolled_back':
                return {'status': 'rolled_back'}
            if entry['status'] != 'succeeded':
                raise Rejected('仅成功发布的任务可手动回滚')
            for name, digest in entry.get('applied', {}).items():
                target = _target(policy, name)
                if digest is None:
                    if target.exists():
                        raise Rejected('设备配置已再次变化，不能覆盖后续修改')
                elif hashlib.sha256(_read_file(target)).hexdigest() != digest:
                    raise Rejected('设备配置已再次变化，不能覆盖后续修改')
            _restore(policy, ident, entry)
            entry['status'] = 'rolled_back'
            _write_state(policy, ident, entry)
            return {'status': 'rolled_back'}
    raise Rejected('设备操作不受支持')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] in {'_apply', '_watchdog'}:
        policy = _policy()
        (_apply if argv[0] == '_apply' else _watchdog)(policy, argv[1])
        return 0
    if argv != ['--ssh'] or os.environ.get('SSH_ORIGINAL_COMMAND', '') not in ('', 'yomesh-executor'):
        return 2
    try:
        policy = _policy()
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise Rejected('设备任务过大')
        request = json.loads(raw)
        if not isinstance(request, dict) or not isinstance(request.get('payload', {}), dict):
            raise Rejected('设备请求格式无效')
        result = _dispatch(policy, request.get('operation'), request.get('payload', {}))
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    except (Rejected, ValueError, TypeError, OSError, KeyError) as error:
        # Do not reflect native stderr, configuration bodies or credentials.
        message = str(error) if isinstance(error, Rejected) else '设备操作未完成'
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
