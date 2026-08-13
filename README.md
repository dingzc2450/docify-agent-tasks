# docify-scripts

用于管理各类 Markdown 文档中的任务项、Docify 问题记录、重点内容沉淀，以及给 agent 提供便捷脚本入口。

## 功能

- 初始化标准文档目录：`md/tasks.md`、`md/issues.md`、`md/highlights.md`
- 快速追加任务 / 问题 / 重点内容
- 读取并输出某一类文档内容

## 安装（本地开发）

```bash
pip install -e .
```

## 命令用法

```bash
# 初始化文档
python -m docify_scripts.cli init
# 或安装后
# docify-md init

# 添加任务
python -m docify_scripts.cli add task "整理 API 文档" --content "按模块拆分并补充示例"

# 添加问题
python -m docify_scripts.cli add issue "图片渲染异常" --content "在暗色主题下样式错位"

# 添加重点内容
python -m docify_scripts.cli add highlight "发布流程" --content "每次发版前需检查 checklist"

# 查看某一类内容
python -m docify_scripts.cli list task
```

## 数据结构

初始化后默认目录：

- `md/tasks.md`
- `md/issues.md`
- `md/highlights.md`

