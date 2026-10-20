# 指定服务器同步与 Hermes 接入

YoMesh 的设备同步以原生 Mihomo / LocalDNS 配置为准。它是显式的“读取 → 编辑 → 预检 → 确认发布”，不是后台自动覆盖，也不把 LocalDNS 转为 MosDNS。普通 sing-box / MosDNS 空间仍只支持导出，不作为此执行器的发布目标。

## 四阶段工作流

1. **登记并安装**：在“配置空间 → 设备同步”登记每台服务器，独立核对 SSH 主机指纹。YoMesh 为每台设备生成一把专用 SSH 密钥；私钥只存于被忽略的 `data/device-keys/`。目标设备一次性安装受限执行器，公钥被强制命令限制，不能获得任意 shell。
2. **只读读取**：执行器读取指定 Mihomo 主配置、其引用的本地文件，以及 LocalDNS 的 `config/`、`rules/`、`generated/`。页面展示文件摘要和差异；确认后导入或更新独立原生配置空间。此阶段不重启服务、不改目标文件。订阅快照和动态表随后仍会由各自内核运行态改变。
3. **预检与发布**：编辑该空间后执行“发布预检”。YoMesh 先比较上次读取时的文件 SHA-256；设备有外部修改即拒绝覆盖。发布包在目标设备用实际 Mihomo / LocalDNS 程序加载检查，展示新增、修改、删除的文件清单和包摘要。用户在工作台对这一包再次确认后，执行器备份目标文件、写入、重启并检查服务；失败自动回滚。成功后才能更新同步基准，且可对未再次改变的文件手动回滚。
4. **Hermes 联动**：工作台生成一次性展示、可轮换/撤销的接入令牌。Hermes 上的 stdio MCP 桥只提供设备列表、只读比对、发布预检、记录与状态；它不能确认发布、回滚、取得设备 root 密码或完整代理凭据。最终发布仍需回到 YoMesh 工作台。

## 安装到一台指定服务器

目标需要 Python 3.11+、SSH、OpenRC 或 systemd，以及已存在的 Mihomo / LocalDNS 程序和服务。先在服务器本机核对主机指纹，再将工作台生成的 `device.pub` 与 `target.json` 连同项目中的 `scripts/device_installer.py`、`scripts/device_executor.py` 放到目标设备的临时目录。不要把私钥、密码、订阅文件或真实 `target.json` 提交到 Git。

```sh
python3 device_installer.py target.json device.pub --check
python3 device_installer.py target.json device.pub
```

安装器要求 root，仅在检查通过后修改 `/root/.ssh/authorized_keys`、`/etc/yomesh-executor/target.json` 和 `/usr/local/lib/yomesh-executor/runner.py`，旧文件备份在 `target.json` 指定的 `state_dir/installer-backups/` 下。`target.json` 固定工作目录、主文件、二进制、服务名和状态目录；每次 SSH 请求不能改这些路径。目标若需 `LD_PRELOAD` 等原生校验环境，使用表单“校验程序所需环境变量”生成 `validation_env`，只填固定路径，不填密码。安装后点击工作台“检查连接”，再“读取并比对”。

新增引用文件若已在设备上存在、却未进入上次读取基准，发布会拒绝覆盖。删除的旧文件会先备份，回滚时恢复。设备上的运行态动态表不作为发布冲突基准；若它们影响效果，需要另外做实网对照。

## Hermes MCP 配置

先在 YoMesh 的“设备同步 → Hermes 接入”生成令牌。令牌只显示一次；服务端只保存 SHA-256，不在配置仓库或日志中保存明文。在 Hermes 主机将下列 JSON 放到仅其运行用户可读的文件，例如 `/root/.config/yomesh/hermes.json`，权限为 `0600`：

```json
{"base_url":"https://your-yomesh.example","token":"<一次性令牌>"}
```

将 `scripts/yomesh_hermes_mcp.py` 放到 Hermes 主机，例如 `/root/.local/share/yomesh/yomesh_hermes_mcp.py`。Hermes 的 `~/.hermes/config.yaml` 增加 stdio 服务器（与现有配置合并，不覆盖其他项）：

```yaml
mcp_servers:
  yomesh:
    command: "python3"
    args:
      - /root/.local/share/yomesh/yomesh_hermes_mcp.py
      - --config
      - /root/.config/yomesh/hermes.json
    enabled: true
```

Hermes 的 `command` 必须是可执行文件，脚本和参数放在 `args` 中；配置后可运行 `hermes mcp test yomesh` 验证工具发现。桥只接受 HTTPS 地址；仅 `127.0.0.1` / `localhost` 的隔离测试允许 HTTP。Hermes 上不需要设备 SSH 密钥；它只连接 YoMesh 的管理地址。轮换或撤销令牌后，旧桥会立即失效。不要让 Hermes 的聊天输出、日志或模型上下文显示令牌。

## 验收边界与恢复

“原生校验通过”证明目标版本可加载，服务检查证明重启后处于活动状态；两者都不能单独证明上游连通性、DNS 接管、客户端路由、动态 FakeIP 或实际流量效果。需要在目标网络分别核对 Mihomo 与 LocalDNS 的版本、监听、上游、服务状态和实际查询/代理结果。部署 YoMesh 工作台之前，另行备份群晖持久化 `data/`、环境文件和旧镜像；设备执行器的回滚备份不能替代整机备份。

设备配置与 Hermes 接入凭据都只放在各自安装的 `data/` 或 root 私有目录。复制本项目的代码不会复制任何人的设备、令牌或配置；新用户要自己登记服务器、核对指纹、安装执行器、读取配置和配置 Hermes。
