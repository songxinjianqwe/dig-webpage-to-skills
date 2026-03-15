#!/usr/bin/env python3
"""
MCP 请求预处理器：两阶段处理，输出与 har_preprocessor.py 完全一致的精简 JSON。

阶段一：过滤候选 reqid（输入：list_network_requests 的原始文本输出，由 Claude 用 Write 工具直接保存）
    python mcp_preprocessor.py filter <requests_list_file> [-o candidate_reqids.json]
    输入格式：list_network_requests 返回的原始文本（含 "reqid=N METHOD URL [STATUS]" 行）
    输出：[reqid, reqid, ...] 的 JSON 数组，供 Claude 逐个调 get_network_request

阶段二：处理详情（输入：get_network_request 详情的 JSON 数组）
    python mcp_preprocessor.py process <details_file> [-o preprocessed.json]
    输出：{ "summary": [...], "details": {...} }，格式同 har_preprocessor.py
"""

import argparse
import io
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Windows 控制台 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ============================================================
# 噪音过滤规则（与 har_preprocessor.py 保持一致）
# ============================================================

NOISE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".css", ".map",
}

NOISE_URL_PATTERNS = [
    "analytics", "track", "beacon", "collect", "/log", "telemetry",
    "favicon", "hotjar", "google-analytics", "gtag", "pixel",
    "sentry", "bugsnag", "datadog",
]

# list_network_requests 返回的 resourceType 噪音类型
NOISE_RESOURCE_TYPES = {"image", "stylesheet", "font", "script", "media"}

# 保留的关键 Headers
KEEP_HEADERS = {
    "cookie", "authorization", "content-type", "accept", "referer",
    "origin", "user-agent",
}
KEEP_HEADER_PREFIXES = ("x-", "sec-")

PREVIEW_LENGTH = 300
DETAIL_LENGTH = 5000


# ============================================================
# 阶段一：从 list_network_requests 原始文本中筛出候选 reqid
# ============================================================

def parse_request_list(text):
    """
    解析 list_network_requests 的原始文本输出，返回请求摘要列表。

    输入格式（由 Claude 用 Write 工具直接保存的原始文本）：
        reqid=1 GET https://example.com/api/data [200]
        reqid=2 POST https://example.com/api/submit [201]
        ...
    也支持行内含 resourceType 的扩展格式：
        reqid=1 GET https://... [200] fetch
    """
    requests = []
    pattern = re.compile(
        r"reqid=(\d+)\s+(\w+)\s+(https?://\S+?)\s+\[(\d+)\](?:\s+(\S+))?",
        re.MULTILINE,
    )
    for m in pattern.finditer(text):
        requests.append({
            "reqid": int(m.group(1)),
            "method": m.group(2),
            "url": m.group(3),
            "status": int(m.group(4)),
            "resourceType": m.group(5) or "",
        })
    return requests


def should_filter_summary(req):
    """阶段一粗过滤：只看 url、resourceType、status，不需要响应体"""
    url = req.get("url", "")
    parsed = urlparse(url)
    path = parsed.path.lower()

    # 状态码为 0 过滤
    if req.get("status", 0) == 0:
        return True

    # resourceType 噪音过滤
    if req.get("resourceType", "").lower() in NOISE_RESOURCE_TYPES:
        return True

    # 扩展名过滤
    for ext in NOISE_EXTENSIONS:
        if path.endswith(ext):
            return True

    # URL 关键词过滤
    url_lower = url.lower()
    for pattern in NOISE_URL_PATTERNS:
        if pattern in url_lower:
            return True

    return False


def cmd_filter(args):
    """阶段一：输出候选 reqid 列表"""
    if args.input == "-":
        text = sys.stdin.read()
    else:
        p = Path(args.input)
        if not p.exists():
            print(f"❌ 文件不存在: {args.input}", file=sys.stderr)
            sys.exit(1)
        text = p.read_text(encoding="utf-8")

    requests = parse_request_list(text)
    print(f"📥 共读取到 {len(requests)} 条请求")

    candidates = [r for r in requests if not should_filter_summary(r)]
    print(f"🔍 粗过滤后保留 {len(candidates)} 条候选请求")

    if not candidates:
        print("⚠️ 没有找到候选请求", file=sys.stderr)
        sys.exit(1)

    reqids = [r["reqid"] for r in candidates]

    if args.output:
        out_path = args.output
    else:
        out_path = str(Path(tempfile.gettempdir()) / "mcp_candidate_reqids.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(reqids, f)

    print(f"✅ 候选 reqid 已输出到: {out_path}")
    print(f"   共 {len(reqids)} 个：{reqids[:20]}{'...' if len(reqids) > 20 else ''}")


# ============================================================
# 阶段二：处理 get_network_request 详情，输出精简 JSON
# ============================================================

def filter_headers(headers):
    """
    过滤 headers，只保留关键字段。
    支持 dict 或 list[{name, value}] 两种格式。
    """
    result = {}
    if isinstance(headers, dict):
        for name, value in headers.items():
            name_lower = name.lower()
            if name_lower in KEEP_HEADERS or any(name_lower.startswith(p) for p in KEEP_HEADER_PREFIXES):
                result[name] = value
    elif isinstance(headers, list):
        for h in headers:
            name = h.get("name", "")
            name_lower = name.lower()
            if name_lower in KEEP_HEADERS or any(name_lower.startswith(p) for p in KEEP_HEADER_PREFIXES):
                result[name] = h.get("value", "")
    return result


def truncate(text, max_len):
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [截断，原始长度 {len(text)} 字符]"


def process_response_body(body, mime=""):
    """格式化响应体为字符串"""
    if not body:
        return ""
    if isinstance(body, (dict, list)):
        return json.dumps(body, ensure_ascii=False, indent=2)
    text = str(body)
    if "json" in mime.lower():
        try:
            obj = json.loads(text)
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError):
            pass
    return text


