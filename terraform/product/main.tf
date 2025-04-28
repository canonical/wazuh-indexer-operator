# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

data "juju_model" "wazuh_indexer" {
  name = var.indexer_model
}

module "wazuh_indexer" {
  source      = "../charm"
  app_name    = var.wazuh_indexer.app_name
  channel     = var.wazuh_indexer.channel
  config      = var.wazuh_indexer.config
  constraints = var.wazuh_indexer.constraints
  model       = data.juju_model.wazuh_indexer.name
  revision    = var.wazuh_indexer.revision
  base        = var.wazuh_indexer.base
  units       = var.wazuh_indexer.units
}

module "grafana_agent" {
  source     = "git::https://github.com/canonical/grafana-agent-operator//terraform?ref=rev469&depth=1"
  app_name   = var.wazuh_indexer.app_name
  channel    = var.wazuh_indexer.channel
  config     = var.wazuh_indexer.config
  model_name = data.juju_model.wazuh_indexer.name
  revision   = var.wazuh_indexer.revision
  units      = 0
}

resource "juju_integration" "grafana_agent_indexer" {
  model = juju_model.wazuh_indexer.name

  application {
    name     = module.wazuh_indexer.app_name
    endpoint = module.wazuh_indexer.provides.cos_agent
  }

  application {
    name     = module.grafana_agent.app_name
    endpoint = "cos-agent"
  }
}
