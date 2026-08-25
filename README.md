# Memory Lifecycle

持久化知识记忆引擎，Claude Code 与 Codex 双端共享同一份数据。记忆文件是**纯 Markdown**（零 frontmatter），元数据由脚本管理的 `metadata.jsonl` 持有。

## 解决的问题

知识记忆的元数据容易漂移。模型手写 YAML frontmatter 会编造字段、忘记更新、绕过校验。

**Memory Lifecycle 提供：**
- 元数据与正文分离 — 模型只写 Markdown body，`metadata.jsonl` 由脚本独占
- CLI 门禁 — `set-metadata` 接受 JSON heredoc，脚本校验字段后原子写入（`os.replace`）
- 双层召回 — 热榜（全局 CLAUDE.md + AGENTS.md 自动加载；项目 SessionStart 注入 HOTLIST.md）+ 温层（grep INDEX.md）
- PostToolUse hook — Claude `Write/Edit/MultiEdit` 与 Codex `apply_patch` 后自动 `sync-and-hint`（软提示，exit 0，不阻断）
- SessionStart hook — 双端 `startup|resume|clear|compact` 注入项目热榜
- 引用图 — 最多 10 条引用，脚本校验完整性，支持 `global:` 跨 scope

## 数据目录（双端 SSOT）

```
~/.cc-switch/memory/
  global/                        ← 全局记忆
    <slug>.md                    ← 纯 Markdown
    metadata.jsonl               ← 脚本写入
    INDEX.md                     ← 自动生成
  projects/<project-slug>/       ← 项目记忆
    HOTLIST.md                   ← 项目热榜（SessionStart 注入）
```

项目 slug 统一算法：git root `realpath` → 小写 → 非 `[a-z0-9]` 替换为 `-` → 折叠连续 `-`。
`C:\Users\a\proj` → `c-users-a-proj`。

## 安装

```bash
# Claude Code
python $HOME/.cc-switch/skills/memory-lifecycle/.claude/install.py

# Codex
python $HOME/.cc-switch/skills/memory-lifecycle/.codex/install.py
# 然后运行 /hooks 批准注册的命令;重装(命令路径变化)后需重新批准

# 自动检测环境(装了哪端装哪端)
python $HOME/.cc-switch/skills/memory-lifecycle/scripts/install.py
```

- 创建 `~/.cc-switch/memory/global/` 与 `projects/`
- Claude：注册 PostToolUse（`Write|Edit|MultiEdit`，无 pathPattern）→ `sync-and-hint`；SessionStart（`startup|resume|clear|compact`）→ `session-start`
- Codex：注册 PostToolUse（`apply_patch`）→ `sync-and-hint`；SessionStart → `session-start`（合并进 `hooks.json`，不覆盖其它条目）
- 全局热榜骨架（`~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md`）由首次全局 sync 惰性创建
- **不改 `autoMemoryEnabled`**，原生 auto-memory 保留并行工作

> cc-switch 同步副本：`~/.claude/skills/` 是 cc-switch 的副本，改造后需在 cc-switch 中
> 重新同步该技能（本目录是 SSOT，直接改这里即可）；`~/.codex/skills/` 为符号链接，自动生效。

## 使用

```bash
# 定义命令缩写
SM="python $HOME/.cc-switch/skills/memory-lifecycle/scripts/memory-sync.py"

# 写一条记忆(纯 Markdown,无 frontmatter),必须用 Write/Edit/MultiEdit 或 apply_patch
# ~/.cc-switch/memory/global/网络排查.md
```

Hook 自动 sync + 软提示，或手动：

```bash
$SM sync
```

新文件创建 stub，提示设置元数据。

```bash
# 查看建议(headings、已有引用、可选 slug)
$SM hint 网络排查

# 一次性写入元数据
$SM set-metadata 网络排查 <<'EOF'
{
  "description": "网络问题排查记录，包括 DNS 超时和 MTU 配置。",
  "read_when": ["网络不通", "DNS 超时", "排查网络问题"],
  "references": []
}
EOF
```

成功后自动重建 INDEX + 热榜。

## 字段

| 字段 | 必填 | 校验 | Exit |
|------|:--:|------|:--:|
| `description` | 是 | >= 20 字符，不在黑名单（TBD/TODO/placeholder/WIP/draft/待补充/记住/记一下/重要/备忘/笔记/总结/概述/相关信息），非模板（含中文模板） | 2 |
| `read_when` | 是 | 1–8 条，每条 >= 2 词或 >= 10 字符，无停用词短语；重复短语仅告警不阻断 | 2 |
| `references` | 否 | 最多 10 条，不能自引用，目标必须存在。`global:` 跨 scope | 1 |

