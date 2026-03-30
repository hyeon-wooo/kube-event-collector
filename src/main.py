import os
import yaml
import logging
import requests
import ast
from jinja2 import Environment, BaseLoader
from kubernetes import client, config, watch

# 로깅 설정
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("kube-event-collector")

def init_k8s_client():
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes config.")
    except config.ConfigException:
        try:
            config.load_kube_config()
            logger.info("Loaded local kubeconfig.")
        except Exception as e:
            logger.error(f"Failed to load Kubernetes config: {e}")
            raise

def get_current_namespace():
    """Pod 내부에서 자신이 배포된 네임스페이스를 가져옵니다."""
    ns_path = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
    if os.path.exists(ns_path):
        with open(ns_path, "r") as f:
            return f.read().strip()
    return "default"

def load_config_from_kube(name, namespace):
    """
    Kubernetes API를 통해 ConfigMap을 읽어옵니다.
    """
    try:
        core_v1 = client.CoreV1Api()
        cm = core_v1.read_namespaced_config_map(name=name, namespace=namespace)
        
        if not cm.data:
            logger.warning(f"ConfigMap '{namespace}/{name}' is empty.")
            return {}
            
        cfg = {}
        for k, v in cm.data.items():
            try:
                parsed_val = yaml.safe_load(v)
                cfg[k] = parsed_val
            except Exception as parse_err:
                logger.warning(f"Failed to parse key '{k}' in ConfigMap: {parse_err}")
                cfg[k] = v
                    
        logger.info(f"Successfully loaded config from ConfigMap '{namespace}/{name}'")
        return cfg
    except Exception as e:
        logger.error(f"Failed to load config from ConfigMap '{namespace}/{name}': {e}")
        return {}

def match_event(event, event_config):
    if not event_config:
        return False
        
    for key, expected_value in event_config.items():
        if key == "type":
            if event.type != expected_value: return False
        elif key == "reason":
            if event.reason != expected_value: return False
        elif key == "kind":
            if event.involved_object.kind != expected_value: return False
        elif key == "namespace":
            if event.involved_object.namespace != expected_value: return False
        elif key == "name":
            if event.involved_object.name != expected_value: return False
            
    return True

def render_template(template_str, context):
    """Jinja2 템플릿 변환. 변수가 없는 일반 문자열도 안전하게 반환합니다."""
    if not isinstance(template_str, str):
        return template_str
    try:
        env = Environment(loader=BaseLoader())
        template = env.from_string(template_str)
        return template.render(**context)
    except Exception as e:
        logger.error(f"Template rendering error: {e}")
        return template_str

def execute_single_fetcher(f_name, f_cfg, context, core_v1, apps_v1):
    """개별 Fetcher 로직 (데이터 수집 및 가공)"""
    resource = f_cfg.get("resource")
    ns_tpl = f_cfg.get("namespace", "default")
    namespace = render_template(ns_tpl, context)
    
    resource_name_tpl = f_cfg.get("resource_name")
    
    resource_name = render_template(resource_name_tpl, context) if resource_name_tpl else None
    
    result = None
    if resource == "Deployment" and resource_name:
        try:
            resp = apps_v1.read_namespaced_deployment(name=resource_name, namespace=namespace)
            # K8s object를 Dict로 직렬화하여 Jinja에서 접근 가능하게 만듦
            result = client.ApiClient().sanitize_for_serialization(resp)
        except Exception as e:
            logger.error(f"Failed to fetch Deployment {namespace}/{resource_name}: {e}")
            
    elif resource == "HorizontalPodAutoscaler" and resource_name:
        try:
            autoscaling_v2 = client.AutoscalingV2Api()
            resp = autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(name=resource_name, namespace=namespace)
            result = client.ApiClient().sanitize_for_serialization(resp)
        except Exception as e:
            logger.error(f"Failed to fetch HPA {namespace}/{resource_name}: {e}")
    

    # 최종 결과물을 Context에 저장하여 후속 Fetcher나 Notifier가 쓸 수 있게 지정
    context[f_name] = result

def execute_fetchers(fetcher_names, fetchers_cfg, context):
    """Topological Sort 로직을 통한 의존성 기반 Fetcher 실행"""
    pending = set(fetcher_names)
    resolved = set()
    
    core_v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    
    while pending:
        progress = False
        for f_name in list(pending):
            f_cfg = fetchers_cfg.get(f_name)
            if not f_cfg:
                pending.remove(f_name)
                continue
            
            depends_on = f_cfg.get("depends_on", [])
            # 의존성이 모두 먼저 해결(resolved)된 경우에만 실행
            if all(dep in resolved for dep in depends_on):
                logger.info(f"      [Fetch] Executing '{f_name}'...")
                execute_single_fetcher(f_name, f_cfg, context, core_v1, apps_v1)
                resolved.add(f_name)
                pending.remove(f_name)
                progress = True
                
        if not progress:
            logger.error(f"Cannot resolve dependencies for fetchers {pending}. Deadlock detected.")
            break

