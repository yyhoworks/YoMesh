# 内核兼容约定

所有输出从同一 Profile 解析。代理与 DNS 是不同组件，不把一种配置格式直接当成另一种配置。

## 锁定版本

| 功能 | Mihomo 1.19.32 | sing-box 1.14.3 | MosDNS 5.3.4 |
|---|---|---|---|
| SS AEAD/2022、Trojan、VMess、VLESS、HY2 | 已测试加载 | 已测试加载 | 不承担代理 |
| WS、gRPC、VLESS Reality | 所列字段已测试 | 所列字段已测试 | 不承担传输 |
| 手动选择、自动测速 | 原生组类型 | selector / urltest | 不承担节点组 |
| 故障转移、负载均衡 | 保留原生类型 | 拒绝非等价转换；可明确选择仅本次改为自动测速 | 不承担节点组 |
| 域名精确、后缀、关键词、正则 | 原生匹配 | route 条件 | domain_set / qname |
| IPv4 / IPv6 CIDR | 代理匹配 | ip_cidr | 独立响应 IP 判定；不冒充域名匹配 |
| AND / OR | 保留完整条件 | 保留逻辑条件 | 不删除未知子条件或扩大命中范围 |

内核下载地址、版本与 SHA-256 见 `tools/cores.lock.json` 和 `tools/cores.linux-amd64.lock.json`。更换内核需同步版本锁、依据与原生测试。

## Profile 与配置空间

- 配置空间保存生成组合、规则启停、入站、基础配置与 DNS。共享资源先在后端解析，再交给编译器；前端不独立解释成员与规则。
- 关闭规则会跳过引用它的整条策略，不删除 AND / OR 子条件。规则顺序、解析选项及唯一兜底保持完整。
- 原生 `mihomo-v1` 保存完整 Mihomo 对象与内置 DNS，不能自动转换成 sing-box。未知字段保留给原生内核验证。
- 旧 `mihomo-localdns-v1` 保留 LocalDNS 文件。它是独立程序，并非 MosDNS；程序不随本仓库分发。原生校验需另行提供与 `tools/localdns.lock.json` 匹配的有权使用程序。
- `Profile.mosdns` 独立于代理和旧 LocalDNS 文件，普通空间与原生空间均可使用。复制空间保留 DNS；共享组合空间通过独立覆盖值保存。

## 独立导出

HTTP、CLI、预览和校验共用 `compile_bundle(..., component="proxy" / "dns" / "both")`。

- `proxy` 仅生成、检查代理，不要求安装 MosDNS 或提供代理本机的 DNS SOCKS 入口。
- `dns` 仅生成、检查 DNS，不依赖代理节点组是否完整。显式保存的 MosDNS 使用独立模型，不自动转换原生 Mihomo 规则。
- `both` 兼容组合导出；新 MosDNS 模型复用两个独立组件的输出，不替换独立保存的监听和 SOCKS 设置。
- 下载前使用实际锁定内核检查。不能通过删除未知字段、修改组类型或替换凭据让校验通过。

## 规则来源

| 输出方式 | Mihomo | sing-box | MosDNS |
|---|---|---|---|
| 内联 | 原生规则 / inline provider | 原生逻辑规则 | domain_set.exps / ip_set.ips |
| 本地 | file provider | local rule_set | 包内域名 / CIDR 快照 |
| 远程 | 支持的原始订阅格式 | 支持的 SRS / source JSON | 工作台刷新后重新导出快照 |

Mihomo 原生 YAML 来源接受 `payload:` 或 `rules:` 字符串列表，不能同时声明两个列表。独立 DNS 的通用来源解析器只接受已支持的明确格式；未知条件、混合域名 / IP 及非等价语法整条拒绝，失败保留原快照。

sing-box 1.14.3 的域名、后缀、关键词和正则属于同一个 OR 匹配族，可以从同一 SRS 条目完整展开；依据 `route/rule/rule_default.go` 中的 `destinationAddressItems` 和 `route/rule/rule_abstract.go` 的匹配循环。与 IP、端口或取反混用仍拒绝整条来源。

## LocalDNS 表单迁移

高级配置可读取当前空间已保存的 LocalDNS 原件，不访问源设备或刷新订阅。监听、上游、SOCKS、ECS、缓存和固定解析读取到表单；域名与客户端规则、响应 IP 来源保留快照并暂时停用。分流优先级、客户端策略和默认上游需核对，不能声明已等价迁移。转换说明随 `Profile.mosdns.migration` 保存；可先保存草稿，确认报告后才允许原生校验与导出，报告随导出包附带。

动态表、响应 IP 正则动作、审计、Requery 等不支持项明确列出。迁移不会修改代理或 LocalDNS 原件。设置文件和粘贴导入均通过后端同一模型校验，失败保留当前草稿；大快照每段最多显示 250 条，可增删和搜索，保存保留全部条件。

## MosDNS 字段依据

新增字段依据官方 **v5.3.4** 源码，未升级内核：

- `plugin/executable/forward/forward.go`：多上游、concurrent、addr、dial_addr、逐上游 socks5、bootstrap。该版本查询超时为固定行为，不冒充可配置字段。
- `plugin/executable/cache/cache.go`：size、lazy_cache_ttl、dump_file、dump_interval。
- `plugin/executable/ecs_handler/handler.go`：send、forward、preset、mask4、mask6。将 CIDR 拆成 IP 与前缀，不能直接把 CIDR 写进 preset。
- `plugin/executable/sequence/fallback/fallback.go`：primary、secondary、threshold、always_standby。
- `plugin/executable/hosts`、`pkg/hosts`：域名表达式在前，IP 在后；`ttl` 提供 TTL 范围。
- `domain_set`、`ip_set`、`qname`、`client_ip`、`qtype`、`resp_ip`：域名、客户端、查询类型及响应 IP 的匹配阶段。
- `coremain/config.go`、`udp_server`、`tcp_server`：api.http 与监听。

源码可在 [MosDNS v5.3.4](https://github.com/IrineSistiana/mosdns/tree/v5.3.4) 核对。

客户端分流禁止使用主缓存，改用各上游独立缓存，避免跨客户端命中错误结果。GeoIP 仅在响应阶段判定。LocalDNS 的动态 FakeIP、路由缓存、审计、重新查询和完整 AdGuard 扩展不声明已等价迁移。

## 验证边界

项目包含 236 项测试，验证结构、规则语义、实际内核加载、本机隔离查询、认证与登录入口及保存失败回滚。可选 LocalDNS 检查在不支持的平台或程序未安装时明确跳过。

MosDNS 检查在隔离副本上关闭 API、日志文件和缓存落盘，将 UDP / TCP 监听改为临时回环端口；正式导出保留原文。通过检查不表示外部节点、上游、SOCKS、目标设备权限、内存占用或透明接管已经验证。
