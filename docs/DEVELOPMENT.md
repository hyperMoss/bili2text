# 开发文档

## 当前目标

这个仓库正在从旧版脚本式实现迁移到一个更稳定的应用结构：

- `CLI` 作为核心入口
- `Window / Web / Server` 作为 Feature
- `Provider` 与核心逻辑解耦
- 依赖管理与开发流程统一到 `uv`

## 目录结构

```text
src/b2t/
  cli.py            CLI 入口
  bootstrap.py      首次启动向导
  user_config.py    本地配置读写
  pipeline.py       核心转写流程
  factory.py        Provider / Downloader 组装
  downloaders/      下载实现
  transcribers/     转写 Provider
  templates/        Web 与桌面共用的 Jinja 模板
  static/           共用的本地样式与交互脚本
  window_app.py     浏览器与 pywebview 窗口启动、服务生命周期
tests/              自动化测试
assets/             logo、截图、favicon 等素材
archive/            旧版脚本与历史文件
```

## 本地开发

### 环境准备

```bash
uv sync --extra whisper --extra web
```

验证桌面入口时改用 `uv sync --extra whisper --extra window`。该 extra 包含 Web 依赖。

如需 SenseVoice / 火山引擎：

```bash
uv sync --extra sensevoice --extra volcengine
```

更推荐直接用 Bootstrap 管理组合环境：

```bash
uv run bili2text bootstrap
uv run bili2text bootstrap --sync-only
```

### 常用命令

```bash
uv run bili2text --help
uv run bili2text doctor
uv run bili2text bootstrap
uv run bili2text tx "<bilibili-url>"
uv run bili2text ui
uv run bili2text win
pytest -q
```

## 配置机制

本地配置默认保存在：

```text
./.b2t/config.json
```

当前配置主要包含：

- `language`
- `enabled_providers`
- `enabled_features`
- `default_provider`
- `default_model`
- `sensevoice.*`
- `volcengine.*`

## Provider 约定

新增 Provider 时，尽量遵守下面的边界：

- 下载逻辑不要塞进 Provider
- Provider 只负责“把音频转成文本”
- 配置项写入 `AppConfig`
- 创建逻辑统一走 `factory.py`
- 真实工作流统一走 `pipeline.py`

## Feature 约定

`Window / Web / Server` 都应该尽量只做壳层：

- 接受输入
- 调用统一 Pipeline
- 输出结果
- 不复制核心业务逻辑

`web` 和 `window` 必须调用同一个 `web.create_app` 和 `TaskService`。桌面窗口仅加载本地 HTTP 页面，不另写转写表单或执行 Pipeline。新增界面功能应放在共用模板与 `static/` 中。

统一入口的回归检查：

```bash
uv run --no-sync pytest tests/test_cli.py tests/test_web.py tests/test_window_app.py tests/test_bootstrap_env.py -q
```

窗口测试使用真实 HTTP 服务验证启动与关闭，原生窗口驱动使用替身。桌面 GUI 仍需实际启动验证。转写接口测试使用固定结果，不代表模型、网络下载或 B 站账号已通过验证。

## 提交建议

当前分支偏向小步提交：

- 一个功能点一个 commit
- 一个修复点一个 commit
- 改结构和改行为尽量拆开
- 提交前先跑相关测试

## 清理策略

- 根目录尽量只保留真正的项目入口和元信息
- 历史脚本放进 `archive/`
- 项目素材放进 `assets/`
- 兼容入口暂时保留 `main.py` 和 `window.py`

## 后续建议

接下来继续推进时，优先顺序建议为：

1. 继续完善 Provider 抽象与错误处理
2. 补充更多自动化测试
3. 为 Server Mode 增加更稳定的部署说明
4. 让 Web UI 继续保持结构简单，等待设计稿接管

## 数据完整性检查（只读）

在转写或恢复批量任务前，运行：

```bash
uv run --no-sync bili2text audit --workspace .b2t
```

该命令不会初始化、修改或修复数据库。输出 JSON 包含文件、元数据、文字稿版本和 SQLite 关联检查；发现异常返回退出码 1。不要在未核实来源身份时自动合并同名文件。需要修改数据库时，先停止任务，并使用 SQLite backup API 备份 WAL 一致性快照。旧版 metadata 缺少摘要时，以数据库版本摘要检查文件。
