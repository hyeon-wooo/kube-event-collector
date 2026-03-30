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

## 퀵 스타트 (Helm 설치)

### 1. 설정 파일 준비 (`my-values.yaml`)

`exmaple/values.yaml`을 참고하여 알림을 보낼 슬랙 정보와 룰을 작성합니다.

```yaml
secretName: "slack-secret" # 수동으로 생성한 Secret 이름

config:
  events:
    # ... 섹션별 설정 내용 ...
```

### 2. Helm을 통한 설치

```bash
kubectl create ns monitoring
helm upgrade --install kube-event-collector ./charts/kube-event-collector \
  -n monitoring \
  -f my-values.yaml
```

---

## 파이프라인 Context 및 템플릿 활용

모든 파이프라인 단계는 **Jinja2** 템플릿 엔진을 사용하여 동적인 설정을 지원합니다. `fetchers`의 설정이나 `notifiers`의 메시지 템플릿에서 `{{ }}` 문법으로 Context 데이터를 참조할 수 있습니다.

### 1. `event` 객체 (기본 제공)

파이프라인이 시작될 때, 트리거된 Kubernetes Event 객체가 `event`라는 이름으로 Context에 자동 주입됩니다.

| 주요 필드 | 설명 | 예시 |
| :--- | :--- | :--- |
| `event.reason` | 이벤트 발생 이유 | `SuccessfulRescale`, `BackOff` |
| `event.message` | 상세 메시지 | `New size: 4; reason: cpu resource...` |
| `event.type` | 이벤트 유형 | `Normal`, `Warning` |
| `event.involvedObject` | 관련 리소스 정보 | `name`, `namespace`, `kind` 등 포함 |
| `event.lastTimestamp` | 마지막 발생 시간 | `2023-10-27T10:00:00Z` |

**예시:** `{{ event.involvedObject.name }}` -> 이벤트가 발생한 리소스의 이름

### 2. Fetcher 결과 활용

`fetchers` 섹션에서 정의된 각 Fetcher는 실행이 완료되면 **자신의 이름을 키(Key)로 사용**하여 수집된 데이터를 Context에 추가합니다.

- **동적 Fetching**: `fetchers` 설정 내부에서도 `event` 객체를 참조하여 동적으로 리소스를 수집할 수 있습니다.
- **의존성(depends_on) 활용**: `depends_on` 설정을 통해 Fetcher 간의 실행 순서를 보장하고, **선행 Fetcher의 실행 결과**를 후속 Fetcher의 템플릿에서 참조할 수 있습니다.
- **데이터 구조**: 수집된 데이터는 Kubernetes API 응답의 JSON/Dict 형태이므로 `.spec`, `.status` 등 하위 필드에 자유롭게 접근 가능합니다.

#### 예시: HPA 스케일링 시 연관 Deployment 정보 가져오기

```yaml
config:
  fetchers:
    # 1. 이벤트 대상인 HPA의 상세 정보 수집
    hpa_info:
      resource: HorizontalPodAutoscaler
      namespace: "{{ event.involvedObject.namespace }}"
      resource_name: "{{ event.involvedObject.name }}"
    
    # 2. HPA 정보로부터 Deployment 이름을 동적으로 추출하여 수집
    target_deployment:
      depends_on: ["hpa_info"] # hpa_info가 먼저 실행되도록 보장
      resource: Deployment
      namespace: "{{ event.involvedObject.namespace }}"
      # 'hpa_info'의 spec.scaleTargetRef.name 필드 참조
      resource_name: "{{ hpa_info.spec.scaleTargetRef.name }}"
  
  notifiers:
    slack/alert:
      template: |
        🚀 [HPA Scaling] {{ event.involvedObject.name }} 리소스에서 {{ event.reason }} 발생!
        - 대상 Deployment: {{ hpa_info.spec.scaleTargetRef.name }}
        - 현재 Deployment 복제본 수: {{ target_deployment.spec.replicas }}
```

### 3. Context 데이터 확인 팁

- `event` 객체는 [Kubernetes Event API V1](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#event-v1-core) 구조를 따릅니다.
- Fetcher로 수집된 데이터(`Deployment`, `Pod` 등) 역시 각 리소스의 표준 API 구조를 유지합니다.
- 복잡한 템플릿 로직이 필요한 경우 Jinja2의 `if`, `for`, `filter` 등을 활용할 수 있습니다.
