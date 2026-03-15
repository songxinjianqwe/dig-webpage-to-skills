#!/usr/bin/env python3
"""
HAR 预处理器：过滤噪音、归组同路径请求、输出精简 JSON 供 Claude 分析。

用法：
    python har_preprocessor.py <har_file1> [har_file2 ...] [-o output.json]

输出：
    <第一个har文件名>_preprocessed.json
"""

import argparse
import io
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Windows 控制台 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 🔇 噪音过滤规则
NOISE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",  # 图片
    ".woff", ".woff2", ".ttf", ".otf", ".eot",  # 字体
    ".css",  # 样式
    ".map",  # sourcemap
}

NOISE_URL_PATTERNS = [
    "analytics", "track", "beacon", "collect", "/log", "telemetry",
    "favicon", "hotjar", "google-analytics", "gtag", "pixel",
    "sentry", "bugsnag", "datadog",
]

# 📌 保留的关键 Headers
KEEP_HEADERS = {
    "cookie", "authorization", "content-type", "accept", "referer",
    "origin", "user-agent",
}
KEEP_HEADER_PREFIXES = ("x-", "sec-")

# 截断长度
PREVIEW_LENGTH = 300
DETAIL_LENGTH = 5000


def should_filter(entry):
    """判断请求是否应该被过滤掉"""
    url = entry.get("request", {}).get("url", "")
    parsed = urlparse(url)
    path = parsed.path.lower()

    # 扩展名过滤
    for ext in NOISE_EXTENSIONS:
        if path.endswith(ext):
            return True

    # URL 关键词过滤
    url_lower = url.lower()
    for pattern in NOISE_URL_PATTERNS:
        if pattern in url_lower:
            return True

    # MIME 类型过滤（图片、字体、CSS）
    mime = entry.get("response", {}).get("content", {}).get("mimeType", "")
    if mime:
        mime_lower = mime.lower()
        if any(t in mime_lower for t in ["image/", "font/", "text/css"]):
            return True

    # 无响应体的过滤
    response_content = entry.get("response", {}).get("content", {})
    text = response_content.get("text", "")
    size = response_content.get("size", 0)
    if not text and size == 0:
        return True

    # 纯 JS 文件加载（非 XHR）—— 看 _resourceType 或 content-type
    resource_type = entry.get("_resourceType", "")
    if resource_type == "script":
        return True
    if not resource_type and "javascript" in mime.lower() and entry.get("request", {}).get("method", "") == "GET":
        # 没有 resourceType 标记但 mime 是 js 且是 GET → 大概率是 JS 文件加载
        return True

    return False


def filter_headers(headers_list):
    """只保留关键 Headers，返回 dict"""
    result = {}
    for h in headers_list:
        name = h.get("name", "").lower()
        if name in KEEP_HEADERS or any(name.startswith(p) for p in KEEP_HEADER_PREFIXES):
            result[h["name"]] = h["value"]
    return result


def get_response_text(entry):
    """提取响应体文本"""
    content = entry.get("response", {}).get("content", {})
    text = content.get("text", "")
    if not text:
        return ""
    # 尝试格式化 JSON
    mime = content.get("mimeType", "")
    if "json" in mime.lower():
        try:
            obj = json.loads(text)
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError):
            pass
    return text


def truncate(text, max_len):
    """截断文本"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [截断，原始长度 {len(text)} 字符]"


def extract_request_body(entry):
    """提取请求体"""
    post_data = entry.get("request", {}).get("postData", {})
    if not post_data:
        return None
    text = post_data.get("text", "")
    mime = post_data.get("mimeType", "")
    if "json" in mime.lower() and text:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
    return text if text else None


def process_har(har_path):
    """处理单个 HAR 文件，返回过滤后的请求列表"""
    with open(har_path, "r", encoding="utf-8") as f:
        har_data = json.load(f)

    entries = har_data.get("log", {}).get("entries", [])
    source_name = Path(har_path).name
    results = []

    for entry in entries:
        if should_filter(entry):
            continue

        request = entry.get("request", {})
        response = entry.get("response", {})
        url = request.get("url", "")
        parsed = urlparse(url)
        response_text = get_response_text(entry)
        response_size = len(response_text.encode("utf-8")) if response_text else 0

        results.append({
            "source": source_name,
            "method": request.get("method", ""),
            "url": url,
            "url_path": parsed.path,
            "query_params": {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()},
            "request_headers": filter_headers(request.get("headers", [])),
            "request_body": extract_request_body(entry),
            "status": response.get("status", 0),
            "response_mime": response.get("content", {}).get("mimeType", ""),
            "response_size": response_size,
            "response_preview": truncate(response_text, PREVIEW_LENGTH),
            "_response_body_full": response_text,  # 内部字段，归组后放 details
        })

    return results


def group_requests(all_requests):
    """按 method + url_path 归组"""
    groups = defaultdict(list)
    for req in all_requests:
        key = f"{req['method']}|{req['url_path']}"
        groups[key].append(req)
    return groups


def build_output(groups):
    """构建最终输出结构"""
    summary = []
    details = {}
    group_id = 1

    # 按 call_count 降序排列（多次调用的接口优先）
    sorted_groups = sorted(groups.items(), key=lambda x: -len(x[1]))

    for key, calls in sorted_groups:
        group = {
            "group_id": group_id,
            "url_path": calls[0]["url_path"],
            "method": calls[0]["method"],
            "call_count": len(calls),
            "calls": [],
        }

        for call_idx, call in enumerate(calls):
            # summary 里的 call（不含完整响应体）
            call_summary = {k: v for k, v in call.items() if k != "_response_body_full"}
            group["calls"].append(call_summary)

            # details 里存完整响应体
            full_body = call.get("_response_body_full", "")
            if full_body:
                details[f"{group_id}-{call_idx}"] = {
                    "response_body": truncate(full_body, DETAIL_LENGTH),
                }

        summary.append(group)
        group_id += 1

    return {"summary": summary, "details": details}


def main():
    parser = argparse.ArgumentParser(
        description="HAR 预处理器：过滤噪音、归组请求、输出精简 JSON"
    )
    parser.add_argument("har_files", nargs="+", help="HAR 文件路径（支持多个）")
    parser.add_argument("-o", "--output", help="输出文件路径（默认自动生成）")
    args = parser.parse_args()

    # 处理所有 HAR 文件
    all_requests = []
    for har_file in args.har_files:
        path = Path(har_file)
        if not path.exists():
            print(f"❌ 文件不存在: {har_file}", file=sys.stderr)
            sys.exit(1)
        print(f"📂 处理: {path.name}")
        requests = process_har(path)
        print(f"   过滤后保留 {len(requests)} 个请求")
        all_requests.extend(requests)

    if not all_requests:
        print("⚠️ 没有找到有效的 API 请求", file=sys.stderr)
        sys.exit(1)

    # 归组
    groups = group_requests(all_requests)
    output = build_output(groups)

    # 统计
    total_groups = len(output["summary"])
    multi_call_groups = sum(1 for g in output["summary"] if g["call_count"] > 1)
    print(f"\n📊 共 {total_groups} 个接口组，其中 {multi_call_groups} 个有多次调用")

    # 输出（默认写到系统临时目录）
    if args.output:
        output_path = args.output
    else:
        tmp_dir = Path(tempfile.gettempdir())
        output_path = str(tmp_dir / (Path(args.har_files[0]).stem + "_preprocessed.json"))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ 已输出到: {output_path}")


if __name__ == "__main__":
    main()
