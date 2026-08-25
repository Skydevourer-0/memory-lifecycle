# memory-lifecycle / workflow-checkpoint 双端可移植性设计（Claude Code + Codex）

> 日期：2026-08-24 ｜ 版本：v2（已过对抗式审阅，修订 30 条意见）
> 范围：一份文档覆盖两个技能的双端改造；SSOT = `~/.cc-switch/skills/` 下每技能独立 git 仓库

## 1. 背景与目标

两个技能（memory-lifecycle、workflow-checkpoint）当前只适配 Claude Code：
依赖 `~/.claude/CLAUDE.md` / `~/.claude/projects/<slug>/memory/MEMORY.md` 自动加载、
`~/.claude/settings.json` hook 注册。目标：**同一份数据、同一套脚本，双端（Claude Code + Codex）可安装、可召回**。

已拍板的总原则：

- 数据 SSOT 中立化到 `~/.cc-switch/memory/` 与 `~/.cc-switch/workflows/`（cc-switch 不清理根级自定义目录，已验证）。
- v1/v2 均不引入 SQLite（cdxe 的 SQLite 服务于 prompt 采集 + 状态机，不适用）；纯 `.md` + `metadata.jsonl`，保留 `format_version` 迁移思想。
- 不注册 UserPromptSubmit 匹配（脚本无法验证命中正确性，污染风险）；WARM 层 = `INDEX.md` 保留（grep 召回）。
- 三个待决问题结论：
  1. 全局 CLAUDE.md 热榜保留（auto-memory 不写 CLAUDE.md，无文件冲突）。
  2. **原生 auto-memory 保留，我们不再写项目级 MEMORY.md**；项目级热榜改为 SessionStart hook 注入独立文件（§4.2）。
  3. `archive-stream --force` 在 checkpoint.py 中**确实存在**（覆盖 WEAK 信号），本次不改其命令面，仅依赖新 wf_dir 解析（§8）；memory-lifecycle 侧无 archive-stream。
- **hook 命中路径统一 exit 0 + additionalContext 软提示**（不再阻断式 review）：Claude exit 1 与 Codex exit 2 语义不同，阻断式跨端不一致且与 fail-open 原则冲突。SKILL.md 需写明此行为变更（旧版为 🔴 + exit 1 REVIEW）。

## 2. 数据层（SSOT）

```
~/.cc-switch/memory/
  global/
    *.md                  # 记忆正文（纯 Markdown，零 frontmatter）
    metadata.jsonl        # 元数据（脚本独占写入）
    INDEX.md              # 温层索引（sync 生成，grep 用）
  projects/<slug>/
    *.md
    metadata.jsonl
    INDEX.md
    HOTLIST.md            # 项目级热榜（sync 生成，SessionStart 注入用，§4.2）
~/.cc-switch/workflows/
  global/
    workflows.jsonl
    archive.jsonl
    archived/             # close/archive-stream 归档的 .md
  projects/<slug>/
    workflows.jsonl
    archive.jsonl
    archived/
```

- **slug 统一规范（两技能共用同一算法，各自实现 + 一致性测试锁定）**：
  git root `os.path.realpath()` → `lower()` → 非 `[a-z0-9]` 替换为 `-` → **折叠连续 `-`**。
  折叠保证 `C:\Users\...` 生成 `c-users-...`（而非 `c--users-...`，后者不满足 SLUG_RE）。
  checkpoint 现有算法（前导 `-`、保留大小写）作废。
- 所有 I/O 显式 `encoding="utf-8"`（Windows 默认 GBK）。
- **所有原子写用 `os.replace`（Windows 上 `os.rename` 目标存在时抛 FileExistsError）**，覆盖 `write_all_metadata`、`inject_hot_list`、HOTLIST.md 生成。
- 旧路径（`~/.claude/global/memory/`、`~/.claude/projects/<slug>/memory/`、`~/.claude/{global,projects/<slug>}/workflows/`）不再读写；仅 `migrate` 子命令读取（§11）。

## 3. 召回分层

| 范围 | Claude Code | Codex |
|------|-------------|-------|
| 全局 | `~/.claude/CLAUDE.md` 标记块热榜（原生自动加载，保留） | `~/.codex/AGENTS.md`（或 `$CODEX_HOME/AGENTS.md`）标记块热榜（原生自动加载；不存在则 sync 首次创建） |
| 项目 | SessionStart hook 注入 `~/.cc-switch/memory/projects/<slug>/HOTLIST.md` | 同一物理文件，SessionStart hook 注入 |
| 温层 | `INDEX.md`（grep） | 同左 |

