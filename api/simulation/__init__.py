"""
BizAI Simulation Engine — 마진 시뮬레이션 파이프라인.

모듈:
    risk_premium       등급 → 리스크 프리미엄 테이블
    margin_simulator   Min/Likely/Max 3가지 시나리오 생성
    macro_adjuster     거시경제(금리/GDP/BSI) → 마진·한도 조정값
    credit_risk        등급+재무+정성 → PD/LGD/Z-Score 종합 평가
    payment_pricer     결제조건별 기간PD·EL 기반 가격결정 + 안전규모
"""
