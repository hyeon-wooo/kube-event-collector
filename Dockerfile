# Python 공식 Slim 이미지를 기반으로 작성 (용량 최적화 및 안정성)
FROM python:3.11-slim

# 작업 디렉터리 설정
WORKDIR /app

# Python 바이트코드(.pyc) 생성 방지 및 버퍼 버퍼링 방지 (실시간 로그 출력용)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 의존성 파일 먼저 복사 및 설치 (Docker 빌드 캐시 효율 극대화)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 소스코드 복사
COPY src/ /app/src/

# 보안 강화를 위한 비루트(Non-root) 사용자 전환
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup appuser
USER appuser

# 컨테이너 실행 시 진입점
CMD ["python", "src/main.py"]