- **全局热榜文件**：`get_hot_list_target` 改为返回双目标（CLAUDE.md + AGENTS.md），sync 时对存在的文件注入标记块；都不存在时惰性创建骨架（带标记块）。标记块格式不变（`<!-- memory-index:start/end -->`）。
- **项目级**：sync 不再写 `~/.claude/projects/<slug>/memory/MEMORY.md`，改生成 `HOTLIST.md`。原生 auto-memory 保留，互不覆盖。
- 不注册 UserPromptSubmit；不做命中率/热度自动判定。
- **字符预算统一 1200 字符**（HOTLIST.md 与 checkpoint 注入）：Codex 默认模型可见 hook 输出上限约 2500 token，中文 1 字符 ≈ 1.5–2 token，1200 字符安全不触发 spilling；Claude 4000 字符上限也满足。不设置 `additionalContextLimit`（保持默认）。
- display 的 usage 视图读取策略：**双读**（CLAUDE.md + AGENTS.md 标记块都尝试读，输出标注来源文件）。

## 4. Hook 设计

### 4.1 事件总览

| 事件 | Claude（settings.json） | Codex（hooks.json） |
|------|--------------------------|---------------------|
| PostToolUse | `Write\|Edit\|MultiEdit`（memory-lifecycle） | `apply_patch`（memory-lifecycle） |
| SessionStart | `startup\|resume\|clear\|compact`（memory + checkpoint） | `startup\|resume\|clear\|compact`（memory + checkpoint） |

- **移除 PostToolUse 的 `pathPattern`**（原 `**/.claude/**/memory/*.md` 不再匹配新路径，Windows/绝对路径 glob 语义不可靠）；matcher 全触发，脚本内守卫过滤（§4.3），no-op exit 0。
- **守卫只认新前缀 `~/.cc-switch/memory/`**：`~/.claude/projects/<slug>/memory/`（原生 auto-memory 地盘）的写入明确 no-op，防止 sync 扫描污染其目录；旧前缀仅 `migrate` 使用（§11）。
- Claude matcher 补 `resume`；双端 SessionStart matcher 统一含 `clear`（Codex /clear 不重建指令链，需重新注入项目热榜）。
- 双端 hook JSON 输出协议兼容：`{"hookSpecificOutput": {"hookEventName": "...", "additionalContext": "..."}}`。
- **fail-open**：任何异常 → stderr 记录 + 空 stdout + exit 0。
- **无 emoji 输出**（`🔴` → `[REVIEW]`、⏸️ 仅读取兼容、文档统一 `PAUSED`）。

### 4.2 SessionStart 注入（项目级热榜 + checkpoint）

- memory-lifecycle 新增子命令 `memory-sync.py session-start`：
  - 用 stdin JSON 的 `cwd` 字段（若为 hook 调用）或 `os.getcwd()` 判定项目 slug；无 git root → 空输出 exit 0。
  - 读 `~/.cc-switch/memory/projects/<slug>/HOTLIST.md`；**文件不存在 → 空输出 exit 0**。
  - HOTLIST.md 内容 = 纯热榜条目（**无头部注释**，省预算），≤ 1200 字符。
  - 输出 SessionStart JSON（`additionalContext` = 热榜内容）。
  - 全局不注入（CLAUDE.md / AGENTS.md 已自动加载，避免重复）。
- checkpoint `list --hook` 保留，**输出截断 ≤ 1200 字符**。
- 两技能各自注册各自的 SessionStart hook（互不感知，§5）。

### 4.3 PostToolUse 合并命令（sync-and-hint）

- 恢复 `sync-and-hint` 为正式命令（去掉 DEPRECATED）：一次触发 = sync（扫描、更新 metadata、重建 INDEX、更新热榜）+ hint（对命中的记忆输出 read-when 提示 additionalContext）。
- **双 payload 解析**（stdin JSON）：
  - Claude：`tool_input.file_path`（Write/Edit/MultiEdit 均含）。
  - Codex `apply_patch`：`tool_input.command` 正则解析 `*** (Update|Add|Delete) File: <path>`，支持多文件；**相对路径一律以 stdin JSON 的 `cwd` 字段为基准解析**（不用 `os.getcwd()`，Codex 可能从子目录启动）。不识别 `write_to_file`（现行 Codex 工具面无此名，避免死代码）。
