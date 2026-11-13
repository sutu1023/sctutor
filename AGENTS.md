# 开发入口

先读 README.md 和 docs/handover/README.md。Python3.12/uv，FastAPI/LangGraph/SQLite/原生前端；开发不要求使用某个AI Agent或Comet。

- 默认只做离线验证：`uv run pytest -q`、`uv run ruff check src tests scripts`、`uv run python scripts/check_js.py`。不要无授权调用收费模型。
- 不读取/打印私人.env值，不提交课件、派生资料、学习数据库或密钥；测试用隔离合成数据。
- 保持生成后检查、角色归属、教师策略、测验限制与失败闭锁；单worker，不把演示账号当生产认证。
- 公开交接用scripts/export_handover.py白名单快照，不自动提交/push/重写历史/清理用户数据。
- Comet是可选工作流。安装可用且存在工作流意图时遵循下面恢复协议；未安装且普通开发任务不要求Comet时，按上述开发说明工作，不伪造状态。

<comet-ambient-resume>
<!-- Managed by Comet. Edits inside this block may be replaced by comet init/update. -->
<!-- Contract: comet.resume_probe.v2 -->

## Comet Ambient Resume

在这个仓库中，开始处理需要改动或调查的任务前，如果可能存在活跃 Comet workflow，把当前用户请求传入只读探针：`comet resume-probe . --stdin --json`。

- 如果用户通过宿主明确调用任意 Comet Skill（例如 `@comet`、`/comet`、`@comet-native` 或 `/comet-hotfix`），显式调用优先于本恢复协议；不要运行 resume probe，直接进入被调用的 Skill。
- 如果用户通过宿主明确调用的是非 Comet 的 Skill 或斜杠命令，任务意图已由该调用明确：不要运行 resume probe，直接执行该 Skill。
- 如果你正在 Comet 流程内（包括正在等待用户回复你在流程中提出的问题），不要运行 resume probe；把这类回复（例如方案/选项选择）当作当前 change 的继续，直接按用户的选择推进。
- 只信任返回的 `workflow`、`skill` 和 `entrySource`；它们只由项目配置或无配置兼容回退决定。不得扫描或切换另一套 workflow。
- 如果 probe 返回 `auto_resume`，简短说明选中的 active change，并进入 `nextCommand` 指向的永久入口。不要把状态命令当作恢复入口直接推进。
- 如果 probe 返回 `ask_user`，只问一个简短问题并等待用户回复。
- 如果当前请求未明确调用 Comet Skill，且 probe 返回 `out_of_scope` 或 `none`，不要进入 Comet workflow。
- `out_of_scope` 或 `none` 只表示不要因为这个新请求进入 Comet workflow；它绝不表示要暂停或退出一个已在进行的 Comet 流程。
- 如果配置或状态无效且没有 `nextCommand`，停止并报告原因；不要猜测另一个 workflow。
- 不能只因为存在 active change 就把无关任务挂到该 change。Native 的未提交改动由 Native 入口检查，不由探针自动归因。
</comet-ambient-resume>
