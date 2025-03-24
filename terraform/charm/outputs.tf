# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

output "app_name" {
  description = "Name of the deployed application."
  value       = juju_application.wazuh_indexer.name
}

output "requires" {
  value = {
    certificates   = "certificates"
    peer_cluster   = "peer-cluster"
    s3_credentials = "s3-credentials"
  }
}

output "provides" {
  value = {
    cos_agent                 = "cos_agent"
    opensearch_client         = "opensearch-client"
    peer_cluster_orchestrator = "peer-cluster-orchestrator"
  }
}
