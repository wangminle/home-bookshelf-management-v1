# 阶段检查点与复盘（design/checkpoints）

本目录保存特定时间点的评估、审计、验收和复盘材料。它们记录当时发现的问题、修复过程和阶段结论，不自动代表当前系统状态，也不覆盖 `design/plans/` 中的现行规划基线。

| 文档 | 说明 |
| --- | --- |
| [BUG-302-存量库前向迁移修复-20261008.md](./BUG-302-存量库前向迁移修复-20261008.md) | 为已执行旧l3的SQLite库新增前向迁移，保留照片数据／约束及历史ID上界，六项迁移回归通过；真实库未部署 |
| [实体书架位置管理-残留修复二次复核-20261007.md](./实体书架位置管理-残留修复二次复核-20261007.md) | 六项残留补丁二次复核历史；第5节补录无效回执及409刷新失败最终复核通过，前端100项及类型检查通过；含BUG-309变异证据与历史诊断入口 |
| [实体书架位置管理-BUG299-308修复复核-20261007.md](./实体书架位置管理-BUG299-308修复复核-20261007.md) | 十项补丁独立复核：五项原问题仍有残留，另有竞态测试假阳性；附隔离复现材料、全量验证及旧库迁移前提 |
| [实体书架位置管理-M0契约冻结-20261006.md](./实体书架位置管理-M0契约冻结-20261006.md) | LOC-01～03 契约冻结记录（产品语义、API／Scope、技术约束）及实施期契约修订（LOC-05～14、BUG-298 等） |
| [实体书架位置管理-开发完成度复核-20261006.md](./实体书架位置管理-开发完成度复核-20261006.md) | LOC-01～22 阶段实现与验收对照；2026-10-07 补记 BUG-298 已修复，后端位置专项99、前端相关37及构建通过；补录闭环及跨库/真实试点/部署验收仍未收口 |
| [frontend-evaluation-report.md](./frontend-evaluation-report.md) | 2026-08-09 Web UI 修复前综合评估及后续修复摘要 |
| [frontend-audit-2026-08-09.md](./frontend-audit-2026-08-09.md) | 2026-08-09 两轮前端技术、可访问性和设计审计复盘 |

## 临时脚本清理与正式回归入口（2026-10-06）

三个临时脚本已清理，历史复核报告及当时的测试结果保留。后续验证由 pytest 自动收集正式用例，无需单独维护验收脚本。

| 已清理脚本 | 正式测试与保留的检查 |
| --- | --- |
| `repro_pln012_recheck_20261003.py` | [test_chk110_regression.py](../../backend/tests/test_chk110_regression.py)：原 8 个隔离用例及后续事务、撤权与租约回归 |
| `repro_intake_review_20261005.py` | [test_review_20261005_regression.py](../../backend/tests/test_review_20261005_regression.py)：以正确行为断言覆盖 BUG-285/286/288/289，替代旧脚本的缺陷存在断言 |
| `verify_intake_review_20261005.py` | 四项验收并入上述正式测试；补齐密钥回显四种编码、拆分候选人工来源与照片集合、实际线程锁等待和并发后的唯一书目及回执 |

BUG-285 的并发窗口与批内照片归属还由 [test_bug285_completion_regression.py](../../backend/tests/test_bug285_completion_regression.py) 覆盖；BUG-288 的编码组合由 [test_bug288_encoding_regression.py](../../backend/tests/test_bug288_encoding_regression.py) 覆盖。

从仓库根目录运行：

```bash
python -m pytest backend/tests/test_chk110_regression.py backend/tests/test_review_20261005_regression.py backend/tests/test_bug285_completion_regression.py backend/tests/test_bug288_encoding_regression.py -q --tb=short
```

这些测试使用临时 SQLite 库、合成图片、虚构密钥和模拟网络；通过不代表真实模型、生产数据库或 PostgreSQL 已验收，也不关闭台账中的 BUG-292～296。
