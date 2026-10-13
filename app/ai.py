"""DeepSeek 对接层：只做「我的回答 ↔ 这张卡的标准回复」的匹配对比。

设计约束（对应需求「三、DeepSeek 的职责：只做匹配对比」）：
- 不要求措辞相同，不因为同义词、语序、句式差异判定不匹配；
- 不评价人格、情商、成熟度或整体社交能力，不生成新的标准答案；
- 必须返回严格 JSON，本地会校验结构、类型与分数范围；
- 任何失败都必须报错，绝不伪造匹配分数。
"""

import json
import socket
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_TIMEOUT = 60
# 这些情况说明「模型这次的结果不可用」，值得自动再问一次
RETRIABLE_OUTPUT_CODES = {"invalid_model_output", "empty_model_output", "truncated_output"}

SYSTEM_PROMPT = """你是「回答匹配度分析器」。你唯一的任务：比较用户对某个社交情境作出的回答，与这道题预先保存的标准回复，判断两者在意思和关键内容上的匹配程度。

必须严格遵守以下规则：
1. 不要求用户措辞与标准回复完全相同。
2. 不因为同义词、语序、句式或表达风格不同而判定不匹配。
3. 重点比较双方实际表达的意思、关键立场、边界和回应策略。
4. 明确区分「意思相同但措辞不同」与「确实遗漏或改变了关键意思」。
5. 只以题目中给出的标准回复作为唯一对照依据。
6. 不自行创造评分标准，不引入与标准回复无关的社交规范。
7. 不评价用户的人格、情商、成熟度或整体社交能力。
8. 不把用户的回答改写成「更好的答案」，也不生成新的标准答案来替代卡片内容。
9. 如果标准回复本身存在歧义，在 explanation 中说明对比依据的局限，而不是假装只有一种正确解释。
10. 只输出 JSON 对象本身，不要输出任何 JSON 以外的文字、解释、Markdown 代码块或注释。

match_score 含义：用户回答与这道题标准回复的匹配程度，0-100 的整数。
参考标尺：
- 90-100：核心意思、立场与关键边界全部一致（措辞可以完全不同）。
- 65-89：主要意思一致，但个别关键点不到位或表述含糊。
- 40-64：方向大致相同，但遗漏了标准回复中的关键点，或态度明显偏软/偏硬。
- 1-39：意思与标准回复相反，或基本没有覆盖标准回复的关键内容。
- 0：回答为空、无意义或完全答非所问。

JSON 结构（五个字段都必须出现）：
{"match_score": 0 到 100 的整数, "matched_points": ["..."], "missing_points": ["..."], "differences": ["..."], "explanation": "一两句话说明该分数的依据"}
其中 matched_points / missing_points / differences 各 0-4 条，每条不超过 40 个字，要求具体、可核对，不要写心理分析。"""


class AiError(Exception):
    """AI 调用或结果校验失败。code 用于界面区分提示，message 是给用户看的中文说明。"""

    def __init__(self, code: str, message: str, retriable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retriable = retriable


def build_messages(front: str, back: str, user_answer: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"【情境 / 问题】\n{front}\n\n【标准回复】\n{back}\n\n【我的回答】\n{user_answer}",
        },
    ]


