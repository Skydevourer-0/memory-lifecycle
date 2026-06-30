# memory-lifecycle v2.1

持久化知识记忆引擎。记忆文件是**纯 Markdown**（零 frontmatter），元数据由脚本管理的 `metadata.jsonl` 持有。

## 快速开始

```bash
# 安装（一次性）
python ~/.claude/skills/memory-lifecycle/scripts/install.py

# 写一条记忆
Write ~/.claude/global/memory/network-debug.md
```

内容，纯 Markdown：

```markdown
## 问题现象
## 排查过程
## 解决方案
```

Hook 自动运行 `sync-memory`，检测到新文件后提示设置元数据：

```
1 new memories awaiting metadata. Run sync-memory --hint <slug> for each.
```

```bash
# 查看建议
sync-memory --hint network-debug

# 一次性写入元数据
sync-memory --set-metadata network-debug <<'EOF'
{
  "description": "网络问题排查记录，包括 DNS 超时和 MTU 配置。",
  "read_when": ["网络不通", "DNS 超时", "排查网络问题"],
  "references": []
}
EOF
```

完成。记忆已加入 INDEX，后续通过关键词 grep 即可召回。

## Hook 自动同步

安装后，每次用 `Write` / `Edit` / `MultiEdit` 修改 `**/.claude/**/memory/*.md` 文件时，
PostToolUse hook 会自动运行 `sync-memory`。检查工具输出中的 `INDEX.md written` 确认已同步；
若无则手动运行 `sync-memory`。

## 引用其他记忆

在 `--set-metadata` 的 `references` 字段中指定目标 slug：

```json
{ "references": ["other-slug", "global:cross-scope-slug"] }
```

- 最多 10 条，不能自引用，目标必须存在
- `global:` 前缀用于跨 scope 引用全局记忆
- 脚本校验引用完整性，失败拒绝写入

## 存储结构

```
~/.claude/global/memory/          ← 全局记忆
  metadata.jsonl                   ← 脚本写入的元数据
  network-debug.md                 ← 纯 Markdown 正文
  INDEX.md                         ← 自动生成的索引

~/.claude/projects/<项目>/memory/  ← 项目记忆
```

`<项目>` 是 git 根目录的完整绝对路径，小写，`/` 替换为 `-`：
`/home/user/my-project` → `~/.claude/projects/-home-user-my-project/memory/`

记忆文件名格式：kebab-case — `[a-z0-9]+(-[a-z0-9]+)*`。不含下划线，不含大写。

## 命令

| 命令 | 作用 |
|------|------|
| `sync-memory` | 扫描 .md 文件，更新元数据，重建索引和热榜 |
| `sync-memory --hint <slug>` | 显示当前记忆的建议（headings、可用引用、缺失字段） |
| `sync-memory --set-metadata <slug> <<'EOF'` | 批量写入 description/read_when/references |
| `sync-memory --delete <slug>` | 删除记忆并清理所有悬空引用 |
| `sync-memory --dry-run` | 只读校验，不写盘 |
| `sync-memory --audit` | 结构审计（孤立节点、单向引用） |

## 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `description` | 是 | 一行摘要，>= 20 字符 |
| `read_when` | 是 | 召回触发短句，1-8 条 |
| `references` | 否 | 引用其他记忆的 slug 列表，最多 10 条 |

所有字段由脚本校验，失败不写入。

## 记忆召回

召回分两层，无需手动操作：

- **热榜**：高分记忆自动写入 `CLAUDE.md` / `MEMORY.md` 的 `<!-- memory-index -->` 标记块，CC 每次会话自动加载
- **温层**：用 Grep 搜索 INDEX.md 中的 `read-when` 关键词，按需读取

## 与 archived-memory-lifecycle 的区别

这是 memory-lifecycle 的独立演进版本，核心差异：

- **零 frontmatter**：记忆文件是纯 Markdown，不再内嵌 YAML 头部
- **metadata.jsonl**：元数据与正文分离，脚本独写，模型不可直接编辑
- **无 Class ①② 警告**：不再有 body-metadata 对齐检查和复杂门禁
- **CLI 门禁**：模型通过 `--set-metadata` 写入元数据，脚本校验并拒绝坏输入
- **不影响 archived-memory-lifecycle**：两个 skill 独立运行，共享存储目录格式但互不依赖
