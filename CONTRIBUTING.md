# Contributing

## Overview

This document explains the processes and practices recommended for contributing enhancements to
this operator.

<!-- TEMPLATE-TODO: Update the URL for issue creation -->

- Generally, before developing enhancements to this charm, you should consider [opening an issue
  ](https://github.com/canonical/wazuh-indexer-operator/issues) explaining your use case.
- If you would like to chat with us about your use-cases or proposed implementation, you can reach
  us at [Canonical Mattermost public channel](https://chat.charmhub.io/charmhub/channels/charm-dev)
  or [Discourse](https://discourse.charmhub.io/).
- Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library
  will help you a lot when working on new features or bug fixes.
- All enhancements require review before being merged. Code review typically examines
  - code quality
  - test coverage
  - user experience for Juju administrators of this charm.
- Please help us out in ensuring easy to review branches by rebasing your pull request branch onto
  the `main` branch. This also avoids merge commits and creates a linear Git commit history.

## Developing
Install `tox`, `poetry`, and `charmcraftcache`

Install pipx: https://pipx.pypa.io/stable/installation/
```shell
pipx install tox
pipx install poetry
pipx install charmcraftcache
```

You can create an environment for development:
```shell
poetry install
```


## Build charm
Build the charm in this git repository using:
```shell
charmcraftcache pack
```

### Testing

To run tests, first build the charm as described above, then run the following

```shell
tox -e format            # update your code according to linting rules
tox -e lint              # code style
tox -e unit              # unit tests
charmcraft test lxd-vm:  # integration tests
tox                      # runs 'format', 'lint', and 'unit' environments
```

## Deploy

OpenSearch has a set of system requirements to correctly function, you can find the list [here](https://opensearch.org/docs/latest/install-and-configure/install-opensearch/index/).
Some of those settings must be set using cloudinit-userdata on the model, while others must be set on the host machine:
```bash
cat <<EOF > cloudinit-userdata.yaml
cloudinit-userdata: |
  postruncmd:
    - [ 'echo', 'vm.max_map_count=262144', '>>', '/etc/sysctl.conf' ]
    - [ 'echo', 'vm.swappiness=0', '>>', '/etc/sysctl.conf' ]
    - [ 'echo', 'net.ipv4.tcp_retries2=5', '>>', '/etc/sysctl.conf' ]
    - [ 'echo', 'fs.file-max=1048576', '>>', '/etc/sysctl.conf' ]
    - [ 'sysctl', '-p' ]
EOF

echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
echo "vm.swappiness=0" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

Then create a new model and set the previously generated file in it.
```bash
# Create a model
juju add-model dev

# Enable DEBUG logging
juju model-config logging-config="<root>=INFO;unit=DEBUG"

# Add cloudinit-userdata
juju model-config --file=./cloudinit-userdata.yaml

# Increase the frequency of the update-status event
juju model-config update-status-hook-interval=1m
```

You can then deploy the charm with a TLS relation.
```bash
# Deploy the self-signed-certificates operator
juju deploy self-signed-certificates --channel=latest/stable --show-log --verbose

# generate a CA certificate
juju config \
    self-signed-certificates \
    ca-common-name="CN_CA" \
    certificate-validity=365 \
    root-ca-validity=365
    
# Deploy the Wazuh Indexer charm
juju deploy -n 1 ./wazuh_indexer_ubuntu-22.04-amd64.charm --series jammy --show-log --verbose

# Relate the Wazuh Indexer charm with the self-signed-certificates operator
juju integrate self-signed-certificates wazuh-indexer
```

**Note:** The TLS settings shown here are for self-signed-certificates, which are not recommended for production clusters. The TLS Certificates Operator offers a variety of configurations. Read more on the self-signed-certificates Operator [here](https://charmhub.io/self-signed-certificates).


## Canonical Contributor Agreement
Canonical welcomes contributions to the Charmed Template Operator. Please check out our [contributor agreement](https://ubuntu.com/legal/contributors) if you're interested in contributing to the solution.
