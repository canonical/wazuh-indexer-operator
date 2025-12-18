# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

variable "model_uuid" {
  description = "Reference to the VM Juju model to deploy the indexer charms to."
  type        = string
}

variable "grafana_agent" {
  type = object({
    app_name = optional(string, "grafana-agent")
    channel  = optional(string, "latest/stable")
    config   = optional(map(string), {})
    revision = optional(number)
  })
}

variable "sysconfig" {
  type = object({
    app_name = optional(string, "sysconfig")
    channel  = optional(string, "latest/stable")
    revision = optional(number)
  })
}


variable "wazuh_indexer" {
  type = object({
    app_name    = optional(string, "wazuh-indexer")
    channel     = optional(string, "4.11/edge")
    config      = optional(map(string), {})
    constraints = optional(string, "arch=amd64")
    revision    = optional(number)
    base        = optional(string, "ubuntu@22.04")
    units       = optional(number, 3)
  })
}
