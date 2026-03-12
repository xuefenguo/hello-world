# hello-world

OpenClaw 桌面助手项目（第一版可安装可使用）。

## 文档

- [基于 OpenClaw 的桌面助手方案（支持一键部署 / 离线安装 / 图形化配置）](docs/openclaw_desktop_assistant_plan.md)
- [OpenClaw 桌面助手下一步执行清单（已确认需求版）](docs/openclaw_next_step_execution.md)

## 当前能力（v1 完整版）

- 银河麒麟/Ubuntu 类系统可运行（Python 3）。
- 本地 Orchestrator 服务：
  - `/health`
  - `/api/config`（读写）
  - `/v1/config/test`
  - `/v1/chat/completions`
  - `/api/sessions`（会话管理）
  - `/api/snapshots`（配置快照）
  - `/api/diagnostics/export`（诊断导出）
- 图形化页面：状态、聊天、配置三页签。
- 聊天支持 provider 切换：`local` / `cloud`，并支持云端失败自动回退本地。
- 支持会话新建、重命名、删除、导出。
- 支持配置快照保存与恢复、基础日志记录。
- 安装与卸载脚本：`scripts/install.sh`、`scripts/uninstall.sh`。

## 安装与运行

### 方式一：直接运行（开发）

```bash
./scripts/run_step1.sh
```

### 方式二：本地安装（v1）

```bash
./scripts/install.sh
~/.local/bin/openclaw-desktop
```

启动后访问：

- `http://127.0.0.1:8765/`
- `http://127.0.0.1:8765/health`

卸载：

```bash
./scripts/uninstall.sh
```

## 测试

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
