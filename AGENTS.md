# 项目协作约定 · AGENTS.md

本文件为 Codex（及兼容 agent）在本项目中的协作约定，会话开始时自动加载。

## 会话结束任务同步（必须）

每次会话结束前，若本次涉及任务完成——包括但不限于 bug 修复、功能开发、代码审查、测试数据准备、文档更新、配置运维——必须：

1. 把新增条目与状态变更写入根目录 `task-list.md`，与实际进度同步；
2. 在本次最后一条回复中告知用户：记录/更新了哪些条目（ID + 简述）。

若本次未涉及任何任务完成，无需操作也无需提示。该时机由 `.Codex/settings.json` 的 `Stop` hook 保证触发（每会话一次）。

记录规范：写入 `task-list.md` 时只追加、ID 不复用；动作字段仅用 8 个枚举（修复 / 开发 / 优化 / 调整 / 规划 / 检查 / 文档 / 运维）；时间为 `YYYY-MM-DD HH:MM` 本地 24 小时制，未完成填 `-`；优先用 `python3 ~/.Codex/skills/task-list-initialization/scripts/task_list_cli.py add` 写入并跑 `check` 校验。
