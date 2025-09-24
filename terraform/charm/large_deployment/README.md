# Terraform module for OpenSearch operator

This is a Terraform module facilitating the deployment of the OpenSearch charm with [Terraform juju provider](https://github.com/juju/terraform-provider-juju/). For more information, refer to the provider [documentation](https://registry.terraform.io/providers/juju/juju/latest/docs). 

## Requirements
This module requires a `juju` model to be available. Refer to the [usage section](#usage) below for more details.

<!-- vale Canonical.007-Headings-sentence-case = NO -->
## API
<!-- vale Canonical.007-Headings-sentence-case = YES -->

### Inputs
The module offers the following configurable inputs:

| Name           | Type                                                                                      | Description                               | Required |
|----------------|-------------------------------------------------------------------------------------------|-------------------------------------------|----------|
| `cluster_name` | string                                                                                    | OpenSearch full-cluster name              | False    |
| `main`         | object <br/>(the object structure as defined in simple deployment input variables)        | Main orchestrator application description | **True**     |         
| `failover`     | object <br/>(the object structure as defined in simple deployment input variables)        | Main orchestrator application description | False    |
| `apps`         | list(object) <br/>(the object structure as defined in simple deployment input variables)  | Main orchestrator application description | False    |


### Outputs
When applied, the module exports the following outputs:

| Name                                           | Description                                                       |
|------------------------------------------------|-------------------------------------------------------------------|
| `app_name`                                     | Application name                                                  |
| `provides`                                     | Map of `provides` endpoints                                       |
| `requires`                                     | Map of `requires` endpoints                                       |
| `certificates_offer_url`                       | Offer URL of the TLS provider if cross-model deployments          |
| `peer_cluster_orchestrator_main_offer_url`     | Offer URL of the main orchestrator if cross-model deployments     |
| `peer_cluster_orchestrator_failover_offer_url` | Offer URL of the failover orchestrator if cross-model deployments |


## Usage

This module is  to be used as part of a higher-level module. When defining one, users should ensure that Terraform is aware of the `juju_model` dependency of the charm module. There are two options to do so when creating a high-level module:

### Define a `juju_model` resource
Define a `juju_model` resource and pass to the `model` input a reference to the `juju_model` resource's name. For example:

```
resource "juju_model" "opensearch-main" {
  name = "main-model"
}

...

module "opensearch" {
  source       = <path-to-this-directory>
  
  // optional
  cluster_name = "mycluster"
  
  main     = {
    app_name   = "main"
    model      = juju_model.opensearch-main.name
    ...
  }
  
  // optional
  failover = {
    app_name   = "failover"
    model      = juju_model.opensearch-failover.name
    ...
  }
  
  // optional
  apps     = [
    {
      app_name = "data1"
      model    = ..
    },
    {
      app_name = "data2"
      model    = ..
    }
  ]
}
```
Where `main, failover, apps` are defined as described in the input variables above.

### Define a `data` source
Define a `data` source and pass to the `model_name` input a reference to the `data.juju_model` resource's name. This will enable Terraform to look for a `juju_model` resource with a name attribute equal to the one provided, and apply only if this is present. Otherwise, it will fail before applying anything.

```
data "juju_model" "opensearch-main-model" {
  name = var.model
}

module "opensearch" {
  source = "<path-to-this-directory>"
  
  main   = {
    app_name   = "main"
    model      = data.juju_model.opensearch-main-model.name
    ...
  }
  ....
}
```

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.6 |
| <a name="requirement_juju"></a> [juju](#requirement\_juju) | >= 0.20.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_juju"></a> [juju](#provider\_juju) | >= 0.20.0 |

## Modules

| Name | Source | Version |
|------|--------|---------|
| <a name="module_opensearch_failover"></a> [opensearch\_failover](#module\_opensearch\_failover) | ../simple_deployment | n/a |
| <a name="module_opensearch_main"></a> [opensearch\_main](#module\_opensearch\_main) | ../simple_deployment | n/a |
| <a name="module_opensearch_non_orchestrator_apps"></a> [opensearch\_non\_orchestrator\_apps](#module\_opensearch\_non\_orchestrator\_apps) | ../simple_deployment | n/a |

## Resources

| Name | Type |
|------|------|
| [juju_integration.peer_cluster-failover-cross_model-relation](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |
| [juju_integration.peer_cluster-main-cross_model-relation](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |
| [juju_integration.tls-opensearch-cross_model-integration](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |
| [juju_offer.opensearch_failover-offer](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/offer) | resource |
| [juju_offer.opensearch_main-offer](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/offer) | resource |
| [juju_offer.self_signed_certificates-offer](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/offer) | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_apps"></a> [apps](#input\_apps) | Non orchestrator apps (e.g: ml, data.hot etc.) | <pre>list(object({<br/>    app_name          = string<br/>    model             = string<br/>    config            = optional(map(string), { "init_hold" : "true" })<br/>    channel           = optional(string, "2/stable")<br/>    base              = optional(string, "ubuntu@22.04")<br/>    revision          = optional(string, null)<br/>    units             = optional(number, 3)<br/>    constraints       = optional(string, "arch=amd64")<br/>    machines          = optional(list(string), [])<br/>    storage           = optional(map(string), {})<br/>    endpoint_bindings = optional(map(string), {})<br/>    expose            = optional(bool, false)<br/>  }))</pre> | `null` | no |
| <a name="input_cluster_name"></a> [cluster\_name](#input\_cluster\_name) | The cluster name of the fleet. | `string` | `"opensearch"` | no |
| <a name="input_failover"></a> [failover](#input\_failover) | Failover orchestrator app definition | <pre>object({<br/>    app_name          = string<br/>    model             = string<br/>    config            = optional(map(string), { "init_hold" : "true" })<br/>    channel           = optional(string, "2/stable")<br/>    base              = optional(string, "ubuntu@22.04")<br/>    revision          = optional(string, null)<br/>    units             = optional(number, 3)<br/>    constraints       = optional(string, "arch=amd64")<br/>    machines          = optional(list(string), [])<br/>    storage           = optional(map(string), {})<br/>    endpoint_bindings = optional(map(string), {})<br/>    expose            = optional(bool, false)<br/>  })</pre> | `null` | no |
| <a name="input_main"></a> [main](#input\_main) | Main orchestrator app definition | <pre>object({<br/>    app_name          = string<br/>    model             = string<br/>    config            = optional(map(string), {})<br/>    channel           = optional(string, "2/stable")<br/>    base              = optional(string, "ubuntu@22.04")<br/>    revision          = optional(string, null)<br/>    units             = optional(number, 3)<br/>    constraints       = optional(string, "arch=amd64")<br/>    machines          = optional(list(string), [])<br/>    storage           = optional(map(string), {})<br/>    endpoint_bindings = optional(map(string), {})<br/>    expose            = optional(bool, false)<br/>  })</pre> | n/a | yes |
| <a name="input_self-signed-certificates"></a> [self-signed-certificates](#input\_self-signed-certificates) | Configuration for the self-signed-certificates app | <pre>object({<br/>    channel     = optional(string, "latest/stable")<br/>    revision    = optional(string, null)<br/>    base        = optional(string, "ubuntu@22.04")<br/>    constraints = optional(string, "arch=amd64")<br/>    machines    = optional(list(string), [])<br/>    config      = optional(map(string), { "ca-common-name" : "CA" })<br/>  })</pre> | `{}` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_app_names"></a> [app\_names](#output\_app\_names) | Output of all deployed application names. |
| <a name="output_offers"></a> [offers](#output\_offers) | List of offers URLs. |
| <a name="output_provides"></a> [provides](#output\_provides) | Map of all "provides" endpoints |
| <a name="output_requires"></a> [requires](#output\_requires) | Map of all "requires" endpoints |
<!-- END_TF_DOCS -->