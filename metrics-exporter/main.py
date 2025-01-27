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
import time
import os
import sys
import json
import config
from flask import Flask
from datetime import timedelta, datetime
import logging
from google.cloud import bigquery
from google.cloud import bigquery_storage_v1
from google.cloud.bigquery_storage_v1 import types
from google.cloud.bigquery_storage_v1 import writer
from google.protobuf import descriptor_pb2
import metric_record_flat_pb2
from google.cloud import monitoring_v3
import math

# If you update the metric_record.proto protocol buffer definition, run:
#
#   protoc --python_out=. metric_record_flat.proto
#
# from the samples/snippets directory to generate the metric_record_pb2.py module.

# Fetch GKE metrics - cpu requested cores, cpu limit cores, memory requested bytes, memory limit bytes, count and all workloads with hpa

# Construct the time interval for the query
now = time.time()
seconds = int(now)
nanos = int((now - seconds) * 10 ** 9)
PROJECT_ID = config.PROJECT_ID

def get_gke_metrics(metric_name, metric, window):
    # [START get_gke_metrics]
    output = []
    client = monitoring_v3.MetricServiceClient()
    project_name = f"projects/{PROJECT_ID}"

    gke_group_by_fields = ['resource.label."location"', 'resource.label."project_id"', 'resource.label."cluster_name"', 'resource.label."controller_name"',
                           'resource.label."namespace_name"', 'metadata.system_labels."top_level_controller_name"', 'metadata.system_labels."top_level_controller_type"']
    hpa_group_by_fields = ['resource.label."location"', 'resource.label."project_id"', 'resource.label."cluster_name"',
                           'resource.label."namespace_name"', 'metric.label."targetref_kind"', 'metric.label."targetref_name"']

    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": seconds},
            "start_time": {"seconds": (seconds - window)},
        }
    )

    aggregation = monitoring_v3.Aggregation(
        {
            "alignment_period": {"seconds": window},
            "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_MEAN,
            "cross_series_reducer": monitoring_v3.Aggregation.Reducer.REDUCE_COUNT if metric_name == "container_count" else monitoring_v3.Aggregation.Reducer.REDUCE_MEAN,
            "group_by_fields": gke_group_by_fields if "hpa" not in metric_name else hpa_group_by_fields,
        }
    )
    try:
        results = client.list_time_series(
            request={
                "name": project_name,
                "filter": f'metric.type = "{metric}" AND resource.label.namespace_name != "kube-system"',
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                "aggregation": aggregation,
            }
        )
        print("Building Row")

        for result in results:
            row = metric_record_flat_pb2.MetricFlatRecord()
            label = result.resource.labels
            metadata = result.metadata.system_labels.fields
            metricdata = result.metric.labels
            row.metric_name = metric_name
            row.location = label['location']
            row.project_id = label['project_id']
            row.cluster_name = label['cluster_name']
            row.controller_name = metricdata['targetref_name'] if "hpa" in metric_name else metadata['top_level_controller_name'].string_value
            row.controller_type = metricdata['targetref_kind'] if "hpa" in metric_name else metadata['top_level_controller_type'].string_value
            row.namespace_name = label['namespace_name']
            row.tstamp = time.time()
            points = result.points
            for point in points:
                if "cpu" in metric_name:
                    row.points = (int(point.value.double_value * 1000)
                                  ) if point.value.double_value is not None else 0
                elif "memory" in metric_name:
                    row.points = (int(point.value.double_value/1024/1024)
                                  ) if point.value.double_value is not None else 0
                else:
                    row.points = (
                        point.value.int64_value) if point.value.int64_value is not None else 0
                break
            output.append(row.SerializeToString())

    except:
        print("No HPA workloads found")

    return output

    # [END gke_get_metrics]

# Build VPA recommendations, memory: get max value over 30 days, cpu: get max and 95th percentile


