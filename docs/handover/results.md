# 本轮验证记录

状态：A1–A10正式验收全部通过，用户已明确接受结果，Comet归档完成。归档记录见 docs/comet/archive/2026-11-12-public-repository-handover/verification.md；公开快照带净化后的 accepted/verification.md。最终快照为 handover-export/final-accepted，发布前仍须完成 publication.md 的人工关卡。

环境：macOS ARM64、Python3.12、uv；Docker Engine 29.9.2 / Compose 5.6.1，Linux ARM64运行镜像。没有使用私人模型密钥、原课程或生产学习库。

| 实际执行 | 结果 |
|---|---|
| 原工作区 `uv run pytest -q` | 147 passed；5项PyMuPDF绑定弃用警告 |
| `uv run ruff check src tests scripts` | 通过 |
| `uv run python scripts/check_js.py` | 全部前端JS语法通过 |
| `tests/browser_smoke.py` | Chrome桌面/390px最终通过；覆盖角色、会话、审核及学习闭环 |
| `tests/prototype_smoke.py` | 通过；合成PDF/模拟搜索，覆盖L5、资料权限、日期CSV、上传/窄屏 |
| `tests/experience_smoke.py` | 通过；草稿、取消、断线/重试、表单/版本、今日任务与窄屏 |
| `scripts/export_handover.py --output handover-export/review-4` | 白名单导出104文件，另有哈希清单；不含旧.git或私人数据；状态待接受 |
| review-4隔离副本 `uv sync --python 3.12 --locked`、pytest、Ruff、JS检查 | 安装成功，147 passed，Ruff/全部JS通过 |
| review-4副本 `sctutor doctor` | 0网络调用；如实提示缺自己的密钥/课件，增强未启用 |
| `scripts/verify_container.py --directory handover-export/review-4 --port 18766` | 实际干净构建、健康/登录、安全拒绝、非root UID10001、宿主loopback端口、干净镜像资产、合成上传及会话重建后保留均通过 |
| 用户接受及归档 | 用户明确接受；Runtime状态done、验收10/10通过、Run completed；未执行Git提交或发布 |
| 测试Compose停止 | 只停止本轮测试容器/网络，未删卷、未清理原数据 |
| 只读Git可达历史检查 | 5提交/44唯一blob，5处疑似私人路径；输出仅提交/blob/行号，无秘密值。旧历史不进入快照 |

首轮测试曾发现文档链接和模拟配置缺项，已修复后重跑。browser_smoke一次在会话改名等待处超时，独立重跑及最终完整回归通过；保留为浏览器时序不确定性，不掩盖该次失败。容器测试脚本初轮受宿主代理及错误字段断言影响，已禁用本地探测的环境代理并改按会话列表核对，最终完整重建验证通过。

未运行：真实LLM/嵌入/Tavily、PowerPoint导出和视觉说明、Windows/Linux桌面浏览器、GitHub托管CI。Linux容器实测不等于所有桌面平台已验证；模拟语义不等于模型永不泄题。不沿用旧236页、16说明或10题评测数字。

启发式扫描不能保证发现全部秘密；许可证/依赖授权、资料权利和密钥轮换仍是公开前人工关卡。没有进行Git提交、push、公开发布、历史重写或收费AI调用。原私有仓库保留旧开发文件，公开交付仅使用审查过的新快照。
