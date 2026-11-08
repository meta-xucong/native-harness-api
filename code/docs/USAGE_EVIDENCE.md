# 无收费原生用量证据投影

本切片只采集官方通知中**已观察到的单模型请求用量证据**。不收费、不估算、
不修改现有 `usage: {state: unknown}`、不改变输出/派发/恢复，也不开放多租户。
新增 `PublicSfeSession.evidence().metering_evidence` 是内部诊断证据；不会自动进入
Bridge账单、Sub2账单、Chat Completions usage 或 SSE。

## 归属与去重

固定参考官方 `@tencent-ai/agent-harness 0.2.0-beta.16 docs/acp-v2.md:65–85`。
收集经过既有严格帧校验的 `session/update.update._meta.usage`；root session、
rootRequestId、requestId、messageId 都须明确，子agent另按其sessionId区分。
缺失身份不猜测，标 `unattributed_usage`。prompt ACK之前可有早到证据，但只有
coordinator确认的session/run与接受事实才能从summary输出对应记录，其他run不混入。

同一模型请求的多帧/完整消息/历史重放不是多次计费：相同记录重复只增加诊断计数；
非冲突字段补全更新同记录；数值改动/字段删减缺少可靠版本序号，保留已有观察值并标
`conflicting_revision`，不擅自最后写入获胜、不产生总计。
返回不直接导出原始ID的稳定 SHA-256 摘要；仅内部诊断，不承诺不可关联或不可枚举。
不导出其他vendor对象。显式子agent若声称与根同session则按身份冲突剔除。

## 数值和未知

仅投影有限allowlist的原生标量计数名称（snake/camel token名及credit/credits）；
保留字段原名而不宣称不同供应商字段可直接相加。明确给0才保存0。
计数必须为非负安全整数，credit必须为有界有限非负数；布尔、NaN、负数、
嵌套provider对象和未知字段不导出，并标相应缺口。**allowlist不是provider能力声明**。

不将 `usage_update.used/size`（上下文占用/预算）、会话累计cost、idle累计usage
当本轮token。不累加root和subagent费用，不兑换credits，不填价格。

输出始终 `billing_eligible:false`、`total_usage:null`、`coverage:unproven`：

- `state:unknown`：没有可归属的单请求记录或尚未确认接受身份。
- `state:partial`：存在已归属观察记录，但不保证覆盖全部模型请求或字段。
- terminal_observed只反映已看到终态，不证明完整计量，也不证明owned进程清理完成。

官方事件流在这里没有提供可证明“全部模型请求均已计量”的完整清单。即使idle成功，
也不能把部分记录升级为complete。发生限额、冲突、缺失身份，缺口均保留；若将来
引入可信覆盖清单和持久收据，必须另审计完整性协议，不能靠terminal bool开启收费。

## 限额与故障边界

每次session collector默认最多4096个观察帧、512条模型请求证据；身份长度≤512。
只保留有限字段和有限原因码，summary返回独立拷贝。限额/非法证据仅标诊断缺口，
不令模型重试、不改变任务终态；既有原生协议错误仍由原有严格处理器处理。

未接原生实际账号验证。本切片不等于普通收费provider完成，不是用户授权价格，
不应向客户将partial记录宣传为准确账单或完整实际token消耗。
