---
name: dig-webpage
description: 自动找到目标网页的 HTTP 接口，分析参数，生成可复用的 Python 调用脚本和 Claude Code skill。支持两种模式：MCP 实时抓包（推荐）和 HAR 文件离线分析。
argument-hint: <url_or_har_file> --goal "目标描述" [--vars "可变参数1" "可变参数2"]
allowed-tools: Read, Grep, Glob, Bash, Write, Edit, mcp__plugin_dig-webpage_chrome-devtools__navigate_page, mcp__plugin_dig-webpage_chrome-devtools__list_pages, mcp__plugin_dig-webpage_chrome-devtools__take_screenshot, mcp__plugin_dig-webpage_chrome-devtools__list_network_requests, mcp__plugin_dig-webpage_chrome-devtools__get_network_request
---

# Dig Webpage: 挖掘网页 HTTP 接口并生成 Skill

自动找到目标网页的 HTTP 接口，分析参数，生成可复用的 Python 调用脚本和 Claude Code skill。

HAR 预处理脚本：`${CLAUDE_SKILL_DIR}/har_preprocessor.py`

## 输入

用户调用时通过 `$ARGUMENTS` 传入，或在对话中提供：
- **URL 或 HAR 文件路径**：目标页面 URL（MCP 模式）或 `.har` 文件路径（离线模式）
- **goal**：想获取什么数据（如 "获取文档的评论列表"）
- **vars**：可变参数列表（如 "文档ID", "评论筛选(全部/我的)"），这些参数在生成的脚本中会暴露为命令行参数

如果用户已经在消息中提供了这些信息，直接使用即可，不必再次询问。

## 模式判断

- 输入是 **URL**（以 `http://` 或 `https://` 开头）→ 走 **MCP 实时抓包模式**（Steps 1A）
- 输入是 **.har 文件路径** → 走 **HAR 离线分析模式**（Steps 1B）

---

## Steps 1A：MCP 实时抓包模式（推荐）

### 1A-1. 用 MCP 打开页面

用 `navigate_page` 打开目标 URL，并截图确认页面已加载：

```
navigate_page(url)
take_screenshot()
```

然后告知用户：
> "页面已打开，请在浏览器中完成所有需要抓取的操作（如切换筛选条件、翻页、点击按钮等）。**完成后回复我**，我将读取所有抓到的请求。"

等待用户回复确认操作完毕。

### 1A-2. 读取网络请求

用户确认后，调用：

```
list_network_requests()
```

获取本次会话的全量请求列表（含 reqid、URL、method、状态码、资源类型）。

### 1A-3. 在内存中构建预处理结构

对请求列表做与 `har_preprocessor.py` 等价的处理：

**过滤噪音**（跳过以下请求）：
- 资源类型为 `image`、`stylesheet`、`font`、`script`、`media`
- URL 含 `analytics`、`track`、`beacon`、`collect`、`/log`、`telemetry`、`hotjar`、`gtag`、`sentry`、`pixel` 等
- 状态码为 0 或无响应体

**按 method + URL path 归组**，同一接口的多次调用归为一组。

对每个候选请求，调用：
```
get_network_request(reqid)
```
获取完整的请求 headers、请求体、响应体。

构建与 `har_preprocessor.py` 输出格式一致的内存结构（summary + details），供后续 Steps 3-7 直接使用。

---

## Steps 1B：HAR 离线分析模式

### 1B-1. 运行预处理

不指定 `-o` 参数，脚本会自动输出到系统临时目录：

```bash
python "${CLAUDE_SKILL_DIR}/har_preprocessor.py" <har_files...>
```

脚本会自动：过滤噪音请求、归组同路径请求、输出精简 JSON，并打印输出文件路径。

### 1B-2. 读取预处理结果

用 Read 工具读取脚本输出的临时文件路径（从上一步的终端输出中获取）。
文件可能很大，先读 summary 部分（前几百行），用 Grep 搜索与 goal 相关的关键词定位候选接口。

---

## Step 3. 定位目标接口

根据用户的 **goal** 和 **vars**，在 summary 中找最匹配的接口组：
- URL path 和请求参数中与 goal 相关的关键词
- `call_count > 1` 的接口组（同一接口多次调用，参数可能不同）
- 响应预览中是否包含目标数据

找到候选后，读取完整响应体确认（MCP 模式已在内存中；HAR 模式从 details 按 `{group_id}-{call_index}` 读取）。

## Step 4. 深入分析

对目标接口分析：

1. **接口用途**：这个接口做什么
2. **参数分析**：
   - 用户声明的 vars → **必须**识别为可变参数，即使在所有请求中值相同
   - 多次调用中值不同的参数 → 可变参数（自动发现）
   - 其余 → 固定参数
3. **认证方式**：Cookie / Authorization header / 无需认证
4. **响应结构**：关键字段和数据格式

## Step 5. 生成产物

产物统一放到当前项目的 `.claude/skills/<skill_name>/` 目录下（脚本和 SKILL.md 在同一目录，自包含）。

#### a) Python CLI 脚本

文件：`.claude/skills/<skill_name>/<skill_name>.py`
- 使用 `urllib`（标准库）调用目标接口
- 脚本头部加 `sys.stdout`/`sys.stderr` 的 UTF-8 包装（Windows 兼容）
- 所有可变参数暴露为 `argparse` 命令行参数
- 认证信息（Cookie 等）从命令行参数获取
- 输出 JSON 格式结果
- 包含错误处理

#### b) Claude Code Skill 文件

文件：`.claude/skills/<skill_name>/SKILL.md`，必须包含 YAML frontmatter。
SKILL.md 中用 `${CLAUDE_SKILL_DIR}` 引用同目录的脚本：

```markdown
---
name: <skill-name>
description: <简短描述，说明这个 skill 做什么>
allowed-tools: Bash, Read
---

# <Skill Name>: <简短描述>

<一段话说明用途>

## Steps

1. 运行脚本获取数据：
   ```bash
   python "${CLAUDE_SKILL_DIR}/<skill_name>.py" --param1 值1 --param2 值2
   ```

2. 报告结果给用户：
   - 展示关键数据
   - 如果出错，提示可能的原因
```

## Step 6. 验证脚本

从抓到的真实请求中提取参数，直接调用生成的脚本进行端到端测试：

1. 提取认证信息：
   - Cookie（从 `request_headers.Cookie` / response headers）
   - 其他认证 headers（如 csrf-token、Authorization 等）
   - 所有可变参数的实际值
2. 用提取的参数调用生成的脚本，验证能正常返回数据
3. 如果接口有多种参数组合（如 "所有评论" 和 "我的评论"），每种组合都测一遍
4. 如果测试失败，分析错误原因并修复脚本后重新测试

## Step 7. 告知用户结果

- 找到的接口（URL、method）
- 参数和认证方式
- 生成的文件路径
- 使用示例
- 验证测试结果（成功/失败）
