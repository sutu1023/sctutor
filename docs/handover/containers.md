# 容器部署（本机）

安装Docker Desktop/Engine及Compose v2+。根目录运行：

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail=80 tutor
```

访问 http://127.0.0.1:8000 。空配置可看页面和演示登录，AI需自己的配置。先复制.env.example为.env并填写；Compose只把列出的模型变量注入运行容器，不把.env打进镜像。Docker管理员仍能查看容器环境，不能把Docker权限当成秘密隔离。不要公开inspect/config/log的原始输出。

镜像使用Python3.12/锁定依赖、非root UID10001、单worker。容器内监听0.0.0.0仅为端口转发，宿主端口只绑定127.0.0.1。保留Host/同源/CSP与角色权限，不提供公网认证、反向代理或多worker方案。

## 课件与数据

默认使用两个命名卷：tutor-data（SQLite及运行记录）与tutor-courses（自备PPTX）。首次启动无需准备目录，教师页可上传。上传本身不调用云端，同步/处理才会调用；请在点击前核对配置与费用。容器禁用Office增强，文本仍可处理；含图无文字页不能冒充已理解。

也可新建 `compose.local.yaml`（保持本地，不公开私人路径），覆盖课程挂载：

```yaml
services:
  tutor:
    volumes:
      - ./my-courses:/app/courses
```

```bash
docker compose -f compose.yaml -f compose.local.yaml up -d
```

自行创建my-courses并放课件；Linux bind目录必须允许UID10001读取，若需要网页上传还需写入。不要通过root运行来掩盖权限问题；只对专用课件目录授权，不对整个主目录chmod。数据库目录同理，切勿同时让宿主进程和容器写同一SQLite。

## 停止和更新

```bash
docker compose down                    # 保留卷
docker compose up --build -d           # 重建应用，仍用原来的卷
```

不要把 `down -v` 作为普通停止命令：它会删除命名卷和学习数据！升级前停止服务并备份，见[维护指南](operations.md)。命名卷带Compose项目前缀，换项目名/目录可能看起来像“数据丢失”，先核对 `docker volume ls`，不要新建空库覆盖旧库。

健康检查只访问本地/api/health，不会调用模型；健康不代表模型可用。doctor可用 `docker compose exec tutor sctutor doctor`。PORT改动只改宿主映射，容器仍8000。模型寻址和本机模型限制见[模型配置](models.md)。

浏览器、开发工具、Office不在运行镜像内；CI/原生环境承担离线测试。镜像构建需要下载基础镜像和依赖，不等于收费AI调用；首次下载网络失败应报阻塞而不是通过。
