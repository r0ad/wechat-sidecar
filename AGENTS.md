# AGENTS.md — 微信 AI 回复助手（wxassist）

> 本文件是给 coding agent（Claude Code / Codex / Cursor / DSH 等）的项目规范。
> **改动代码前先读完 §1 硬约束和 §2 命令**；行为、架构或约定变化时，同步更新本文件。
> 所有面向用户的输出、UI 文案、提交信息、文档都用**中文**。

---

## 0. 项目是什么

Windows 上的旁挂式微信回复助手：

- **只读截屏 + 本地 OCR** 读取当前微信聊天窗口，不注入、不 Hook、不读数据库；
- 调用 OpenAI 兼容大模型（默认 DeepSeek）判断意图，生成 3 条候选回复；
- 用户点「填入」把候选粘到微信输入框，**发送永远由人手动完成**；
- 长期记忆（自由文本业务资料）与个性化提示词（人设/风格）整段注入上下文；
- PySide6 悬浮窗，Windows 托盘、全局热键、单实例；PyInstaller 打包成 exe。

核心取舍：**能力换安全**。不做个人微信全自动机器人，不做聊天记录解密导出。

---

## 1. 硬约束（违反任何一条都算改坏项目）

1. **永不自动发送**
   - `src/wxassist/filler.py` 不得出现回车、点击「发送」、自动提交的逻辑；
   - 只允许：复制到剪贴板，或「激活微信 → 点输入框 → Ctrl+V」。

2. **不注入、不 Hook、不读微信进程内存、不解密数据库**
   - 采集只允许 WGC（`windows-capture`）或 GDI 截屏（`PIL.ImageGrab`）；
   - 不得引入 WeChatFerry/wxhelper/wxauto/itchat 这类依赖；
   - 不得新增 DB 解密、内存扫 key 功能。

3. **截图不落盘**
   - 截屏帧只在内存（numpy）流转；
   - 调试快照只能写到 `snapshots/`（已 gitignore），且不得包含在提交里；
   - 日志不得写聊天内容（只写会话名、状态、错误）。

4. **产品里没有假数据**
   - 候选只能来自真实模型调用；失败就报错，不伪造；
   - `provider=mock` 只允许出现在**测试代码**里，不得出现在 CLI/设置页/文档作为用户选项；
   - 已删除 `--demo`、`--screenshot`、DemoController，不要加回来。

5. **长期记忆是「自由文本整段注入」**
   - 用户已明确否决分词、结构化条目、关键词召回；
   - 不要重新引入 `memories` 表、分词/相关性打分/RAG（除非用户再次明确要求）；
   - 记忆文件：`%LOCALAPPDATA%\wxassist\memory.txt`，上限 `memory.max_chars`（默认 200000）。

6. **个性化提示词独立于记忆**
   - 文件：`%LOCALAPPDATA%\wxassist\persona.txt`；
   - 「学我说话」写入 `<!-- AUTO-STYLE:BEGIN/END -->` 区块，**不得覆盖用户手写内容**；
   - 设置 → 个性化里可见、可编辑、可导入导出。

7. **API Key 安全**
   - 只存 DPAPI 加密文件（`secret.bin`）或环境变量；
   - 不写 `config.json`、不写日志、不导出、不进 git。

8. **不提交运行时产物/隐私数据**
   - `.gitignore` 已排除：`dist/ build/ logs/ snapshots/ data/ .venv/ config.local.json .wxassist.pid data/*.db` 等；
   - 不要 `git add -f` 这些目录。

9. **单实例 + 托盘 + 热键行为保持**
   - 文件锁 `%LOCALAPPDATA%\wxassist\wxassist.lock`，重复启动提示「已经在运行」后退出；
   - 托盘图标、双击恢复、右键菜单、`Ctrl+Alt+J`、最小化到任务栏都要保留。

---

## 2. 环境与命令

**只支持 Windows 10 1903+ / Windows 11，Python 3.10–3.12（python.org 版本）。**

