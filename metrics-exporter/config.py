# Copyright 2022 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
#User variables
PROJECT_ID = os.getenv("PROJECT_ID", "")
RECOMMENDATION_WINDOW_SECONDS = os.getenv("REC_WINDOW", 2592000)
LATEST_WINDOW_SECONDS = os.getenv("METRIC_WINDOW", 300)

PUBSUB_TOPIC = "mql_metric_export"
BIGQUERY_DATASET = "metric_export"
BIGQUERY_TABLE = "mql_metrics"
RECOMMENDATION_TABLE = "vpa_container_recommendations"


# IMPORTANT: to guarantee successfully retriving data, please use a time window greater than 5 minutes

MQL_QUERY = {
    "container_count" :["kubernetes.io/container/cpu/request_cores", LATEST_WINDOW_SECONDS, "gke_metric"]
,
    "cpu_requested_cores" :["kubernetes.io/container/cpu/request_cores", LATEST_WINDOW_SECONDS, "gke_metric"]
,
    "cpu_limit_cores": ["kubernetes.io/container/cpu/limit_cores", LATEST_WINDOW_SECONDS, "gke_metric"]
,
    "memory_requested_bytes":["kubernetes.io/container/memory/request_bytes", LATEST_WINDOW_SECONDS, "gke_metric"]
,
    "memory_limit_bytes":["kubernetes.io/container/memory/limit_bytes", LATEST_WINDOW_SECONDS, "gke_metric"]
,
    "memory_request_recommendations": ["kubernetes.io/autoscaler/container/memory/per_replica_recommended_request_bytes", RECOMMENDATION_WINDOW_SECONDS, "vpa_metric"]
,
    "cpu_request_recommendations": ["kubernetes.io/autoscaler/container/cpu/per_replica_recommended_request_cores", RECOMMENDATION_WINDOW_SECONDS, "vpa_metric"]
,
    "hpa_cpu":["custom.googleapis.com/podautoscaler/hpa/cpu/target_utilization", LATEST_WINDOW_SECONDS, "gke_metric"]
,
    "hpa_memory":["custom.googleapis.com/podautoscaler/hpa/memory/target_utilization", LATEST_WINDOW_SECONDS, "gke_metric"]

}

BASE_URL = "https://monitoring.googleapis.com/v3/projects"
QUERY_URL = f"{BASE_URL}/{PROJECT_ID}/timeSeries:query"


BQ_VALUE_MAP = {
    "INT64": "int64_value",
    "BOOL": "boolean_value",
    "DOUBLE": "double_value",
    "STRING": "string_value",
    "DISTRIBUTION": "distribution_value"
}

API_VALUE_MAP = {
    "INT64": "int64Value",
    "BOOL": "booleanValue",
    "DOUBLE": "doubleValue",
    "STRING": "stringValue",
    "DISTRIBUTION": "distributionValue"
}
