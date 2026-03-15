# Dig Webpage to Skill — 需求与设计文档

## 需求起源

用户经常需要从 web 页面中提取特定数据（如评论列表、文档信息、作者等），但面临以下痛点：

1. **不知道哪个 HTTP 请求包含目标数据** — 一个页面可能有几十个网络请求
2. **手动在 DevTools 里逐个翻看** — 费时费力
3. **数据可能藏在 XHR、纯 HTML、甚至对象存储文件中** — 不确定在哪
4. **同一接口有不同参数组合** — 如"所有评论"vs"我的评论"对应不同 filter 值

用户希望：给一个 HAR 抓包文件 + 描述需求，AI 自动找到目标接口并生成可复用的 skill。

## 核心设计决策

### 为什么用「Python 预处理 + Claude 直接分析」而非「Python 调 claude CLI」？

最初设计是 Python 脚本内部通过 `subprocess` 调用 `claude -p "..."` 来分析。但问题是：

- 这个脚本在 Claude Code 的 skill 中被调用 → 导致 Claude 嵌套调用自己
- Python 中间层增加了复杂度，且 AI 分析时无法利用对话上下文

**最终方案**：Python 只做纯数据处理（过滤噪音、归组、截断），所有智能分析由当前 Claude 会话直接完成。好处：
- 没有嵌套调用
- Claude 上下文里同时有 goal、vars、所有请求数据，分析质量更高
- 生成脚本和 skill 一气呵成

### 多 HAR 文件 vs 单 HAR 多次调用

两种场景都需要支持：

1. **多 HAR 文件**：用户分别在不同操作下抓了多个包
   ```bash
   python har_preprocessor.py all_comments.har my_comments.har
   ```

2. **单 HAR 多次调用**：用户在同一个页面上先选"所有评论"再选"我的评论"，然后导出一个 HAR
   ```bash
   python har_preprocessor.py comments.har
   ```

预处理器会**按 method + URL path 自动归组**，不管请求来自同一 HAR 还是不同 HAR，同一接口的多次调用都会归为一组，便于 Claude 对比参数差异。

### `--vars` 声明可变参数

用户比 AI 更清楚哪些参数是真正的变量。例如 `doc_id` 在所有请求中值相同（因为用户只看了一个文档），AI 会误判为固定值。

通过 `--vars "文档ID" "评论筛选"` 预先声明，告诉 Claude：
- 这些是可变参数，即使在所有请求中值相同也不能视为固定值
- 在请求中重点寻找这些变量对应的字段
- 生成的脚本中必须暴露为命令行参数

## 工作流全景

### MCP 实时抓包模式（推荐）

```
用户                             Claude Code (MCP)
─────────────────               ──────────────────────
1. 调用 /dig-webpage <url>
   提供 goal + vars
                                2. navigate_page 打开目标页面
                                3. 告知用户"请操作页面"
4. 在浏览器完成所有操作
   （切换条件、翻页等）
5. 回复"操作完了"
                                6. list_network_requests 读取全量请求
                                7. get_network_request 逐个获取详情
                                8. 在内存中构建等价 preprocessed 结构
                                9. AI 定位目标接口
                                10. Claude 分析参数、认证、响应结构
                                11. Claude 生成 Python 脚本 + skill 文件
                                12. 用真实参数验证脚本
                                13. 用户即可使用新生成的 skill
```

### HAR 离线分析模式（兼容）

```
用户操作浏览器                    Claude Code
─────────────────               ──────────────────────
1. 打开目标页面
2. DevTools → Network
3. 操作页面（切换条件等）
4. 右键 → Save all as HAR
5. 把 .har 放到项目目录
                                6. 用户调用 /dig-webpage <har路径>
                                7. 用户提供 goal + vars
                                8. 运行 har_preprocessor.py → 精简 JSON
                                9. Claude 读取 JSON，AI 定位接口
                                10. Claude 分析参数、认证、响应结构
                                11. Claude 生成 Python 脚本 + skill 文件
                                12. 用户即可使用新生成的 skill
```

## 文件清单

| 文件 | 用途 |
|------|------|
| `dig_webpage_to_skill/har_preprocessor.py` | HAR 预处理脚本（过滤噪音、归组、输出 JSON） |
| `dig_webpage_to_skill/requirements.md` | 本文档 |
| `.claude/skills/dig-webpage.md` | Claude Code skill 定义 |

## 预处理输出格式

```json
{
  "summary": [
    {
      "group_id": 1,
      "url_path": "/api/v2/docs/xxx/comments",
      "method": "GET",
      "call_count": 2,
      "calls": [
        {
          "source": "comments.har",
          "method": "GET",
          "url": "https://example.com/api/v2/docs/xxx/comments?filter=all",
          "query_params": {"filter": "all", "doc_id": "xxx"},
          "request_headers": {"Cookie": "..."},
          "request_body": null,
          "status": 200,
          "response_mime": "application/json",
          "response_size": 12300,
          "response_preview": "前300字符..."
        },
        { "...第二次调用，filter=mine..." }
      ]
    }
  ],
  "details": {
    "1-0": { "response_body": "完整响应（截断到5000字符）" },
    "1-1": { "response_body": "..." }
  }
}
```

- `summary`：快速浏览和定位（体积小）
- `details`：按需读取完整响应体，key 格式为 `{group_id}-{call_index}`
