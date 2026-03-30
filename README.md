# kube-event-collector

쿠버네티스 클러스터 이벤트를 실시간 모니터링하고, 조건에 따라 연관 리소스를 탐색(Fetch)하여 다중 채널로 알림을 보내는 OTel 스타일 파이프라인 엔진입니다.

## 주요 특징

- **선언적 파이프라인**: OpenTelemetry Collector의 구조를 차용하여 `events` -> `fetchers` -> `notifiers`를 유연하게 조립 가능.
- **의존성 기반 데이터 수집**: Fetcher 간의 `depends_on` 설정을 통해 데이터 수집 순서를 위상 정렬(Topological Sort)로 관리.
- **다중 알림 지원**: 현재는 Slack(Webhook/Token)만 지원하며 Jinja2 템플릿 엔진으로 자유로운 메시지 포맷 정의 가능. 동일 이벤트에 대해 여러 Notifier에 일괄적으로 알림을 발송할 수 있음.
- **실시간 리로딩**: ConfigMap 기반 설정을 사용하여 컨트롤러 재시작 시 최신 정책 즉시 반영.

## 아키텍처

1. **Events (Receiver)**: Kubernetes 이벤트를 Watch하며 필터링 조건 정의.
2. **Fetchers (Processor)**: 이벤트와 관련된 리소스(Deployment, Pod 등)의 상세 상태 정보 수집 및 가공.
3. **Notifiers (Exporter)**: 최종 정보를 렌더링하여 외부 플랫폼으로 발송.

---

## 퀵 스타트 (Helm 설치)

### 1. 설정 파일 준비 (`my-values.yaml`)

`exmaple/values.yaml`을 참고하여 알림을 보낼 슬랙 정보와 룰을 작성합니다.

```yaml
env:
  - name: SLACK_WEBHOOK_URL
    value: "https://hooks.slack.com/services/XXXX/YYYY"

config:
  events:
    hpa_scaling:
      reason: "SuccessfulRescale"
      kind: "HorizontalPodAutoscaler"

  service:
    pipelines:
      my_alert:
        events: ["hpa_scaling"]
        notifiers: ["slack/alert"]
```

### 2. Helm을 통한 설치

프로젝트 루트 디렉토리에서 다음 명령을 실행합니다.

```bash
# 네임스페이스 생성
kubectl create ns monitoring

# 헬름 차트 설치
helm upgrade --install kube-event-collector ./charts/kube-event-collector \
  -n monitoring \
  -f my-values.yaml
```
