# -*- coding: utf-8 -*-
"""Kimi 模型雷达 - 纯逻辑模块。

不依赖 AstrBot，便于本地单独测试。职责：
- 定义监控的接口与场景
- 对两轮模型列表做逐字段深比较（flatten + diff）
- 构建人类可读的通知消息文本
"""

from __future__ import annotations

import json
import re
from datetime import datetime

API_URL = "https://www.kimi.com/apiv2/kimi.gateway.config.v1.ConfigService/GetAvailableModels"
USER_AGENT = "kimi-android/3.1.2"

# 监控场景：scene 编号 -> 展示名
SCENES = (
    (0, "主菜单"),
    (1, "Agent 菜单"),
)

# 接口字段 -> 中文名（用于变化消息里更好读）
FIELD_NAMES = {
    "displayName": "显示名",
    "key": "标识 key",
    "modelId": "模型 ID",
    "scenario": "场景",
    # 场景响应的顶层全局字段（v1.6.0 起纳入监控）
    "defaultScenario": "默认场景",
    "defaultAgentModel": "默认 Agent 模型",
    "defaultChatModel": "默认聊天模型",
    "description": "描述",
    "inputPlaceholder": "输入提示",
    "agentMode": "Agent 模式",
    "switchableTo": "可切换至",
    "kimiPlusId": "Kimi+ 标识",
    "reasoningEffortOptions": "思考档位",
    "defaultReasoningEffort": "默认思考档位",
    "contextLengthOptions": "上下文档位",
    "defaultContextLength": "默认上下文档位",
    "minMembershipLevel": "会员门槛",
    "effort": "档位",
    "contextLength": "档位",
    "available": "可用",
    "label": "标签",
    "greeting": "开场白",
    # Kimi Code 接口（/v1/models）字段
    "id": "模型 ID",
    "display_name": "显示名",
    "created": "创建时间戳",
    "created_at": "创建时间",
    "object": "对象类型",
    "type": "类型",
    "context_length": "上下文长度",
    "supports_reasoning": "支持推理",
    "supports_image_in": "支持图片输入",
    "supports_video_in": "支持视频输入",
    "supports_dynamic_tools": "支持动态工具",
    "supports_thinking_type": "思考模式类型",
    "think_efforts": "思考档位设置",
    "support": "支持",
    "valid_efforts": "有效档位",
    "default_effort": "默认档位",
    # Moonshot 官方接口（api.moonshot.cn/v1/models）字段
    "owned_by": "归属方",
    "permission": "权限信息",
    "root": "根模型",
    "parent": "父模型",
    "reasoning_efforts": "推理档位设置",
    "organization": "组织",
    "group": "分组",
    "is_blocking": "是否阻断",
}

_MISSING = object()

# 每轮请求都会轻微漂移的字段（接口侧的列表生成时间，非模型属性），不参与对比：
# Moonshot 官方的 created 每次返回都会变动若干秒，若参与对比会每分钟误报一次
IGNORED_FIELDS = ("created",)


def model_key(model):
    """为模型取一个稳定的匹配键（兼容 kimi.com 与 Kimi Code 两种字段名）。"""
    for field in ("key", "modelId", "id", "displayName", "display_name"):
        value = model.get(field)
        if value:
            return str(value)
    return "?"


def model_display_name(model):
    """取模型的展示名（兼容多种字段名）。"""
    for field in ("displayName", "display_name"):
        value = model.get(field)
        if value:
            return str(value)
    return model_key(model)


