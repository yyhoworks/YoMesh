# Docker / 群晖部署

需要 x86_64 Linux 容器环境、Docker Compose 和 Python 3.11+。仓库不包含用户配置、下载缓存或内核二进制。

## 准备与启动

在项目目录执行：

```sh
python scripts/prepare-linux-cores.py
docker compose build
mkdir -p data
sudo chown 10001:10001 data
docker compose up -d
```

Linux 内核按版本锁下载并核对 SHA-256；镜像安装 PyYAML 时需要访问 Python 包源。镜像构建会运行测试，失败不完成构建。正式服务使用 UID/GID 10001:10001、只读根文件系统、临时 `/tmp`、512 MiB 内存上限及自动重启策略。

默认访问 [本机工作台](http://127.0.0.1:8767)。仅启动配置工作台，不会自动启动导出的代理 / DNS、修改宿主机 DNS 或接管路由。

## 群晖 Container Manager

把代码放在自己选择的项目目录中，再运行上述命令或以 `compose.yaml` 创建项目。挂载的 `data/` 必须允许容器 UID 10001 写入；使用 DSM ACL 时也需要核对容器实际访问权限。不要把数据目录混进源码提交或镜像。

从其他设备访问前，将 `.env.example` 复制为 `.env`，明确配置：

| 变量 | 含义 | 默认值 |
|---|---|---|
| YOMESH_BIND_IP | 宿主机发布地址 | 127.0.0.1 |
| YOMESH_PORT | 宿主机端口 | 8767 |
| YOMESH_ALLOWED_HOST | 浏览器实际使用的 Host，含端口 | 127.0.0.1:8767 |

选择自己的地址或域名，启用工作台账号设置中的登录，再运行 `docker compose up -d`。变更端口时同步允许的 Host。多个反向代理域名可在不提交的 `compose.override.yaml` 中给服务命令增加相应 `--allowed-host`。

客户端订阅链接带独立令牌。需要时反向代理只开放 `/s/`；管理接口仍使用登录和 Host / Origin 检查。不要把真实订阅链接放进公开 issue 或日志。

## 可选 LocalDNS

普通 Mihomo / sing-box / MosDNS 功能只需三个官方内核。LocalDNS 是另外的原生程序，本仓库不分发、也不自动下载。

若要校验已有 LocalDNS 原件，需自行取得有权使用且摘要匹配的 Linux 程序，放在 `tools/linux-amd64/localdns/localdns` 后再构建镜像。摘要与兼容范围见 `tools/localdns.lock.json`。缺少程序时可选测试明确跳过，对应原生 DNS 导出仍会拒绝，不能绕过校验。

## 更新与回滚

更新前备份完整 `data/`、自己的环境与 Compose 覆盖配置，并保留旧镜像。使用新代码构建、测试，确认成功后再切换服务。

```sh
docker compose ps
docker compose logs --tail 100
docker compose build
docker compose up -d --no-build
```

更新后检查 `/api/health`、容器健康和原有配置。需要回滚时使用自己的备份与旧镜像；不要用样例覆盖真实数据。

少数 Container Manager 版本在 Compose 重建后可能仍显示旧容器记录。先核对 Docker 实际容器 ID 与名称，再依据自己的备份处理对应管理记录；不要批量重写其他容器的记录。
