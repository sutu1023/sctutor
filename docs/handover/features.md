# 已实现功能与验证方法

下面是代码能力清单，不是“所有模型都能正确回答”的承诺。先运行[离线回归](verification.md)；每行测试可用 `uv run pytest -q tests/文件名` 单独跑。人工操作需自己的课件/模型，收费处必须明确同意。

| 功能与入口 | 前提及人工验证步骤 | 预期结果 / 自动化依据 / 限制 |
|---|---|---|
| 教师/学生演示登录 | 空应用分别登录、刷新、退出；学生直接请求管理API | 未登录401、学生管理403；双方学习记录隔离。test_auth.py；不是生产认证 |
| 会话创建/搜索/改名/删除，答疑侧栏 | 新建空草稿不发送，首问才建会话；搜索改名，取消删除，再单条/批量确认删除 | 取消不改库，越权/运行中整批拒绝；不删独立学习。test_sessions.py、test_navigation.py |
| L1–L4阶梯答疑，答疑页 | 有索引及生成服务。新问题→追问→逐级升级→我懂了；重复旧升级 | 服务端决定等级、追问不升级、过期拒绝；定义类可L2起。test_tutor.py、test_learning_api.py；真实语义需人工抽查 |
| L5完整解答，教师辅导策略 | 教师打开完整答案、上限L5；非受限新问题强制L1走完L1–L4 | 只有完整阶梯最新合法轮次能升级；关闭撤回旧访问。test_l5.py；不是严格禁答系统 |
| 生成后检查/SSE | 用模拟拒绝、无效JSON/引用、超时；网页看阶段 | 检查前不发正文，最多3草稿，失败固定反馈；历史不漏草稿。test_api_models.py、test_structured_output.py、test_tutor.py |
| 断线核对/重试/取消等待 | 模拟SSE中断/503，刷新状态再明确重试 | 不自动重发；未知结果复用request_id，服务器去重；不保证供应商停止计费。test_turn_recovery.py、experience_smoke.py |
| 答疑排版/阅读 | 看代码块、复制、滚动历史、反馈与等待计时 | 安全DOM，不执行HTML/链接；阅读历史不强制拉底。experience_smoke.py、test_navigation.py |
| PPTX解析/增量入库 | 自备授权PPTX；parse不收费，ingest需嵌入。重复、修改、删除文件 | 放映顺序/备注正确；重复复用向量，失败单文件原子保旧，删除不映射新页。test_ingest.py |
| 混合检索/课程限定 | 自备索引，限定一份文件提问；空输入及换嵌入 | BM25/向量top20提名、RRF、页聚合、去重、审核别名；配置变化拒混向量。test_ingest.py、test_knowledge.py、test_retrieval_regression.py；不是准确率保证 |
| 教师上传/同步，课件管理 | 上传PPTX后显式同步；测试危险ZIP、宏/外部资源、同名不同内容 | 上传不收费，安全限制拒绝，不覆写不同内容；同步会嵌入。test_course_files.py |
| 原页/邻页/缩略图/整PDF | macOS+Office原生增强；L3+引用或审核知识复习打开依据 | 逐页校验来源/策略/测验，受限文件不整份下载；有可靠坐标才高亮原图。test_rendering.py、test_course_files.py；容器无Office |
| 少字含图页说明 | ENABLE_ENHANCEMENT=true且模型有图像能力，显式enhance | 少于30字含图才生成，机器标签、哈希/缓存，失败不发半套；test_vision.py。纯文本模式不虚构图内容 |
| 知识/别名/前置关系审核 | 教师处理/抽取，人工修改批准驳回撤回 | 未审/失效不参与，关系不能成环，驳回不自动复活。test_knowledge.py |
| 有界流水线与自动复核 | 课件管理一键处理；切换自动出题/自动审题四组合 | 文本模式明确跳过增强；开启增强失败阻塞；独立调用复核，限制批次，进度可恢复。test_processing.py；自动不等于人工保证 |
| 四题型出题/表单版本 | 教师审核知识后生成choice/blank/short/code；改表单保存 | 新pending版本、旧版停用，学生预览无答案；失败留草稿。test_quiz.py、experience_smoke.py |
| 小测答题/提示/二答/交卷 | 已发布题，学生开始小测；首错再答，刷新/重启后继续 | 客观程序判、主观模型判，不执行代码；首错受检L2，终结才发解答；活动测验封资料/答疑，重复交卷幂等。test_quiz.py、test_learning_api.py |
| 小测本地草稿/取消 | 输入不同题，刷新/切账号，取消先返回再确认 | 草稿不计次数，账号隔离，确认取消成功才清；存储失败警告。experience_smoke.py；草稿非加密 |
| BKT掌握/间隔复测 | 完成审核题交卷；看证据和到期任务 | .2/.2/.1/.1参数；先错后对算错；1/3/7天或7天；理解自述不加概率。test_learning.py、test_quiz.py；不是教育诊断 |
| 复习/今日任务 | 复习或小测首页；有真实知识/到期复测 | 到期优先、薄弱前置推荐，无题诚实空态；活动测验优先恢复。test_learning_api.py、experience_smoke.py |
| 学习报告/DAG/CSV | 本人完成练习；筛7/30天/自定义日期，导出 | 当前快照非历史回放；含分母，未知不假0，CSV防公式注入；test_learning.py、test_reporting.py |
| 教师策略/禁答/作业 | 标作业、设关键词/禁答知识，收紧上限再读旧历史/资料 | 服务端统一约束，旧越级API隐藏；已复制内容不能撤回。test_policy.py、test_l5.py |
| 教师匿名概览 | 学生有活动，教师选期间，检查分母与导出 | 仅student匿名聚合、不混teacher，不泄原始问答；零分母暂无数据。test_analytics.py、test_reporting.py |
| 可选Tavily联网 | 自备TAVILY_API_KEY，教师开启且课件不足 | 最多5结果，外部标记，限制URL/费用/重试；失败不靠模型记忆。test_websearch.py；真实搜索本轮不收费验证 |

浏览器综合回归：browser_smoke.py（角色/会话/学习）、prototype_smoke.py（L5/联网/资料/日期/上传/窄屏）、experience_smoke.py（草稿/网络/表单/今日任务）。均为隔离临时库与模拟服务，不修改生产内容。原页单测用合成PDF，不替代PowerPoint实机验收。
