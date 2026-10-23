# YoMesh

代理配置工作台，以同一份 Profile 管理 Mihomo / sing-box 代理配置与独立 DNS。当前版本 **1.0.1**，界面主要使用中文。

## 1.0.1 正式版

统一前端 CSS 格式规范并新增 CI 格式检查；为工作台 HTTP 请求增加统一超时控制与长耗时接口适配；将 `persist()` 改为串行保存队列并避免中间响应覆盖后续内存编辑；隔离配置空间与预设数据启动加载、保留失败重试状态并加固弹窗与未保存草稿保护。

## 功能

- 按配置空间保存生成组合、入站、基础配置和 DNS；节点组、规则与分流策略由后端统一解析。
- 管理节点订阅、手动节点、筛选、节点分组与实际内核测速。
- 有序规则支持域名、IP、端口、订阅及 AND / OR 组合；关闭规则时跳过整条引用策略，保留完整条件。
- 代理独立导出为 Mihomo YAML 或 sing-box JSON，支持内联、本地文件或远程规则。提供可停用的配置订阅链接。
- MosDNS 支持多上游、逐上游 SOCKS、ECS、监听、分层缓存、TTL、有序域名 / 客户端规则、重写、主备回退与响应 IP 判定。
- 域名和响应 IP 来源可按间隔刷新；失败保留上次有效快照。部署后的 MosDNS 使用导出包，刷新后需重新导出和部署。
- 原生 Mihomo 空间保留未知字段与复杂规则，不擅自转换成 sing-box。已有 LocalDNS 空间默认使用原生 LocalDNS 编辑、预览、校验和导出；另存的 MosDNS 迁移草稿不会覆盖原生 DNS。
- 预览、校验与导出使用同一后端模型；下载前使用锁定版本的实际内核验证。
- 已保存的 LocalDNS 原件可直接生成 MosDNS 表单草稿，附转换报告；也支持设置 JSON 文件或粘贴导入。大型规则快照按段编辑、搜索，保存时完整保留其他条件。
- 可登记指定服务器，使用设备专用受限 SSH 执行器只读拉取原生 Mihomo / LocalDNS；编辑独立空间后逐文件预检、显式确认发布并支持回滚。设备外部修改、未纳入基准的已有文件及内核版本不符会阻止覆盖。
- Hermes 可通过可轮换的专用令牌和 stdio MCP 桥读取设备摘要、准备发布；不能直接确认发布或取得设备 root 密码。

## 本地运行（Windows）

需要 Python 3.11+、PowerShell 和网络下载权限。

```powershell
python -m pip install -r requirements.txt
./scripts/bootstrap-cores.ps1
python scripts/yomesh.py serve --port 8767
```

