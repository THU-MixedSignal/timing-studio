# 会话操作

以下示例使用 PowerShell。将 `$skill` 设为本 SKILL.md 所在目录的实际绝对路径，`$data` 设为用户选定的可写数据目录。变量只是当前命令会话中的示例，不要求用户设置系统环境变量。

```powershell
$run = Join-Path $skill 'scripts/run.cmd'
$session = Join-Path $data 'sessions/adc-clock-01'
$candidate = Join-Path $data 'requests/adc-clock-01/candidate.timing.json'
& $run doctor
```

`run.cmd` 可自动准备 Python。已经知道合适解释器时，也可用 `python -B <skill>/scripts/timing_studio.py` 执行同样的子命令。引导器允许用 TIMING_STUDIO_PYTHON 指定 Python，或用 TIMING_STUDIO_RUNTIME_DIR 指定私有运行时目录。没有网络且没有 Python 时应说明缺少环境，不声称启动成功。

## 生成与校验

按 [工程格式](project-format.md) 写出候选 JSON，随后执行实际模型校验及 VA 编译检查：

```powershell
& $run validate --project $candidate
```

时间用整数 fs；需要使用 model.parse_time() 等模型函数时，由 doctor 结果定位 runtime，将其加入生成脚本的 sys.path 后导入 model。不要假定父工程已在 Python 搜索路径，也不要把 state 信封当成 project 文件。

示例生成只用于用户要求试用时，输出到明确的新文件：

```powershell
& $run demo --output $candidate
```

## 新建、恢复与读取

```powershell
# --project 仅用于新会话；已有会话不会被此参数重置。
& $run launch --session $session --project $candidate

# 恢复已知会话。
& $run launch --session $session

# 只启动后台供读回，不打开窗口。
& $run launch --session $session --no-open

& $run session --session $session state --output (Join-Path $data 'requests/latest-state.json')
```

launch 和 session 都要求显式 --session。浏览器、后台临时文件、日志和版本保存在该会话内。浏览器未被自动发现时，可在 launch 加 `--browser <浏览器可执行文件>`。窗口只监听 127.0.0.1。不要分享 connection.json、token 或带 token 的完整窗口地址。

session 子命令不会启动后台；连接失败时先用 launch 恢复同一已知会话。窗口关闭后后台约 30 分钟无请求退出，工程保留。

## 修改、导出与停止

先读 state，检查 revision 和其中的 project。下面的 7 必须换成实际刚读出的版本。局部操作优先 edit；整体替换前保留用户编辑和未要求变更的字段。

```powershell
& $run session --session $session edit --revision 7 --file (Join-Path $data 'requests/operation.json')
& $run session --session $session replace --revision 7 --file $candidate --label '采样延后'
& $run session --session $session export --revision 7
& $run session --session $session stop
```

这些命令是各自的示例，不能不读回就连续使用同一个 revision。409 表示存在新修改，应重新读取并比较，不换个版本号强行覆盖。请求完成状态不确定时先读回，不盲目重放。stop 仅停止所指定会话，不删除文件。

单个 operation 的例子：

```json
{"op":"boundary","signal_id":"sample","item_id":"sample_1","new_end_fs":300000}
```

item_id 指跳变左侧的片段，new_end_fs 是大周期内的绝对时间。拖动扫过的区间会被相应电平覆盖，可能吞并其他脉冲；不要用这种操作代替整个脉冲的平移。合并会改变 item ID，下一次编辑先重新读取。

```json
{"op":"paint","signal_id":"sample","start_fs":100000,"end_fs":250000,"level":1}
```

其他 operation：settings、rename、view、add_signal、delete_signal、undo、redo。具体字段以 runtime 内 local_window/store.py 为准。

导出文件在会话 exports/rXXXXXX/，同时保存 VA 和对应的 timing.json。交付两者的实际路径，核对它们属于目标 revision。界面与逻辑校验不等于真实模拟仿真。
