# Codex / AnyRouter 思考强度抓取

Windows 本机被动抓包工具，带轻量浏览器界面，逐次对比 Codex 请求中的 `reasoning.effort` 与返回的 `response.completed` 事件中的 `response.reasoning.effort`。

**响应字段是服务端报告值。脚本不能独立证明上游模型的内部执行强度，也不会用推理 token 数推算强度。**

## 可视化界面（推荐）

**双击 `StartUI.cmd`**，会自动在默认浏览器打开本机界面。也可以在 PowerShell 中运行：

```powershell
& 'E:\AI\repo\anygpt思考强度抓取\Start.ps1' -UI
```

保持 CC Switch 正常运行，在页面点击“开始抓取”，然后正常使用 Codex。**默认持续抓取，限定会话留空即可自动记录所有新对话，无需重开工具。** 请求的完整 HTTP 响应结束后自动显示。

列表的“对话”列和请求详情显示真实对话名称，可以搜索名称、会话 ID、模型或请求 ID。名称直接读取本机 Codex 的 `session_index.jsonl`（`id` → `thread_name`）和只读 `state_5.sqlite`（`threads.id` → `threads.name`，优先采用数据库名称），不读取首条提示词充当名称。运行中每 5 秒最多刷新一次，新增会话 ID 会立即触发查询；Codex 写入名称后，前台抓取页面通常在 7 秒内更新，重命名也无需重启。未写入或无法读取时显示“等待对话名称”，详情保留精确 ID。支持 `CODEX_HOME`，未设置时使用用户目录下的 `.codex`。

抓取批次下拉框显示开始时间，同一批次可以包含多个对话。仍可设置限时时长、端口与精确会话 ID，筛选差异、查看详情和导出 CSV。开始新批次后自动切换到新记录。名称只在显示时关联，不改写已有抓包文件；CSV 保留抓取时的原始字段。

“停止”只停止抓包，仍可查看记录；“退出界面”会结束抓包和本地服务。**仅关闭浏览器标签页不会结束服务**，也可以关闭启动窗口或在启动窗口按 Ctrl+C。界面地址只绑定 `127.0.0.1`，端口自动选择，启动窗口会显示地址。

### 资源占用

- 不新增运行时依赖，不使用 Electron、Node 服务、前端框架、外部字体或图表库。
- 页面与抓包共用一个 Python 进程；只有点击开始后才打开 Npcap 并创建抓包线程，停止后关闭设备并退出该线程。
- 静态页面约 27 KB；请求列表只保留最近 300 条，历史 JSONL 增量读取、每次最多读取 1 MiB。完整记录仍保存在磁盘，CSV 可导出全部已保存记录。
- 抓取中每 2 秒刷新、空闲每 10 秒刷新；标签页隐藏时暂停刷新，重新显示时补读。第一次加载较大历史文件时每 300 ms 分批读取，直到读完。
- Npcap 接收缓冲固定为 8 MiB，避免默认缓冲在大请求突发时溢出；停止抓取即释放。名称查询没有额外后台进程，索引按字节增量读取，数据库只查询当前列表的 ID。
- 新版持续抓取时 Python 后端实测约 31.6 MiB，5 秒采样 CPU 增量为 0，没有子进程。**采样值不包含浏览器和驱动缓冲，流量期间的峰值会变化。**

实现与验证说明见 [UI-NOTES.md](UI-NOTES.md)。

## 命令行启动

本机链路已核实为 `Codex → http://127.0.0.1:18080/v1/responses → CC Switch → AnyRouter`。

需要 Windows、Python 3.11 或以上、已安装的 Npcap。当前机器已具备 Python 和 Npcap 1.83。

在 PowerShell 中运行：

```powershell
cd 'E:\AI\repo\anygpt思考强度抓取'
.\Start.ps1
```

首次启动会在本目录创建 `.venv` 并安装 `dpkt==1.9.8`。默认抓取 5 分钟，正常使用 Codex 即可。不会改动 Codex / CC Switch 配置，不会额外发送模型请求。

```powershell
# 检查本机抓包能力
.\Start.ps1 -Doctor

# 抓到 3 次完整 HTTP 交互后退出，最多等 5 分钟
.\Start.ps1 -Count 3

# 持续记录，Ctrl+C 停止
.\Start.ps1 -Seconds 0

# 仅保存指定会话；值必须使用日志里的精确 thread_id
.\Start.ps1 -ThreadId '从会话日志复制的精确值'
```

