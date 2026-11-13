# 验证指南

## 1. 不花模型费用的回归

```bash
uv sync --python 3.12 --locked
uv run pytest -q
uv run ruff check src tests scripts
# 安装Node后，逐个检查所有前端JS（跨平台脚本）
uv run python scripts/check_js.py
uv run sctutor doctor
```

测试使用合成资料/模拟模型，无需.env和原开发者PPT。pytest不触发live脚本；不要把本机生产库当测试库。GitHub CI执行同类离线检查；依赖安装仍需网络。

有Chrome/Chromium时运行：

```bash
uv run python tests/browser_smoke.py
uv run python tests/prototype_smoke.py
uv run python tests/experience_smoke.py
```

可以设置CHROME_BIN为浏览器可执行文件完整路径；找不到浏览器是blocked，不是通过。脚本使用临时库和模拟服务，不需Office/模型密钥。

## 2. 容器实测

使用清理快照目录，无.env时构建启动，查看健康；teacher/student登录，学生管理API拒绝。上传合成课件、创建会话，重建容器后仍可看到它们。不要在生产卷做破坏测试，不用down -v清理用户数据。步骤见[容器部署](containers.md)。也可在原仓库运行以下隔离脚本（仅构建下载，不调用AI；会创建并保留专用合成数据卷）：

```bash
uv run python scripts/verify_container.py --directory handover-export/你的快照目录 --port 18766
```

该目录不得有.env；脚本校验非root、镜像无私人资产、宿主端口只loopback、安全拒绝、上传及会话在重建后保留，最后只停止自己的测试项目，不删除卷。

## 3. 自备模型与课件的验收（会收费）

先取得授权和费用预算，再执行：

```bash
uv run sctutor parse
uv run sctutor smoke
uv run sctutor ingest
uv run sctutor smoke --question '这里换成你的课程问题'
uv run sctutor evaluate --questions /path/to/your-questions.json
```

评测JSON数组格式：

```json
[{"question":"你的问题","expected":[{"file":"your-course.pptx","pages":[1,2]}]}]
```

自行写实际课件问题及人工标准页，不携带原作者的课程评测题/答案。evaluate只测检索命中，不证明答疑正确；基于自己的课程从答疑走阶梯，核对引用/禁答/JSON/语义；再走知识审核、四题型小测、主观批改与报告。缺少题时先人工审核，不批准随便生成的生产内容。仅在临时库做破坏性测试。

原页/视觉须在macOS+Office原生环境显式开启增强，运行 `uv run sctutor enhance`，核对实际页数、源哈希和说明；不要强制要求旧236页/16份说明。联网用自己的Tavily配置单独验收。容器文本测试不能替代Office实际导出。

## 4. 怎样记结果

更新[验证记录](results.md)：环境、命令、真实结果、使用模拟还是真实服务、未执行原因。通过/失败/阻塞/未运行分开。没有密钥、Office、配额或网络，不能写“通过”。本轮不承诺出处90%、泄题5%、20秒或教育增益，不把固定小集成绩外推全课程。
