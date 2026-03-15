---
name: dig-webpage
description: 自动找到目标网页的 HTTP 接口，分析参数，生成可复用的 Python 调用脚本和 Claude Code skill。支持两种模式：MCP 实时抓包（推荐）和 HAR 文件离线分析。
argument-hint: <url_or_har_file> --goal "目标描述" [--vars "可变参数1" "可变参数2"]
allowed-tools: Read, Grep, Glob, Bash, Write, Edit, mcp__plugin_dig-webpage-to-skills_chrome-devtools__navigate_page, mcp__plugin_dig-webpage-to-skills_chrome-devtools__list_pages, mcp__plugin_dig-webpage-to-skills_chrome-devtools__take_screenshot, mcp__plugin_dig-webpage-to-skills_chrome-devtools__list_network_requests, mcp__plugin_dig-webpage-to-skills_chrome-devtools__get_network_request
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

### 1A-3. 用 Python 脚本预处理请求

**第一步：保存原始请求列表文本到临时文件**

⚠️ **即使请求量很少、肉眼已能看出目标接口，也必须执行本步骤**，否则后续 filter/process 脚本无输入可用。

`list_network_requests()` 的输出有两种情况：
- **输出直接可见**（小请求量）：用 Write 工具将文本直接保存到 `C:/Temp/mcp_list.txt`
- **输出超大被保存到 tool-results 文件**（返回提示 "Output too large, saved to: <path>"）：需要用 Python 提取文本再保存：

```bash
python -c "
import json, sys
sys.stdout.reconfigure(encoding='utf-8')
with open(r'<tool-results文件路径>', encoding='utf-8') as f:
    data = json.load(f)
text = data[0]['text']
with open(r'C:/Temp/mcp_list.txt', 'w', encoding='utf-8') as f:
    f.write(text)
print('Done, length:', len(text))
"
```

**第二步：用 goal 关键词在请求列表中快速定位候选 reqid**

不要直接批量获取所有过滤后的 reqid 详情（可能上百个，非常慢）。先在 `mcp_list.txt` 文本里搜索与 goal 相关的关键词，缩小到少数几个候选：

```bash
python "${CLAUDE_SKILL_DIR}/mcp_preprocessor.py" filter "C:/Temp/mcp_list.txt"
# 输出：临时目录下的 mcp_candidate_reqids.json，内容为 [reqid, reqid, ...]
```

然后直接在 `mcp_list.txt` 文本中用 Grep 或肉眼搜索与 goal 相关的路径关键词（如 "docs"、"comment"、"list" 等），从候选中进一步筛出 **最相关的 3-10 个 reqid**，只对这些调用 `get_network_request`。

**第三步：获取精选候选请求的完整详情**

对筛出的少数 reqid 调用：
```
get_network_request(reqid)
```
将所有返回结果收集为 JSON 数组，用 Write 工具保存到临时文件（如 `C:/Temp/mcp_details.json`）。

**第四步：处理详情，生成精简结构**

```bash
python "${CLAUDE_SKILL_DIR}/mcp_preprocessor.py" process "C:/Temp/mcp_details.json"
# 输出：临时目录下的 mcp_requests_preprocessed.json
# 格式与 har_preprocessor.py 完全一致：{ "summary": [...], "details": {...} }
```

用 Read 工具读取输出文件，供后续 Steps 3-7 使用。

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
