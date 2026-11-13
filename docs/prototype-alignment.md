# 报告总体设计与五页面功能对齐

来源：`期中报告.pdf` 第4、5章（物理页25–37），并参考第2章用况与第3章检索/BKT定义。功能对齐而非截图逐像素复刻；数字来自本机数据库，原型样例不填入生产记录。

## 功能命名与空白会话

以图5-2至5-7为准：学生导航为「答疑、复习、小测、学习报告」，教师主要导航为「班级概览、辅导策略、题目审核、课件管理」，并保留教师学习入口。菜单与页面标题一致；复习直达现有建议复习，辅导策略/题目审核直达已有对应面板。原型反馈/操作名称采用「我再想想、再给一点提示、我懂了」「再试一次」「保存策略」「确认/驳回」「上传课件」「打开文件」「导出」；CSV/PDF格式、安全/费用提示仍明确保留。课程网盘同步仍按既定本地目录方案命名「同步课程目录」，不伪装远程API。

空白答疑只保留本地草稿，第一次发送才创建持久会话。登录和反复点击答疑不创建空白记录；已有空白会话复用，不自动删除；开始新草稿后切换页面不自动打开旧会话。原有会话/账号归属和显式删除边界不变。`prototype_smoke.py`覆盖双角色命名、直达入口、无会话连续/并发点击、已有空会话重复点击/首次发送复用。

`TAVILY_API_KEY`位于项目根目录`.env`，仅本地编辑后重启；模板在`.env.example`，聊天/前端不接收真实密钥。

## 页面与实现/验收映射

