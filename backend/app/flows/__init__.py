"""
多流程配置化平台（docs/10-多流程配置化平台设计.md）

分层：
  model.py      配置元模型 + 全部派生物（字段 / 校验 / 抽取 / 卡片 / 工具 schema）
  hooks.py      命名扩展点白名单与三态生命周期
  validator.py  dry-run 八项校验
  store.py      配置持久化、版本、双人发布、灰度、回滚、审计、Git 备份
  registry.py   已发布配置缓存 + 后端权威意图路由
  service.py    通用运行时业务 API（状态机 / 附件 / 审核）
  tools.py      通用只读四件套
  context.py    请求级上下文（身份 / 槽位 / 版本锁定）
  seed.py       入园配置导出与老数据迁移

框架硬约束：AI 只读、确认后写库、人工审批、PII 打码、附件归属隔离、审计。
"""
