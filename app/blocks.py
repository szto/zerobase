"""
블록 타입 레지스트리 — 에디터 편집 필드와 기본값의 단일 소스.

새 블록 타입 추가 절차:
1. 여기 BLOCK_TYPES 에 항목 추가 (fields = 에디터에 노출할 편집 필드)
2. app/templates/app/blocks/<type>.html 파셜 작성
3. app/models.py Block.Type choices 에 추가
"""

BLOCK_TYPES = {
    "hero": {
        "label": "히어로",
        "fields": [
            {"key": "title", "label": "제목", "widget": "input"},
            {"key": "subtitle", "label": "부제", "widget": "input"},
        ],
        "defaults": {"title": "우리 가게에 오신 것을 환영합니다", "subtitle": "한 줄로 매력을 소개해보세요"},
    },
    "about": {
        "label": "소개",
        "fields": [
            {"key": "heading", "label": "섹션 제목", "widget": "input"},
            {"key": "body", "label": "본문", "widget": "textarea"},
        ],
        "defaults": {"heading": "소개", "body": "무엇을 하는 곳인지, 왜 특별한지 적어보세요."},
    },
    "services": {
        "label": "서비스/상품",
        "fields": [
            {"key": "heading", "label": "섹션 제목", "widget": "input"},
            {
                "key": "items_raw",
                "label": "항목 (한 줄에 하나: 이름 | 설명 | 가격)",
                "widget": "textarea",
            },
        ],
        "defaults": {
            "heading": "서비스",
            "items_raw": "기본 서비스 | 간단한 설명 | 10,000원",
        },
    },
    "contact": {
        "label": "문의폼",
        "fields": [
            {"key": "heading", "label": "섹션 제목", "widget": "input"},
            {"key": "description", "label": "안내 문구", "widget": "input"},
        ],
        "defaults": {"heading": "문의하기", "description": "궁금한 점을 남겨주시면 연락드리겠습니다."},
    },
}


def default_config(block_type: str) -> dict:
    return dict(BLOCK_TYPES[block_type]["defaults"])


def parse_service_items(items_raw: str) -> list:
    """'이름 | 설명 | 가격' 줄들을 렌더링용 리스트로 변환."""
    items = []
    for line in (items_raw or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        items.append(
            {
                "name": parts[0],
                "description": parts[1] if len(parts) > 1 else "",
                "price": parts[2] if len(parts) > 2 else "",
            }
        )
    return items


def render_context(block) -> dict:
    """파셜 템플릿에 넘길 블록별 컨텍스트 (config 가공 포함)."""
    cfg = {**default_config(block.type), **(block.config or {})}
    if block.type == "services":
        cfg["items"] = parse_service_items(cfg.get("items_raw", ""))
    return cfg