def get_vpa_recommenation_metrics(metric_name, metric, window):

    # [START get_vpa_recommenation_metrics]
    client = monitoring_v3.MetricServiceClient()
    project_name = f"projects/{PROJECT_ID}"

    rec_group_by_fields = ['resource.label."location"', 'resource.label."project_id"', 'resource.label."cluster_name"',
                           'resource.label."controller_name"', 'resource.label."namespace_name"', 'resource.label."controller_kind"']

    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": seconds},
            "start_time": {"seconds": (seconds - window)},
        }
    )
    results = client.list_time_series(
        request={
            "name": project_name,
            "filter": f'metric.type = "{metric}" AND resource.label.namespace_name != "kube-system"',
            'interval': interval,
            'aggregation': {
                'alignment_period': {'seconds': window},
                'cross_series_reducer': 'REDUCE_MAX',
                'per_series_aligner': 'ALIGN_MAX',
                'group_by_fields': rec_group_by_fields
            },
        }
    )
    output = []
    for result in results:
        label = result.resource.labels
        row = metric_record_flat_pb2.MetricFlatRecord()
        label = result.resource.labels
        row.location = label['location']
        row.project_id = label['project_id']
        row.cluster_name = label['cluster_name']
        row.controller_name = label['controller_name']
        row.controller_type = label['controller_kind']
        row.namespace_name = label['namespace_name']
        row.metric_name = metric_name
        row.tstamp = time.time()
        points = result.points
        for point in points:
            if "memory" in metric_name:
                row.points = (int(point.value.int64_value/1024/1024)
                              ) if point.value.int64_value is not None else 0
            else:
                row.points = (int(point.value.double_value * 1000)
                              ) if point.value.double_value is not None else 0
            break
        output.append(row.SerializeToString())

    # Get the 95th percentile for burstable
    if 'cpu' in metric_name:
        results = client.list_time_series(
            request={
                "name": project_name,
                "filter": f'metric.type = "{metric}" AND resource.label.namespace_name != "kube-system"',
                'interval': interval,
                'aggregation': {
                    'alignment_period': {'seconds': window},
                    'cross_series_reducer': 'REDUCE_PERCENTILE_95',
                    'per_series_aligner': 'ALIGN_MEAN',
                    'group_by_fields': rec_group_by_fields
                },
            }
        )
        for result in results:
            label = result.resource.labels
            row = metric_record_flat_pb2.MetricFlatRecord()
            label = result.resource.labels
            row.location = label['location']
            row.project_id = label['project_id']
            row.cluster_name = label['cluster_name']
            row.controller_name = label['controller_name']
            row.controller_type = label['controller_kind']
            row.namespace_name = label['namespace_name']
            row.metric_name = 'cpu_request_95th_percentile_recommendations'
            row.tstamp = time.time()
            for point in result.points:
                if((point.value.double_value) != 0):
                    row.points = int(point.value.double_value * 1000)
                else:
                    row.points = int(point.value.int64_value/1024/1024)
            output.append(row.SerializeToString())
    return output
    # [END get_vpa_recommenation_metrics]


# Write rows to BigQuery
def append_rows_proto(rows):
    """Create a write stream, write some sample data, and commit the stream."""
    write_client = bigquery_storage_v1.BigQueryWriteClient()
    parent = write_client.table_path(
        PROJECT_ID, config.BIGQUERY_DATASET, config.BIGQUERY_TABLE)
    write_stream = types.WriteStream()

    # When creating the stream, choose the type. Use the PENDING type to wait
    # until the stream is committed before it is visible. See:
    # https://cloud.google.com/bigquery/docs/reference/storage/rpc/google.cloud.bigquery.storage.v1#google.cloud.bigquery.storage.v1.WriteStream.Type
    write_stream.type_ = types.WriteStream.Type.PENDING
    write_stream = write_client.create_write_stream(
        parent=parent, write_stream=write_stream
    )
    stream_name = write_stream.name

    # Create a template with fields needed for the first request.
    request_template = types.AppendRowsRequest()

    # The initial request must contain the stream name.
    request_template.write_stream = stream_name

    # So that BigQuery knows how to parse the serialized_rows, generate a
    # protocol buffer representation of your message descriptor.
    proto_schema = types.ProtoSchema()
    proto_descriptor = descriptor_pb2.DescriptorProto()
    metric_record_flat_pb2.MetricFlatRecord.DESCRIPTOR.CopyToProto(
        proto_descriptor)
    proto_schema.proto_descriptor = proto_descriptor
    proto_data = types.AppendRowsRequest.ProtoData()
    proto_data.writer_schema = proto_schema
    request_template.proto_rows = proto_data

    # Some stream types support an unbounded number of requests. Construct an
    # AppendRowsStream to send an arbitrary number of requests to a stream.
    append_rows_stream = writer.AppendRowsStream(
        write_client, request_template)

    # Create a batch of row data by appending proto2 serialized bytes to the
    # serialized_rows repeated field.
    proto_rows = types.ProtoRows()
    for row in rows:
        proto_rows.serialized_rows.append(row)
    request = types.AppendRowsRequest()
    request.offset = 0
    proto_data = types.AppendRowsRequest.ProtoData()
    proto_data.rows = proto_rows
    request.proto_rows = proto_data

    append_rows_stream.send(request)

    # Shutdown background threads and close the streaming connection.
    append_rows_stream.close()

    # A PENDING type stream must be "finalized" before being committed. No new
    # records can be written to the stream after this method has been called.
    write_client.finalize_write_stream(name=write_stream.name)

    # Commit the stream you created earlier.
    batch_commit_write_streams_request = types.BatchCommitWriteStreamsRequest()
    batch_commit_write_streams_request.parent = parent
    batch_commit_write_streams_request.write_streams = [write_stream.name]
    write_client.batch_commit_write_streams(batch_commit_write_streams_request)

    print(f"Writes to stream: '{write_stream.name}' have been committed.")

