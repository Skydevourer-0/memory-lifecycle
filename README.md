# Memory Lifecycle

持久化知识记忆引擎。记忆文件是**纯 Markdown**（零 frontmatter），元数据由脚本管理的 `metadata.jsonl` 持有。

## 解决的问题

知识记忆的元数据容易漂移。模型手写 YAML frontmatter 会编造字段、忘记更新、绕过校验。
archived-memory-lifecycle 用 Class ①② 警告 + exit-2/3/4 gate 反复打补丁，越打越重。

**Memory Lifecycle 提供：**
- 元数据与正文分离 — 模型只写 Markdown body，`metadata.jsonl` 由脚本独占
- CLI 门禁 — `--set-metadata` 接受 JSON heredoc，脚本校验字段后原子写入
- 双层召回 — 热榜（CLAUDE.md / MEMORY.md 自动加载）+ 温层（grep INDEX.md）
- PostToolUse hook — `Write` / `Edit` / `MultiEdit` 后自动触发 `sync` + `hint`
- 引用图 — 最多 10 条引用，脚本校验完整性，支持 `global:` 跨 scope

## 安装

```bash
python3 $HOME/.claude/skills/memory-lifecycle/scripts/install.py
```

- 创建 `~/.claude/global/memory/`
- 在 `~/.claude/CLAUDE.md` 中注入 `<!-- memory-index:start/end -->` 标记
- 注册 PostToolUse hook：`Write|Edit|MultiEdit` → 自动 `sync` + `hint`
- 项目 MEMORY.md 标记在首次 sync 时惰性创建

## 使用

```bash
# 定义命令缩写
SM="python3 $HOME/.claude/skills/memory-lifecycle/scripts/memory-sync.py"

# 写一条记忆（纯 Markdown，无 frontmatter）
Write ~/.claude/global/memory/网络排查.md
```

Hook 自动 sync，或手动：

```bash
$SM sync
```

新文件创建 stub，提示设置元数据：
```
1 new memories awaiting metadata. Run $SM --hint <slug> for each.
```

```bash
# 查看建议（headings、已有引用、可选 slug）
# hook 自动触发时 slug 从 stdin 提取；手动使用需传 slug
$SM hint 网络排查

# 一次性写入元数据
$SM --set-metadata 网络排查 <<'EOF'
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
| `description` | 是 | >= 20 字符，不在黑名单（TBD/TODO/placeholder/WIP/draft/待补充），非模板 | 2 |
| `read_when` | 是 | 1–8 条，每条 >= 2 词或 >= 10 字符，无停用词短语 | 2 |
| `references` | 否 | 最多 10 条，不能自引用，目标必须存在。`global:` 跨 scope | 1 |

`--set-metadata` 失败不写盘，成功自动 sync。

## 召回

- **热榜**：高分记忆自动写入 CLAUDE.md（全局）/ MEMORY.md（项目）的 `<!-- memory-index -->` 块，CC 自动加载
- **温层**：grep INDEX.md 中的 `read-when` 短语，按需读取

## 命令

```
$SM sync                             # 全量同步
$SM hint [slug]                      # 元数据提示（hook 中 slug 从 stdin 提取）
$SM set-metadata <slug> <<'EOF'      # 批量写入元数据（成功自动 sync）
$SM delete <slug>                    # 删除 + 清理引用 + 重建
$SM audit                            # 结构审计（孤立节点、单向边）
$SM display [--view graph|stats|timeline|usage|all] [--scope global|project|auto] [--exclude slug1,slug2] [--out <file>] [--no-mermaid]   # 只读:输出可贴 Feishu 的可视化素材
```

## 对外展示

`display` 命令将记忆库真实数据转化为可粘贴 Feishu 文档的可视化素材(四视图):

- **知识图谱**(`--view graph`):mermaid `graph LR`,节点=记忆 slug,边=引用关系。孤立节点圆角,枢纽节点(入度≥3)圆角矩形加粗。
- **全景统计**(`--view stats`):记忆总数/引用边数/枢纽数/覆盖率/Top 5 热榜等统计表格。
- **积累时间线**(`--view timeline`):mermaid `timeline`,按文件 mtime 分月展示持续积累。
- **使用效果流**(`--view usage`):热榜分数分布柱状图 + 真实热榜块(自动召回效果)+ 演示脚本。

脱敏:对外展示前用 `--exclude` 过滤含内部细节的记忆。所有视图只输出 slug 与数值,不输出 description 全文与 .md 正文。

## 存储结构

```
~/.claude/global/memory/          ← 全局
  metadata.jsonl                   ← 脚本写入
  <slug>.md                        ← 纯 Markdown
  INDEX.md                         ← 自动生成

~/.claude/projects/<slug>/memory/  ← 项目
```

`<slug>`（项目）：git 根目录的完整绝对路径，小写，`/` → `-`。
`<slug>`（记忆文件）：kebab-case `[a-z0-9]+(-[a-z0-9]+)*`。

Scope 自动检测：`~/.claude/` 下始终全局；否则从 CWD 向上找 `.git` → 项目；找不到 → 全局。

## 依赖

- Python 3.8+（仅标准库：`json`、`os`、`re`、`tempfile`、`argparse`、`datetime`、`unittest`）
- Claude Code（PostToolUse hook）