- **命中判定**：解析出的路径必须落在 `~/.cc-switch/memory/` 下（新前缀）；否则空输出 exit 0。
- **Delete 分支**：命中记忆正文 → 走删除清理（metadata 条目 + 引用 + 正文 + 重建 INDEX 与热榜）；命中 `INDEX.md`/`HOTLIST.md`/`README.md` 等基础设施文件或未知 slug → no-op exit 0。
- **hint 输出**：命中多个记忆文件时输出 ≤3 条提示（各自 read-when），格式为软提示文本。**metadata 为空（刚 sync 出的 stub）时输出'记忆已存在、元数据待补（set-metadata）'软提示**，保证 review 闭环不因 exit 0 化而断裂。
- 命中路径 **exit 0**（软提示，不阻断；见 §1）。

## 5. 安装结构（仿 superpowers：分端安装、互不感知）

```
memory-lifecycle/
  SKILL.md                  # 单文件，双平台小节（§5.2）
  scripts/
    common.py               # 路径/门禁/原子写（改造点 §9）
    memory-sync.py          # sync / sync-and-hint / hint / set-metadata / delete / audit / display / session-start / migrate
    install.py              # 兼容旧入口：检测环境并转发 .claude/install.py 或 .codex/install.py
  .claude/
    install.py              # 只写 ~/.claude/settings.json
  .codex/
    install.py              # 写 $CODEX_HOME/hooks.json（默认 ~/.codex），输出 /hooks trust 指引
workflow-checkpoint/
  SKILL.md
  scripts/                  # checkpoint.py / migrate_v2.py / install.py（同 memory-lifecycle 模式）
  .claude/
    install.py
  .codex/
    install.py
```

### 5.1 安装器职责

- `.claude/install.py`（memory-lifecycle）：
  - **先清理本技能旧 hook**（`sync`、`hint` 两条旧 command、旧 pathPattern、旧版 sync-and-hint 命令）再写入新块，防止升级后双跑；按 command 去重，不触碰其它技能块。
  - 注册 PostToolUse（`Write|Edit|MultiEdit`，无 pathPattern）+ SessionStart（`startup|resume|clear|compact`，`memory-sync.py session-start`）。
  - **不改 `autoMemoryEnabled`**（原生 auto-memory 保留）。
  - hook 命令用 `sys.executable` + `__file__` 派生的脚本绝对路径。
- `.codex/install.py`（memory-lifecycle）：
  - **Codex home 解析**：`$CODEX_HOME` 若设置则用之，否则 `~/.codex`。
  - 幂等注册 PostToolUse（`apply_patch`）+ SessionStart（`startup|resume|clear|compact`）到 `hooks.json`（读-合并-写，不覆盖其它技能/插件块；先移除本技能旧条目）。
  - **检测 `config.toml` 内联 `[hooks]`**：存在则提示（Codex 会合并并启动告警）。
  - 输出 `/hooks` trust 指引，并注明**重装（命令路径变化）后 hook 按哈希重新 trust**。
- workflow-checkpoint 的 `.claude/install.py`：matcher 改为 `startup|resume|clear|compact`；`.codex/install.py`：注册 SessionStart。
- 无 `--app both`；每端只装自己的。

### 5.2 SKILL.md

- 单文件：现有内容保留，新增 "Platforms" 小节（Claude：`python <skill>/.claude/install.py`；Codex：`python <skill>/.codex/install.py`；数据/命令双端一致）。
- 写明 hook 行为变更：命中记忆文件 = 软提示（不再 🔴 阻断）。
- 命令缩写示例统一为 `~/.cc-switch/skills/...` 路径。

## 6. 平台差异修正（Windows）

1. 强制 UTF-8：`sys.stdout/stderr.reconfigure(encoding="utf-8")`（hook 模式）；所有 `open()` 显式 `encoding="utf-8"`。
2. stdin 非 tty（hook 管道）用**线程 + 短超时**读取（替代 `select.select`，Windows 管道不可用）；**读到不完整 JSON → 空输出 exit 0，绝不 fallback 到 cwd scope**（防止全局记忆被 sync 进项目目录）。
3. `detect_scope`（cwd 版）特判范围**收窄为 `~/.cc-switch/skills/`**（用路径边界判断，避免 `~/.cc-switch-foo` 误伤）→ global；`~/.claude/` 特判保留。`detect_scope_from_file` 按新前缀解析（global vs projects），不做 blanket 特判。
4. slug 统一 `realpath` + 小写 + 折叠（§2）。
5. 所有安装命令用 `sys.executable`（Windows 无 `python3`）。
6. hook 输出无 emoji。