```powershell
cd D:\ws-dsh\wechat
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest + pyinstaller

# 运行（源码）
python run.py
python run.py --selftest --debug      # 只验证 截屏+OCR+解析
python run.py --test-llm              # 只测试模型连接
python run.py --set-key "sk-..."      # 无界面保存 Key

# 测试（不依赖微信窗口/网络）
python -m pytest -q

# 打包（默认目录版 windowed，无控制台）
python build.py
python build.py --onefile
python build.py --console             # 仅排查启动问题时用
```

> ⚠ 不要用 Anaconda 的 Python 建 venv（`msvcp140.dll` 冲突 → `DLL load failed while importing QtCore`）。
> ⚠ 若 `.venv\Scripts\python.exe` 只有 ~270KB 且 InternalName 是 `Python Launcher`，说明被换成了启动器：
> 用 base 安装目录的 `python.exe` 覆盖它。详见 README 故障排查。

---

## 3. 架构与模块

```
采集线程 pipeline.py
  WGC/GDI 取帧 → capture
  → layout 切面板 + 自适应气泡检测 + 发言人提取
  → ocr RapidOCR（只识别聊天面板）
  → tracker 跨帧对齐/去重/会话稳定
  → store SQLite（消息、建议、风格画像）
  → engine：意图判断（1 次）、候选起草（1 次）
       注入：memory.txt / persona.txt / 群聊发言人 / 待回复消息
  → overlay 悬浮窗
  → filler 复制/粘贴（不发送）
```

| 文件 | 职责 |
|---|---|
| `capture.py` | 找微信窗口、WGC/GDI 采集、DPI、遮挡检测 |
| `layout.py` | 面板区域、自适应气泡检测、群聊发言人、时间/系统消息过滤 |
| `ocr.py` | RapidOCR 封装（懒加载/预热/只识别面板） |
| `tracker.py` | 跨帧锚点对齐、多重集兜底、会话切换 |
| `engine.py` | 意图/起草/换一批/改写/总结/学风格/主动生成 |
| `prompts.py` | 提示词（注入防护、群聊、多问题、记忆、人设） |
| `memory.py` / `persona.py` | 自由文本记忆 / 个性化提示词（文件读写 + 注入区块） |
| `privacy.py` | 手机号/证件/银行卡/邮箱脱敏、敏感话题 |
| `llm.py` | OpenAI 兼容客户端、错误映射、测试连接、模型列表 |
| `providers.py` | 服务商预设 |
| `secrets_store.py` | API Key DPAPI 加密 |
| `store.py` | SQLite：消息、建议、风格画像 |
| `pipeline.py` | 采集主循环、防抖、主动生成、读取更多历史 |
| `overlay.py` | 悬浮窗（卡片、复制反馈、上下文面板、跟随） |
| `settings_dialog.py` | 设置：模型 / 长期记忆 / 个性化 |
| `memory_widget.py` / `persona_widget.py` | 两个自由文本设置页 |
| `summary_dialog.py` | 会话总结 |
| `icons.py` / `resources.py` | 矢量图标 / 应用图标 |
| `app.py` | 组装、托盘、热键、单实例、跟随定时器 |
| `cli.py` | 命令行入口 |
| `build.py` / `wxassist.spec` / `packaging/version_info.txt` | 打包 |

### 数据目录（`%LOCALAPPDATA%\wxassist\`）

| 文件 | 说明 |
|---|---|
| `config.json` | 运行配置（**不含 Key**） |
| `secret.bin` | DPAPI 加密的 Key |
| `memory.txt` | 长期记忆/业务资料（自由文本） |
| `persona.txt` | 个性化提示词（含 AUTO-STYLE 区块） |
| `wxassist.db` | SQLite：消息、建议、风格画像 |
| `wxassist.log` / `console.log` | 日志（不含聊天内容/Key） |
| `wxassist.lock` | 单实例文件锁 |

---

## 4. 关键实现约定

### 4.1 区域与气泡

