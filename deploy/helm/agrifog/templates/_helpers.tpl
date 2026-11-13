{{/* Étiquettes communes à tous les objets du chart. */}}
{{- define "agrifog.labels" -}}
app.kubernetes.io/part-of: agrifog
app.kubernetes.io/managed-by: {{ .Release.Service }}
agrifog/fog: {{ .Values.fogId | quote }}
{{- end -}}