| 报告位置 / 功能 | 实际行为及代码 | 检查证据 |
| --- | --- | --- |
| §4.1 图4-1 分层架构/受控数据 | FastAPI、LangGraph、SQLite；模型只接必要证据，不任意访问磁盘/数据库；`app.py / tutor.py / store.py` | `test_api_models.py / test_tutor.py / test_auth.py` |
| §4.2 图4-2 页级增量 | 放映顺序/文字/备注/图片；哈希、失败旧版与缓存；`pptx.py / retrieval.py` | `test_ingest.py / local_asset_check.py` |
| §4.2 少字含图说明 | 同模型生成，机器标签、空白不虚构；安全PowerPoint私有副本原页；`vision.py / rendering.py` | `test_vision.py / test_rendering.py / local_asset_check.py` |
| §4.2 表4-1 别名/混合检索 | 审核别名扩展、BM25/向量RRF及近重复正文优先；限定课程仍使用原融合算法；`knowledge.py / retrieval.py` | `test_knowledge.py / test_retrieval_regression.py / test_course_files.py` |
| §4.3 表4-2 L1–L4/独立检查 | 服务器选起点/逐级升级、分离模型检查、三份草稿失败闭锁；`tutor.py / rules/tutor.md` | `test_tutor.py / test_l1_citations.py / test_policy.py` |
| §4.3 条件L5完整解答 | 教师最高设L5并允许，当前问题实际依序完成L1–L4、未结束且非作业禁答后才能请求；L5仍检查出处/策略。自适应L2不能冒充L1，可选新问题从L1；`store.py / policy.py / tutor.py` | `test_l5.py / prototype_smoke.py` |
| 图5-1/5-6 联网开关 | 默认关；本地Tavily配置后教师可开启，课件不足才搜索，外部片段单独标记/引用/检查，关闭收回后续访问；`websearch.py / tutor.py / policy.py` | `test_websearch.py / prototype_smoke.py`（模拟服务，不冒充真实Tavily调用） |
| §4.4 图4-5 小测 | 审核非限制题、先答后看、客观/主观批改、首错受检L2和二答，终结后解答/来源；`quiz.py / learning.js` | `test_quiz.py / test_learning_api.py / browser_smoke.py` |
| §4.4 BKT/复测 | 交卷幂等，先错后对仍错误观察，错题1/3/7天/首次对7天；`learning.py / quiz.py` | `test_learning.py / test_quiz.py / browser_smoke.py` |
| §4.5 图4-6 领域模型 | 课程文档/页/知识/前置，账号会话/轮次/引用、题目/作答/掌握/复测；增量迁移L5 CHECK而不丢旧数据；`store.py` | `test_l5.py / test_auth.py / test_sessions.py` |
| 图5-1/5-2/5-3 学生答疑 | 三栏、五级阶梯、三反馈、每轮检查/重写、知识点掌握和候选元数据；`app.js / index.html` | `browser_smoke.py / prototype_smoke.py` |
| 图5-2 课程/专题导航 | 实际文档路径/页数；可限定新问题的检索文档，追问沿用原文档范围，全部课程可复位；不因点导航直接开放低级别正文；`preview.js / retrieval.py / turn_options` | `test_course_files.py / prototype_smoke.py` |
| 图5-1至5-3 相邻页/缩略图/跳页 | 绑定原引用/审核知识上下文，同一现行文档逐页鉴权；限制页不额外公开；`course_files.py / preview.js` | `test_course_files.py / prototype_smoke.py` |
| 图5-3 代码定位/放大 | 用户选择真实原文片段，核验子串；可提取PDF坐标时高亮原图矩形，无坐标明确只定位文字，不伪造高亮；`Materials.page / preview.js` | `test_course_files.py / prototype_smoke.py` |
| 图5-1 打开文件 | 登记原页旁的现行渲染PDF，以附件打开/保存；校验源哈希/清单/页数，全文件任何页有作业/禁答则拒绝；`Materials.pdf` | `test_course_files.py / prototype_smoke.py` |
| 图5-4 学生报告 | 审核DAG/等价列表、概率/未知、期间提问、当前掌握/证据、弱点/前置原因、来源/小测；分页/详情不变；`learning.py / report.js` | `test_analytics.py / test_learning.py / browser_smoke.py` |
| 图5-4/5-6 日期筛选与导出 | 7/30天和自定义≤366天，日历日转[start,end)；期间提问/正确率，掌握为当前快照；UTF-8 CSV同口径/防公式注入；`reporting.py / analytics.py / preview.js` | `test_reporting.py / prototype_smoke.py` |
| 图5-5 小测布局 | 待做/到期、进度、知识点与规则、二答/交卷复测摘要；新增通用资料/搜索均封锁；`learning.js / app.py / Materials.anchor` | `test_learning_api.py / test_course_files.py / prototype_smoke.py` |
| 图5-6 教师概览 | 只student匿名聚合，不混teacher；独立问题去重、平均结束级含L5、到达L4含L5、检查分母/失败分列、疑难与最多20条原因类别，无原始文本或ID；`analytics.py / dashboard.js` | `test_analytics.py / test_reporting.py / browser_smoke.py` |
| 图5-6 策略 | 全局L1–L5/作业L1–L3、完整答案/联网开关、禁答知识/字面关键词；历史/发布/资料/学习入口统一执行；`policy.py` | `test_policy.py / test_l5.py / test_websearch.py` |
| 图5-7 上传/同步 | 教师PPTX受管上传（不覆盖同名不同内容），本地课程目录新增/修改/删除增量同步，可用本机网盘同步文件夹；`course_files.py / app.py / admin.js` | `test_course_files.py / prototype_smoke.py` |
| 图5-7 处理结果/审核 | 真实文件/页数/少字/增量/失败/待审；四类审核队列、依据/编辑/批准/驳回/撤回、两个题目开关和有界流水线；`processing.py / admin.js` | `test_processing.py / test_knowledge.py / browser_smoke.py` |
| 全五页面 角色/响应式/安全文本 | 学生仅学习、教师另管理且保留学习；会话管理、退出清理/异步结果版本校验、390px与键盘/恶意文本；`auth.py / static/` | `test_auth.py / test_sessions.py / browser_smoke.py / prototype_smoke.py` |

## P0/P1 审查优化

