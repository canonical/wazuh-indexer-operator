# Terraform module for OpenSearch operator

This is a Terraform module facilitating the deployment of the OpenSearch charm with [Terraform juju provider](https://github.com/juju/terraform-provider-juju/). For more information, refer to the provider [documentation](https://registry.terraform.io/providers/juju/juju/latest/docs). 

## Requirements
This module requires a `juju` model to be available. Refer to the [usage section](#usage) below for more details.

<!-- vale Canonical.007-Headings-sentence-case = NO -->
## API
<!-- vale Canonical.007-Headings-sentence-case = YES -->

### Inputs
The module offers the following configurable inputs:

| Name          | Type        | Description                                               | Required   |
|---------------|-------------|-----------------------------------------------------------|------------|
| `app_name`    | string      | Application name                                          | False      |
| `channel`     | string      | Channel that the charm is deployed from                   | False      |
| `base`        | string      | The series to be used for this charm                      | False      |
| `config`      | map(string) | Map of the charm configuration options                    | False      |
| `model`       | string      | Name of the model that the charm is deployed on           | **True**       |
| `resources`   | map(string) | Map of the charm resources                                | False      |
| `revision`    | number      | Revision number of the charm name                         | False      |
| `units`       | number      | Number of units to be deployed                            | False      |
| `constraints` | string      | Machine constraints for the charm                         | False      |
| `storage`     | map(string) | Storage description, must follow the Juju provider schema | False      |
| `expose`      | bool        | Expose block, if set to true, opens to anyone's access    | False      |


### Outputs
When applied, the module exports the following outputs:

| Name       | Description                 |
|------------|-----------------------------|
| `app_name` | Application name            |
| `provides` | Map of `provides` endpoints |
| `requires` | Map of `requires` endpoints |

## Usage

This module is intended to be used as part of a higher-level module. When defining one, users should ensure that Terraform is aware of the `juju_model` dependency of the charm module. There are two options to do so when creating a high-level module:

### Define a `juju_model` resource
Define a `juju_model` resource and pass to the `model` input a reference to the `juju_model` resource's name. For example:

```
resource "juju_model" "opensearch" {
  name = opensearch
}

module "opensearch-operator" {
  source = "<path-to-this-directory>"
  model = juju_model.opensearch.name
}
```

### Define a `data` source
Define a `data` source and pass to the `model` input a reference to the `data.juju_model` resource's name. This will enable Terraform to look for a `juju_model` resource with a name attribute equal to the one provided, and apply only if this is present. Otherwise, it will fail before applying anything.

```
data "juju_model" "opensearch" {
  name = var.model
}

module "opensearch" {
  source = "<path-to-this-directory>"
  model = data.juju_model.opensearch.name
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

No modules.

## Resources

| Name | Type |
|------|------|
| [juju_application.opensearch](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/application) | resource |
| [juju_application.self-signed-certificates](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/application) | resource |
| [juju_integration.tls-opensearch-same-model_integration](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/integration) | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_app_name"></a> [app\_name](#input\_app\_name) | Application name | `string` | `"opensearch"` | no |
| <a name="input_base"></a> [base](#input\_base) | Charm base (old name: series) | `string` | `"ubuntu@22.04"` | no |
| <a name="input_channel"></a> [channel](#input\_channel) | Charm channel | `string` | `"2/stable"` | no |
| <a name="input_config"></a> [config](#input\_config) | Map of charm configuration options | `map(string)` | <pre>{<br/>  "profile": "testing"<br/>}</pre> | no |
| <a name="input_constraints"></a> [constraints](#input\_constraints) | String listing constraints for this application | `string` | `"arch=amd64"` | no |
| <a name="input_endpoint_bindings"></a> [endpoint\_bindings](#input\_endpoint\_bindings) | Map of endpoint bindings | `map(string)` | `{}` | no |
| <a name="input_expose"></a> [expose](#input\_expose) | Expose the application for external access. | `bool` | `false` | no |
| <a name="input_machines"></a> [machines](#input\_machines) | List of machines for placement | `list(string)` | `[]` | no |
| <a name="input_main_model"></a> [main\_model](#input\_main\_model) | Model name of the main orchestrator (to detect same-model apps) | `string` | `null` | no |
| <a name="input_model"></a> [model](#input\_model) | Model name | `string` | n/a | yes |
| <a name="input_revision"></a> [revision](#input\_revision) | Charm revision | `number` | `null` | no |
| <a name="input_self-signed-certificates"></a> [self-signed-certificates](#input\_self-signed-certificates) | Configuration for the self-signed-certificates app | <pre>object({<br/>    channel     = optional(string, "latest/stable")<br/>    revision    = optional(string, null)<br/>    base        = optional(string, "ubuntu@22.04")<br/>    constraints = optional(string, "arch=amd64")<br/>    machines    = optional(list(string), [])<br/>    config      = optional(map(string), { "ca-common-name" : "CA" })<br/>  })</pre> | `{}` | no |
| <a name="input_storage"></a> [storage](#input\_storage) | Map of storage used by the application | `map(string)` | `{}` | no |
| <a name="input_units"></a> [units](#input\_units) | Charm units | `number` | `3` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_app_names"></a> [app\_names](#output\_app\_names) | Output of all deployed application names. |
| <a name="output_provides"></a> [provides](#output\_provides) | Map of all "provides" endpoints |
| <a name="output_requires"></a> [requires](#output\_requires) | Map of all "requires" endpoints |
<!-- END_TF_DOCS -->