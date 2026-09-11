# Knowledge Workflow

面向 Codex 的工程协作与私有知识工作流。帮助你从已有证据出发完成开发和 review，
把可复用结论沉淀到自己的知识库，并在后续任务中检索、读取和验证原文。

**当前为开发候选，正式安装、性能和外部使用者验收仍在进行。**

[English overview](README.en.md) · [安装与恢复](docs/install.md) · [隐私边界](docs/privacy.md) · [架构](docs/architecture.md)

## 你会获得什么

- 通用工程流程：取证、实施、review、验证与交接；普通 Python 项目和嵌入式项目都可适配。
- 私有知识闭环：保存结论、维护索引、语义检索、正文读取、反馈和独立恢复。
- 明确的证据边界：搜索结果、旧报告、源码检查、实机验证分别呈现。
- 可控的安装生命周期：隔离 Python 环境、固定依赖和模型哈希、更新、回退与数据保留。

使用自然语言描述任务即可。日常任务不要求 Entry 登记、结算记录、固定开场或重复发送“继续”。

## 安装入口

v1 目标环境为 Windows 11 x64、标准 CPython 3.14 和 Codex。
初始验证基线是 Python 3.14.2 与 Codex CLI 0.153.4；其他版本按实际兼容记录判断。
先安装并登录自己的 Codex，安装标准 Python 3.14。此项目不提供账号或认证资料。

发行包解压后，在该目录的终端执行：

```powershell
.\install.cmd --dry-run
.\install.cmd --apply
```

安装预览列出程序目录、数据目录、Codex 配置目录，以及本地维护进程的登录启动项。
默认使用独立数据目录；不替换全局 AGENTS、不修改回答模型、provider、PATH 或 ExecutionPolicy。
`--no-startup` 可关闭登录启动；此时需要在登录后手动启动维护 runner。

模型下载受网络环境影响，支持 `--offline --model-source <已有模型目录>`。
程序只接受固定清单中的模型文件，验证哈希后使用。具体命令见安装文档。

## 使用

安装完成后使用终端显示的 `kw.cmd` 完整路径。打开新的 Codex 任务，让客户端发现插件和工具。
先完成一次合成资料的沉淀与原文读回，再接入自己的项目资料。

项目适配通过 `project-init --root <项目目录> --template python --dry-run` 预览。
去掉 `--dry-run` 后只添加受管理的入口区块与项目画像；已有指令和 Git dirty/staged 内容保留。
嵌入式项目可选择 `embedded`，模板不会自动构建、烧录或操作设备。

## 知识和隐私

公开仓库提供代码、规则、模板和合成样例。你的知识正文、原始资料、反馈、索引与缓存留在本机，
不会随工作流发行。被你选中用于回答的知识片段会进入你自己的 Codex 会话。
不要把个人知识目录放进公开源码仓库；不要上传未经审查的诊断日志。

卸载保留知识数据、模型和快照。备份使用 SQLite 一致性副本，并保留反馈引用的历史代次。
恢复到独立目录后核对哈希和正文；模型文件作为单独依赖提供。

## 开发验证

```powershell
python -I -B -X utf8 tests/run.py
python -I -B -X utf8 tools/audit_public.py --history
```

模型自检使用 `tools/semantic_smoke.py --model-dir <固定模型目录>`。
它只创建合成知识，不读取个人语料。源码测试、安装后调用、真实客户端和性能验收分别记录。

原始代码采用 MIT；依赖和模型保留各自许可证，见 [第三方说明](THIRD_PARTY_NOTICES.md)。