| 审查项 | 实现/边界 | 回归 |
| --- | --- | --- |
| 小测其他题草稿丢失 | `experience.js / learning.js`按账号/测验/题目保留输入；刷新恢复自己的未终结题，草稿不算提交。完成题/交卷/确认取消清理；存储失败警告，页面输入保留 | `experience_smoke.py`跨题/刷新/存储失败/提交失败/切账号 |
| 取消误操作 | 可键盘操作确认框，Esc/返回不请求；说明不更新掌握及清草稿，确认成功才取消；提交/取消互斥 | `experience_smoke.py` |
| 答疑阅读与滚动 | 安全DOM实现Markdown子集、代码复制，不执行HTML/链接；检查详情折叠；阅读历史保持位置，显式跳最新 | `experience_smoke.py`恶意标签、代码、滚动 |
| 等待/恢复 | `app.js`阶段/计时、断线核对、明确重试；`turn_requests`事务记录ID，未知请求复用ID，终结失败才允许新调用；停止等待不保证供应商取消计费 | `test_turn_recovery.py / experience_smoke.py`截断SSE、未知/离线、刷新/重试、running锁、停止 |
| 反馈与可见原因 | 最新回答三反馈名称不变，说明级别及禁用原因，服务端阶梯/授权约束保持 | 既有`prototype_smoke.py`阶梯及`experience_smoke.py`反馈位置 |
| 教师JSON门槛 | `question-editor.js / admin.js`四题型字段、就地校验、无答案学生预览、来源/差异与新待审版本；保留其他行草稿与队列筛选分页 | `experience_smoke.py`四题型/失败/版本/局部保持 |
| 今日学习 | `experience.js`复用真实复测与报告，优先到期再薄弱前置/未评估；复习详情/练习/报告，不自动补题；活动测验先恢复 | `experience_smoke.py`空到期/真实到期/跳转封锁 |

草稿和待核对请求分别在本浏览器localStorage/sessionStorage，不是加密隐私区；演示身份可由同设备用户选择。退出清页面/内存但保留本人小测草稿便于恢复，存储不可用不能保证持久化或清理。安全排版仅受检正文，不为流式体验提前发布草稿。

现行规格采用Requirement章节的替代规则；旧MVP限制仅历史兼容，不重新关闭已有L5/联网/教师等能力。此次不修改BKT/检索参数、不纳入P2增强或生产身份，不承诺真人教学收益。

## 运行与安全边界

- 交接不含原课程资产；由开发者自备PPTX，不要求固定页数或说明数量。教师上传和配置目录同步可增加文件，费用和时间按实际发生，不做无人确认的云端处理。
- 本地`.env`配置`TAVILY_API_KEY`，重启服务后教师才可开启联网；不在聊天或前端填写密钥。Tavily仅POST官方HTTPS API，不服务器抓任意URL。问题最小必要文字外发，外部链接非课件且不保证真实性。
- 联网、L5默认关闭；L1–L4不完整作答。L5是用户明确授权的新增能力，不再宣称系统一律不给完整答案。
- 相邻页和整文件会增加可读资料范围，但仍鉴权；含禁答/作业的文档不整份下载。活动小测禁用课程导航/通用资料/报告导出/联网；已终结题仍只能读自身绑定依据。
- 上传每次≤10文件、单≤50MiB、总≤100MiB、ZIP解压≤200MiB/10000项；拒绝宏、外链、重复/危险ZIP路径和符号链接。上传本身无云端调用，随后教师显式同步/处理。
- 教师统计CSV仅匿名聚合；学生CSV只自己的报告，不导出原始会话/草稿/个人作答。概率是简化模型估计，不是教育诊断。

## 明确保留的设计差异

1. 使用LangGraph与有界模型调用实现角色职责，不迁Deep Agents；本机双演示账号不是真实学校/生产身份系统。
2. 同一模型的分离检查不等于独立模型体系或可靠防作弊保证；不发布未审草稿、不执行学生代码，不恢复检查失败整页兜底。
3. 定义/掌握起点只L2；“我懂了”自述不增BKT。原图没有可核验坐标时仅文字高亮并告知，不伪造图中代码框。
4. 网盘同步为本机已同步目录的增量读取，不接远程网盘账号/API；“打开文件”为现行渲染PDF，不向模型上传原PPTX。
5. 不复刻截图示例数字，不提供真实全班人数、原始学生记录后台、150题/C0–C5/ColPali/真人实验。研究准确率/泄题率/5秒首字/20秒目标尚未证明。

## 本轮检查状态

离线单测、Ruff、全部JS语法、旧浏览器烟测及新增`tests/prototype_smoke.py`覆盖产品和服务端边界。浏览器用临时库/模拟LLM与Tavily、来源匹配的合成PDF；不修改生产审核，不冒充真实联网或PowerPoint导出。

历史真实课件验证只针对原开发环境，不随公开快照交接，不算接手环境通过。真实搜索与外部来源语义需用自己的配置验证；离线测试不能证明实际模型效果。当前逐项结果见交接目录的验证记录，正式验收由Comet报告保存。