`set-metadata` 失败不写盘，成功自动 sync。

## 召回

- **热榜（HOT）**：
  - 全局：高分记忆自动写入 `~/.claude/CLAUDE.md` 与 `~/.codex/AGENTS.md` 的 `<!-- memory-index -->` 块，双端自动加载
  - 项目：sync 生成 `HOTLIST.md`（纯条目、无注释、≤1200 字符），SessionStart hook 注入会话
- **温层（WARM）**：grep INDEX.md 中的 `read-when` 短语，按需读取

已知限制（Codex）：`~/.codex/AGENTS.override.md` 存在时优先于 `AGENTS.md`，热榜失效；
AGENTS.md 合并上限 32 KiB（热榜约 1200 字符，影响极小）；`/clear` 后全局热榜需下次启动恢复。

## Hook 行为（v2.2 变更）

命中记忆文件 → **exit 0 + additionalContext 软提示**（不阻断、无 emoji、无 🔴 REVIEW）。
metadata 为空（刚 sync 的 stub）→ 提示"元数据待补（set-metadata）"。命中多个文件最多 3 条提示。
Delete 分支 → 删除 + 清理引用 + 重建 INDEX 与热榜。基础设施文件（INDEX/MEMORY/README/HOTLIST）→ 跳过。
守卫只认 `~/.cc-switch/memory/` 前缀；原生 auto-memory 路径与无关文件 → no-op exit 0。
任何异常 → stderr + 空 stdout + exit 0（fail-open）；stdin 不完整 JSON → 空输出 exit 0，不 fallback 到 cwd scope。

## 命令

```
$SM sync                             # 全量同步
$SM sync-and-hint                   # PostToolUse hook:sync + 软提示(exit 0)
$SM hint [slug]                      # 元数据提示(hook 中 slug 从 stdin 提取)
$SM set-metadata <slug> <<'EOF'      # 批量写入元数据(成功自动 sync)
$SM delete <slug>                    # 删除 + 清理引用 + 重建
$SM audit                            # 结构审计(孤立节点、单向边)
$SM display [--view graph|stats|timeline|usage|all] [--scope global|project|auto] [--exclude slug1,slug2] [--out <file>] [--no-mermaid]   # 只读:输出可贴 Feishu 的可视化素材
$SM session-start                    # SessionStart hook:注入项目 HOTLIST.md
$SM migrate                          # 一次性迁移旧 ~/.claude 数据(幂等,已存在跳过)
```

## 迁移（可选）

`$SM migrate` 将旧布局迁入 `~/.cc-switch`（幂等、已存在跳过、不覆盖）：

- `~/.claude/global/memory/*` → `~/.cc-switch/memory/global/`（正文复制 + metadata.jsonl 合并）
- `~/.claude/{global,projects/<slug>}/workflows` → `~/.cc-switch/workflows/...`
- **不迁移** `~/.claude/projects/<slug>/memory/`（原生 auto-memory 地盘）

## 对外展示

`display` 命令将记忆库真实数据转化为可粘贴 Feishu 文档的可视化素材(四视图):

- **知识图谱**(`--view graph`):mermaid `graph LR`,节点=记忆 slug,边=引用关系。孤立节点圆角,枢纽节点(入度≥3)圆角矩形加粗。
- **全景统计**(`--view stats`):记忆总数/引用边数/枢纽数/覆盖率/Top 5 热榜等统计表格。
- **积累时间线**(`--view timeline`):mermaid `timeline`,按文件 mtime 分月展示持续积累。
- **使用效果流**(`--view usage`):热榜分数分布柱状图 + 真实热榜块(全局双读 CLAUDE.md/AGENTS.md,标注来源)+ 演示脚本。

脱敏:对外展示前用 `--exclude` 过滤含内部细节的记忆。所有视图只输出 slug 与数值,不输出 description 全文与 .md 正文。

## 依赖

- Python 3.8+（仅标准库：`json`、`os`、`re`、`tempfile`、`argparse`、`datetime`、`threading`、`queue`、`shutil`、`unittest`）
- Claude Code（PostToolUse + SessionStart hook）或 Codex（hooks.json）