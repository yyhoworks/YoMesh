"""One Profile-backed path for web and Hermes device operations."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import secrets
import tempfile
import threading
import time
import zipfile
from copy import deepcopy
from pathlib import Path

import yaml

from .compiler import compile_bundle
from .device_registry import DeviceRegistry, _save_json
from .model import ConfigError, Profile
from .native import serialize_config
from .native_configuration import checked, safe_path


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode()).hexdigest()


def _relative(name, home):
    if not isinstance(name, str) or not name or '\n' in name:
        raise ConfigError('设备资产路径无效')
    if name.startswith('/'):
        base = home.rstrip('/') + '/'
        if not name.startswith(base):
            raise ConfigError('设备资产位于 Mihomo 工作目录以外，需要手动迁移后再绑定')
        name = name[len(base):]
    return safe_path(name.removeprefix('./'))


def _refs(config, home):
    paths = []
    for section in ('proxy-providers', 'rule-providers'):
        for value in config.get(section, {}).values():
            if isinstance(value, dict) and value.get('path'):
                paths.append(_relative(value['path'], home))
    for listener in config.get('listeners', []):
        for field in ('certificate', 'private-key'):
            value = listener.get(field)
            if isinstance(value, str) and '\n' not in value:
                paths.append(_relative(value, home))
    return sorted(set(paths))


def _source_profile(result, home):
    try:
        files = {name: base64.b64decode(body, validate=True) for name, body in result['files'].items()}
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise ConfigError('设备返回的配置快照无效') from None
    main = files.pop('mihomo.yaml', None)
    if main is None:
        raise ConfigError('设备没有返回 Mihomo 主配置')
    try:
        config = yaml.safe_load(main.decode('utf-8-sig'))
    except (UnicodeError, yaml.YAMLError):
        raise ConfigError('设备 Mihomo 主配置无法解析') from None
    if not isinstance(config, dict) or not isinstance(config.get('rules'), list) or any(not isinstance(line, str) for line in config['rules']):
        raise ConfigError('设备 Mihomo 规则格式不受支持')
    rules = config.pop('rules')
    if not rules or not rules[-1].startswith(('MATCH,', 'FINAL,')):
        raise ConfigError('设备 Mihomo 原始规则缺少末尾兜底')
    for section in ('proxy-providers', 'rule-providers'):
        for provider in config.get(section, {}).values():
            if provider.get('path'):
                provider['path'] = _relative(provider['path'], home)
    for listener in config.get('listeners', []):
        for field in ('certificate', 'private-key'):
            value = listener.get(field)
            if isinstance(value, str) and '\n' not in value:
                listener[field] = _relative(value, home)
    documents, assets = {'archive/original-mihomo.yaml': main.decode('utf-8')}, {}
    for name, body in files.items():
        safe_path(name)
        if name.lower().endswith(('.srs', '.mrs', '.db', '.bin')):
            assets[name] = base64.b64encode(body).decode()
        else:
            try:
                documents[name] = body.decode('utf-8')
            except UnicodeError:
                assets[name] = base64.b64encode(body).decode()
    if 'localdns/config/localdns.json' in assets:
        raise ConfigError('设备 LocalDNS 主配置不是 UTF-8 文本，不能导入')
    has_dns = 'localdns/config/localdns.json' in documents
    native = {'format': 'mihomo-localdns-v1' if has_dns else 'mihomo-v1',
              'mihomo': config,
              'policies': [{'id': 'device_' + hashlib.sha256((str(i) + '\0' + text).encode()).hexdigest()[:20],
                            'text': text, 'enabled': True} for i, text in enumerate(rules)],
              'documents': documents, 'assets': assets,
              'origin': {'source': 'connected-device'}}
    return checked(native)


class DeviceManagement:
    def __init__(self, root: Path, spaces, registry: DeviceRegistry | None = None):
        self.root, self.spaces = Path(root), spaces
        self.registry = registry or DeviceRegistry(root)
        self.directory = self.root / 'data' / 'device-releases'
        self.path = self.root / 'data' / 'device-releases.json'
        self.lock = threading.RLock()
        self.releases = _load_releases(self.path)

    def host_key(self, host, port):
        from .device_registry import _host
        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigError('SSH 端口无效')
        return {'fingerprint': self.registry.transport.host_key(_host(host), port)}

    def listing(self):
        return self.registry.listing()

    def add(self, settings, revision):
        return self.registry.add(settings, revision)

    def bind(self, ident, space_id, revision):
        return self.registry.bind(ident, space_id, revision, self.spaces)

    def unbind(self, ident, revision):
        with self.lock:
            if any(row['device_id'] == ident and row['status'] in {'prepared', 'running'} and
                   row['expires'] > time.time() for row in self.releases['releases']):
                raise ConfigError('此设备有待确认或运行中的发布任务，暂不能解除绑定')
            return self.registry.unbind(ident, revision)

    def probe(self, ident):
        return self.registry.transport.call(self.registry.device(ident), 'probe')

    def _read(self, row):
        transport = self.registry.transport
        first = transport.call(row, 'read')
        encoded = first.get('files', {}).get('mihomo.yaml')
        if not isinstance(encoded, str):
            raise ConfigError('设备没有返回 Mihomo 主配置')
        try:
            main = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ConfigError('设备返回的 Mihomo 主配置无效') from None
        try:
            config = yaml.safe_load(main.decode('utf-8-sig'))
        except (UnicodeError, yaml.YAMLError):
            raise ConfigError('设备 Mihomo 主配置无法解析') from None
        if not isinstance(config, dict):
            raise ConfigError('设备 Mihomo 主配置需要对象')
        home = row['mihomo_home']
        refs = _refs(config, home)
        second = transport.call(row, 'read', {'refs': refs}) if refs else first
        return second, refs

    def _preview_pull(self, ident):
        row = self.registry.device(ident)
        result, refs = self._read(row)
        native = _source_profile(result, row['mihomo_home'])
        snapshot = self.spaces.workspace_snapshot(row['space_id']) if row.get('space_id') else None
        current = snapshot['profile'].get('native_configuration') if snapshot else None
        return {'device': self.registry._public(row), 'remote_digest': _digest(result['manifest']),
                'remote_manifest': result['manifest'], 'refs': refs,
                'workspace_revision': snapshot['revision'] if snapshot else None,
                'local_changed_since_pull': bool(current and (not row.get('baseline_digest') or _digest(current) != row['baseline_digest'])),
                'different': current != native,
                'counts': {'files': len(result['files']), 'policies': len(native['policies']),
                           'proxy_groups': len(native['mihomo'].get('proxy-groups', []))},
                '_native': native}

    def preview_pull(self, ident):
        """Return comparison metadata without exposing credentials in the profile."""
        result = self._preview_pull(ident)
        result.pop('_native')
        return result

    def pull(self, ident, expected_remote_digest, workspace_revision=None, *, replace=False):
        row = self.registry.device(ident)
        result = self._preview_pull(ident)
        if result['remote_digest'] != expected_remote_digest:
            raise ConfigError('设备配置已变化，请重新预览读取')
        if result['local_changed_since_pull'] and not replace:
            raise ConfigError('YoMesh 草稿已有修改，请先处理本地与设备的差异')
        native = result['_native']
        if row.get('space_id'):
            snapshot = self.spaces.workspace_snapshot(row['space_id'])
            if snapshot['revision'] != workspace_revision:
                raise ConfigError('配置空间已变化，请刷新后重试')
            profile = deepcopy(snapshot['profile'])
            profile['native_configuration'] = native
            Profile(profile)
            self.spaces.save_profile(profile, workspace_revision, row['space_id'], 'current')
            space_id = row['space_id']
        else:
            catalog_before = self.spaces.snapshot()
            old_ids = {item['id'] for item in catalog_before['spaces']}
            catalog = self.spaces.import_native(native, row['name'], catalog_before['revision'],
                                                description='从已绑定服务器读取的原生配置')
            space_id = next(item['id'] for item in catalog['spaces'] if item['id'] not in old_ids)
        self.registry.record_pull(ident, space_id, result['remote_manifest'], _digest(native),
                                  ['mihomo', 'localdns'] if native['format'] == 'mihomo-localdns-v1' else ['mihomo'],
                                  result['refs'])
        return {'device': self.registry._public(row), 'space_id': space_id}

    def _bundle(self, profile):
        native = profile.get('native_configuration')
        if not native:
            raise ConfigError('发布只支持原生 Mihomo / LocalDNS 空间')
        component = 'both' if native['format'] == 'mihomo-localdns-v1' else 'proxy'
        bundle = compile_bundle(profile, 'mihomo', rule_output='remote', component=component)
        files = {name: data for name, data in bundle['files'].items()
                 if name not in {'README.txt', 'manifest.json'} and not name.startswith('archive/')}
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, data if isinstance(data, bytes) else serialize_config(data).encode())
        body = output.getvalue()
        if len(body) > 16 * 1024 * 1024:
            raise ConfigError('发布包超过 16 MiB')
        return bundle, files, body

    def _save_releases(self):
        _save_json(self.path, self.releases)

    def plan(self, ident, workspace_revision):
        with self.lock:
            row = self.registry.device(ident)
            if not row.get('space_id') or row.get('baseline') is None:
                raise ConfigError('先读取并绑定设备配置')
            snapshot = self.spaces.workspace_snapshot(row['space_id'])
            if snapshot['revision'] != workspace_revision:
                raise ConfigError('配置空间已变化，请刷新后重试')
            native = snapshot['profile']['native_configuration']
            refs = _refs(native['mihomo'], row['mihomo_home'])
            current = self.registry.transport.call(row, 'manifest', {'refs': row.get('refs', [])})['manifest']
            if current != row['baseline']:
                raise ConfigError('设备配置已变化，请先重新读取并处理冲突')
            if _digest(native) == row.get('baseline_digest'):
                raise ConfigError('当前空间与上次读取一致，无需发布')
            for release in self.releases['releases']:
                if release['device_id'] == ident and release['status'] in {'prepared', 'running'} and release['expires'] > time.time():
                    raise ConfigError('此设备已有待确认或运行中的发布任务')
            bundle, files, body = self._bundle(snapshot['profile'])
            refs = sorted(set(refs) | {name for name in files
                                       if name != 'mihomo.yaml' and not name.startswith('localdns/')})
            hashes = {name: hashlib.sha256(data if isinstance(data, bytes) else serialize_config(data).encode()).hexdigest()
                      for name, data in files.items() if not name.startswith('localdns/generated/')}
            changes = [{'file': name, 'before': current.get(name), 'after': hashes.get(name)}
                       for name in sorted(set(current) | set(hashes)) if current.get(name) != hashes.get(name)]
            if not changes:
                raise ConfigError('当前配置与设备一致，无需发布')
            ident_release = 'release_' + secrets.token_hex(16)
            digest = hashlib.sha256(body).hexdigest()
            prepare = self.registry.transport.call(row, 'prepare', {
                'release_id': ident_release, 'baseline': current, 'refs': row.get('refs', []),
                'bundle': base64.b64encode(body).decode(), 'bundle_sha256': digest,
                'versions': bundle['manifest']['versions']})
            if prepare.get('status') != 'prepared' or not all(value == 'passed' for value in prepare.get('validation', {}).values()):
                raise ConfigError('设备原生校验未通过')
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / (ident_release + '.zip')
            fd, temporary = tempfile.mkstemp(prefix='.release-', dir=self.directory)
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(body)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            release = {'id': ident_release, 'device_id': row['id'], 'device_name': row['name'],
                       'space_id': row['space_id'], 'revision': workspace_revision,
                       'native_digest': _digest(native), 'refs': refs, 'baseline_refs': row.get('refs', []),
                       'baseline_digest': _digest(current), 'bundle_sha256': digest,
                       'created': time.time(), 'expires': time.time() + 1800,
                       'status': 'prepared', 'validation': prepare['validation'], 'changes': changes}
            self.releases['releases'].append(release)
            self._save_releases()
            return self._public_release(release)

    @staticmethod
    def _public_release(release):
        return deepcopy(release)

    def releases_for(self, device_id=None):
        with self.lock:
            return [self._public_release(row) for row in self.releases['releases']
                    if device_id is None or row['device_id'] == device_id]

    def _release(self, ident):
        row = next((item for item in self.releases['releases'] if item['id'] == ident), None)
        if row is None:
            raise ConfigError('发布任务不存在')
        return row

    def approve(self, ident, device_id, digest, workspace_revision):
        with self.lock:
            release = self._release(ident)
            if release['device_id'] != device_id or release['bundle_sha256'] != digest or release['revision'] != workspace_revision:
                raise ConfigError('发布确认与服务器或配置版本不一致')
            if release['status'] != 'prepared' or release['expires'] < time.time():
                raise ConfigError('发布申请已执行或过期，请重新准备')
            row = self.registry.device(device_id)
            if row.get('space_id') != release['space_id']:
                raise ConfigError('服务器绑定的空间已变化')
            snapshot = self.spaces.workspace_snapshot(release['space_id'])
            if snapshot['revision'] != workspace_revision:
                raise ConfigError('配置草稿已变化，需要重新校验和确认')
            _, _, body = self._bundle(snapshot['profile'])
            if hashlib.sha256(body).hexdigest() != digest:
                raise ConfigError('发布包已变化，需要重新确认')
            current = self.registry.transport.call(row, 'manifest', {'refs': release.get('baseline_refs', [])})['manifest']
            if _digest(current) != release['baseline_digest']:
                raise ConfigError('设备配置已变化，需要重新读取')
            result = self.registry.transport.call(row, 'commit', {'release_id': ident, 'bundle_sha256': digest})
            release['status'] = result['status']
            self._save_releases()
            return self._public_release(release)

    def status(self, ident):
        with self.lock:
            release = self._release(ident)
            if release['status'] not in {'prepared', 'running', 'succeeded', 'rolled_back', 'failed', 'conflict', 'needs_manual_recovery'}:
                raise ConfigError('发布任务状态无效')
            if release['status'] == 'running':
                result = self.registry.transport.call(self.registry.device(release['device_id']), 'status', {'release_id': ident})
                release['status'] = result['status']
                if release['status'] == 'succeeded':
                    device = self.registry.device(release['device_id'])
                    device['baseline'] = self.registry.transport.call(device, 'manifest', {'refs': release['refs']})['manifest']
                    device['baseline_digest'] = release['native_digest']
                    device['refs'] = list(release['refs'])
                    self.registry._save()
                self._save_releases()
            return self._public_release(release)

    def rollback(self, ident):
        with self.lock:
            release = self._release(ident)
            if release['status'] != 'succeeded':
                raise ConfigError('只能回滚已成功的发布')
            device = self.registry.device(release['device_id'])
            result = self.registry.transport.call(device, 'rollback', {'release_id': ident})
            release['status'] = result['status']
            device['baseline'] = None
            device['baseline_digest'] = None
            self.registry._save()
            self._save_releases()
            return self._public_release(release)


def _load_releases(path):
    if not path.exists():
        return {'releases': []}
    result = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(result, dict) or not isinstance(result.get('releases'), list):
        raise ConfigError('发布记录无法读取，请恢复备份')
    return result
