# ESP Agent Kit 工作约定

本文件是 Agent 的工作入口，规定工作区、Skill 使用方式与开发约束。产品与实现说明分别维护在：

- [项目定位、核心特性与路线图](docs/project-positioning-and-roadmap.md)
- [架构、当前支持与验收进度](docs/architecture-and-development.md)

产品目标、已有实现和实测范围须分别说明；不能把规划、上游工具支持或一次识别成功表述为全面适配。

## 工作区约定

- 以 `esp-agentkit` 仓库根目录作为 Agent 工作区和默认命令执行目录。
- 新设备项目放在 `device-projects/<project-name>/`，保存配置、`app/`、资源、接线说明和开发记录。已有项目沿用原位置；业务修改写入设备项目，通用工具或适配改进才修改 `skill/` 等共享内容。
- 续作时先通过 CLI 的 `projects` 定位项目，读取当前源码、配置和最近记录；多个匹配时明确选择，不猜测。历史记录不能替代当前设备状态。
- 交付、暂停或交接时保存源码、依赖、板型与固件版本、接线和未解决问题，操作与观察注明时间及对应部署清单。`device-projects/` 已被 Git 忽略，须保留完整目录并单独备份。

桌面环境由 uv 管理 Python 3.12，位于 `skill/esp-agentkit/.venv`。从仓库根目录执行：

```sh
uv sync --project skill/esp-agentkit --locked
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py --help
```

uv 的 `--project` 选择工具环境，CLI 自身的 `--project` 选择设备项目。切换工作目录时使用正确的绝对路径，并保持项目查找范围明确。

## Skill 使用指引

设备开发或续作前，主动阅读仓库内的 [Skill](skill/esp-agentkit/SKILL.md)，再按阶段查看相关章节。Skill 无需单独安装或注册；纯文档和工具集维护按涉及的功能查阅。

| 当前任务 | 查阅内容 |
| --- | --- |
| 准备环境、创建或继续项目 | [环境与工作区](skill/esp-agentkit/SKILL.md#environment-and-workspace)、[项目配置与查找](skill/esp-agentkit/SKILL.md#work-with-a-project) |
| 识别设备、核对接线 | [设备检测](skill/esp-agentkit/SKILL.md#detect-a-connected-device)、[串口排查](skill/esp-agentkit/SKILL.md#find-the-boards-serial-port)、[板型资料](skill/esp-agentkit/boards/) |
| 首次配置 | [固件安装](skill/esp-agentkit/SKILL.md#install-the-firmware-on-a-new-board)、[配置与验证](skill/esp-agentkit/SKILL.md#configure-and-verify) |
| 编程、调试与部署 | [无线调试](skill/esp-agentkit/SKILL.md#wireless-debugging)、[应用部署](skill/esp-agentkit/SKILL.md#deploy-an-application) |
| 故障恢复 | [登录重试](skill/esp-agentkit/SKILL.md#retry-a-lan-login)、[应用恢复](skill/esp-agentkit/SKILL.md#recover-an-application)、[USB 恢复](skill/esp-agentkit/SKILL.md#recover-over-usb) |
| 验证与交付 | [实板验证](skill/esp-agentkit/SKILL.md#verify-on-the-selected-board)、[验收进度](docs/architecture-and-development.md#分阶段开发与验收) |

## 开发约束

- **用户协作。** 用户描述目标、操作实物和确认结果；Agent 负责软件与工具操作，只补问影响实现的歧义。在已有授权内推进，覆盖、删除等动作说明范围与影响，缺少授权时再确认。
- **设备与会话。** 操作前核对目标、接线及影响，不从串口桥或芯片识别推定载板与部署兼容性。同一设备的串口、REPL、部署和复位顺序执行；`info`、`run`、`stop` 会中断应用，连续观察时按 Skill 选择合适方式。
- **凭据与配置。** 按核实信息创建 `project.json`。管理密码与 Wi-Fi 密码分开，凭据不得进入公开仓库、说明文档、命令输出、部署回执或应用快照。业务部署保护联网配置、管理密码、平台启动文件及应用目录外的内容。
- **结果记录。** 部署成功、管理连接恢复、应用启动和用户验收分别记录。未知结果保留未知；`accept` 只记录用户已明确确认的观察，不代替实物测试或沿用旧版本的验收结论。
- **依赖与兼容。** Python 版本由 Skill 内的 `.python-version` 固定；依赖声明在 `pyproject.toml`，修改时同步更新 `uv.lock`。保持跨系统、跨 Agent 的本地文件与 CLI 接口，不依赖专属插件。新增硬件后端分别说明识别、编程、运行、观测和恢复的支持情况。
- **文档与发布。** 定位和架构文档使用中文；面向国际贡献者的公开文档、代码注释、命令帮助和示例说明使用英文，双语 README 保持一致。首次正式发布说明板型实测范围、上游版本、项目及依赖许可证。
- **验证范围。** 按改动运行[相关核心回归](tests/README.md)，跨链路修改再运行完整核心套件；覆盖关键失败恢复，避免逐辅助函数、参数或等价组合重复测试，通过后不无故重跑。
- **基础设施。** 优先本地可复现检查，只有维护者需反复执行具体检查时才引入对应 CI；首版不建设发布基础设施或自动发布流水线。