## 7. 门禁加强（v1）

- `DESCRIPTION_BLACKLIST` 扩展中英泛化短语：`记住`、`记一下`、`重要`、`备忘`、`笔记`、`总结`、`概述`、`相关信息`、`tbd`、`todo`、`wip`、`draft`、`placeholder`、`待补充` 等。
- `boilerplate_patterns` 扩展中文模式：`^这是关于.+的记忆$`、`^描述了.+$`、`^一些.+的笔记$` 等。
- 重复 read_when：warn-only（不阻断）。
- 不做命中率/热度分自动判定；LLM suggest-metadata 延后 v2（照 cdxe 隔离 `codex-exec --ephemeral --ignore-user-config --ignore-rules --disable hooks` + 隔离 HOME 防递归）。

## 8. workflow-checkpoint 改动清单

1. 数据目录迁移到 `~/.cc-switch/workflows/`（global / projects/<slug> + archived/，§2）。
2. slug 改用统一规范（§2；`slugify_project_key` 作废，改用 `detect_scope_dir` 内的项目根解析 + 共享算法本地实现）。
3. Claude SessionStart matcher `startup|resume|clear|compact`（补 resume；clear 已含）。
4. `list --hook` 输出截断 ≤ 1200 字符。
5. ⏸️ 检测兼容（`PAUSED|⏸️`），文档统一 `PAUSED`。
6. 新增 `.codex/install.py`（hooks.json SessionStart）。
7. 项目根判定函数（现为 `detect_scope_dir` / `_find_project_root`）：`~/.claude/` 与 `~/.cc-switch/skills/` 特判为 global；**`.git` 为文件（worktree/submodule）时也判定为项目根**（`os.path.exists` + 读 gitdir 指针），memory 侧 `_find_git_root` 同步处理。

## 9. memory-lifecycle 脚本改造点

- `common.py`：
  - `get_memory_dir`：global → `~/.cc-switch/memory/global`；project → `~/.cc-switch/memory/projects/<slug>`。
  - `get_hot_list_target`：global → `[CLAUDE.md, AGENTS.md]`（双目标注入）；project → `HOTLIST.md`。
  - `detect_scope_from_file`：按新前缀解析（`~/.cc-switch/memory/global` → global，`~/.cc-switch/memory/projects/` → project）；旧前缀仅 migrate 判定用。
  - `inject_hot_list`：支持独立文件全量写入（HOTLIST.md，无标记块要求）。
  - **所有 `os.rename` → `os.replace`**。
- `memory-sync.py`：
  - **抽取单点 `resolve_mem_dir_from_file(filepath)`**：替换 `get_mem_dir(scope_from_file)`、`cmd_sync`/`cmd_delete`/`cmd_hint`/`cmd_set_metadata`/`cmd_display` 内部所有旧路径前缀硬编码（`~/.claude/global/memory`、`~/.claude/projects/<slug>/memory`）。
  - scope 判定（`mem_dir == ~/.claude/global/memory` 的比较）改为与 `~/.cc-switch/memory/global` 比较。
  - stub 排除表与 hint 跳过名单加入 `HOTLIST.md`（防自注册为 slug `hotlist`）。
  - `_is_under_memory_dir` 只认新前缀（§4.1）。
  - 新增 `sync-and-hint`（正式）、`session-start`、`migrate` 子命令。
  - display 的 `_resolve_hot_target_for_read` 改双读（§3）。

## 10. Spike 验证清单（实现过程中逐项验证）

1. Codex `apply_patch` PostToolUse 输入实际形态（临时 Bash hook 打印 JSON）：确认 `tool_name`、`tool_input.command`、**`cwd` 字段**；含"子目录启动、路径含空格、反斜杠"用例。
2. Codex SessionStart 注入生效（`additionalContext` 出现；`source` 在 resume/clear/compact 时的取值与 matcher 命中）。
3. `~/.codex/AGENTS.md` 全局自动加载生效（修改后新会话引用）。
4. Windows：hook 管道 stdin 分片/慢写入 + UTF-8 中文路径；**`os.replace` 原子写实际验证**。
5. Claude PostToolUse 无 pathPattern 全触发 + 脚本守卫正确；**原生 auto-memory 写 `~/.claude/projects/<slug>/memory/` 时不触发我们的 sync**。
6. Claude SessionStart `resume` matcher 生效（`claude --resume`）。
7. **复核 Claude 官方 hooks 参考页**：SessionStart matcher 取值、additionalContext 上限、PostToolUse 输入字段、exit code 语义（镜像材料无直接出处）。
8. Windows 冷启动延迟测量（每次 hook 触发 Python 冷启动，记录耗时，>200ms 则记录并评估）。
9. git worktree/submodule（`.git` 为文件）项目根判定。

