# IBM MQ queue directory on OpenShift

This project runs 389 Directory Server on OpenShift. Tomcat reads IBM MQ queue names and connection settings from the directory. Operators can change those entries from a workstation with IBM’s JMS30Admin tool. Messages travel directly between Tomcat and IBM MQ.

| Your role | Start here |
| --- | --- |
| Platform admin | [Request storage, certificates and access](docs/platform-checklist.md), then [install the directory](#set-up-the-private-deployment). |
| Tomcat developer | [Connect the application and look up a queue](#look-up-a-queue-in-tomcat). |
| MQ operator | [Inspect and update names from a workstation](#manage-names-from-a-workstation). |

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

Choose one starting configuration before copying files into the **private** repository.

| How clients reach the directory | Template |
| --- | --- |
| In-cluster Service or a separately managed private listener | [Internal registry](examples/internal-registry/kustomization.yaml) |
| AWS PrivateLink with a two-subnet internal load balancer | [AWS PrivateLink](examples/aws-privatelink/kustomization.yaml) |
| Azure internal load balancer for a workstation and remote Tomcat | [Azure private access](examples/azure-private/kustomization.yaml) |

```sh
template=examples/internal-registry    # or examples/aws-privatelink or examples/azure-private
mkdir -p overlays/work
if [ -e overlays/work/kustomization.yaml ]; then
  echo "A work overlay already exists. Edit overlays/work/kustomization.yaml."
else
  cp "$template/kustomization.yaml" overlays/work/kustomization.yaml
fi
```

In `overlays/work/kustomization.yaml`, set `images[].newName` to the same value as `mirror_repo`. Kustomize keeps the digest from the base. If your organization rebuilds the image, set `images[].digest` to the approved internal build’s digest and test that build. [Kustomize image settings](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/#customizing)

**AWS.** In the work overlay, replace the two subnet IDs, the reserved private address for each subnet (same order, comma-separated without spaces), and the security group. The overlay copies both addresses into the directory's access rules. The AWS teams still need to create the endpoint service, interface endpoint and private DNS. [Private access checklist](docs/platform-checklist.md#private-access)

**Azure.** Set the workstation and remote Tomcat ranges in the work overlay. Keep its `replacements` in that final overlay so the directory rules receive the same addresses. If the network translates an address on the way to the pod, remove that client's replacement and patch its NetworkPolicy to the source seen by the directory.

| Setting | Where it belongs |
| --- | --- |
| Directory project, storage class and permitted client networks | Set these in the private overlay. The default project is `mq-directory`. The base requests a 10 GiB filesystem volume with `ReadWriteOnce` access. Its network rule allows pods labeled `mq-ldap-client=true` in the `mq-apps` project. |
| Directory credentials and TLS | Supply `mq-ldap-admin` with a `password` key, `mq-ldap-tls` with `tls.crt` and `tls.key`, and the `mq-ldap-ca` ConfigMap with one public issuer certificate per `.crt` file. Use your secret manager for passwords and the private key. The certificate must cover `mq-ldap.<directory-project>.svc` and the private names clients use. |
| Queue names and connection settings | Copy the [sample LDIF](base/ldap/mq-objects.ldif) into the private repository and replace the example MQ values. Keep the reader password in Tomcat’s secret store and the publisher password in the operators’ vault. |
| Clients outside the cluster | Use private DNS and a private connection to the directory. Optional [AWS PrivateLink](cross-cluster/aws-privatelink/directory-access.yaml) and [Azure](cross-cluster/azure-private/kustomization.yaml) examples need site-specific addresses and access rules. |

Set the directory cluster context and the project named by `namespace:` in the work overlay. Use `oc config get-contexts -o name` to see your context names. The following shell function sends the installation commands to that cluster and project.

```sh
DIRECTORY_CONTEXT=your-directory-context
DIRECTORY_PROJECT=mq-directory
dir_oc() { oc --context="${DIRECTORY_CONTEXT:?Set the directory context}" -n "${DIRECTORY_PROJECT:?Set the directory project}" "$@"; }
```

If the secret manager already supplies the three directory resources, go straight to the checks below. For a first installation from approved files, use the following names and keys. Add a separate `--from-file=intermediate.crt=...` for each intermediate, giving each file its own name.

```sh
dir_oc create secret generic mq-ldap-admin --from-file=password=/secure/admin-password
dir_oc create secret tls mq-ldap-tls --cert=/secure/server.crt --key=/secure/server.key
dir_oc create configmap mq-ldap-ca --from-file=root.crt=/secure/root.crt
```

If this deployment needs its own image pull secret, create the service account before linking the existing secret. Run `dir_oc apply -f base/deploy/serviceaccount.yaml`, then `dir_oc secrets link mq-ldap YOUR_PULL_SECRET --for=pull`. A site-wide registry grant may already provide this access.

Check the resources exist, preview the rendered image and network rules, and validate the manifests with the cluster. The API accepts a pod manifest that refers to a missing Secret, so check those names separately. [Kubernetes Secret requirements](https://kubernetes.io/docs/concepts/configuration/secret/#optional-secrets)

```sh
dir_oc get secret mq-ldap-admin mq-ldap-tls
dir_oc get configmap mq-ldap-ca
oc kustomize overlays/work
dir_oc apply --dry-run=server -k overlays/work
```

After review, deploy and wait for the pod. If the wait times out, `dir_oc get pvc data-mq-ldap-0` shows whether storage was assigned and `dir_oc describe pod mq-ldap-0` shows events for scheduling, image pulls, mounts and probes.

```sh
dir_oc apply -k overlays/work
dir_oc rollout status statefulset/mq-ldap --timeout=10m
```

From the directory operator's machine, validate the private LDIF, initialize the two accounts and publish. The `<` reads a file from that machine. A valid file prints its entry count. Validation checks file structure, names and numbered properties. Verify the queue targets with a lookup and an MQ message test. File validation can also run on a workstation or in CI. [Local checks](tests/README.md)

```sh
dir_oc exec -i mq-ldap-0 -- python3 /opt/mq-ldap/directory.py validate < /secure/mq-objects.ldif
dir_oc exec -i mq-ldap-0 -- python3 /opt/mq-ldap/directory.py initialize < /secure/reader-password
dir_oc exec -i mq-ldap-0 -- python3 /opt/mq-ldap/directory.py writer < /secure/publisher-password
dir_oc exec -i mq-ldap-0 -- python3 /opt/mq-ldap/directory.py publish < /secure/mq-objects.ldif
```

The publisher checks the file before writing and updates one name at a time. A server error can leave earlier entries applied. Correct the problem and rerun the file. Names omitted from the file stay in the directory. For names included in the file, the class, factory, addresses and optional class list follow the file. An older file can overwrite a workstation edit, so agree who owns each name.

## Build and check the clients

### Look up a queue in Tomcat

Build the Tomcat helper in work CI using JDK 11 or later. The CI Maven settings can send dependency and plugin downloads through the company repository with `mirrorOf` set to `*`. The build copies the IBM MQ Jakarta client and runtime dependencies to `tomcat/target/dependency/`. [Maven mirror settings](https://maven.apache.org/guides/mini/guide-mirror-settings.html)

```sh
mvn -B -s /secure/maven/settings.xml -f tomcat/pom.xml clean package
```

Install the helper JAR from `tomcat/target/` and its runtime dependencies together in Tomcat’s shared `lib` directory. Keep one copy of the IBM and Jakarta Messaging libraries across Tomcat and the application. Merge the directory resource into the application’s existing Tomcat Context. Mount the reader password at `/etc/mq-ldap-reader/password` and give the Tomcat JVM the directory's public CA trust. Set `-Djava.security.properties=/absolute/path/to/mq-ldap.security` for the two IBM factories used in the sample. [Tomcat resource](tomcat/context.xml) · [Java lookup settings](tomcat/mq-ldap.security)

If Tomcat reports missing files such as `fscontext.jar` while scanning the IBM client at startup, first check the build's runtime JARs and the typed lookup below. The IBM client also lists libraries for other naming providers. On Tomcat 10.1, adding `com.ibm.mq.jakarta.client*.jar` to the existing `tomcat.util.scan.StandardJarScanFilter.jarsToSkip` list in `conf/catalina.properties` removes those scan warnings. Keep the existing list entries. [Tomcat JAR scan settings](https://tomcat.apache.org/tomcat-10.1-doc/config/jar-scan-filter.html)

**In the directory cluster**, the default URL uses the `mq-directory` project. The default access rule expects Tomcat in `mq-apps` with `mq-ldap-client: "true"` on its pod template. **In another cluster**, set the URL explicitly on the Tomcat JVM. Use the private hostname on the server certificate, with the catalog path. For example, set `-Dmq.ldap.url=ldaps://mq-ldap.internal.example:636/ou=mq,dc=messaging`. Override it for a renamed directory project as well.

Compile the application with `example.mqldap:tomcat-mq-ldap:1.1.0` and `jakarta.jms:jakarta.jms-api:3.0.0` available. Publish the built helper to your work Maven repository if needed. Use `provided` scope for both when Tomcat supplies the JARs at runtime. The helper opens the configured resource, `java:comp/env/ldap/MqCatalog`. Pass the alias exactly as the operator named it. One open catalog can serve both lookups.

```java
jakarta.jms.ConnectionFactory factory;
jakarta.jms.Queue queue;
try (example.mqldap.MqCatalog catalog = example.mqldap.MqCatalog.open()) {
    factory = catalog.lookup("OrdersCF", jakarta.jms.ConnectionFactory.class);
    queue = catalog.lookup("OrdersIn", jakarta.jms.Queue.class);
}
// The application can now use the factory and queue with the LDAP connection closed.
```

The block closes the LDAP connection on success or error. A missing alias raises `javax.naming.NameNotFoundException`. A Java factory configuration error names the affected factory. Existing applications can also access the underlying `DirContext` directly. Before retiring individual `java:comp/env/jms/...` definitions, update the application to use the catalog. A listener that caches its queue or connection factory needs a fresh lookup and new messaging resources after a change.

### Manage names from a workstation

Install JMS30Admin from your approved IBM distribution. Configure its Java runtime to trust the directory issuer. Save a private copy of the workstation configuration with the real provider URL and publisher credentials, and restrict the file to the operator. In a dedicated operator shell, load the supplied Java factory settings and list the site's aliases. [Workstation configuration](workstation/JMS30Admin.config.example) · [IBM tool configuration](https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=tools-configuring-jmsadmin-jms30admin)

```sh
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:+$JAVA_TOOL_OPTIONS }-Djava.security.properties=/secure/mq-ldap.security"
JMS30Admin -cfg /secure/JMS30Admin.config < workstation/inspect.scp
```

To check a target, open the tool interactively and use `DISPLAY Q(alias)` for a queue or `DISPLAY QCF(alias)` for a queue connection factory. Replace `alias` with a name from the inventory.

For an edit check, start `JMS30Admin -cfg /secure/JMS30Admin.config` interactively. Replace `RolloutProbe` with a fresh alias for this run and use approved test targets. The MQ team creates the underlying queues. IBM's batch tool can exit successfully after reporting an individual command error.

| Step | Workstation command | Confirm before continuing |
| --- | --- | --- |
| Check the name | `DISPLAY CTX` | If the alias already exists, choose a different name. |
| Create | `DEFINE Q(RolloutProbe) QUEUE(APP.TEST.ONE) QMANAGER(QM1)` | Check the command succeeded. Use `DISPLAY Q(RolloutProbe)` and a Tomcat lookup to confirm the first target. Stop on an error. |
| Change | `ALTER Q(RolloutProbe) QUEUE(APP.TEST.TWO)` | Confirm the second target in both clients. |
| Clean up | `DELETE Q(RolloutProbe)` | Delete only the alias created in this run. Confirm it disappears from inventory and a fresh Tomcat lookup reports it missing. Exit with `END`. |

After an approved workstation edit, export the catalog into a new file and review the diff before updating the private repository. On a machine with OpenLDAP tools, the reader can export over TLS. Supply the private hostname and CA path. The command prompts for the reader password.

```sh
LDAPTLS_CACERT=/pki/ca-bundle.pem ldapsearch -LLL -x -W \
  -H ldaps://mq-ldap.internal.example:636 -D 'uid=tomcat-reader,ou=services,dc=messaging' \
  -b 'ou=mq,dc=messaging' '(objectClass=javaNamingReference)' \
  objectClass cn javaClassName javaClassNames javaFactory javaReferenceAddress > reviewed-catalog.ldif
```

## License

The deployment files and helper code in this repository use the Apache License, Version 2.0. The 389 Directory Server image and IBM MQ software carry their own license terms. [Repository license](LICENSE)
