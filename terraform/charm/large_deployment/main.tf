# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  apps = [
    for app in concat(var.apps != null ? var.apps : []) : app if app != null
  ]

  apps_not_in_main_model = [
    for app in concat([var.failover], local.apps) :
    app if app != null && app.model != var.main.model
  ]
  apps_not_in_failover_model = [
    for app in local.apps :
    app if app.model != var.failover.model
  ]

  all_models = distinct(concat(
    [var.main.model],
    var.failover != null ? [var.failover.model] : [],
    var.apps != null ? [for app in var.apps : app.model] : [],
  ))
}

#--------------------------------------------------------
# 1. DEPLOYMENTS
#--------------------------------------------------------

# main orchestrator opensearch app
module "opensearch_main" {
  source = "../simple_deployment"

  channel  = var.main.channel
  revision = var.main.revision
  base     = var.main.base

  app_name          = var.main.app_name
  units             = var.main.units
  config            = merge(var.main.config, { "cluster_name" : var.cluster_name, "init_hold" : "false" })
  model             = var.main.model
  constraints       = var.main.constraints
  storage           = var.main.storage
  endpoint_bindings = var.main.endpoint_bindings
}

# failover orchestrator opensearch app
module "opensearch_failover" {
  for_each = var.failover != null ? { "deployed" = true } : {}
  source   = "../simple_deployment"

  # required to flag whether this app is in the same model as the main orchestrator for TLS relation
  main_model = var.main.model

  channel  = var.failover.channel
  revision = var.failover.revision
  base     = var.failover.base

  app_name          = var.failover.app_name
  units             = var.failover.units
  config            = merge(var.failover.config, { "cluster_name" : var.cluster_name, "init_hold" : "true" })
  model             = var.failover.model
  constraints       = var.failover.constraints
  storage           = var.failover.storage
  endpoint_bindings = var.failover.endpoint_bindings
}

# all non orchestrator apps
module "opensearch_non_orchestrator_apps" {
  for_each = { for idx, app in local.apps : idx => app if app != null }
  source   = "../simple_deployment"

  # required to flag whether this app is in the same model as the main orchestrator for TLS relation
  main_model = var.main.model

  channel  = each.value.channel
  revision = each.value.revision
  base     = each.value.base

  app_name    = each.value.app_name
  units       = each.value.units
  config      = merge(each.value.config, { "cluster_name" : var.cluster_name, "init_hold" : "true" })
  model       = each.value.model
  constraints = each.value.constraints
  storage     = each.value.storage
}

#--------------------------------------------------------
# 2. OFFERS (if cross model)
#--------------------------------------------------------

# offer TLS certificates if needed
resource "juju_offer" "self_signed_certificates-offer" {
  for_each = length(local.all_models) > 1 ? { "offered" = true } : {}

  model            = var.main.model
  application_name = "self-signed-certificates"
  endpoint         = "certificates"
}

resource "juju_offer" "opensearch_main-offer" {
  for_each = length(local.all_models) > 1 ? { "offered" = true } : {}

  model            = var.main.model
  application_name = var.main.app_name
  endpoint         = "peer-cluster-orchestrator"
}

resource "juju_offer" "opensearch_failover-offer" {
  for_each = var.failover != null && length(local.apps_not_in_failover_model) > 1 ? { "offered" = true } : {}

  model            = var.failover.model
  application_name = var.failover.app_name
  endpoint         = "peer-cluster-orchestrator"
}


#--------------------------------------------------------
# 3. INTEGRATIONS
#--------------------------------------------------------

# For CROSS-MODEL TLS integrations
resource "juju_integration" "tls-opensearch-cross_model-integration" {
  # Only if cross-model
  for_each = { for app in local.apps_not_in_main_model : app.app_name => app }
  model    = each.value.model

  application {
    offer_url = juju_offer.self_signed_certificates-offer["offered"].url
  }
  application {
    name = each.value.app_name
  }

  depends_on = [
    module.opensearch_main,
    juju_offer.self_signed_certificates-offer,
  ]
}

# large deployments peer-cluster integrations with main orchestrator
resource "juju_integration" "peer_cluster-main-cross_model-relation" {
  for_each = { for app in local.apps_not_in_main_model : app.app_name => app }
  model    = each.value.model

  application {
    name     = each.value.app_name
    endpoint = "peer-cluster"
  }
  application {
    offer_url = juju_offer.opensearch_main-offer["offered"].url
  }

  depends_on = [
    module.opensearch_main,
    module.opensearch_failover,
    juju_offer.opensearch_main-offer,
  ]
}

# large deployments peer-cluster integrations with failover orchestrator if any
resource "juju_integration" "peer_cluster-failover-cross_model-relation" {
  for_each = var.failover != null ? { for app in local.apps_not_in_failover_model : app.app_name => app } : {}
  model    = each.value.model

  application {
    name     = each.value.app_name
    endpoint = "peer-cluster"
  }
  application {
    offer_url = juju_offer.opensearch_failover-offer["offered"].url
  }

  depends_on = [
    module.opensearch_failover,
    juju_offer.opensearch_failover-offer,
  ]
}
