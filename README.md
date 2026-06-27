# Memory Lifecycle

跨会话知识管理——记录、评分、召回，两 tier 召回 + 热度排序 + 点击反馈。

## 解决的问题

Claude Code 会话结束后，上次踩过的坑、修过的 bug、总结的模式全部丢失。下次遇到同样问题，模型 不知道之前怎么解决的。

**Memory Lifecycle** 提供：
- 4 字段极简写入——模型 一行 `Write` 即可沉淀知识
- 两 tier 召回——热点自动注入上下文，冷门按需 grep
- 评分排序——引用关系 + 新鲜度 + 召回点击反馈
- 自动校验——5 项检查，多余字段静默删除

## 安装

```bash
python ~/.claude/skills/memory-lifecycle/scripts/install.py
```

一步完成：
- 创建 `~/.claude/global/memory/` 目录
- 注入 `CLAUDE.md` 的 `<!-- memory-index -->` 标记块
- 注册 PostToolUse hook（Write/Edit 记忆文件 → 自动同步 INDEX）

幂等——重复运行安全。

## 使用

Scope 自动检测——无需传 `--global`/`--project`。脚本根据当前目录向上查找 `.git` 自动判断。

```bash
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py              # 校验 + 重建 INDEX + 更新热点
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --fix        # + 删除无效引用
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --audit      # 矛盾检测
python ~/.claude/skills/memory-lifecycle/scripts/memory-sync.py --hit <slug> # 记录一次召回命中
python ~/.claude/skills/memory-lifecycle/scripts/remove-memory.py <slug>     # 安全删除 + 引用清理
```

## 两 tier 召回

| Tier | 机制 | 代价 |
|------|------|------|
| HOT | Top-N 高分链接自动注入 CLAUDE.md 标记块 | 0（始终在上下文） |
| WARM | Grep `~/.claude/global/memory/INDEX.md` 匹配 `read-when` | 0（无匹配时） |

命中后运行 `--hit <slug>` 记录召回，+5 分（30 天内有效）。高频记忆自然升温进入 HOT tier。

## 记忆格式

```yaml
---
name: my-topic
description: 一句话摘要
references: []
read-when:
  - 遇到这个场景时能找到这条记忆的短语
  - 另一个会用到的场景
---
```

Body 自由格式。推荐 `### 实体名 — 描述` 结构。

## 评分公式

```
score = 被引用数 × 2.0 + 引用数 × 0.5 + max(0, 10 − 距上次修改天数) + (30天内被命中 ? 5.0 : 0)
```

决定 INDEX.md 排序和 HOT tier 入选。不决定是否召回——`read-when` 匹配决定是否召回。

## 存储结构

```
~/.claude/
├── global/memory/                  ← 全局记忆
│   ├── INDEX.md                    ← 人可读索引（用于 grep 召回）
│   ├── INDEX.json                  ← 脚本缓存（增量 hash 对比）
│   └── <slug>.md                   ← 记忆源文件
├── projects/<project>/memory/      ← 项目记忆
│   ├── INDEX.md / INDEX.json
│   └── <slug>.md
└── CLAUDE.md                       ← 全局 HOT list 注入目标
```

`.md` 是唯一真相源。`INDEX.json` 可重建。`content_hash` 仅存在于 INDEX.json，不污染源文件。

## 文件说明

| 文件 | 用途 |
|------|------|
| `skill.md` | 技能定义 + 完整使用说明 |
| `scripts/memory-sync.py` | 同步引擎（扫描 → 校验 → 评分 → INDEX + HOT list） |
| `scripts/remove-memory.py` | 安全删除工具（引用清理 + INDEX 重建） |
| `scripts/install.py` | 跨平台安装器（注册 hook + 注入标记 + 创建目录） |

## 依赖

Python 3.7+，仅标准库（`json`, `re`, `pathlib`, `hashlib`, `datetime`, `argparse`, `subprocess`, `platform`）。

## 与 [Workflow Checkpoint](https://github.com/Skydevourer-0/workflow-checkpoint#) 的关系

Memory Lifecycle 管理**知识**（长期记忆），Workflow Checkpoint 管理**任务**（暂停/恢复）。两者互补但独立——数据目录分离（`global/memory/` vs `global/workflows/`），脚本分离（`memory-sync.py` vs `task-cli.py`）。
