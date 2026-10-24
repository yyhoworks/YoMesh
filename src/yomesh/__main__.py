from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .compiler import compile_bundle
from .model import ConfigError
from .native import validate_bundle, write_json, write_bundle_files


def project_root():
    return Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="yomesh")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "export"):
        cmd = sub.add_parser(name)
        cmd.add_argument("profile", type=Path)
        cmd.add_argument("--proxy", choices=("mihomo", "sing-box", "both"), default="both" if name == "check" else "mihomo")
        cmd.add_argument("--rule-output", choices=("inline", "local", "remote"), default="inline")
        cmd.add_argument("--component", choices=("proxy", "dns", "both"), default="both")
        if name == "export":
            cmd.add_argument("--output", type=Path, required=True)
    server = sub.add_parser("serve")
    server.add_argument("--port", type=int, default=8767)
    server.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .server import serve
            serve(project_root(), args.port, args.host)
            return 0
        value = json.loads(args.profile.read_text(encoding="utf-8-sig"))
        targets = ("mihomo",) if args.component == "dns" else ("mihomo", "sing-box") if args.proxy == "both" else (args.proxy,)
        bundles = {}
        for target in targets:
            bundle = compile_bundle(value, target, rule_output=args.rule_output, component=args.component)
            validate_bundle(bundle, project_root())
            bundles[target] = bundle
        if args.command == "check":
            print(json.dumps({target: bundle["manifest"]["validation"] for target, bundle in bundles.items()}, ensure_ascii=False, indent=2))
            return 0
        output = args.output.resolve()
        if output.exists():
            raise ConfigError("输出目录已存在；请选择新的目录，保留已有配置")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".yomesh-export-", dir=output.parent) as temporary:
            staging = Path(temporary) / "bundle"
            staging.mkdir()
            for target, bundle in bundles.items():
                folder = staging / target if len(bundles) > 1 else staging
                folder.mkdir(exist_ok=True)
                write_bundle_files(folder, bundle["files"])
                write_json(folder / "manifest.json", bundle["manifest"])
            os.rename(staging, output)
        print(f"已通过原生校验并导出：{output}")
        return 0
    except (ConfigError, ValueError, OSError, TypeError, KeyError) as exc:
        # Core output is already sanitized by the native validator.
        print(f"YoMesh: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
