# 维护与贡献

本仓库包含可独立安装的 skill。主要文件：

- `skills/timing-studio/SKILL.md` 与 `references/`：助手使用规范。
- `skills/timing-studio/scripts/timing_studio.py`：公共 CLI。
- `skills/timing-studio/scripts/runtime/model.py`：整数 fs 时序和 VA 编译。
- `skills/timing-studio/scripts/runtime/edit_ops.py`：局部编辑语义。
- `skills/timing-studio/scripts/runtime/local_window/`：本机后台与静态界面。
- `run.cmd` / `bootstrap.ps1`：Windows 运行环境入口。

用 Python 3.12+ 在仓库执行 `python -B run-tests.py`。测试输出与临时资料位于 `.cache/`；不需要仿真器。入口改变还需从含空格/中文的新目录实际打开窗口、保存编辑、导出，不能只靠单元测试声称图形验证。

修改时序模型或 VA 生成逻辑时，先建立独立预期，再比较实际仿真输出。不得通过改预期或放宽容差隐藏回归。显示顺序与 VA 端口顺序分离；edit/export 必须检查会话 revision。

不要提交 `.runtime/`、`.cache/`、用户 `data/`、会话、浏览器 profile、日志或带 token 的 `connection.json`。发布只收录源码、文档、测试和明确的示例。需要新增公开文件时更新 `package-manifest.json`，skill 内新增文件也应加入 `skills/timing-studio/install-manifest.json`；安装器会核对后者。修改已登记文件后运行 `python refresh-manifests.py` 刷新哈希，再运行回归。

发行版源自已有项目的独立快照，因此本仓库无需原作者的项目路径、Git 历史或开发环境即可运行。对公开仓库进行后续修改时，应提交完整依赖并保持 skill 单目录可移植。许可证为 MIT。