- 聊天面板：`chat_left_px=300 / header_px=64 / input_px=160`，按 `GetDpiForWindow` 缩放；
- 气泡检测用**背景中位色差值**（`diff > 8`），兼容浅色/深色/自定义聊天背景；
  不要退回到只看纯白/纯绿阈值（会把自定义背景整屏误判）；
- 群聊：群名含 `(n)` 视为群；发言人两种来源：
  1. 气泡上方独立短行；
  2. 被并进气泡的第一行短名字（`_split_leading_sender`，要求比正文行左且更矮）；
- 时间分隔符支持 `9月21日 15:05` 这类格式，必须过滤，不能当消息。

### 4.2 稳定性

- 会话名 OCR 抖动按相似度归并（`_resolve_session`），空标题沿用当前会话；
- 启动 2 秒 warmup，不生成建议；
- 消息 1.5 秒防抖合并；
- 两帧确认后才写上下文（过滤滚动碎片）；
- 聊天页门禁：底部必须有「发送」按钮，否则不读（防公众号/小程序）；
- GDI 兜底前检查窗口是否被遮挡；WGC 帧尺寸异常必须重连，禁止 OCR 错画面。

### 4.3 模型调用

- 普通新消息：意图判断 + 起草 = 2 次调用；
- 主动生成（无新消息）：跳过意图判断，只起草 1 次；
- JSON 解析失败自动重试一次；
- 候选过滤：复述对方、重复、超长、提示词注入，一律丢弃；
- 群聊：待回复消息带 `发言人：` 前缀，提示词要求覆盖所有未答问题；
- 上下文顺序：最近对话 → 记忆 → 人设 → 群聊规则 → 主动生成规则。

### 4.4 UI

- 悬浮窗 440×730，深色卡片、无边框、置顶；
- 标题栏图标用 `icons.py` 的 QPainter 矢量图（图钉/滑杆/横线），不要用 emoji（会变形）；
- 「对方最新消息」最多 6 条 / 118px，可滚动，不能无限撑高；
- 候选卡必须有完整「改写 / 复制 / 填入」按钮；双击候选文字=复制；复制后按钮高亮「已复制」1.6s；
- 设置固定三个 Tab：模型 / 长期记忆 / 个性化；
- 离开微信自动隐藏，📌 可常驻；悬浮窗跟随微信窗口（拖动后按相对偏移跟随）；
- 托盘菜单：显示/隐藏、暂停、跟随、长期记忆、模型设置、退出。

---

## 5. 开发流程与提交规范

1. 先读相关文件，再改；小步修改，避免无关重构。
2. 新增/修改逻辑必须补测试；改 UI 至少手动跑一次。
3. 提交前：
   ```powershell
   python -m pytest -q
   git status --short        # 确认没有 dist/build/logs/snapshots/密钥
   ```
4. 提交信息用 Conventional Commits + 中文摘要，例如：
   - `feat(ai): 无新消息主动生成候选；个性化提示词进设置并可迁移`
   - `fix(ui): 改写菜单深色可读，禁止交互中和交互后自动隐藏`
   - `refactor(memory): 长期记忆改为自由文本整段注入，去掉分词/条目/检索`
   - `build: 增加 PyInstaller 打包脚本与 spec`
5. 一次提交只做一件事；不要 amend 已推送历史；不要 `git reset --hard` 丢用户改动。
6. 动了采集/引擎/UI 且用户需要运行版本时，重新 `python build.py`（必要时 `--onefile`），并重启 exe；
   重启前先结束旧实例（`wxassist.exe` 或 `python run.py`），否则单实例锁会拒绝新实例。
7. 行为/配置/命令变化时同步更新：`README.md`、本 `AGENTS.md`、必要时 `docs/`。

---

## 6. 测试地图