打开 [本机工作台](http://127.0.0.1:8767)。请通过 HTTP 使用工作台，直接打开 HTML 文件不能连接持久化接口。

默认只监听回环地址。需要其他设备访问时，显式设置监听与允许的 Host，并在账号设置中启用登录。真实订阅、节点凭据、证书和用户配置保存在被忽略的 `data/`，不要提交到仓库。

启用密码保护后，未登录访问工作台会先进入独立的 `/login.html` 登录页，成功后返回工作台；退出登录和会话过期也回到该页面。桌面顶栏或手机导航中的“账号与密码设置”可管理账号及退出。新密码至少 12 个字符；首次使用 `YOMESH_REQUIRE_AUTH=1` 启动时必须显式设置至少 12 字符的 `YOMESH_INIT_PASSWORD`，没有公开默认口令。已有凭据不会被启动参数覆盖。尚未启用密码时保留免密模式，客户端订阅链接不受登录影响。凭据文件损坏或不可读时，管理访问会锁定；请恢复有效的 `data/auth.json` 备份或修正文件权限，不要通过删除凭据文件恢复访问。

Mihomo 自动导入保留原生配置。明确选择便携转换时，不支持的规则、解析选项或出口会阻止导入；不会丢弃规则或把出口改成其他组。附带 LocalDNS 只有成功生成迁移草稿才记为已接入，草稿仍需人工核对后确认。快速创建先读取真实订阅，失败不创建空间；不会使用示例节点发布配置。控制器 Secret 留空保持原值，需清除时显式勾选。

Hermes 安装命令由工作台固定安装器和桥脚本的 SHA-256，校验通过后才执行或写入；请从可信工作台复制完整命令。MCP 客户端使用 `/mcp` 并单独设置 `Authorization: Bearer …`，不在地址中携带密钥；旧查询参数地址兼容保留，访问日志会隐藏查询串。远程管理与 MCP 连接应通过 HTTPS。

## Docker / 群晖

先下载并校验 Linux 内核，再构建镜像：

```sh
python scripts/prepare-linux-cores.py
docker compose build
mkdir -p data
sudo chown 10001:10001 data
docker compose up -d
```

Compose 默认发布到 `127.0.0.1:8767`，持久化目录为 `./data`。可将 [.env.example](.env.example) 复制为不提交的 `.env`，配置自己的监听地址、外部端口及允许的访问 Host。部署步骤和群晖权限说明见 [Docker 部署](docs/synology-docker.md)。

镜像构建需要访问 Python 包源；三个官方内核由脚本按 SHA-256 校验。本仓库不分发 LocalDNS 程序或任何用户设备文件。LocalDNS 原生校验需要另行提供与版本锁匹配且有权使用的程序；缺少时不能导出对应的原生 DNS 包，普通 Mihomo / sing-box / MosDNS 功能不依赖它。

## 命令行

```sh
python scripts/yomesh.py check examples/profile.json --proxy mihomo
python scripts/yomesh.py export examples/profile.json --proxy mihomo --component proxy --output outputs/proxy
python scripts/yomesh.py export examples/profile.json --component dns --output outputs/dns
```

`examples/profile.json` 是明确标注的本地兼容性样例，包含虚构测试节点与测试密码，不能用于连接外网。实际配置请在自己的工作区创建；导出目录必须不存在。

## 兼容与验证

| 内核 | 锁定版本 | 用途 |
|---|---|---|
| Mihomo | 1.19.32 | 代理、原生规则和节点组 |
| sing-box | 1.14.3 | 可移植 Profile 的代理输出 |
| MosDNS | 5.3.4 | 独立 DNS |

```sh
python -m unittest discover -s tests -v
```

自动测试覆盖原生加载、规则语义、隔离 DNS 查询、HTTP API、认证与独立登录页、迁移报告、原生 DNS 优先级、无损导出，以及安装器完整性和失败时保留原数据的回归。前端安全回归运行 `node --test tests/frontend-security.cjs`；用例数量以执行结果为准。可选 LocalDNS 测试在程序未安装或平台不支持时明确跳过。内核加载与本机测试不代表真实订阅、外部节点、设备接管或上游连通性已验证。

左侧独立的“设备同步”页面集中管理服务器与 Hermes，并提供接入步骤、安装脚本下载、配置文件下载和可复制的命令。设备同步须先启用工作台密码保护，并在每台目标设备上安装受限执行器；新服务器需先安装 Mihomo / LocalDNS、准备初始配置和服务。复制安装、Hermes 接入、发布及恢复步骤见 [指定服务器同步](docs/device-sync.md)。

更多说明：[兼容约定](docs/compatibility.md) · [架构](docs/architecture.md) · [规则工作台](docs/rule-workbench.md) · [MosDNS 能力边界](docs/mosdns-localdns-gap-analysis.md) · [发布隐私说明](docs/privacy.md)。

## 第三方资源

项目使用的内核与字体遵循各自许可证；内核下载地址和摘要见 `tools/*.lock.json`，字体许可证随 `web/assets/` 提供。私有配置、设备快照、部署记录、截图、缓存和二进制运行文件不包含在公开版本中。
