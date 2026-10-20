"""Explicit, root-run installation of the restricted on-demand executor."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

RUNNER = Path(__file__).with_name('device_executor.py')
DESTINATION = Path('/usr/local/lib/yomesh-executor/runner.py')
POLICY = Path('/etc/yomesh-executor/target.json')
AUTHORIZED = Path('/root/.ssh/authorized_keys')


def _atomic_bytes(path: Path, body: bytes, mode: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.yomesh-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _autodetect_settings(settings: dict):
    home = Path(settings['mihomo_home'])
    if not Path(settings['mihomo_config']).is_file() and home.is_dir():
        for name in ('config.yaml', 'mihomo.yaml', 'config.yml'):
            candidate = home / name
            if candidate.is_file():
                settings['mihomo_config'] = str(candidate)
                break
    if not Path(settings['mihomo_binary']).is_file():
        for raw in (shutil.which('mihomo'), '/usr/local/bin/mihomo', '/usr/bin/mihomo',
                    '/opt/mihomo/mihomo', str(home / 'mihomo')):
            if raw and Path(raw).is_file():
                settings['mihomo_binary'] = str(Path(raw))
                break
    dns_root = Path(settings['localdns_root'])
    if not dns_root.is_dir():
        for raw in ('/opt/localdns', '/etc/localdns', '/usr/local/localdns'):
            if Path(raw).is_dir():
                dns_root = Path(raw)
                settings['localdns_root'] = raw
                break
    if not Path(settings['localdns_binary']).is_file():
        for raw in (str(dns_root / 'localdns'), shutil.which('localdns'),
                    '/usr/local/bin/localdns', '/usr/bin/localdns'):
            if raw and Path(raw).is_file():
                settings['localdns_binary'] = str(Path(raw))
                break
    if RUNNER == Path(__file__).with_name('device_executor.py'):
        has_systemd = Path('/run/systemd/system').is_dir() or bool(shutil.which('systemctl'))
        has_openrc = bool(shutil.which('rc-service'))
        if settings.get('manager') == 'openrc' and not has_openrc and has_systemd:
            settings['manager'] = 'systemd'
        elif settings.get('manager') == 'systemd' and not has_systemd and has_openrc:
            settings['manager'] = 'openrc'


def install(settings_path, public_key_path, *, dry_run=False):
    if os.geteuid() != 0:
        raise RuntimeError('请由目标设备管理员以 root 执行安装')
    settings = json.loads(Path(settings_path).read_text(encoding='utf-8'))
    if settings.get('format') != 'yomesh-executor-v1' or settings.get('manager') not in ('openrc', 'systemd'):
        raise RuntimeError('执行器目标设置无效')
    for key in ('mihomo_home', 'mihomo_config', 'mihomo_binary', 'localdns_root', 'localdns_binary', 'state_dir'):
        value = settings.get(key)
        if not isinstance(value, str) or not value.startswith('/') or '..' in Path(value).parts:
            raise RuntimeError('目标路径必须是绝对路径')
    _autodetect_settings(settings)
    broad = {'/', '/etc', '/opt', '/usr', '/var', '/root', '/home'}
    mihomo_home = Path(settings['mihomo_home']).resolve()
    localdns_root = Path(settings['localdns_root']).resolve()
    state_dir = Path(settings['state_dir']).resolve()
    if (str(mihomo_home) in broad or str(localdns_root) in broad or str(state_dir) in broad or
            mihomo_home == localdns_root or
            state_dir.is_relative_to(mihomo_home) or state_dir.is_relative_to(localdns_root)):
        raise RuntimeError('工作目录或状态目录范围过宽、重叠')
    if not Path(settings['mihomo_config']).resolve().is_relative_to(mihomo_home):
        raise RuntimeError('Mihomo 主配置需位于其工作目录内')
    if not Path(settings['mihomo_config']).is_file() or not Path(settings['mihomo_binary']).is_file():
        raise RuntimeError('目标设备缺少指定的 Mihomo 配置或内核')
    if not Path(settings['localdns_root']).is_dir() or not Path(settings['localdns_binary']).is_file():
        raise RuntimeError('目标设备缺少指定的 LocalDNS 目录或内核')
    if set(settings.get('services', {})) != {'mihomo', 'localdns'} or any(not isinstance(s, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', s) for s in settings['services'].values()):
        raise RuntimeError('目标服务名称无效')
    extra_env = settings.get('validation_env', {})
    if not isinstance(extra_env, dict) or any(not re.fullmatch(r'[A-Z][A-Z0-9_]*', k) or not isinstance(v, str) or len(v) > 512 for k, v in extra_env.items()):
        raise RuntimeError('原生校验环境设置无效')
    public_key = Path(public_key_path).read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'ssh-rsa [A-Za-z0-9+/=]+ yomesh-device_[0-9a-f]{32}', public_key):
        raise RuntimeError('请使用 YoMesh 生成的设备专用公钥')
    if not RUNNER.is_file():
        raise RuntimeError('安装包缺少设备执行器')
    if any(path.is_symlink() for path in (DESTINATION, POLICY, AUTHORIZED,
                                         DESTINATION.parent, POLICY.parent, AUTHORIZED.parent,
                                         Path(settings['state_dir']))):
        raise RuntimeError('执行器目标位置不能是符号链接')
    if dry_run:
        return {'ready': True, 'manager': settings['manager']}
    backup = Path(settings['state_dir']) / 'installer-backups' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    if backup.exists():
        raise RuntimeError('安装备份编号重复，请稍后再试')
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    if AUTHORIZED.is_file():
        shutil.copy2(AUTHORIZED, backup / 'authorized_keys')
    if POLICY.is_file():
        shutil.copy2(POLICY, backup / 'target.json')
    if DESTINATION.is_file():
        shutil.copy2(DESTINATION, backup / 'runner.py')
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    POLICY.parent.mkdir(parents=True, exist_ok=True)
    AUTHORIZED.parent.mkdir(parents=True, exist_ok=True)
    for folder in (DESTINATION.parent, POLICY.parent, AUTHORIZED.parent, Path(settings['state_dir'])):
        os.chmod(folder, 0o700)
    _atomic_bytes(DESTINATION, RUNNER.read_bytes(), 0o700)
    _atomic_bytes(POLICY, (json.dumps(settings, ensure_ascii=False, indent=2) + '\n').encode(), 0o600)
    existing = AUTHORIZED.read_text(encoding='utf-8') if AUTHORIZED.is_file() else ''
    marker = public_key.split()[-1]
    lines = [line for line in existing.splitlines() if not line.rstrip().endswith(marker)]
    forced = 'command="' + sys.executable + ' ' + str(DESTINATION) + ' --ssh",restrict ' + public_key
    _atomic_bytes(AUTHORIZED, ('\n'.join([*lines, forced]) + '\n').encode(), 0o600)
    return {'installed': True, 'backup': str(backup), 'ssh_key_restricted': True}


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != '--check'):
        raise SystemExit('用法：python3 device_installer.py target.json device.pub [--check]')
    print(json.dumps(install(sys.argv[1], sys.argv[2], dry_run=len(sys.argv) == 4 and sys.argv[3] == '--check'), ensure_ascii=False))
