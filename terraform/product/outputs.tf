# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

output "app_name" {
  description = "Name of the deployed Wazuh indexer application."
  value       = module.wazuh_indexer.app_name
}

output "grafana_agent_app_name" {
  description = "Name of the deployed Grafana agent application."
  value       = module.grafana_agent.app_name
}

output "grafana_agent_requires" {
  value = {
    logging           = "logging-consumer"
    senf_remote_write = "send-remote-write"
  }
}

output "grafana_agent_provides" {
  value = {
    grafana_dashboards_provider = "grafana-dashboards-provider"
  }
}