| 文件 | 覆盖 |
|---|---|
| `tests/test_layout.py` | 气泡、群聊发言人、时间过滤、区域 |
| `tests/test_tracker.py` | 跨帧去重、会话切换、sender |
| `tests/test_config_models.py` | 配置合并、模型校验 |
| `tests/test_privacy_json.py` | 脱敏、JSON 容错 |
| `tests/test_store.py` | SQLite、seq 排序、去重 |
| `tests/test_engine.py` / `test_engine_ai.py` | 候选、过滤、改写/总结/风格、多问题 |
| `tests/test_persona.py` | 人设注入、AUTO-STYLE、主动生成 |
| `tests/test_memory.py` | 自由文本记忆读写/注入 |
| `tests/test_pipeline_flow.py` | 合成帧端到端（含聊天页门禁、会话稳定、上下文） |
| `tests/test_pipeline_helpers.py` / `test_overlay_position.py` | 截图签名、跟随位置 |
| `tests/test_llm_http.py` | 本地 HTTP 桩验证 OpenAI 兼容收发 |
| `tests/test_secrets_store.py` | DPAPI 往返/不落明文 |
| `tests/test_cli.py` | 单实例文件锁 |

**测试禁止**：打开真实微信窗口、调用外部 API、读写真实用户数据目录。

---

## 7. 已知坑（优先怀疑这些）

1. **Anaconda Python 建 venv → QtCore DLL 失败**：用 python.org Python 重建 venv。
2. **venv 的 python.exe 是 Python Launcher**（约 270KB）→ 多一个子进程、环境错乱；用 base 的 python.exe 覆盖。
3. **自定义聊天背景**：气泡可能比背景更暗，旧的白色阈值会失效；用自适应背景检测。
4. **群聊名字 OCR**：字号小/被头像遮挡会漏识别；不要把名字当消息，也不要强行编造 @。
5. **窗口最小化无法采集**：WGC/GDI 都不行；只能等窗口还原。
6. **双实例**：两个实例会抢 WGC/热键/写数据库；单实例锁生效时，先杀旧进程再启动。
7. **windowed exe 无控制台**：`print` 输出到 `%LOCALAPPDATA%\wxassist\console.log`；排查问题用 `--console` 重新打包。
8. **`--demo`/`--screenshot` 已删除**：不要根据旧文档加回来。
9. **长期记忆不要加检索**：用户明确要求整段注入；RAG 只在用户再次提出时讨论。
10. **`ui.auto_hide` / `follow_wechat` / `auto_proactive`** 都有用户预期行为，改动前想清楚。

---

## 8. 当前产品行为（用户已确认，不要回退）

- 长期记忆：设置 → 长期记忆，一个自由文本框，整段注入（`memory.txt`，默认 20 万字上限）；
- 个性化：设置 → 个性化，人设/风格自由文本；「学我说话」写 AUTO-STYLE 区块，不覆盖手写；
- 多问题综合：以「我最后一条消息之后对方所有未回复消息」为回复目标，必答要点写进提示词；
- 无新消息时：切会话 2 秒后自动生成一次候选（`ui.auto_proactive`），按钮显示「生成候选」；
- 群聊：发言人识别 + `名字：内容` 上下文 + 覆盖多人问题；
- 界面：最新消息限高可滚动、候选按钮完整、双击复制、复制高亮；
- 悬浮窗：跟随微信、自动隐藏/📌常驻、托盘、最小化到任务栏、单实例；
- 打包：默认 windowed、目录版；单文件产物叫 `wxassist-onefile.exe`。

---

## 9. 需要先问用户再做的改动

- 引入 RAG / 向量库 / embedding；
- 自动发送、自动点击发送、批量群发；
- 聊天记录解密/导出、读取微信数据库；
- 改变记忆/个性化存储位置或入口；
- 换 UI 框架、换模型协议、大范围重构；
- 新增需要联网上传用户数据的能力。

## 10. Agent 提交前检查清单

- [ ] 没有违反 §1 硬约束
- [ ] 没有把 mock/假数据带回产品路径
- [ ] `python -m pytest -q` 通过
- [ ] `git status` 没有 dist/build/logs/snapshots/密钥/聊天数据
- [ ] 提交信息符合规范
- [ ] 行为变化已同步 README / AGENTS.md
- [ ] 需要时已重新打包并重启（注意单实例）