# Purge all data from metrics table. mql_metrics table is used as a staging table and must be purged to avoid duplicate metrics
def purge_raw_metric_data():
    t = time.time() - 5400
    client = bigquery.Client()
    metric_table_id = f'{PROJECT_ID}.{config.BIGQUERY_DATASET}.{config.BIGQUERY_TABLE}'

    purge_raw_metric_query_job = client.query(
        f"""DELETE `{metric_table_id}` WHERE TRUE AND tstamp < {t}
        """
    )
    print("Raw metric data purged from  {}".format(metric_table_id))
    purge_raw_metric_query_job.result()

# Use recommendation.sql to build vpa container recommendations
def build_recommenation_table():
    """ Create recommenations table in BigQuery
    """
    client = bigquery.Client()
    recommendation_table = f'{PROJECT_ID}.{config.BIGQUERY_DATASET}.{config.RECOMMENDATION_TABLE}'
    metric_table = f'{PROJECT_ID}.{config.BIGQUERY_DATASET}.{config.BIGQUERY_TABLE}'
    print(metric_table)
    print(recommendation_table)
    update_query = f"""UPDATE `{recommendation_table}`
        SET latest = FALSE
        WHERE latest = TRUE"""
    print(update_query)
  
    query_job = client.query(update_query)
    results = query_job.result()
    for row in results:
        print(row)

    with open('./recommendation.sql', 'r') as file:
        sql = file.read()
        sql = sql.replace("[RECOMMENDATION_TABLE]",recommendation_table)
        sql = sql.replace("[METRIC_TABLE]",metric_table)
    
    with open('./recommendation.sql', 'w') as file:
        file.write(sql)        
    print("Query results loaded to the table {}".format(recommendation_table))

    # Start the query, passing in the recommendation query.
    query_job = client.query(sql)  # Make an API request.
    query_job.result()  # Wait for the job to complete.
    #purge_raw_metric_data()

def run_pipeline():
    data = []
    for metric, query in config.MQL_QUERY.items():
        if query[2] == "gke_metric":
            print(f"Processing GKE system metric {metric}")
            data = get_gke_metrics(metric, query[0], query[1])
        else:
            print(f"Processing VPA recommendation metric {metric}")
            data = get_vpa_recommenation_metrics(metric, query[0], query[1])
        append_rows_proto(data)
    build_recommenation_table()

# Retrieve Job-defined env vars
TASK_INDEX = os.getenv("CLOUD_RUN_TASK_INDEX", 0)
TASK_ATTEMPT = os.getenv("CLOUD_RUN_TASK_ATTEMPT", 0)

# Start script
if __name__ == "__main__":
    try:
        run_pipeline()

    except Exception as err:
        message = f"Task #{TASK_INDEX}, " \
                  + f"Attempt #{TASK_ATTEMPT} failed: {str(err)}"

        print(json.dumps({"message": message, "severity": "ERROR"}))
        # [START cloudrun_jobs_exit_process]
        sys.exit(1)  # Retry Job Task by exiting the process
        # [END cloudrun_jobs_exit_process]   
