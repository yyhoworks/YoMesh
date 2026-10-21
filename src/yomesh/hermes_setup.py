"""Pin both installation stages to bytes from the authenticated workbench."""
import hashlib
from pathlib import Path
import shlex

from .model import ConfigError
from .publication import public_base


def hermes_setup_metadata(root):
    try:
        return {name: hashlib.sha256((Path(root) / "scripts" / filename).read_bytes()).hexdigest()
                for name, filename in (("installer_sha256", "hermes_installer.py"), ("bridge_sha256", "yomesh_hermes_mcp.py"))}
    except OSError:
        raise ConfigError("Hermes 安装文件不可读，请检查 scripts 目录后重新生成命令") from None


def installation_command(base_url, token, setup):
    base_url = public_base(base_url)
    # The digest is copied with the command, never fetched from the download URL.
    bootstrap = (
        "import hashlib,os,sys,urllib.request; "
        "no_redirect=type('NoRedirect',(urllib.request.HTTPRedirectHandler,),{'redirect_request':lambda *a,**k:None}); "
        "opener=urllib.request.build_opener(no_redirect()); "
        "response=opener.open(os.environ['YOMESH_URL']+'/api/setup/hermes_installer.py',timeout=15); "
        "code=response.read(524289); response.close(); "
        f"sys.exit('安装器 SHA-256 校验失败，未执行下载内容；请重新从可信工作台复制命令') if hashlib.sha256(code).hexdigest()!={setup['installer_sha256']!r} else None; "
        "exec(compile(code,'hermes_installer.py','exec'),{'__name__':'__main__'})"
    )
    environment = {"YOMESH_URL": base_url, "YOMESH_TOKEN": token, "YOMESH_MCP_SHA256": setup["bridge_sha256"]}
    return " ".join(name + "=" + shlex.quote(value) for name, value in environment.items()) + " python3 -c " + shlex.quote(bootstrap)
