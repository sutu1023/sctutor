# 模型与配置

## 先填自己的服务

复制根目录 `.env.example` 为 `.env`，填写两组变量：

| 配置 | 含义 |
|---|---|
| LLM_BASE_URL / LLM_MODEL / LLM_API_KEY | 生成、检查、分类、抽取、出题、批改使用的服务 |
| EMBEDDING_BASE_URL / EMBEDDING_MODEL / EMBEDDING_API_KEY | 文本向量服务；与生成服务可以完全不同 |
| LLM_PROVIDER / EMBEDDING_PROVIDER | 默认 openai；表示 OpenAI **兼容协议**，不限定 OpenAI 公司 |
| LLM_PROFILE | standard 默认不加供应商特例；DeepSeek需要时选 deepseek-json |
| EMBEDDING_REVISION | 同名模型升级/维度变化时改版本标记，并重新入库 |
| COURSE_DIR / DATA_DIR | 原生路径默认 courses / data，容器固定 /app/courses / /app/data |
| ENABLE_ENHANCEMENT | 默认false。仅macOS PowerPoint可设true |
| TAVILY_API_KEY | 可选联网密钥，填后仍需教师显式开启联网 |
| PORT | 本机访问端口，默认8000 |

兼容接口要求：BASE_URL填到版本根（例如 `https://your-provider.example/v1`），程序追加 `/chat/completions` 或 `/embeddings`。生成必须遵守提示输出JSON对象；嵌入返回有index的有限非零向量。视觉增强额外要求PNG图像输入能力。

OpenCode使用诚实的SC-Tutor标识及稳定会话头；其他主机不发该专属头。确认供应商条款允许教学用途，不通过伪装客户端绕限制。

先运行 `uv run sctutor doctor`，只检查本地配置，不发网络请求。然后按需运行 `uv run sctutor smoke`（**收费连接测试**）。`.env`变更后重启原生进程或重新 `docker compose up -d`。不要贴出完整配置、供应商响应或 `docker compose config` 输出，可能含密钥。

## 本机模型

原生运行可以把服务设为 `http://127.0.0.1:端口/v1`，并显式设置对应 `LLM_ALLOW_LOCAL=true` 或 `EMBEDDING_ALLOW_LOCAL=true`；此时可以不填密钥。只允许localhost/loopback，远程仍须无URL内凭据的HTTPS。不要用此开关暴露公网服务。

容器内localhost是容器自己，不是宿主。默认Compose不开放任意私网明文地址；宿主模型请提供可信HTTPS端点，或直接用原生方式运行本项目。模型不随镜像打包，不自动下载。

## 接非兼容协议

`Models.chat(system, payload, tid=None, purpose=..., image=None)` 返回JSON字典，`Models.embed(texts, tid=None)` 返回同维度向量列表；业务层和检查规则保持不变。

较轻的协议转换可新增可信Python模块，导出 `provider` 对象：

```python
class Adapter:
    def prepare(self, kind, endpoint, body, settings):
        # endpoint只允许当前BASE_URL下的相对路径；密钥仍由公共传输层处理。
        if kind == 'embedding':
            return '/vectorize', {'model': body['model'], 'texts': body['input']}
        return endpoint, body

    def normalize(self, kind, response):
        if kind == 'embedding':
            return {'data': [{'index': i, 'embedding': v}
                             for i, v in enumerate(response['vectors'])]}
        return response

provider = Adapter()
```

配置 `EMBEDDING_PROVIDER=python:your_package.adapter`。模块须可导入，容器需将其纳入自己的源码/依赖后重建。适配器是**可信本机代码**，不是网页可上传的插件；不允许用户问题或模型输出指定模块。
公共传输层保留Bearer凭据、60秒请求预算、有限重试、脱敏及返回值校验。不兼容Bearer认证的协议需要另写完整Models契约实现，再经 `create_app(..., models=...)` 注入；不能通过放松检查“兼容”。回归示例见 `tests/test_handover.py`。更换服务先跑连接与结构检查，再用自己的课程做人工语义验收；任意模型不保证同等效果。
