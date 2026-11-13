# 可选：用 Comet 接着开发

不用Comet也可以按README、开发导航和测试开发，不绑定某个AI Agent。

## 带过去的内容

- `.comet/config.yaml`：项目选择Native、中文文档及产物根；不含个人密钥。
- `docs/comet/specs/guided-tutoring/spec.md`：净化后的当前完整目标，不再携带旧个人语料验收段落。
- 本目录的决策、功能清单和验证记录；本轮正式接受并归档后，快照还带 `accepted/brief.md`、`accepted/verification.md`，作为净化后的只读验收归档。
- 根AGENTS：Agent无关开发规则与可选Comet恢复约定。

不带 `~/.comet`、本机会话/锁/日志、`.pi`或其他开发Agent状态。旧开发档案只在原仓库保留；快照中的验收文档是只读交接证据，不是“复制就能恢复旧进程”的状态。导出不会修改原正式产物，也不把净化文档伪装成哈希绑定不变的active Run。

## 新机器怎么做

1. 从可信的Comet官方分发安装CLI及你所用Agent平台的Comet Skill；版本和安装参数按该版本官方文档，不依赖原开发者个人安装路径。先用 `comet --help` 核对公开命令。
2. 在新仓库根运行 `comet workflow resolve . --activate --json`，确认项目路由到Native；若本机未装对应Skill，按官方初始化入口安装，不能假装已经加载。
3. 使用所选Agent的Comet入口，例如 `/comet`，提出新的开发任务；让CLI核对项目状态，再创建新change。历史决定及规格供其调查参考，不将旧验收当新代码的通过证据。
4. 有真正跨设备未完成change时，应由原仓库携带其**未经篡改的正式产物**，用CLI select/status及诊断恢复；本次公开快照不携带未完成Run，不能用本机状态或手改YAML推进。

工作规约：先Shape确认，再Build/Verify；失败/阻塞/未执行不算通过；用户接受结果后才归档。提交、merge、push、PR、清理各自需要授权，不能拿归档授权替代公开发布授权。

遇到版本不兼容先停止并查公开CLI帮助，勿扫描个人路径找内部bundle。运行状态属于本机，正式规格和只读交接证据属于仓库，两者不能互相冒充。
