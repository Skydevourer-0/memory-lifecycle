# memory-lifecycle / workflow-checkpoint 双端可移植性实现计划 v2

> 日期：2026-08-24 ｜ 上游设计：`2026-08-24-codex-portability-design.md`（v2，两轮对抗式审阅通过）
> 计划审阅：一轮对抗式审阅完成，v2 修订 B1/A1-A7/D1-D7（见文末"审阅修订记录"）。

**Goal:** 两技能数据层迁至 `~/.cc-switch/{memory,workflows}`，Claude + Codex 双端安装/召回一致（Claude 全局 CLAUDE.md 热榜 + 项目 SessionStart 注入；Codex 全局 AGENTS.md 热榜 + 项目 SessionStart 注入；原生 auto-memory 保留）。

**全局约束（来自设计，不得偏离）**
- 所有原子写 `os.replace`；所有 I/O `encoding="utf-8"`；hook 模式 stdout reconfigure UTF-8。
- hook 命中一律 exit 0 + additionalContext 软提示（不阻断、无 emoji）。
- 守卫只认新前缀 `~/.cc-switch/memory/`；旧前缀仅 migrate 用。
- slug：realpath → lower → 非 `[a-z0-9]` 替换 `-` → 折叠连续 `-`。
- 预算 1200 字符（HOTLIST.md / checkpoint 注入）。
- stdin 不完整 JSON → 空输出 exit 0，不 fallback cwd。
- fail-open：任何异常 → stderr + 空 stdout + exit 0。

**测试基线（2026-08-24 实测，实现前必须以此为准）**
- memory-lifecycle：
  - 技能根目录 `python -m pytest` → collection 失败（2 errors）：顶层 `tests/test_parser.py` 引用不存在的 `parse_frontmatter`（v1 遗留，**删除该文件**）；`scripts/tests/` 无 conftest/sys.path 配置。
  - `cd scripts && python -m pytest` → **18 failed / 89 passed**：全部为 `TestHintHookMode` 系列，断言旧行为（exit 1、🔴 REVIEW）——正是本次要改为 exit 0 软提示的行为，**随 Task 2 重写**。
- workflow-checkpoint（技能根目录 `python -m pytest`）→ **6 failed / 115 passed**：`tests/test_install.py` 5 个（期望的 installer 行为与现行 `scripts/install.py` 不符）+ `tests/test_checkpoint.py::test_commit_omitted_global_scope`。随 Task 5 修复。

---

## Task 0：协议核对 + 基线（前置，快速）

**Files:** 无
- [ ] 0.1 核对 Codex hook 协议（对照 `%TEMP%\codex-hooks.md`）：PostToolUse 输入（`tool_name`、`tool_input.command`、`cwd` 字段）、SessionStart `source` 取值、JSON 输出格式、matcher 语义、`additionalContextLimit` 默认 2500 token。结论已获两轮审阅确认，实现按此。
- [ ] 0.2 核对 Claude hook 官方参考（网络）：SessionStart matcher 取值（startup/resume/clear/compact）、additionalContext 上限（4000 字符）、PostToolUse 输入字段、exit code 语义。若与设计冲突，回填设计并标注。
- [ ] 0.3 记录基线：上文测试基线数字直接采用，不重跑。
- **真实 hook 实测（Codex payload、SessionStart 注入、AGENTS.md 加载、resume/clear 场景）统一放到 Task 6 验收**，用临时 `CODEX_HOME` 或备份-恢复 `~/.codex/hooks.json` 方式，附录记录清理步骤。

## Task 1：memory-lifecycle `common.py`

**Files:** `skills/memory-lifecycle/scripts/common.py`、`skills/memory-lifecycle/scripts/tests/test_common.py`
**Steps:**
- [ ] 1.1 `get_memory_dir`：global → `~/.cc-switch/memory/global`；project → `~/.cc-switch/memory/projects/<slug>`。
- [ ] 1.2 新增 `codex_home()`：`$CODEX_HOME` 或 `~/.codex`。
- [ ] 1.3 `get_hot_list_target`：global → `[~/.claude/CLAUDE.md, <codex_home>/AGENTS.md]`；project → `<mem_dir>/HOTLIST.md`。
- [ ] 1.4 `detect_scope`：`~/.claude/` 与 `~/.cc-switch/skills/`（路径边界）→ global。
- [ ] 1.5 `detect_scope_from_file`：按新前缀解析；旧前缀仅 migrate 判定。
- [ ] 1.6 新增 `resolve_mem_dir_from_file(filepath)` 单点（新前缀 global/projects；否则 None）。
- [ ] 1.7 新增 `project_slug(path)`（realpath → lower → 替换 → 折叠）；`get_memory_dir` 改用它。
- [ ] 1.8 `_find_git_root`：`.git` 为文件（worktree/submodule）也判为项目根。
- [ ] 1.9 全部 `os.rename` → `os.replace`（`write_all_metadata`、`inject_hot_list` 等）。
- [ ] 1.10 `inject_hot_list` 支持独立文件全量写（无标记块 target 直接写全部条目，用于 HOTLIST.md）。
- [ ] 1.11 门禁：`DESCRIPTION_BLACKLIST` 加中文短语；`boilerplate_patterns` 加中文模式；重复 read_when 的 warn 由调用方（`cmd_set_metadata`/`cmd_sync`）输出。
- [ ] 1.12 预算常量 `HOTLIST_BUDGET = 1200`。
- **Tests:** 路径断言更新；slug 折叠（`C:\Users\a\proj` → `c-users-a-proj`）；`resolve_mem_dir_from_file` 新旧前缀；os.replace 覆盖已存在目标；门禁中文用例；`codex_home()`；worktree `.git` 文件判定。
- **运行：`cd scripts && python -m pytest scripts/tests/test_common.py -q`**