def models_url(base_url):
    """拼模型列表地址：base_url 可能不带 /v1，也可能已经带上了。"""
    base = str(base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


def flatten(obj, prefix=""):
    """把嵌套结构压平成 {路径: 叶子值}，用于逐字段比较。"""
    items = {}
    if isinstance(obj, dict):
        for name in obj:
            path = "%s.%s" % (prefix, name) if prefix else name
            items.update(flatten(obj[name], path))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            items.update(flatten(value, "%s[%d]" % (prefix, index)))
    else:
        items[prefix] = obj
    return items


def humanize_path(path):
    """reasoningEffortOptions[2].displayName -> 思考档位[2].显示名"""
    parts = []
    for segment in path.split("."):
        match = re.match(r"^([A-Za-z0-9_]+)(\[\d+\])?$", segment)
        if not match:
            parts.append(segment)
            continue
        name, index = match.group(1), match.group(2) or ""
        parts.append(FIELD_NAMES.get(name, name) + index)
    return ".".join(parts)


def format_value(value, limit=80):
    """把叶子值转成简短可读文本；长文本保留头尾，便于看出变化。"""
    if isinstance(value, str):
        text = value or "（空）"
    elif isinstance(value, bool):
        text = "是" if value else "否"
    elif value is None:
        text = "（空）"
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        text = json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    head = limit // 2 + 5
    tail = limit - head - 1
    return text[:head] + "…" + text[-tail:]


def focus_pair(old_value, new_value, limit=80, context=30):
    """两个值的变化展示：长文本裁掉公共前后缀，只保留变化附近。"""
    if not (isinstance(old_value, str) and isinstance(new_value, str)):
        return format_value(old_value), format_value(new_value)
    if not old_value or not new_value:
        # 空字符串无法做前后缀裁剪，直接按普通值展示
        return format_value(old_value), format_value(new_value)
    old_text = old_value.replace("\n", " ")
    new_text = new_value.replace("\n", " ")
    if len(old_text) <= limit and len(new_text) <= limit:
        return old_text, new_text

    prefix = 0
    while (prefix < min(len(old_text), len(new_text))
           and old_text[prefix] == new_text[prefix]):
        prefix += 1
    suffix = 0
    while (suffix < min(len(old_text), len(new_text)) - prefix
           and old_text[-1 - suffix] == new_text[-1 - suffix]):
        suffix += 1

    def crop(text):
        start = max(0, prefix - context)
        stop = len(text) - suffix + context
        head_mark = "…" if start > 0 else ""
        tail_mark = "…" if stop < len(text) else ""
        segment = text[start:stop]
        if len(segment) > 160:
            segment = segment[:159] + "…"
            tail_mark = ""
        return head_mark + segment + tail_mark

    return crop(old_text), crop(new_text)


def _compare_fields(old_model, new_model, display, skip=()):
    """两个同模型对象的逐字段比较，返回细节行列表。"""
    old_flat = flatten(old_model)
    new_flat = flatten(new_model)
    details = []
    for path in sorted(set(old_flat) | set(new_flat)):
        if path in skip or path in IGNORED_FIELDS:
            continue
        old_value = old_flat.get(path, _MISSING)
        new_value = new_flat.get(path, _MISSING)
        field = humanize_path(path)
        if old_value is _MISSING:
            details.append(
                "  %s 新增字段 %s，值：%s"
                % (display, field, format_value(new_value))
            )
        elif new_value is _MISSING:
            details.append(
                "  %s 删除字段 %s，原值：%s"
                % (display, field, format_value(old_value))
            )
        elif old_value != new_value:
            old_show, new_show = focus_pair(old_value, new_value)
            separator = " " if (field and ord(field[0]) < 128) else ""
            details.append(
                "  %s 的%s%s：%s 变为 %s"
                % (display, separator, field, old_show, new_show)
            )
    return details


def diff_models(old_models, new_models):
    """逐个模型逐字段比较，返回人类可读的变化行列表。"""
    old_by_key = {model_key(m): m for m in old_models}
    new_by_key = {model_key(m): m for m in new_models}

    lines = []

    # 同名配对：标识变了但显示名没变，视为同一模型更换了内部标识
    removed = [(k, m) for k, m in old_by_key.items() if k not in new_by_key]
    added = [(k, m) for k, m in new_by_key.items() if k not in old_by_key]
    paired_old, paired_new = set(), set()
    for old_key, old_model in removed:
        name = old_model.get("displayName") or old_model.get("display_name")
        if not name:
            continue
        match = next(
            ((k, m) for k, m in added
             if k not in paired_new
             and (m.get("displayName") or m.get("display_name")) == name),
            None,
        )
        if match is None:
            continue
        new_key, new_model = match
        paired_old.add(old_key)
        paired_new.add(new_key)
        details = ["  %s 的标识：%s 变为 %s" % (name, old_key, new_key)]
        details.extend(_compare_fields(old_model, new_model, name, skip=("key",)))
        lines.append("字段变化：")
        lines.extend(details)

    for key, model in added:
        if key in paired_new:
            continue
        lines.append("新增模型：%s（标识 %s）" % (model_display_name(model), key))
    for key, model in removed:
        if key in paired_old:
            continue
        lines.append("移除模型：%s（标识 %s）" % (model_display_name(model), key))

    for key in new_by_key:
        if key not in old_by_key:
            continue
        old_model, new_model = old_by_key[key], new_by_key[key]
        display = (
            new_model.get("displayName") or new_model.get("display_name")
            or old_model.get("displayName") or old_model.get("display_name") or key
        )
        details = _compare_fields(old_model, new_model, display)
        if details:
            lines.append("字段变化：")
            lines.extend(details)

    return lines


def scene_models(payload):
    """从场景响应里取出模型列表；兼容旧版快照直接存模型列表的情况。"""
    if isinstance(payload, dict):
        return payload.get("availableModels") or []
    return payload or []


def diff_scene(old_payload, new_payload):
    """场景响应的完整比较：接口顶层全局配置 + 模型列表。

    顶层的 defaultScenario / defaultAgentModel / defaultChatModel 等全局字段
    在 v1.6.0 之前不在监控范围内，现在一并对比。
    """
    old_obj = (
        old_payload if isinstance(old_payload, dict)
        else {"availableModels": scene_models(old_payload)}
    )
    new_obj = (
        new_payload if isinstance(new_payload, dict)
        else {"availableModels": scene_models(new_payload)}
    )
    old_meta = {k: v for k, v in old_obj.items() if k != "availableModels"}
    new_meta = {k: v for k, v in new_obj.items() if k != "availableModels"}
    lines = list(_compare_fields(old_meta, new_meta, "接口"))
    lines.extend(diff_models(scene_models(old_obj), scene_models(new_obj)))
    return lines


def build_change_message(changes_by_scene, now=None):
    """把各监测源的变化行合并成一条通知消息。"""
    now = now or datetime.now()
    lines = ["Kimi 模型有新变化（%s）" % now.strftime("%m-%d %H:%M"), ""]
    for label, diffs in changes_by_scene.items():
        lines.append(label + "：")
        lines.extend("  " + line for line in diffs)
        lines.append("")
    return "\n".join(lines).rstrip()


def build_online_message(scenes_models, first, saved_at=None, now=None, extra_sources=None):
    """插件激活时的上线通知。

    extra_sources：[（标签, 模型列表或 None）, ...]，None 表示未配置或暂无基线。
    """
    now = now or datetime.now()
    parts = []
    if first:
        parts.append("Kimi 模型雷达已上线（首次部署，已建立基线）")
    else:
        parts.append("Kimi 模型雷达已重载上线（基线完好，继续监视）")
    parts.append("")
    for scene, label in SCENES:
        models = scene_models(scenes_models.get(scene))
        names = "、".join(model_display_name(m) for m in models) or "（空）"
        parts.append("%s %d 个：%s" % (label, len(models), names))
    for label, models in (extra_sources or []):
        if models is None:
            parts.append("%s：未配置 Key 或暂无基线（跳过）" % label)
        else:
            names = "、".join(model_display_name(m) for m in models) or "（空）"
            parts.append("%s %d 个：%s" % (label, len(models), names))
    parts.append("")
    if not first and saved_at:
        parts.append("上次快照：%s" % saved_at)
    parts.append("有变化我会马上告诉你（当前时间 %s）" % now.strftime("%m-%d %H:%M"))
    return "\n".join(parts)


def build_source_ready_message(label, models, now=None):
    """某个数据源的基线建立完成通知（填好 Key 后第一次成功拉取时发送）。"""
    now = now or datetime.now()
    names = "、".join(model_display_name(m) for m in models) or "（空）"
    return (
        "%s 模型监测已就绪（%s）\n\n"
        "当前 %d 个模型：%s\n"
        "API Key 已生效，之后任何字段变化都会通知你。"
        % (label, now.strftime("%m-%d %H:%M"), len(models), names)
    )


def split_message(text, limit=1600):
    """按行切分超长消息，保证每条不超过 limit 字符。"""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        chunks.append(current)
    return chunks
