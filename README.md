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

## 설치 및 배포

본 프로젝트는 Helm Repository를 통해 편리하게 설치할 수 있습니다. 

### 1. Helm CLI를 이용한 직접 설치

가장 일반적인 설치 방법입니다. 아래 명령어로 깃허브 레포지토리를 직접 등록하고 설치할 수 있습니다.

```bash
# 1. 레포지토리 등록 및 업데이트
helm repo add kube-event-collector https://hyeon-wooo.github.io/kube-event-collector/
helm repo update

# 2. 설정 파일 준비 (exmaple/values.yaml 참고하여 my-values.yaml 작성)

# 3. 차트 설치
kubectl create ns monitoring
helm upgrade --install kube-event-collector kube-event-collector/kube-event-collector \
  -n monitoring \
  -f my-values.yaml
```

### 2. GitOps를 이용한 설치 (ArgoCD)

ArgoCD를 사용하는 경우, 소스 코드와 설정(Values)을 분리하여 관리하는 **Multiple Sources** 패턴을 권장합니다.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: kube-event-collector
spec:
  sources:
    # 1. 원본 차트 (GitHub 레포지토리를 Helm Repo로 직접 참조)
    - repoURL: "https://hyeon-wooo.github.io/kube-event-collector/"
      chart: "kube-event-collector"
      targetRevision: "0.1.0" # Chart.yaml의 버전
      helm:
        valueFiles:
          - $myconfig/values.yaml
          
    # 2. 사용자 설정 (개인 GitOps 레포지토리)
    - repoURL: "git@github.com:my-org/my-gitops-repo.git"
      targetRevision: HEAD
      ref: myconfig
  # ... (생략)
```

---

## 파이프라인 Context 및 템플릿 활용

모든 파이프라인 단계는 **Jinja2** 템플릿 엔진을 사용하여 동적인 설정을 지원합니다. `fetchers`의 설정이나 `notifiers`의 메시지 템플릿에서 `{{ }}` 문법으로 Context 데이터를 참조할 수 있습니다.

### 1. `event` 객체

파이프라인이 시작될 때, 트리거된 Kubernetes Event 객체가 `event`라는 이름으로 Context에 자동 주입됩니다.

| 주요 필드              | 설명             | 예시                                   |
| :--------------------- | :--------------- | :------------------------------------- |
| `event.reason`         | 이벤트 발생 이유 | `SuccessfulRescale`, `BackOff`         |
| `event.message`        | 상세 메시지      | `New size: 4; reason: cpu resource...` |
| `event.type`           | 이벤트 유형      | `Normal`, `Warning`                    |
| `event.involvedObject` | 관련 리소스 정보 | `name`, `namespace`, `kind` 등 포함    |
| `event.lastTimestamp`  | 마지막 발생 시간 | `2023-10-27T10:00:00Z`                 |

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
      title: "알림: {{ event.involvedObject.name }} - {{ event.reason }}"
      color: "#36a64f"
      template: |
        대상 리소스에서 {{ event.reason }} 이벤트가 발생했습니다.
        현재 Deployment의 원하는 복제본 수: {{ target_deployment.spec.replicas }}
```

### 3. Notifier 타입별 설정

#### Slack Notifier

Slack은 단순 텍스트 외에 풍부한 레이아웃(Rich Layout)을 위한 전용 필드를 지원합니다.

| 필드                   | 설명                               | 기본값                                |
| :--------------------- | :--------------------------------- | :------------------------------------ |
| `webhook_url`          | Slack Incoming Webhook URL         | (없음)                                |
| `token`                | Slack API 사용을 위한 Bot Token    | (없음)                                |
| `channel`              | 알림을 보낼 슬랙 채널 ID 또는 이름 | (없음)                                |
| `title` 또는 `subject` | 메시지 상단 제목 (Jinja2 지원)     | (없음)                                |
| `color`                | 왼쪽 사이드 바 색상 (Hex)          | Normal: `#36a64f`, Warning: `#f2c744` |
| `message`              | 메시지 본문 (Jinja2 지원)          | (필수)                                |

> `token`과 `channel` 두 필드를 입력하거나, `webhook_url` 필드만 입력합니다.  
> `webhook_url`이 지정된 경우 `token`과 `channel`은 무시됩니다.

#### HTTP Notifier

일반적인 Webhook 연동이나 외부 API 호출을 위해 사용합니다.

- `endpoint`: 호출할 URL (환경변수 지원)
- `authorization`: 헤더에 담길 인증 토큰으로, 인증 방식(Basic, Bearer 등)을 포함한 헤더 전체를 입력합니다.
- `body`: 전송할 JSON 바디

### 4. Context 데이터 확인 팁

- `event` 객체는 [Kubernetes Event API V1](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#event-v1-core) 구조를 따릅니다.
- Fetcher로 수집된 데이터(`Deployment`, `Pod` 등) 역시 각 리소스의 표준 API 구조를 유지합니다.
- 복잡한 템플릿 로직이 필요한 경우 Jinja2의 `if`, `for`, `filter` 등을 활용할 수 있습니다.
