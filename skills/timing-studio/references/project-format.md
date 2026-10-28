# 工程格式与语义

生成时读取此文件；修改已有工程先读会话 state 并保留原有字段。完整模型由 runtime 内 `model.py` 维护，窗口准备规则在 `local_window/store.py`。运行 doctor 可取得实际 runtime 位置；发行包中是 `scripts/runtime/`。不要另写 VA 编译器。

最小 version 2 示例（1ns 大周期，信号前 100ps 低、随后 200ps 高，余下低）：

```json
{
  "version": 2,
  "module_name": "timing_stimulus",
  "unit": "ps",
  "period_fs": 1000000,
  "rise_fs": 10000,
  "fall_fs": 10000,
  "vlow": 0.0,
  "vhigh": 1.0,
  "cycles": 0,
  "signals": [
    {"id": "sample", "name": "sample", "items": [
      {"id": "sample_0", "type": "segment", "level": 0, "duration_fs": 100000},
      {"id": "sample_1", "type": "segment", "level": 1, "duration_fs": 200000},
      {"id": "sample_2", "type": "segment", "level": 0, "duration_fs": 700000}
    ]}
  ],
  "view": {"row_height": 64, "signal_order": ["sample"]}
}
```

- 1ps = 1000fs，1ns = 1000000fs。使用 `model.parse_time('0.2n')` 等解析，避免浮点计算后取整损失相位；周期和段长均为正整数 fs，最大值由 `model.MAX_FS` 定义（约 9 秒），少于 1fs 的输入拒绝。
- 模块名与信号名必须是合法且非保留的 VA 标识符。signal ID、item ID 唯一；signal 数组顺序决定 VA 端口顺序。最多 64 路、50000 个展开片段，具体以模型校验为准。
- 每路段长累计表示跳变时刻，第一段为初始电平。短于大周期时末态保持；超过大周期应在生成时明确处理，不能静默截掉所需事件。建议新工程各路明确填满整个大周期，合并相邻同电平段。
- `cycles: 0` 持续重复大周期；1 为单次；有限次结束保持最后电平。此字段不是内部小周期的次数；内部重复通过生成多个平坦片段表示。
- `rise_fs` / `fall_fs` 公共且必须大于 0。边沿时刻表示跳变开始，0 时刻首段通过初始工作点建立。要求模拟电压越过阈值的具体时刻时，不能直接当成逻辑边沿时刻。短脉冲可能来不及达到目标电平，应报告模型警告。
- `view.row_height` 是 36–128 的整数，`signal_order` 必须恰好包含所有 signal ID 各一次。视图排序不重排 `signals`。
- 当前模型支持固定高低电平、公共电压及边沿时间，不支持逐路 rise/fall、任意模拟曲线、高阻态、基于 DUT 反馈的条件事件。不能默默把这些需求近似为现有功能。

## 重复脉冲的生成

将“起点 s、每 T 重复 N 次、高电平持续 W”转为区间 `[s+i*T, s+i*T+W)`。对于非交叠 phi1/phi2 分别计算每个高区间，两相的间隔也必须在周期回环处核对。不要通过浮点累加生成时刻。

构造普通段时，以 0、大周期终点及所有区间端点为切分点；按区间覆盖确定各段电平并合并相邻同电平段。先检查 `0 <= start < end <= period`，再构造。如果从 0 开始为高，首段就为高；不要额外插入零时长低段。真实边沿是电平变化处，最后一段的结束不是可拖动边沿。

保留简短的自然语言描述/生成参数便于后续理解，但实际微调后的 project 是当前时序依据。没有“按描述重新生成即可自动保留手改”的保证。