## Task 2：memory-lifecycle `memory-sync.py`

**Files:** `skills/memory-lifecycle/scripts/memory-sync.py`、`scripts/tests/test_memory_sync.py`、`scripts/tests/test_display.py`
**Steps:**
- [ ] 2.1 全部旧路径前缀硬编码 → `resolve_mem_dir_from_file`；scope 判定改与 `~/.cc-switch/memory/global` 比较。
- [ ] 2.2 `_is_under_memory_dir` 只认新前缀。
- [ ] 2.3 stub 排除表与 hint 跳过名单加 `HOTLIST.md`。
- [ ] 2.4 `cmd_sync`：全局双目标注入（存在则注入；都不存在惰性创建骨架）；项目生成 HOTLIST.md（无头部注释、预算 1200）。
- [ ] 2.5 `sync-and-hint` 转正：双 payload 解析（Claude `file_path`；Codex `apply_patch` 解析 `*** (Update|Add|Delete) File:`，相对路径以 stdin `cwd` 为基准）；多文件；Delete 分支（基础设施/未知 slug no-op；删除后重建——**项目 scope 重建 HOTLIST.md，全局 scope 重建双目标标记块**）；hint ≤3 条；空 metadata → "元数据待补"软提示；命中 exit 0。
- [ ] 2.6 新增 `session-start`：stdin `cwd` 或 `os.getcwd()` → slug；无 git root / HOTLIST 不存在 → 空输出 exit 0；输出 SessionStart JSON。**/clear 决策：不注入全局热榜（接受不对称，SKILL.md 注明；Claude /clear 重载 CLAUDE.md 无此问题）。**
- [ ] 2.7 新增 `migrate`：旧 global memory 复制 + metadata 合并；旧 workflows → 新目录；目标存在跳过；幂等；不迁 `~/.claude/projects/<slug>/memory/`。
- [ ] 2.8 `cmd_display`：`_resolve_hot_target_for_read` 双读（CLAUDE.md + AGENTS.md，标注来源）。
- [ ] 2.9 hook 模式 stdin 读取改线程 + 短超时；不完整 JSON → 空输出 exit 0（不 fallback）。
- [ ] 2.10 **删除顶层 `tests/test_parser.py`**（stale，引用不存在的 `parse_frontmatter`）。
- **Tests:** 双 payload 解析（cwd 基准、相对路径、多文件、Delete）；session-start（有/无 slug、HOTLIST 不存在）；HOTLIST 生成且不产生 slug `hotlist` stub；**重写 `TestHintHookMode` 断言（exit 0、软提示、空 metadata 提示、无 emoji）**；migrate 幂等；fail-open（坏输入、不完整 JSON 不 fallback）；display 双读；Delete 边界与按 scope 重建。
- **运行：`cd scripts && python -m pytest -q`（目标：全绿，TestHintHookMode 重写后）**

## Task 3：memory-lifecycle 安装器

