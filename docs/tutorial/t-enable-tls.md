> [Charmed OpenSearch Tutorial](/t/9722) >  3. Enable TLS encryption

# Enable encryption with TLS

[Transport Layer Security (TLS)](https://en.wikipedia.org/wiki/Transport_Layer_Security) is a protocol used to encrypt data exchanged between two applications. Essentially, it secures data transmitted over a network.

Typically, enabling TLS internally within a highly available database or between a highly available database and client/server applications requires a high level of expertise. This has all been encoded into Charmed OpenSearch so that configuring TLS requires minimal effort on your end.

TLS is enabled by integrating Charmed OpenSearch with the [Self Signed Certificates Charm](https://charmhub.io/self-signed-certificates). This charm centralises TLS certificate management consistently and handles operations like providing, requesting, and renewing TLS certificates.

In this section, you will learn how to enable security in your OpenSearch deployment using TLS encryption.

[note type="caution"]
**[Self-signed certificates](https://en.wikipedia.org/wiki/Self-signed_certificate) are not recommended for a production environment.**

Check [this guide](/t/11664) for an overview of the TLS certificates charms available. 
[/note]

---

## Configure TLS

Before enabling TLS on Charmed OpenSearch we must first deploy the `self-signed-certificates` charm:

```shell
juju deploy self-signed-certificates --config ca-common-name="Tutorial CA"
```

Wait until `self-signed-certificates` is active. Use `juju status --watch 1s` to monitor the progress.

```shell
Model     Controller       Cloud/Region         Version  SLA          Timestamp
tutorial  opensearch-demo  localhost/localhost  3.5.3    unsupported  13:22:05Z

App                       Version  Status   Scale  Charm                     Channel        Rev  Exposed  Message
opensearch                         blocked      1  opensearch                2/beta         117  no       Missing TLS relation with this cluster.
self-signed-certificates           active       1  self-signed-certificates  latest/stable  155  no

Unit                         Workload  Agent  Machine  Public address  Ports  Message
opensearch/0*                blocked   idle   0        10.214.176.107         Missing TLS relation with this cluster.
self-signed-certificates/0*  active    idle   1        10.214.176.116

Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.214.176.107  juju-b0826b-0  ubuntu@22.04      Running
1        started  10.214.176.116  juju-b0826b-1  ubuntu@22.04      Running
```

## Integrate with OpenSearch

To enable TLS on Charmed OpenSearch, you must integrate (also known as "relate") the two applications. We will go over integrations in more detail in the [next page](/t/9714) of this tutorial.

To integrate `self-signed-certificates` with `opensearch`, run the following command:

```shell
juju integrate self-signed-certificates opensearch
```

The OpenSearch service will start. This might take some time. Once done, you can see the new integrations with `juju status --relations`.

```shell
Model     Controller       Cloud/Region         Version  SLA          Timestamp
tutorial  opensearch-demo  localhost/localhost  3.5.3    unsupported  13:23:24Z

App                       Version  Status  Scale  Charm                     Channel        Rev  Exposed  Message
opensearch                         active      1  opensearch                2/beta         117  no
self-signed-certificates           active      1  self-signed-certificates  latest/stable  155  no

Unit                         Workload  Agent  Machine  Public address  Ports     Message
opensearch/0*                active    idle   0        10.214.176.107  9200/tcp
self-signed-certificates/0*  active    idle   1        10.214.176.116

Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.214.176.107  juju-b0826b-0  ubuntu@22.04      Running
1        started  10.214.176.116  juju-b0826b-1  ubuntu@22.04      Running

Integration provider                   Requirer                       Interface           Type     Message
opensearch:node-lock-fallback          opensearch:node-lock-fallback  node_lock_fallback  peer
opensearch:opensearch-peers            opensearch:opensearch-peers    opensearch_peers    peer
opensearch:upgrade-version-a           opensearch:upgrade-version-a   upgrade             peer
self-signed-certificates:certificates  opensearch:certificates        tls-certificates    regular
```

Notice the last relation: `self-signed-certificates:certificates  opensearch:certificates        tls-certificates    regular`. 
> **Next step:** [4. Integrate with a client application](/t/9714)