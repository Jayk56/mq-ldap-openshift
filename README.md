# IBM MQ queue directory on OpenShift

This project runs 389 Directory Server on OpenShift. Tomcat reads IBM MQ queue names and connection settings from the directory. Operators can change those entries from a workstation with IBM’s JMS30Admin tool. Messages travel directly between Tomcat and IBM MQ.

## Bring a release into your work system

Import the reviewed Git commit into a private repository on your work system. Record the release tag and full commit ID in your intake ticket. If you clone it by hand, point the `origin` remote at the private repository before adding workplace settings. A source archive downloaded by commit ID also works when the import system accepts ZIP files. [GitHub source archives](https://docs.github.com/en/repositories/working-with-files/using-files/downloading-source-code-archives)

The [base deployment](base/kustomization.yaml) uses one container image. The [Tomcat helper](tomcat/pom.xml) has a separate build with Java dependencies. The following steps cover both.

## Copy the directory image

Use your organization’s image import process. If it uses Skopeo, run the example from the repository root on a host that can read Quay and write to your registry. Set `mirror_repo` to the destination repository. Use the approved registry login for that host. [Skopeo copy options](https://github.com/podman-container-tools/skopeo/blob/main/docs/skopeo-copy.1.md)

```sh
source_ref=$(awk '$1 == "image:" {print $2; exit}' base/deploy/statefulset.yaml)
image_digest=${source_ref##*@}
mirror_repo=registry.example.invalid/platform/dirsrv

skopeo copy --all --preserve-digests \
  "docker://$source_ref" "docker://${mirror_repo}:sha256-${image_digest#sha256:}"
skopeo inspect --raw "docker://${mirror_repo}@$image_digest" > /dev/null
```

The copy preserves the image digest and all platforms present in the source. The last command checks that the mirror serves that digest. Give the `mq-ldap` service account pull access, and configure cluster trust for the registry’s certificate issuer if needed.

## Set up the private deployment

From the root of the **private** repository, create your work configuration on the first import.

```sh
mkdir -p overlays/work
cp -n examples/internal-registry/kustomization.yaml overlays/work/kustomization.yaml
```

In [the example](examples/internal-registry/kustomization.yaml), `newName` sets the image repository. Set it to the same value as `mirror_repo`. Kustomize keeps the digest from the base. If your organization rebuilds the image, set `images[].digest` to the approved internal build’s digest and test that build. [Kustomize image settings](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/#customizing)

| Setting | Where it belongs |
| --- | --- |
| Directory project, storage class and permitted client networks | Set these in the private overlay. The default project is `mq-directory`. The base requests a 10 GiB filesystem volume with `ReadWriteOnce` access. Its network rule allows pods labeled `mq-ldap-client=true` in the `mq-apps` project. |
| Directory credentials and TLS | Supply `mq-ldap-admin` with a `password` key, `mq-ldap-tls` with `tls.crt` and `tls.key`, and the `mq-ldap-ca` ConfigMap with one public issuer certificate per `.crt` file. Use your secret manager for passwords and the private key. The certificate must cover `mq-ldap.<directory-project>.svc` and the private names clients use. |
| Queue names and connection settings | Copy the [sample LDIF](base/ldap/mq-objects.ldif) into the private repository and replace the example MQ values. Keep the reader password in Tomcat’s secret store and the publisher password in the operators’ vault. |
| Clients outside the cluster | Use private DNS and a private connection to the directory. Optional [AWS PrivateLink](cross-cluster/aws-privatelink/directory-access.yaml) and [Azure](cross-cluster/azure-private/kustomization.yaml) examples need site-specific addresses and access rules. |

Preview the configuration and check the image reference. Once the project and required resources exist, validate against your work cluster. Run the cluster command using the intended OpenShift context and project.

```sh
oc kustomize overlays/work
oc -n mq-directory apply --dry-run=server -k overlays/work
```

After review, deploy with `oc -n mq-directory apply -k overlays/work` and wait for `oc -n mq-directory rollout status statefulset/mq-ldap`. From the directory operator’s machine, initialize the accounts and publish the reviewed queue entries. Substitute the private file paths for your environment.

```sh
oc -n mq-directory exec -i mq-ldap-0 -- python3 /opt/mq-ldap/directory.py initialize < /secure/reader-password
oc -n mq-directory exec -i mq-ldap-0 -- python3 /opt/mq-ldap/directory.py writer < /secure/publisher-password
oc -n mq-directory exec -i mq-ldap-0 -- python3 /opt/mq-ldap/directory.py publish < /secure/mq-objects.ldif
```

## Build and check the clients

Build the Tomcat helper in work CI using JDK 11 or later. The CI Maven settings can send dependency and plugin downloads through the company repository with `mirrorOf` set to `*`. The build copies the IBM MQ Jakarta client and runtime dependencies to `tomcat/target/dependency/`. [Maven mirror settings](https://maven.apache.org/guides/mini/guide-mirror-settings.html)

```sh
mvn -B -s /secure/maven/settings.xml -f tomcat/pom.xml package
```

Install the helper JAR from `tomcat/target/` and its runtime dependencies in Tomcat’s shared `lib` directory when building your approved application image. Configure the [directory resource](tomcat/context.xml), mount the reader password at `/etc/mq-ldap-reader/password`, and trust the directory’s issuer in Java. Apply the [Java lookup settings](tomcat/mq-ldap.security) with `-Djava.security.properties=/absolute/path/to/mq-ldap.security`. From Tomcat, open `java:comp/env/ldap/MqCatalog` and look up a known queue.

On the workstation, install JMS30Admin from your approved IBM distribution. Give its Java runtime the same issuer trust and lookup settings. Save a private copy of the [workstation configuration](workstation/JMS30Admin.config.example) with the publisher credentials, restrict the file to the operator, and check the catalog with `JMS30Admin -cfg /secure/JMS30Admin.config < workstation/inspect.scp`. [IBM workstation tool configuration](https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=tools-configuring-jmsadmin-jms30admin)

## License

The deployment files and helper code in this repository use the Apache License, Version 2.0. The 389 Directory Server image and IBM MQ software carry their own license terms. [Repository license](LICENSE)