**Files:** `skills/memory-lifecycle/.claude/install.py`（新）、`skills/memory-lifecycle/.codex/install.py`（新）、`skills/memory-lifecycle/scripts/install.py`（改兼容入口）
**Steps:**
- [ ] 3.1 `.claude/install.py`：清理本技能旧条目（sync/hint 旧 command、旧 pathPattern、旧版 sync-and-hint）→ 写入 PostToolUse（`Write|Edit|MultiEdit` 无 pathPattern）+ SessionStart（`startup|resume|clear|compact`，`memory-sync.py session-start`）；命令 `sys.executable` + `__file__`；不改 autoMemoryEnabled；幂等。
- [ ] 3.2 `.codex/install.py`：`codex_home()` 解析；hooks.json 读-合并-写（清理本技能旧条目）；检测 config.toml 内联 `[hooks]` 提示；/hooks trust 指引（含重装后重新 trust）。
- [ ] 3.3 `scripts/install.py` 兼容入口：检测目标环境转发（存在 `~/.claude/settings.json` → .claude 安装器；`codex_home()` 存在 → .codex 安装器；无参数打印用法）。
- **Tests:** 合并幂等 + 无旧残留（mock settings.json / hooks.json 双场景，用临时 HOME/CODEX_HOME）。
- **运行：`cd scripts && python -m pytest tests/test_install.py -q`（如该文件存在则并入；否则新建 install 相关测试于 scripts/tests/）**

## Task 4：memory-lifecycle SKILL.md / README

**Files:** `skills/memory-lifecycle/SKILL.md`、`skills/memory-lifecycle/README.md`
**Steps:**
- [ ] 4.1 SKILL.md：Platforms 小节（Claude/Codex 安装命令）；路径全部更新 `~/.cc-switch/skills/...` 与 `~/.cc-switch/memory/...`；hook 行为变更（软提示替代 🔴 阻断）；项目级召回 = SessionStart 注入；**注明 Codex `AGENTS.override.md` 存在时 AGENTS.md 热榜失效**；**注明 AGENTS.md 合并上限 32 KiB**；**注明 /clear 后全局热榜需下次启动恢复（Codex）**。
- [ ] 4.2 README：安装/使用/架构同步；cc-switch 同步 `~/.claude/skills` 副本步骤。
- **验收：grep 旧路径 `~/.claude/global/memory` 与 `~/.claude/projects/` 在文档中零残留（migrate 说明除外）。**

## Task 5：workflow-checkpoint（可与 Task 1–4 并行）

**Files:** `skills/workflow-checkpoint/scripts/checkpoint.py`、`scripts/install.py`、`scripts/migrate_v2.py`、`.claude/install.py`（新）、`.codex/install.py`（新）、`SKILL.md`、`tests/test_checkpoint.py`、`tests/test_install.py`、`tests/test_migrate_v2.py`
**Steps:**
- [ ] 5.1 数据目录迁 `~/.cc-switch/workflows/`（global / projects/<slug>）；archived/ 首次归档时创建。
- [ ] 5.2 slug 统一算法（与 memory 相同规范）；`slugify_project_key` 作废。
- [ ] 5.3 项目根判定：`~/.claude/`、`~/.cc-switch/skills/` 特判 global；`.git` 文件（worktree）判项目根。
- [ ] 5.4 `list --hook` 截断 ≤1200 字符。
- [ ] 5.5 `.claude/install.py`：matcher `startup|resume|clear|compact`；清理旧条目。
- [ ] 5.6 `.codex/install.py`：SessionStart 注册（同 memory 模式：codex_home 解析、合并幂等、trust 指引）。
- [ ] 5.7 `scripts/install.py` 兼容入口。
- [ ] 5.8 SKILL.md：路径、Platforms、PAUSED 统一；/clear 不对称注明。
- [ ] 5.9 **修复 6 个存量测试失败**：`tests/test_install.py` 5 个按新安装器语义更新；`test_commit_omitted_global_scope` 排查修复。
- **Tests:** 新路径；slug 期望值（与 memory 同一组向量：`C:\Users\a\proj` → `c-users-a-proj`）；截断；⏸️ 兼容；安装器幂等。
- **运行：技能根目录 `python -m pytest -q`（目标全绿）**

## Task 6：集成验收

- [ ] 6.1 memory-lifecycle：`cd scripts && python -m pytest -q` 全绿；workflow-checkpoint：根目录 `python -m pytest -q` 全绿。
- [ ] 6.2 **真实 hook 实测（临时 CODEX_HOME 或备份-恢复 `~/.codex/hooks.json`）**：apply_patch PostToolUse payload 实测；SessionStart 注入生效；`~/.codex/AGENTS.md` 全局加载；记录实测 payload 到附录 B。
- [ ] 6.3 手动验收 Claude 端：安装 → 写一条记忆 → 新会话项目热榜注入出现；全局 CLAUDE.md 热榜出现；auto-memory 目录未被触碰；`claude --resume` 注入生效；`/clear` 后项目热榜仍在。
- [ ] 6.4 手动验收 Codex 端：`.codex/install.py` → /hooks trust → 写记忆 → 新会话 SessionStart 注入；AGENTS.md 全局热榜。
- [ ] 6.5 cc-switch 同步 `~/.claude/skills` 副本，确认新文件落位。
- [ ] 6.6 附录 B 回填（实测 payload、延迟、清理步骤）。

---