def extract_json_text(raw) -> str | None:
    """去掉可能的 Markdown 代码块包裹，并截取最外层 JSON 对象。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            lines = lines[1:-1]
        elif lines[0].strip().startswith("```"):
            lines = lines[1:]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def parse_model_json(content: str) -> dict | None:
    """尽量把模型输出解析成 JSON 对象：原文 → 去掉代码块包裹 → 截取最外层大括号。
    全都失败时返回 None（上层会报错并重试），绝不编造结果。"""
    text = (content or "").strip()
    if not text:
        return None
    candidates = [text]
    fenced = extract_json_text(text)
    if fenced and fenced != text:
        candidates.append(fenced)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        sliced = text[start : end + 1]
        if sliced not in candidates:
            candidates.append(sliced)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _clean_points(value, field: str) -> list[str]:
    if value is None:
        raise AiError("invalid_model_output", f"DeepSeek 返回的分析结果不完整：缺少字段 {field}")
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise AiError("invalid_model_output", f"DeepSeek 返回的分析结果格式错误：{field} 应为字符串数组")
    cleaned = []
    for item in items:
        if not isinstance(item, str):
            raise AiError("invalid_model_output", f"DeepSeek 返回的分析结果格式错误：{field} 中的元素应为字符串")
        text = item.strip()
        if text:
            cleaned.append(text[:200])
        if len(cleaned) >= 6:
            break
    return cleaned


def validate_analysis(raw) -> dict:
    """严格校验模型返回的 JSON。校验失败一律抛错，绝不退化成默认分数。"""
    if not isinstance(raw, dict):
        raise AiError("invalid_model_output", "DeepSeek 返回的结果不是 JSON 对象")

    score = raw.get("match_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise AiError("invalid_model_output", "DeepSeek 返回的结果缺少 match_score，或它不是数字")
    if score != score or score in (float("inf"), float("-inf")):
        raise AiError("invalid_model_output", "DeepSeek 返回的 match_score 不是有效数字")
    if score < 0 or score > 100:
        raise AiError("invalid_model_output", f"DeepSeek 返回的 match_score 超出 0-100 范围：{score}")

    matched = _clean_points(raw.get("matched_points"), "matched_points")
    missing = _clean_points(raw.get("missing_points"), "missing_points")
    differences = _clean_points(raw.get("differences"), "differences")

    explanation = raw.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise AiError("invalid_model_output", "DeepSeek 返回的结果缺少 explanation，或它是空的")

    return {
        "match_score": int(round(score)),
        "matched_points": matched,
        "missing_points": missing,
        "differences": differences,
        "explanation": explanation.strip()[:500],
    }


def analyze(
    front: str,
    back: str,
    user_answer: str,
    api_key: str,
    model: str = "deepseek-flash",
    thinking: str = "disabled",
    timeout: int = DEFAULT_TIMEOUT,
    api_url: str = API_URL,
    opener=urllib.request.urlopen,
    retries: int = 1,
) -> dict:
    """调用 DeepSeek 并返回经过校验的分析结果；失败抛 AiError。

    模型偶尔会返回格式不对或没写完的 JSON。这类「结果不可用」的情况会自动再问一次
    （最多 retries 次），仍然失败才报错。网络、密钥、超时等问题不会重试。
    """
    last_error: AiError | None = None
    for attempt in range(retries + 1):
        try:
            return _analyze_once(
                front, back, user_answer, api_key,
                model=model, thinking=thinking, timeout=timeout, api_url=api_url, opener=opener,
            )
        except AiError as error:
            last_error = error
            if error.code not in RETRIABLE_OUTPUT_CODES or attempt >= retries:
                raise
    raise last_error  # pragma: no cover - 循环里必定已经 return 或 raise


def _analyze_once(
    front: str,
    back: str,
    user_answer: str,
    api_key: str,
    model: str = "deepseek-flash",
    thinking: str = "disabled",
    timeout: int = DEFAULT_TIMEOUT,
    api_url: str = API_URL,
    opener=urllib.request.urlopen,
) -> dict:
    """真正发一次请求。"""
    answer = (user_answer or "").strip()
    if not (front or "").strip():
        raise AiError("bad_request", "缺少卡片正面内容", retriable=False)
    if not (back or "").strip():
        raise AiError("bad_request", "缺少卡片标准回复", retriable=False)
    if not answer:
        raise AiError("bad_request", "请先输入你的回答，再提交", retriable=False)
    if not api_key:
        raise AiError(
            "not_configured",
            "尚未配置 DeepSeek API 密钥：点左侧边栏下方的「API 设置」填写，或设置系统环境变量 DEEPSEEK_API_KEY。",
            retriable=False,
        )

    payload = {
        "model": model,
        "messages": build_messages(front, back, answer),
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 2000,
        "thinking": {"type": "enabled" if thinking == "enabled" else "disabled"},
        "stream": False,
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )

    try:
        with opener(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            body = error.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            detail = (parsed.get("error") or {}).get("message") or body[:300]
        except Exception:
            detail = str(error.reason or "")
        if error.code in (401, 403):
            raise AiError("unauthorized", f"API 密钥无效或无权限（HTTP {error.code}）：{detail}。请在「API 设置」中检查密钥。") from error
        if error.code == 402:
            raise AiError("upstream_error", f"DeepSeek 账户余额或状态异常（HTTP 402）：{detail}") from error
        if error.code == 429:
            raise AiError("upstream_error", f"请求过于频繁或超出限额（HTTP 429）：{detail}。稍后再试。") from error
        raise AiError("upstream_error", f"DeepSeek 返回错误（HTTP {error.code}）：{detail}") from error
    except (socket.timeout, TimeoutError) as error:
        raise AiError("timeout", f"DeepSeek 请求超时（超过 {timeout} 秒），可以重试。") from error
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise AiError("timeout", f"DeepSeek 请求超时（超过 {timeout} 秒），可以重试。") from error
        raise AiError("network", f"无法连接 DeepSeek：{reason}。请检查网络。") from error
    except Exception as error:
        raise AiError("network", f"调用 DeepSeek 失败：{error}") from error

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise AiError("invalid_model_output", "DeepSeek 返回的响应不是合法 JSON。") from error

    choices = body.get("choices") or []
    content = None
    if choices:
        content = (choices[0].get("message") or {}).get("content")
    finish_reason = choices[0].get("finish_reason") if choices else None
    if finish_reason == "length":
        raise AiError("truncated_output", "DeepSeek 这次返回的内容被截断了，正在重试。")
    if not isinstance(content, str) or not content.strip():
        suffix = f"（finish_reason={finish_reason}）" if finish_reason else ""
        raise AiError("empty_model_output", f"DeepSeek 没有返回可用的分析内容{suffix}。")

    parsed_content = parse_model_json(content)
    if parsed_content is None:
        raise AiError("invalid_model_output", "DeepSeek 这次返回的 JSON 格式不对，正在重试。")

    result = validate_analysis(parsed_content)
    usage = body.get("usage") or {}
    return {"analysis": result, "model": model, "thinking": thinking, "usage": usage}


def check_connection(
    api_key: str,
    model: str = "deepseek-flash",
    thinking: str = "disabled",
    timeout: int = 30,
    api_url: str = API_URL,
    opener=urllib.request.urlopen,
) -> dict:
    """「测试连接」按钮：用一张示例卡片做一次真实对比。"""
    return analyze(
        front="女生说：「你是不是对每个女生都这么热情啊？」",
        back="哈哈，那得看遇到谁了。",
        user_answer="哈哈，那要分人的。",
        api_key=api_key,
        model=model,
        thinking=thinking,
        timeout=timeout,
        api_url=api_url,
        opener=opener,
    )
