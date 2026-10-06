"""Legacy copy text helpers keep unverified product facts visible."""


def ensure_unknown_markers(analysis: dict) -> str:
    """Append missing-fact markers unless the copy already contains one."""
    unknowns = analysis.get("unknowns") or []
    text = str(analysis.get("text") or "").strip()
    if not unknowns or "待补充" in text:
        return text
    return f'{text}\n待补充：{"、".join(unknowns)}'


def build_module_brief(
    module_name: str, brief: str, analysis: dict, settings: dict
) -> str:
    """Preserve reference fidelity and distinguish facts from unknowns."""
    facts = "；".join(analysis.get("facts") or []) or "待补充"
    unknowns = "；".join(analysis.get("unknowns") or []) or "无"
    return (
        f"请根据商品原图制作电商详情页模块「{module_name}」。保留原商品主要外观、包装、颜色和商标，不增添不存在的配件或认证。"
        f"已知事实：{facts}。用户资料：{brief}。未知或未证实：{unknowns}，不得把未知事项写成事实。"
        f"平台：{settings.get('platform', '')}；市场：{settings.get('market', '')}；"
        f"语言：{settings.get('language', '')}；"
        f"风格：{settings.get('style', '')}；比例：{settings.get('ratio', '')}。"
        "请生成画面和简短、可读的模块文案。"
    )


def module_ratio(name: str, requested: str = "自动适配") -> str:
    """Use the legacy module defaults only for automatic ratio selection."""
    if "自动" not in requested:
        return requested
    if name in ("首屏主视觉", "系列展示图"):
        return "16:9"
    if name in ("使用场景图", "场景氛围图", "品牌故事图"):
        return "4:5"
    return "1:1"