## 11. 迁移与初始化

- 安装 = 幂等初始化：创建 `~/.cc-switch/memory/{global,projects}`、`~/.cc-switch/workflows/{global,projects,<slug>/archived}` 目录骨架。
- `migrate` 子命令（可选）：
  - 旧 `~/.claude/global/memory/*.md` → `~/.cc-switch/memory/global/`（正文复制 + metadata.jsonl 合并）。
  - 旧 workflows（global/projects）→ 新目录。
  - **语义：目标已存在 → 跳过不覆盖；重复运行幂等；同 slug 冲突跳过并报告。**
  - **不迁移** `~/.claude/projects/<slug>/memory/`（原生 auto-memory 地盘）。
  - checkpoint 旧 slug（前导 `-`、大小写保留）无法反推项目路径，**不做 slug 重映射**（本机无内容数据，仅通用占位能力）。
- 本机现状：`~/.claude/projects/` 下有 4 个**空骨架** memory 目录（无内容数据），`~/.claude/global/memory` 不存在 → migrate 实际无存量可迁。

## 12. 测试策略

- pytest（现有 `tests/` 更新路径断言）新增：
  - 双 payload 解析（Claude file_path / Codex apply_patch 多文件 / Delete / cwd 字段基准 / 相对路径）。
  - `session-start` 输出（有 slug → JSON 注入；无 git root / HOTLIST 不存在 → 空输出 exit 0）。
  - HOTLIST.md 生成 + **不产生 slug `hotlist` stub** + 预算 1200 字符。
  - 门禁扩展（中文泛化短语、boilerplate 中文模式）。
  - **slug 跨技能一致性**：两技能各自测试锁定同一组路径→slug 期望值常量（如 C:\Users\ruanletian\projects\foo → c-users-ruanletian-projects-foo），互不 import 也能间接保证一致。
  - **Windows 原子写（os.replace）**：目标已存在时成功覆盖。
  - fail-open（坏输入 → exit 0 空输出；**不完整 JSON 不 fallback cwd**）。
  - Delete 边界（基础设施文件/未知 slug → no-op；删除后重建 INDEX + 热榜）。
  - 安装器重装幂等且无旧 hook 残留（settings.json / hooks.json 合并）。
  - migrate 幂等（重复运行、已存在跳过）。

## 13. 风险与开放项

1. Codex 全局 `AGENTS.override.md` 存在时优先级高于 `AGENTS.md`，热榜失效 → 文档注明（不改行为）。
2. Codex 全局 hooks 首次需 `/hooks` approve；重装后按哈希重新 trust → 安装器指引注明。
3. **`$CODEX_HOME` 未设置时默认 `~/.codex`**；安装器按环境变量解析，文档注明。
4. `~/.codex/config.toml` 已存在内联 `[hooks]` 时与 hooks.json 合并并告警 → 安装器检测提示。
5. AGENTS.md 合并上限 `project_doc_max_bytes` 32 KiB：热榜 ≤1200 字符追加在尾部，影响极小；文档注明。
6. 双端 slug 一致性依赖 realpath 归一化 + 折叠算法 + 跨技能测试锁定。
7. Claude `additionalContext` 4000 字符上限：HOTLIST 1200 + checkpoint 1200 各自独立 hook 计算，安全。
8. `~/.claude/skills/` 是 cc-switch 副本：改造后需重新同步副本（README 注明步骤）；`~/.codex/skills/` 为符号链接，自动生效。
9. 项目级 SessionStart 注入优先用 stdin JSON 的 cwd 字段（hook 调用）或 os.getcwd()（手动调用）判定 slug；子目录启动时 slug 仍用 git root（向上查找），一致。
10. hook 冷启动延迟（Windows Python 启动 ~50–100ms）叠加到工具调用；spike #8 测量后记录，不预先优化。

