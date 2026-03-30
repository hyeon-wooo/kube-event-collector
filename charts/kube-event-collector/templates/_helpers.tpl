{{/*
ServiceAccount 이름 결정 방식을 정의합니다.
*/}}
{{- define "kube-event-collector.serviceAccountName" -}}
{{- if .Values.serviceAccount.name }}
{{- .Values.serviceAccount.name }}
{{- else }}
{{- .Chart.Name }}
{{- end }}
{{- end }}
