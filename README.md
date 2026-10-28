# Timing Studio · 时序工坊

用自然语言描述多路周期时序，让助手生成工程；在独立窗口里拖动微调，再导出 Verilog-A。开环 ADC 时钟、复位、采样和非交叠控制信号都可以用同一份工程协作。

本仓库是完整的 **timing-studio skill 源码发行版 0.1.0**。包含窗口、时序模型和 VA 导出器，不需要旧版 TimingStudioLite.exe、Qt、实验室服务器或额外模型 API。自然语言理解依赖你正在使用的 Codex 助手，窗口本身不是聊天软件。

## 下载后先试用

1. 下载仓库 ZIP 或 clone 到本机可写目录，完整解压。建议使用空间充足的磁盘和较短的目录，例如 `D:\TimingStudio`。未启用 Windows 长路径支持的系统可能在深层目录读写失败；安装器不会修改系统长路径设置。
2. 在 **Windows 10/11 x64** 上双击 `Open Timing Studio.cmd`。
3. 已有 Python 3.12+ 时使用现有解释器；否则首次启动从 python.org 下载约 11 MB 的官方私有 Python，并校验 SHA256。无需管理员权限，不修改系统 PATH。需要 Edge 或 Chrome；首次准备 Python 可能需要联网。
4. 窗口会打开六路 10 us 示例，包含 100 次内部时钟。修改会自动保存，再次打开会恢复。密集区点击可放大；拖动边沿，或精确输入 `1n` / `200p`。

默认试用工程保存在仓库 `data/sessions/demo/`。导出文件在该会话 `exports/rXXXXXX/`，同时有 `.va` 和 `.timing.json`。窗口关闭后后台在约 30 分钟无请求时退出。项目保存不依赖后台持续运行。

也可在 PowerShell 执行：

```powershell
.\skills\timing-studio\scripts\run.cmd doctor
.\skills\timing-studio\scripts\run.cmd launch --session '.\data\sessions\my-adc' --project '.\examples\high-speed-800ps.timing.json'
```

同一个会话再次打开时去掉 `--project`。这个参数只用于初始化，已有会话会拒绝用新候选覆盖。

## 安装为 Codex skill

双击 `Install Skill.cmd`，把这个仓库里的完整 `skills/timing-studio` 安装到当前用户的 `.agents/skills/timing-studio`。如果目标已经存在，安装器会停止，不覆盖旧版本或工程。

当前 Codex 官方支持用户目录 `.agents/skills` 和项目目录 `.agents/skills` 的本地发现；不同客户端版本的目录约定可能不同。你也可以在 Codex 使用 `$skill-installer`，提供本仓库的 GitHub 地址和 `skills/timing-studio` 子目录。新 skill 未出现时重新打开 Codex。[官方安装与发现说明](https://learn.chatgpt.com/docs/build-skills)

希望安装在指定项目中时：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-skill.ps1 -Destination 'D:\MyProject\.agents\skills\timing-studio'
```

安装器只复制清单中的 skill 代码和文档，不复制 Python 运行时、会话或日志。完整仓库的 examples 供单独试用；安装后的 skill 可用 demo 命令生成同样的六路示例。此命令的 ExecutionPolicy 只作用于该 PowerShell 进程，不更改系统策略。

然后给 Codex 这条指令：

> 使用 $timing-studio：生成 10us 大周期，reset 在 0–100ns 为高；从 200ns 开始产生 100 次周期 50ns、高电平 20ns 的 clk；sample 在 600–1000ns 和 1400–1800ns 为高。电平 0–1V，升降沿都是 200ps。把工程保存在我指定的数据目录，打开窗口让我微调。

在窗口里改到显示“已保存”，回到聊天说：

> 改好了，读取这个会话的最新修改并导出 VA。

窗口不会自动发消息给助手。改动保存在会话里，助手继续操作时会检查版本，避免用旧工程覆盖你的微调。

## 环境与文件

- **Python**：3.12+，仅使用标准库。自动下载版本固定为 3.12.10，用于复现既有验证环境，不代表最新 Python 版本。私有运行时默认位于 skill 内 `.runtime/`。
- **浏览器**：Windows Edge/Chrome 应用窗口。可在 launch 命令后加 `--browser '完整的浏览器exe路径'`。macOS/Linux 图形窗口尚未验证。
- **自选 Python**：进程环境变量 `TIMING_STUDIO_PYTHON` 指向解释器；`TIMING_STUDIO_RUNTIME_DIR` 可指定私有运行时目录。助手应遵守你指定的数据盘，不替你更改系统存储设置。
- **源码**：`skills/timing-studio/scripts/runtime/`，可直接阅读或修改。
- **工程数据**：由 `--session` 明确指定；浏览器资料、临时目录和日志也在该会话内。
- **网络**：窗口只监听 `127.0.0.1`。Python 准备完成后，编辑与导出无需网络。助手是否联网由其运行环境决定。
- **更新**：保留旧 skill 和工程，新包先在另一目录验证。不要直接覆盖正在运行的安装；工程文件可在新版本中新建会话后导入。

## 能做什么、不能做什么

支持固定高低电平、多路周期信号、有限次或无限重复、按段精确时间、波形行高/排序、边沿先后与近邻提醒、密集波形局部放大。最多 64 路；较复杂序列可以由助手批量生成普通时间段。

不支持任意模拟曲线、逐路电平或逐路 rise/fall、高阻态、等待 DUT 反馈的条件事件，也不生成实际晶体管电路。界面边沿表示电压开始变化的时刻，不等于模拟电压跨过某个阈值的时刻。极密集、长序列的 VA 仿真可能较慢。

VA 中有公共参考端 `vref`，通常接地。各输出是理想电压源；使用前在自己的仿真器中编译检查。导出的工程 JSON 用于继续编辑，VA 不能反向还原工程。

## 验证与维护

本发行版保留既有时序模型、编辑和 VA 语义；本次验证关注安装、移位、窗口与导出。此前同一核心版本完成过 60 组窗口到 Spectre 对照，但不代表本次重跑了仿真，也不代表任意自然语言都已验证。具体范围见 [验证说明](docs/VALIDATION.md)。

本地回归：`python -B run-tests.py`。不用安装 Python 测试依赖。源码入口与维护规则见 [CONTRIBUTING](CONTRIBUTING.md)。

代码采用 [MIT License](LICENSE)。Python 和浏览器仍遵循各自许可，见 [第三方说明](THIRD_PARTY_NOTICES.md)。