def resolve_config_value(cfg, key):
    """
    설정 딕셔너리에서 값을 가져옵니다. 
    1. '{key}_env' 속성이 선언되어 있다면 환경변수를 우선 참조합니다.
    2. 값이 문자열이고 '$'로 시작하면 해당 이름의 환경변수 값으로 치환합니다.
    """
    env_key = cfg.get(f"{key}_env")
    if env_key and os.getenv(env_key):
        return os.getenv(env_key)
        
    val = cfg.get(key)
    if isinstance(val, str) and val.startswith("$"):
        env_val = os.getenv(val[1:])
        if env_val is not None:
            return env_val
            
    return val

def execute_notifiers(notifier_names, notifiers_cfg, context):
    """포맷 렌더링 및 알림 발송"""
    for n_name in notifier_names:
        n_cfg = notifiers_cfg.get(n_name)
        if not n_cfg:
            continue
            
        # "slack/alert" -> n_type="slack"
        parts = n_name.split('/')
        n_type = parts[0] if len(parts) > 1 else n_name
        
        template_str = n_cfg.get("template", "")
        message = render_template(template_str, context)
        
        logger.info(f"      [Notify] Dispatching via '{n_name}' (type: {n_type})...")
        
        if n_type == "slack":
            # URL 또는 (Token + Channel) 중 1개 사용
            webhook_url = resolve_config_value(n_cfg, "webhook_url") or resolve_config_value(n_cfg, "endpoint")
            token = resolve_config_value(n_cfg, "token")
            channel = resolve_config_value(n_cfg, "channel")
            
            if webhook_url:
                requests.post(webhook_url, json={"text": message})
                logger.info("             -> Sent via Slack Webhook")
            elif token and channel:
                requests.post(
                    "https://slack.com/api/chat.postMessage", 
                    headers={"Authorization": f"Bearer {token}"},
                    json={"channel": channel, "text": message}
                )
                logger.info("             -> Sent via Slack API (Token)")
            else:
                logger.error(f"Slack notifier '{n_name}' needs either 'webhook_url' OR ('token' AND 'channel').")

def watch_events(cfg):
    events_cfg = cfg.get("events", {})
    fetchers_cfg = cfg.get("fetchers", {})
    notifiers_cfg = cfg.get("notifiers", {})
    pipelines_cfg = cfg.get("service", {}).get("pipelines", {})
    
    if not events_cfg or not pipelines_cfg:
        logger.warning("No valid 'events' or 'pipelines' found in config. Exiting.")
        return

    v1 = client.CoreV1Api()
    w = watch.Watch()

    logger.info("🚀 Starting to watch Kubernetes events...")
    
    for event_obj in w.stream(v1.list_event_for_all_namespaces):
        event = event_obj['object']
        
        # 트리거된 이벤트가 있는지 확인
        triggered_events = []
        for ev_name, ev_config in events_cfg.items():
            if match_event(event, ev_config):
                triggered_events.append(ev_name)
                
        if not triggered_events:
            continue
            
        logger.info(f"🔥 Event Detected! Triggered Events: {triggered_events}")
        logger.info(f"   - Target: {event.involved_object.kind} ({event.involved_object.namespace}/{event.involved_object.name}) | Reason: {event.reason}")
        
        # k8s 이벤트를 Json 형태로 직렬화하여 초기 Context 구성
        event_dict = client.ApiClient().sanitize_for_serialization(event)
        
        # 파이프라인 매칭 및 실행
        for pl_name, pl_config in pipelines_cfg.items():
            pl_events = pl_config.get("events", [])
            
            if set(triggered_events).intersection(pl_events):
                logger.info(f"   => 🛠 Pipeline '{pl_name}' is active.")
                
                # Context 초기화 (템플릿 엔진에 주입될 변수들)
                context = {"event": event_dict}
                
                pl_fetchers = pl_config.get("fetchers", [])
                if pl_fetchers:
                    execute_fetchers(pl_fetchers, fetchers_cfg, context)
                
                pl_notifiers = pl_config.get("notifiers", [])
                if pl_notifiers:
                    execute_notifiers(pl_notifiers, notifiers_cfg, context)
                    
                logger.info("-" * 60)

if __name__ == "__main__":
    # k8s client를 먼저 초기화해야 ConfigMap을 조회할 수 있습니다.
    init_k8s_client()
    
    cm_name = "kube-event-collector-cm"
    cm_namespace = get_current_namespace()
    
    app_config = load_config_from_kube(cm_name, cm_namespace)
    
    try:
        watch_events(app_config)
    except KeyboardInterrupt:
        logger.info("🛑 Stopped watching events.")
    except Exception as e:
        logger.error(f"Error while watching events: {e}")