如果系统阻止执行本地 PowerShell 脚本，可仅为本次进程指定执行策略：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start.ps1
```

也可以使用已安装依赖的 Python：

```powershell
python -m pip install -r requirements.txt
python capture.py --count 3 --seconds 300
```

## 输出

每次运行创建独立的 `captures/日期时间-随机后缀/`：

| 文件 | 内容 |
|---|---|
| `summary.csv` | Excel 可打开的逐请求对照表 |
| `records.jsonl` | 每行一条完整记录，适合后续自动分析 |
| `0001.json` 等 | 单次请求元数据、响应快照、字段路径对应值与摘要 |
| `0001.response.sse` 等 | 完整 HTTP 响应体，已经去除 HTTP 分块编码 |
| `status.json` | 运行状态、样本数、重组错误、未完成连接、Npcap 丢包统计 |

关键字段：

| 输出字段 | 精确来源 / 含义 |
|---|---|
| `requested_effort` | 请求 JSON 的 `reasoning.effort` |
| `response_effort` | `response.completed` 事件的 `response.reasoning.effort` |
| `effort_differs` | 两值都存在时比较；信息缺失时为 `null` |
| `reasoning_tokens` | 完成事件的 `response.usage.output_tokens_details.reasoning_tokens` |
| `upstream_request_id` | 响应头 `x-oneapi-request-id` |
| `routed_channel_id` | 响应头 `x-new-api-routed-channel-id` 原值，不解析其中数字 |
| `response_completed` | 是否捕获到完整的 `response.completed` SSE 事件 |
| `execution_verified` | 始终为 `false`：客户端抓包不构成内部执行证明 |

同时保留 `response.created`、`response.in_progress`、`response.completed` 的响应元数据快照。请求侧只保存模型、强度、服务档位和关联 ID 等选定字段；不保存请求提示词或 Authorization/Cookie。**原始 SSE 可能包含回答、工具调用内容和项目文本，分享前应检查。**

一次用户提问通常会产生多次模型请求。按 HTTP 交互逐条记录，不把整轮聊天合并为一个强度值。

## 已实测的边界

- 只支持本机回环 IPv4、HTTP/1.1、路径 `/v1/responses`；默认端口 18080，可用 `--port` 修改。HTTPS、HTTP/2 和 WebSocket 不在该实现范围内。
- Npcap 在本机使用端口 BPF 时漏掉了大请求后续分段；使用 `ip proto 6 and host 127.0.0.1` 后，捕获到连续的 TCP 序号。因此脚本在内存中再筛选端口，其他端口的数据不会写文件。
- 接收缓冲在激活设备前设置为 8 MiB；本机 9 次跨会话/复用连接测试，含 2.2 MB 和 6 MB 请求，全部捕获且丢包为 0。更大的突发或其他回环流量仍可能导致丢包；抓取中的 `status.json` 每 5 秒记录 Npcap 统计，页面显示已发生的丢包数。
- TCP 分段、乱序、重传、序号回绕、HTTP Content-Length 和 chunked 均有处理。单连接重组上限 64 MiB，闲置 5 分钟的连接释放；IPv4 分片及无长度的响应不支持。
- 从连接中途开始、丢包、超时、停止时尚未结束的交互，不会伪装成完整样本。查看 `status.json` 的错误与未完成统计；`response_completed=false` 表示 HTTP 交互已结束但没有完成事件。
- 不支持的请求压缩格式会标记 `json_decoded=false`，请求强度留空；响应仍可留存。当前实测请求未压缩。
- 每次运行目录中创建名为 `STOP` 的文件也能停止抓取。退出码：`0` 有样本或检查成功；`2` 没有完整样本；`1` 抓包环境失败。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

协议重组测试覆盖超过 64 KiB 的请求、乱序重传、缺段、分块扩展/尾部字段、UTF-8 与 TCP 序号回绕。字段对照测试使用本次实测响应提取的脱敏固定样本，不猜测 JSON 路径。

Windows / Npcap 真实回环回归：`.\.venv\Scripts\python.exe .\tests\verify_loopback.py`。它只向临时本地 HTTP 服务发送测试数据，不调用模型 API，不写入正式抓取目录。

实际观测结论和证据位置见 [OBSERVATIONS.md](OBSERVATIONS.md)。
