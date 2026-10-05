# 2026-10-05 实测记录

本机链路：Codex → `http://127.0.0.1:18080/v1/responses` → CC Switch → `https://anyrouter.top/v1/responses`。本地端口的进程和 CC Switch 转发日志均已核实。

## 观测结果

共捕获 5 次完整 HTTP 交互，每次都收到完整的 `response.completed` 事件。5 次请求均发送 `reasoning.effort = "xhigh"`，对应完成事件均返回 `response.reasoning.effort = "high"`。开始事件中的值也为 `high`。

这证明该链路上请求值和响应报告值不同。抓包位置无法判定是哪一级改变了参数，也无法独立验证上游模型内部如何执行。不要把结果推广为所有模型、所有请求的固定映射。

| 阶段 / 样本 | 捕获时间（UTC） | 请求强度 | 响应强度 | 推理 token | 证据 |
|---|---|---|---|---:|---|
| 初次验证 / 1 | 2026-10-04T17:42:55.053272+00:00 | xhigh | high | 39 | [JSON](captures/initial-check/0001.json) |
| 初次验证 / 2 | 2026-10-04T17:43:38.397026+00:00 | xhigh | high | 529 | [JSON](captures/initial-check/0002.json) |
| 初次验证 / 3 | 2026-10-04T17:46:17.743799+00:00 | xhigh | high | 367 | [JSON](captures/initial-check/0003.json) |
| 正式脚本 / 1 | 2026-10-04T17:50:08.504820+00:00 | xhigh | high | 156 | [JSON](captures/20261005-014920-074502/0001.json) |
| 正式脚本 / 2 | 2026-10-04T17:50:55.890258+00:00 | xhigh | high | 1034 | [JSON](captures/20261005-014920-074502/0002.json) |

原始 SSE 与上述 JSON 放在同一目录；JSON 中记录响应体 SHA-256、响应 ID、`x-oneapi-request-id` 和路由响应头原值。`captures/` 已被 `.gitignore` 排除，证据保留在本地。

## 正式入口验证

通过 Windows PowerShell 执行 `Start.ps1 -Count 2 -Seconds 180`，成功捕获两次请求后自动退出。输出为 [summary.csv](captures/20261005-014920-074502/summary.csv)。

该次运行重组错误为 0，Npcap 报告丢包为 0；达到数量限制时还有 1 个未结束连接，该连接没有计入两份完整样本。详见 [status.json](captures/20261005-014920-074502/status.json)。

独立 `.venv` 内的 8 项单元测试通过，`Start.ps1 -Doctor` 能实际打开 Npcap 回环接口。没有修改 Codex、CC Switch 配置，没有重启代理，没有额外发送测试模型请求。

## 抓包实现选择

最初使用端口 BPF 时，只得到大请求的首个 65,495 字节 TCP 负载。改用回环 IPv4 TCP 过滤后，同一请求的后续分段可见，序号连续；脚本随后在内存中检查端口。保留该方案，避免把被截断的请求当成完整请求。

HTTP 分块响应必须等到结束块后再作为完整交互落盘。`dpkt 1.9.8` 对不完整 chunked 数据会抛出解析异常，因此先做完整性检查，再解析消息。分块扩展和尾部字段由脚本显式处理。
