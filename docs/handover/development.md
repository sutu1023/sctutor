# 开发导航

## 请求怎样走

浏览器原生JS → FastAPI鉴权/策略 → SQLite与业务模块 → Models适配器 → 模型服务。
答疑用LangGraph固定状态图：校验→检索→相关性→生成→独立检查→有限重写→保存→SSE发送受检正文。不要把未审草稿流式输出。

| 文件 | 修改场景 |
|---|---|
| src/sctutor/app.py / cli.py | API、命令、单worker启动 |
| config.py / models.py / providers.py | 配置、公共HTTP、协议扩展；密钥不能进前端 |
| auth.py / policy.py | 演示身份/归属、教师禁答/作业/L5限制 |
| store.py | SQLite结构及兼容迁移；先备份，不删除旧库解决迁移 |
| tutor.py / rules/ / prompts.py | 分级状态、模型规则、生成/检查分离 |
| pptx.py / retrieval.py | 解析、增量原子替换、BM25+向量RRF、别名和去重 |
| course_files.py / rendering.py / vision.py | 上传安全、资料授权、PowerPoint与图像说明 |
| processing.py / knowledge.py | 有界处理、知识/别名/关系抽取与审核 |
| quiz.py / learning.py | 四题型、评分、BKT与复测，禁止执行学生代码 |
| analytics.py / reporting.py / websearch.py | 匿名统计、日期CSV、Tavily |
| static/ | app答疑、admin题审、learning小测、report报告、preview资料等 |

## API从哪里查

运行后打开 http://127.0.0.1:8000/docs 查看OpenAPI（准确字段以此为准）。主要入口：

- `/api/auth/login|me|logout`：teacher/student演示选择，不是生产账号系统。
- `/api/sessions`、`/api/sessions/{id}`、`/turns`：会话/历史/重命名/删除；POST turns用SSE。批量删除为 `/api/sessions/delete`。
- `/api/turns/{id}/citations/{page_id}`：受等级、归属、策略与来源版本限制。
- `/api/courses`、`/api/materials/{anchor}`及image/file：课程范围与受控资料。
- `/api/quizzes`、`/api/quiz-items/{id}/answers`、`/api/reviews`：小测及复测。
- `/api/report`及export：本人报告；测验中受限制。
- `/api/admin/*`：教师专用；含upload/ingest/enhance/pipeline/processing、知识与题审、policy、overview及export。
- `/api/policy`：公开给当前用户的安全能力摘要，不含密钥。

改API时更新输入校验和权限测试；不要只改隐藏按钮。源码里已有create_app(settings,store,models,retriever)注入入口，适合离线测试。线程/多进程不能绕单writer/流水线锁。

## 提交前

按[验证指南](verification.md)运行离线回归。新功能写功能清单和测试，记录真实结果，不复制旧数字。保持受管AGENTS区块；任何Agent都先读根AGENTS和本目录索引。Comet可选，见[续接](comet.md)。
