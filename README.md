# 도원결의 (桃園結義) — 정부지원사업 통합 플랫폼

K-Startup 공공 API 기반으로 정부지원사업을 한눈에 확인할 수 있는 웹 플랫폼입니다.

## ✨ 주요 기능
- **대시보드**: 전체 공고 수 / 모집중 / 마감임박(D-7) / 총 지원금액 KPI
- **캘린더**: 월별 마감일 기준 공고 시각화, 날짜 클릭 시 해당일 공고 상세
- **목록/필터**: 상태(모집중·마감임박·상시·마감) / 분야별 필터링
- **D-day 배지**: 남은 기간을 색상으로 즉시 인식 (D-3 이내 🔴, D-14 이내 🟡)
- **지원금액 자동 추출**: 공고 본문에서 `억원/만원/천만원` 패턴 파싱

## 🚀 로컬 실행
```bash
pip install -r requirements.txt
export KSTARTUP_API_KEY="..."
python app.py
# http://127.0.0.1:5055
```

## ☁️ Render 배포
1. 이 저장소를 GitHub에 푸시
2. Render 대시보드 → **New → Blueprint** → 저장소 선택
3. `render.yaml` 자동 감지 → Apply
4. 배포 완료 후 도메인 연결: **Settings → Custom Domains → Add**