## 审阅修订记录（v1 → v2）
- B1：加入"测试基线"节（实测 2+18+6 个存量失败），Task 2.10 删除 stale test_parser.py，Task 5.9 修复存量失败，运行目录明确。
- A1：测试路径修正为 `scripts/tests/`（test_memory_sync.py / test_display.py / test_common.py）。
- A2：`_find_git_root` worktree 改造移入 Task 1.8。
- A3：并行边界注明（Task 1 可与 Task 0 并行；Task 5 与 Task 1–4 并行）。
- A4：Task 2.6 明确 /clear 决策（接受不对称，文档注明）。
- A5：Task 4.1 补 AGENTS.override.md / 32 KiB 注明。
- A6：Task 5 Tests 明确同一组 slug 期望向量（互不 import）。
- A7：Task 6.3/6.4 补 resume / clear 场景。
- D1：Task 2.5 明说 Delete 重建按 scope（HOTLIST vs 双目标）。
- D2：Task 3.3 无测试可接受（兼容入口）；Task 4 增加 grep 旧路径零残留验收。
- D3：Task 5 Files 明确三个真实测试文件。
- D4：Task 1.11 明确重复 read_when 的 warn 归属调用方。
- D5：Task 0 改为协议核对，真实 hook 实测移 Task 6（临时 CODEX_HOME / 备份恢复）。
- D6：各 Task 注明 pytest 运行目录。
- D7：并入 D1。

## 附录 A：协议核对结论（Task 0 回填）

- Codex Common input fields（所有 hook 事件）：`session_id`、`transcript_path`、`cwd`、`hook_event_name`、`model`。**payload 必须含 `hook_event_name`**，否则 `_parse_payload_bytes` 判 invalid（fail-open）。
- SessionStart：matcher 应用于 `source`（`startup|resume|clear|compact`）；输入额外字段 `source`；输出 JSON `hookSpecificOutput.{hookEventName,additionalContext}`；plain text stdout 也可注入。
- PostToolUse：输入 `tool_name`（apply_patch 等）、`tool_input.command`（apply_patch/Bash）、`tool_input.description`；输出支持 `hookSpecificOutput.additionalContext`；exit 2 + stderr = 阻断（本项目用 exit 0 软提示，不阻断）。
- `additionalContextLimit` 默认约 2500 token，超限 spilling（全文落盘 + 预览）→ 本设计预算 1200 字符不触发。
- Claude 侧：SessionStart matcher `startup|resume|clear|compact`、PostToolUse 输入 `tool_input.file_path`、additionalContext 4000 字符上限（官方 hooks 参考页；真实 `claude --resume` 验证留 Task 6）。
- Codex AGENTS.md：全局发现 `$CODEX_HOME/AGENTS.md`（AGENTS.override.md 优先）；项目 root→cwd 分层；合并上限 32 KiB（project_doc_max_bytes）。

## 附录 B：实测记录（Task 6 回填）

- 端到端冒烟（临时 USERPROFILE，不污染真实数据）：
  - 全局记忆 → sync → 惰性创建 `~/.claude/CLAUDE.md` + `~/.codex/AGENTS.md` 标记块，双目标热榜均更新 ✓
  - 项目记忆 → slug `c-users-ruanletian-appdata-local-temp-mem-smoke4-proj`（折叠后无 `--`）→ HOTLIST.md 生成 ✓
  - `session-start`（完整 Codex payload）→ SessionStart JSON，additionalContext=HOTLIST 内容（31 字符）✓
  - `sync-and-hint`：Codex apply_patch 与 Claude file_path 双 payload 均输出软提示 JSON（exit 0）；非记忆文件空输出 ✓；不完整 JSON → exit 0 空输出（fail-open，不 fallback）✓
  - hook stdout 字节 UTF-8 校验通过（GBK 终端显示乱码仅为本地渲染问题）✓
  - `set-metadata` 前置要求 sync 创建 stub（设计如此）；read-when 单词过短门禁正常拒绝 ✓
- 真实安装（2026-08-24）：
  - `~/.claude/settings.json`：SessionStart（memory session-start + checkpoint list --hook，matcher 含 resume）+ PostToolUse（sync-and-hint，无 pathPattern）；env/插件配置完整保留；`autoMemoryEnabled` 未动 ✓
  - `~/.codex/hooks.json`：PostToolUse（apply_patch）+ SessionStart（memory + checkpoint）✓（首次创建）
  - checkpoint 安装器输出 em dash 在 GBK 控制台乱码 → 已改 ASCII `-`
  - 待用户操作：Codex `/hooks` approve（重装后需重新 approve）；新会话验证注入与 resume 场景

## 附录 B：实测记录（Task 6 回填）
（待填）

