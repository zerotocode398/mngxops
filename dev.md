
## 背景
D:\PyCharm\联动优势\works\django-labs\mngxops 项目是 Django 框架开发的 Nginx 集中式管理平台。
现需要再 ngxops 下重写一份此项目， Django 框架调整为 FastApi + Jinja2 + JQuery 等技术栈。

## mngxops 项目说明
- 鉴于使用 Django 开发时，对 Django 框架不熟悉，可能存在冗余/垃圾/无效/未用代码。
- 功能实现的方式/策略/性能方面可能有不足。

## ngxops 项目说明

- 技术栈：Python、FastApi、Jinja2、JQuery、JSON、Sqlite3

## ngxops 开发环境
- Python 版本：3.9
- 本机默认 venv 为 `D:\PyCharm\联动优势\works\django-labs\venv3\Scripts\activate`

## 可选环境配置

- `MNGXOPS_RELEASE_BACKUP_DIR`：发布时远程备份文件根目录；默认 `/opt/app/mascloud/ansible/mngxops`，实际按节点主机名创建子目录。

## ngxops 开发规范
- 1:1 复刻 mngxops 项目
- PEP 风格；注释与函数 docstring 使用中文，函数须有一句话说明。
- 函数单一职责；避免重复、过长函数与过深嵌套。PEP 风格；注释与函数 docstring 使用中文，函数须有一句话说明。
- UI 尽量保持一致（重要，因为我不懂产品，UI是调整了很久）
- 相同的功能的代码可以抽取为公共部分，避免重复造轮子。
- 注意项目结果的规范性
- 注意fastapi 接口文档有效性（后续功能测试可能会依赖）
- mngxops 可能存在冗余/垃圾/无效的代码，需要有识别能力
- mngxops 可能存在性能方面访问，在1:1复刻时，可能需要进行条调整

## ngxops 开发策略
- 熟悉 mngxops 后，可以按照功能或模块等粒度进行拆分。
- 拆分后可以分为多批次阶段实现。
- 尽量拆分细粒度，若拆分不细，模型的上下文可能会被压缩导致有问题。
- 拆分后，可以填充至 AGENTS.md 文档里，后续此文件会被作为台账记录，基于此文件进行开发

## ngxops 注意事项
- mngxops 数据可以不要，都是测试数据，不需要考虑数据迁移、保护。
- “开发规范”里的最后 2 条是我预期的，若无法避免可以先按原有方式实现，后续再做调整。
- 历史代码已经全部删除了，且“clear && init repo; ready for fastapi”是初始化仓库的提交记录，之前的提交可以都忽略。
