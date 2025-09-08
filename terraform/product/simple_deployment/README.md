# Terraform module for OpenSearch operator

This is a Terraform module facilitating the deployment of the OpenSearch charm with [Terraform juju provider](https://github.com/juju/terraform-provider-juju/). For more information, refer to the provider [documentation](https://registry.terraform.io/providers/juju/juju/latest/docs). 

## Requirements
This module requires a `juju` model to be available. Refer to the [usage section](#usage) below for more details.

<!-- vale Canonical.007-Headings-sentence-case = NO -->
## API
<!-- vale Canonical.007-Headings-sentence-case = YES -->

### Inputs
The module offers the following configurable inputs:

| Name                       | Type                                                                                                                                                          | Description                              | Required |
|----------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------|----------|
| `opensearch`               | object <br/>(structure as defined in opensearch simple deployment input variables)                                                                            | OpenSearch main application              | **True** |
| `opensearch-dashboards`    | object <br/>(structure as defined in opensearch-dashboards input variables)                                                                                   | OpenSearch Dashboards application        | False    |
| `backups-integrator`       | object <br/>(structure as defined in the azure-storage/s3-integrator charms, with the addition of an attribute: <br/>- `storage_type` = "s3" or "azure" <br/> | Backup (s3/azure) integrator application | False    |
| `data-integrator`          | object <br/>(structure as defined in the data-integrator charm)                                                                                               | data-integrator application              | False    |
| `self-signed-certificates` | object <br/>(structure as defined in the self-signed-certificates charm)                                                                                      | self-signed-certificates application     | False    |
| `grafana-agent`            | object <br/>(structure as defined in the grafana-agent charm)                                                                                                 | grafana-agent application                | False    |


### Outputs
When applied, the module exports the following outputs:

| Name        | Description                               |
|-------------|-------------------------------------------|
| `app_names` | Map of List of deployed application names |
| `provides`  | Map of `provides` endpoints               |
| `requires`  | Map of `requires` endpoints               |

Example output:
```
app_names = {
  "backups-integrator" = "s3-integrator"
  "data-integrator" = "data-integrator"
  "grafana-agent" = "grafana-agent"
  "opensearch" = "opensearch"
  "opensearch-dashboards" = "opensearch-dashboards"
  "self-signed-certificates" = "self-signed-certificates"
}
offers = {}
provides = {
  "cos_agent" = "cos-agent"
  "opensearch_client" = "opensearch-client"
  "peer_cluster_orchestrator" = "peer-cluster-orchestrator"
}
requires = {
  "certificates" = "certificates"
  "peer_cluster" = "opensearch-client"
  "s3_credentials" = "s3-credentials"
}

```

## Usage

This module is intended to be a product module, deploying all components for a proper yet simple opensearch deployment.

It may be used as-is and directly as follows:
```
tf plan \
  -var='opensearch={"model": "dev"}' \
  -var='backups-integrator={"config": {"bucket": "mybucket"}}' \
  -out terraform.out
  
tf apply terraform.out
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
| <a name="module_opensearch"></a> [opensearch](#module\_opensearch) | ../../charm/simple_deployment | n/a |
| <a name="module_opensearch-dashboards"></a> [opensearch-dashboards](#module\_opensearch-dashboards) | git::https://github.com/canonical/opensearch-dashboards-operator//terraform | 2/edge |

## Resources

| Name | Type |
|------|------|
| [juju_application.backups-integrator](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/application) | resource |
| [juju_application.data-integrator](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/application) | resource |
| [juju_application.grafana-agent](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/application) | resource |
| [juju_integration.backups_integrator-opensearch-integration](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |
| [juju_integration.data_integrator-opensearch-integration](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |
| [juju_integration.grafana_agent-opensearch-integration](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |
| [juju_integration.grafana_agent-opensearch_dashboards-integration](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |
| [juju_integration.opensearch_dashboards-opensearch-integration](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |
| [juju_integration.opensearch_dashboards-tls-integration](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_backups-integrator"></a> [backups-integrator](#input\_backups-integrator) | Configuration for the backup integrator | <pre>object({<br/>    storage_type = optional(string, "s3")<br/>    config       = map(string)<br/>    channel      = optional(string, "latest/edge")<br/>    base         = optional(string, "ubuntu@22.04")<br/>    revision     = optional(string, null)<br/>    constraints  = optional(string, "arch=amd64")<br/>    machines     = optional(list(string), [])<br/>  })</pre> | n/a | yes |
| <a name="input_data-integrator"></a> [data-integrator](#input\_data-integrator) | Configuration for the data-integrator | <pre>object({<br/>    config      = optional(map(string), { "index-name" : "test", "extra-user-roles" : "admin" })<br/>    channel     = optional(string, "latest/edge")<br/>    base        = optional(string, "ubuntu@22.04")<br/>    revision    = optional(string, null)<br/>    constraints = optional(string, "arch=amd64")<br/>    machines    = optional(list(string), [])<br/>  })</pre> | `{}` | no |
| <a name="input_grafana-agent"></a> [grafana-agent](#input\_grafana-agent) | Configuration for the grafana-agent | <pre>object({<br/>    channel     = optional(string, "1/stable")<br/>    revision    = optional(string, null)<br/>    base        = optional(string, "ubuntu@22.04")<br/>    constraints = optional(string, "arch=amd64")<br/>    config      = optional(map(string), {})<br/>  })</pre> | `{}` | no |
| <a name="input_opensearch"></a> [opensearch](#input\_opensearch) | OpenSearch app definition | <pre>object({<br/>    app_name          = optional(string, "opensearch")<br/>    model             = string<br/>    config            = optional(map(string), { "cluster_name" : "opensearch" })<br/>    channel           = optional(string, "2/stable")<br/>    base              = optional(string, "ubuntu@22.04")<br/>    revision          = optional(string, null)<br/>    units             = optional(number, 3)<br/>    constraints       = optional(string, "arch=amd64")<br/>    machines          = optional(list(string), [])<br/>    storage           = optional(map(string), {})<br/>    endpoint_bindings = optional(map(string), {})<br/>    expose            = optional(bool, false)<br/>  })</pre> | n/a | yes |
| <a name="input_opensearch-dashboards"></a> [opensearch-dashboards](#input\_opensearch-dashboards) | OpenSearch Dashboards app definition | <pre>object({<br/>    app_name          = optional(string, "opensearch-dashboards")<br/>    config            = optional(map(string), {})<br/>    channel           = optional(string, "2/stable")<br/>    base              = optional(string, "ubuntu@22.04")<br/>    revision          = optional(string, null)<br/>    units             = optional(number, 1)<br/>    constraints       = optional(string, "arch=amd64")<br/>    machines          = optional(list(string), [])<br/>    endpoint_bindings = optional(map(string), {})<br/>    tls               = optional(bool, false)<br/>    expose            = optional(bool, false)<br/>  })</pre> | `{}` | no |
| <a name="input_self-signed-certificates"></a> [self-signed-certificates](#input\_self-signed-certificates) | Configuration for the self-signed-certificates app | <pre>object({<br/>    channel     = optional(string, "latest/stable")<br/>    revision    = optional(string, null)<br/>    base        = optional(string, "ubuntu@22.04")<br/>    constraints = optional(string, "arch=amd64")<br/>    machines    = optional(list(string), [])<br/>    config      = optional(map(string), { "ca-common-name" : "CA" })<br/>  })</pre> | `{}` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_app_names"></a> [app\_names](#output\_app\_names) | Output of all deployed application names. |
| <a name="output_offers"></a> [offers](#output\_offers) | List of offers URLs. |
| <a name="output_provides"></a> [provides](#output\_provides) | Map of all "provides" endpoints |
| <a name="output_requires"></a> [requires](#output\_requires) | Map of all "requires" endpoints |
<!-- END_TF_DOCS -->