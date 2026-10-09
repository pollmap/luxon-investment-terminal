"""Public metric definitions. No semantic aliases between different cash flows."""

VERSION = "analysis-metrics-v1"


def _metric(code, label, group, description, formula, default=None, ttm=True):
    return {"code": code, "label": label, "group": group, "description": description,
            "formula": formula, "unit": "원/주", "default_multiple": default,
            "supports_ttm": ttm, "definition_version": VERSION}


METRICS = {
    row["code"]: row for row in (
        _metric("EPS", "기본 EPS", "이익", "보통주 한 주에 돌아가는 순이익이에요.",
                "보통주 귀속이익 / 기간 가중평균 보통주 수", 15),
        _metric("EPS_DILUTED", "희석 EPS", "이익", "전환권 등의 영향을 반영한 주당순이익이에요.",
                "희석 조정 귀속이익 / 희석 가중평균 주식 수", 15),
        _metric("EPS_ADJUSTED", "조정 EPS", "이익", "회사가 조정 내역을 공개한 주당순이익이에요.",
                "기업 공시의 조정 내역과 주당이익"),
        _metric("EBITDA", "EBITDA/주", "이익", "이자·세금·감가상각·상각 전 이익을 주당으로 환산해요.",
                "(순이익 + 법인세비용 + 이자비용 + 감가상각·상각비) / 기간 가중평균 주식 수"),
        _metric("EBIT", "EBIT/주", "이익", "이자·세금 전 이익을 주당으로 환산해요.",
                "(순이익 + 법인세비용 + 이자비용) / 기간 가중평균 주식 수"),
        _metric("OCF", "영업현금흐름/주", "현금흐름", "영업활동으로 들어오고 나간 현금의 차이예요.",
                "영업활동 현금흐름 / 기간 가중평균 주식 수"),
        _metric("FCF", "잉여현금흐름/주", "현금흐름", "영업현금흐름에서 유형·무형자산 취득 지출을 뺀 금액이에요.",
                "(영업현금흐름 - 유형·무형자산 취득 현금지출) / 기간 가중평균 주식 수"),
        _metric("FCFE", "FCFE/주", "현금흐름", "투자 지출과 차입·상환까지 반영한 주주 현금흐름이에요.",
                "(이자 지급 분류를 정규화한 영업현금흐름 - 설비투자 + 순차입) / 기간 가중평균 주식 수"),
        _metric("FFO", "FFO/주", "현금흐름", "기업이 공시한 FFO와 조정 내역을 사용해요.", "기업 공시 FFO / 검증된 주식 수"),
        _metric("AFFO", "AFFO/주", "현금흐름", "기업이 공시한 AFFO와 조정 내역을 사용해요.", "기업 공시 AFFO / 검증된 주식 수"),
        _metric("SALES", "매출/주", "매출·자산", "매출액을 주당으로 환산한 숫자예요.", "매출액 / 기간 가중평균 주식 수"),
        _metric("BPS", "주당순자산 BPS", "매출·자산", "해당 시점의 보통주 한 주에 해당하는 순자산이에요.",
                "보통주 귀속 순자산 / 해당 시점 보통주 수", 1, False),
    )
}


def metric_definition(code):
    code = "EPS" if code == "EPS_BASIC" else code
    if code not in METRICS:
        raise ValueError("Unknown analysis metric")
    return dict(METRICS[code])


def definitions():
    return {"metrics": [dict(row) for row in METRICS.values()], "version": VERSION}
