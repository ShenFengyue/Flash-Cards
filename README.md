# 社交直觉闪卡（桌面版）

双击 `社交直觉闪卡.pyw` 打开的本地桌面小工具：练社交直觉的闪卡应用。
不依赖浏览器，不需要命令行参数，数据全部保存在程序文件夹内的 `data` 目录。

## 目录结构

```
社交直觉闪卡.pyw      双击入口（pythonw 运行，无控制台窗口）
app/                  程序代码
  paths.py            路径与本地设置（settings.json / drafts.json）
  grading.py          匹配分数 → FSRS 评分的固定映射（集中配置）
  storage.py          SQLite 数据层：卡片、作答记录、调度状态初始化与读取
  scheduling.py       FSRS 封装 + scheduling 表唯一的更新入口
  session_base.py     两种练习模式共用的流程骨架（不含任何调度写入）
  free_session.py     自由练习流程（不导入 scheduling）
  review_session.py   算法复习流程（确认后才写入调度）
  ai.py               DeepSeek 调用与严格结果校验
  backup.py           JSON 导入导出与格式校验
  ui.py               Tkinter 界面
vendor/               随程序分发的成熟 FSRS 库（py-fsrs，MIT）与 typing_extensions
tests/                标准库 unittest 测试
data/                 运行后自动生成：数据库、设置、草稿
```

## 依赖

程序只使用 Python 标准库（tkinter、sqlite3、urllib、threading 等）加上随程序分发的 py-fsrs，
**不需要 pip 安装任何东西**。要求 Python 3.10 或更高版本（在 Python 3.13 上验证）。

## 运行

双击 `社交直觉闪卡.pyw`。也可以用命令行启动（便于看日志）：

```powershell
python 社交直觉闪卡.pyw
```

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖：评分映射边界、卡片增删改查与搜索筛选、初始卡片只写一次、
正式复习写入与幂等、**自由练习前后 FSRS 数据逐字段不变**（含重复练习与 AI 失败情形）、
AI 结果校验与错误分支、导入导出与格式校验、以及源码级的隔离检查。

## 数据与备份

- `data\flashcards.db`：SQLite，包含 cards / scheduling / review_logs / attempts / meta 五张表，带 schema_version。
- `data\settings.json`：API 密钥、模型、思考模式（密钥只在本机）。
- 界面里「全部闪卡 → 导出 JSON / 导入 JSON」用于备份与迁移；导入前会先校验格式，
  校验失败不会改动任何本地数据；覆盖导入需要额外勾选确认。

## 隔离设计（为什么自由练习不会影响复习计划）

1. 数据分表：内容在 `cards`，调度在 `scheduling`，正式复习日志在 `review_logs`，作答在 `attempts`（用 mode 区分 free / review）。
2. 代码路径：`app/free_session.py` 与 `app/session_base.py` 都不导入 `app.scheduling`，自由练习没有写入调度的代码路径。
3. 唯一写入入口：`app/scheduling.py` 的 `commit_formal_review` 会拒绝自由练习记录、拒绝没有有效分析结果的记录、
   拒绝与固定映射区间不一致的评分，并用 attempt_id 做幂等保护。
4. 自动化测试直接比较练习前后的调度状态、正式复习日志与卡片内容，逐字段断言完全一致。

## 许可

本程序使用 py-fsrs（MIT License，Copyright (c) 2022 Open Spaced Repetition），许可证文本见
`vendor/fsrs-6.3.2.dist-info/licenses/LICENSE`。
