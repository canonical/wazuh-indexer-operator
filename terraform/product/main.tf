# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

data "juju_model" "wazuh_indexer" {
  uuid = var.model_uuid
}

module "wazuh_indexer" {
  source      = "../charm"
  app_name    = var.wazuh_indexer.app_name
  channel     = var.wazuh_indexer.channel
  config      = var.wazuh_indexer.config
  constraints = var.wazuh_indexer.constraints
  model_uuid  = data.juju_model.wazuh_indexer.uuid
  revision    = var.wazuh_indexer.revision
  base        = var.wazuh_indexer.base
  units       = var.wazuh_indexer.units
}

resource "juju_application" "sysconfig" {
  name       = var.sysconfig.app_name
  model_uuid = data.juju_model.wazuh_indexer.uuid

  charm {
    name     = "sysconfig"
    revision = var.sysconfig.revision
    channel  = var.sysconfig.channel
  }

  config = {
    sysctl = "{vm.max_map_count: 262144, vm.swappiness: 0, net.ipv4.tcp_retries2: 5, fs.file-max: 1048576}"
  }
}

resource "juju_integration" "wazuh_indexer_sysconfig" {
  model_uuid = data.juju_model.wazuh_indexer.uuid

  application {
    name     = module.wazuh_indexer.app_name
    endpoint = "juju-info"
  }
  application {
    name     = juju_application.sysconfig.name
    endpoint = "juju-info"
  }
}

resource "juju_application" "grafana_agent" {
  name       = var.grafana_agent.app_name
  model_uuid = data.juju_model.wazuh_indexer.uuid
  trust      = true

  charm {
    name     = "grafana-agent"
    channel  = var.grafana_agent.channel
    revision = var.grafana_agent.revision
    base     = var.wazuh_indexer.base
  }
}

resource "juju_integration" "grafana_agent_indexer" {
  model_uuid = data.juju_model.wazuh_indexer.uuid

  application {
    name     = module.wazuh_indexer.app_name
    endpoint = module.wazuh_indexer.provides.cos_agent
  }

  application {
    name     = juju_application.grafana_agent.name
    endpoint = "cos-agent"
  }
}