def should_filter_detail(req):
    """阶段二精过滤：在已获取详情的基础上再次检查"""
    url = req.get("url", "")
    parsed = urlparse(url)
    path = parsed.path.lower()

    if req.get("status", 0) == 0:
        return True
    if req.get("resourceType", "").lower() in NOISE_RESOURCE_TYPES:
        return True
    for ext in NOISE_EXTENSIONS:
        if path.endswith(ext):
            return True
    url_lower = url.lower()
    for pattern in NOISE_URL_PATTERNS:
        if pattern in url_lower:
            return True

    # 无响应体过滤
    mime = req.get("response_mime") or req.get("mimeType", "")
    if any(t in mime.lower() for t in ["image/", "font/", "text/css", "javascript"]):
        return True

    return False


def process_details(detail_list):
    """处理 get_network_request 详情列表，返回归组后的输出结构"""
    processed = []

    for req in detail_list:
        if should_filter_detail(req):
            continue

        url = req.get("url", "")
        parsed = urlparse(url)
        method = req.get("method", "GET")
        status = req.get("status", 0)
        mime = req.get("response_mime") or req.get("mimeType", "")

        response_text = process_response_body(req.get("response_body", ""), mime)
        response_size = len(response_text.encode("utf-8")) if response_text else 0

        # 无响应体跳过
        if not response_text:
            continue

        req_headers = filter_headers(req.get("request_headers") or req.get("headers", {}))
        req_body = req.get("request_body")

        processed.append({
            "reqid": req.get("reqid"),
            "method": method,
            "url": url,
            "url_path": parsed.path,
            "query_params": {k: v[0] if len(v) == 1 else v
                             for k, v in parse_qs(parsed.query).items()},
            "request_headers": req_headers,
            "request_body": req_body,
            "status": status,
            "response_mime": mime,
            "response_size": response_size,
            "response_preview": truncate(response_text, PREVIEW_LENGTH),
            "_response_body_full": response_text,
        })

    return processed


def group_requests(all_requests):
    """按 method + url_path 归组"""
    groups = defaultdict(list)
    for req in all_requests:
        key = f"{req['method']}|{req['url_path']}"
        groups[key].append(req)
    return groups


def build_output(groups):
    """构建最终输出结构（与 har_preprocessor.py 完全一致）"""
    summary = []
    details = {}
    group_id = 1

    for key, calls in sorted(groups.items(), key=lambda x: -len(x[1])):
        group = {
            "group_id": group_id,
            "url_path": calls[0]["url_path"],
            "method": calls[0]["method"],
            "call_count": len(calls),
            "calls": [],
        }
        for call_idx, call in enumerate(calls):
            call_summary = {k: v for k, v in call.items() if k != "_response_body_full"}
            group["calls"].append(call_summary)
            full_body = call.get("_response_body_full", "")
            if full_body:
                details[f"{group_id}-{call_idx}"] = {
                    "response_body": truncate(full_body, DETAIL_LENGTH),
                }
        summary.append(group)
        group_id += 1

    return {"summary": summary, "details": details}


def cmd_process(args):
    """阶段二：处理详情并输出精简 JSON"""
    if args.input == "-":
        raw = sys.stdin.read()
    else:
        p = Path(args.input)
        if not p.exists():
            print(f"❌ 文件不存在: {args.input}", file=sys.stderr)
            sys.exit(1)
        raw = p.read_text(encoding="utf-8")

    try:
        detail_list = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(detail_list, list):
        print("❌ 输入必须是 JSON 数组", file=sys.stderr)
        sys.exit(1)

    print(f"📥 读取到 {len(detail_list)} 条请求详情")

    processed = process_details(detail_list)
    print(f"🔍 精过滤后保留 {len(processed)} 条有效 API 请求")

    if not processed:
        print("⚠️ 没有找到有效的 API 请求", file=sys.stderr)
        sys.exit(1)

    groups = group_requests(processed)
    output = build_output(groups)

    total = len(output["summary"])
    multi = sum(1 for g in output["summary"] if g["call_count"] > 1)
    print(f"📊 共 {total} 个接口组，其中 {multi} 个有多次调用")

    if args.output:
        out_path = args.output
    else:
        out_path = str(Path(tempfile.gettempdir()) / "mcp_requests_preprocessed.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ 已输出到: {out_path}")


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="MCP 请求预处理器：两阶段处理网络请求"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_filter = sub.add_parser("filter", help="阶段一：从 list_network_requests 输出中筛出候选 reqid")
    p_filter.add_argument("input", help="list_network_requests 输出的文本文件，或 '-' 从 stdin 读取")
    p_filter.add_argument("-o", "--output", help="输出 reqid 列表的 JSON 文件路径")

    p_process = sub.add_parser("process", help="阶段二：处理 get_network_request 详情，输出精简 JSON")
    p_process.add_argument("input", help="get_network_request 详情的 JSON 数组文件，或 '-' 从 stdin 读取")
    p_process.add_argument("-o", "--output", help="输出文件路径（默认写到临时目录）")

    args = parser.parse_args()
    if args.cmd == "filter":
        cmd_filter(args)
    elif args.cmd == "process":
        cmd_process(args)


if __name__ == "__main__":
    main()
