# IBM MQ queue directory on OpenShift

This project runs 389 Directory Server on OpenShift. Tomcat reads IBM MQ queue names and connection settings from the directory. Operators can change those entries from a workstation with IBM’s JMS30Admin tool. Messages travel directly between Tomcat and IBM MQ.

Use the [platform checklist](docs/platform-checklist.md) to request storage, certificates and private access, and to agree on the checks for cutover.

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

For an Azure internal load balancer serving a workstation and a remote Tomcat, start with `examples/azure-private/kustomization.yaml` as the file you copy into `overlays/work`. Set both client ranges there. Keep its `replacements` in that final overlay so the directory rules receive the same addresses. If the network translates an address on the way to the pod, remove that client's replacement and patch its NetworkPolicy to the source seen by the directory. [Azure example](examples/azure-private/kustomization.yaml)

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

The publisher checks the file before writing and updates one name at a time. A server error can leave earlier entries applied. Correct the problem and rerun the file. Names omitted from the file stay in the directory. For names included in the file, the class, factory, addresses and optional class list follow the file. An older file can overwrite a workstation edit, so agree who owns each name.

## Build and check the clients

Build the Tomcat helper in work CI using JDK 11 or later. The CI Maven settings can send dependency and plugin downloads through the company repository with `mirrorOf` set to `*`. The build copies the IBM MQ Jakarta client and runtime dependencies to `tomcat/target/dependency/`. [Maven mirror settings](https://maven.apache.org/guides/mini/guide-mirror-settings.html)

```sh
mvn -B -s /secure/maven/settings.xml -f tomcat/pom.xml clean package
```

Install the helper JAR from `tomcat/target/` and its runtime dependencies together in Tomcat’s shared `lib` directory. Keep one copy of the IBM and Jakarta Messaging libraries across Tomcat and the application. Merge the directory resource into the application’s existing Tomcat Context. Mount the reader password at `/etc/mq-ldap-reader/password` and give the Tomcat JVM the directory's public CA trust. Set `-Djava.security.properties=/absolute/path/to/mq-ldap.security` for the two IBM factories used in the sample. [Tomcat resource](tomcat/context.xml) · [Java lookup settings](tomcat/mq-ldap.security)

If Tomcat reports missing files such as `fscontext.jar` while scanning the IBM client at startup, first check the build's runtime JARs and the typed lookup below. The IBM client also lists libraries for other naming providers. On Tomcat 10.1, adding `com.ibm.mq.jakarta.client*.jar` to the existing `tomcat.util.scan.StandardJarScanFilter.jarsToSkip` list in `conf/catalina.properties` removes those scan warnings. Keep the existing list entries. [Tomcat JAR scan settings](https://tomcat.apache.org/tomcat-10.1-doc/config/jar-scan-filter.html)

**In the directory cluster**, the default URL uses the `mq-directory` project. The default access rule expects Tomcat in `mq-apps` with `mq-ldap-client: "true"` on its pod template. **In another cluster**, set the URL explicitly on the Tomcat JVM. Use the private hostname on the server certificate, with the catalog path. For example, set `-Dmq.ldap.url=ldaps://mq-ldap.internal.example:636/ou=mq,dc=messaging`. Override it for a renamed directory project as well.

Application code opens the catalog and looks up each name relative to it. A lookup returns a Jakarta Messaging object. Applications that currently look up individual resources in `java:comp/env/jms/...` need to use the catalog before those resources are retired. Each catalog lookup opens an LDAP connection. Close it even if a name is missing.

```java
InitialContext naming = new InitialContext();
try {
    DirContext catalog = (DirContext) naming.lookup("java:comp/env/ldap/MqCatalog");
    try {
        jakarta.jms.ConnectionFactory factory =
                (jakarta.jms.ConnectionFactory) catalog.lookup("cn=OrdersCF");
        jakarta.jms.Queue queue = (jakarta.jms.Queue) catalog.lookup("cn=OrdersIn");
        // Pass the factory and queue to the application's messaging code.
    } finally {
        catalog.close();
    }
} finally {
    naming.close();
}
```

`InitialContext` and `DirContext` come from `javax.naming` and `javax.naming.directory`. A running listener that holds an existing queue or connection factory sees changes when it looks the name up again and recreates the affected messaging resources.

On the workstation, install JMS30Admin from your approved IBM distribution. Give its Java runtime the same issuer trust and lookup settings. Save a private copy of the [workstation configuration](workstation/JMS30Admin.config.example) with the publisher credentials, restrict the file to the operator, and check the catalog with `JMS30Admin -cfg /secure/JMS30Admin.config < workstation/inspect.scp`. [IBM workstation tool configuration](https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=tools-configuring-jmsadmin-jms30admin)

To check editing, choose an unused test alias on a designated queue manager. Run the following in the workstation tool, one change at a time. After each command, look up `cn=RolloutProbe` from Tomcat. Expect the first target, the second target, then a missing name. Check the result in Tomcat even when the tool exits successfully. The MQ team creates the actual queues on the queue manager.

```text
DEFINE Q(RolloutProbe) QUEUE(APP.TEST.ONE) QMANAGER(QM1)
ALTER Q(RolloutProbe) QUEUE(APP.TEST.TWO)
DELETE Q(RolloutProbe)
END
```

After an approved workstation edit, export the catalog into a new file and review the diff before updating the private repository. On a machine with OpenLDAP tools, the reader can export over TLS. Supply the private hostname and CA path. The command prompts for the reader password.

```sh
LDAPTLS_CACERT=/pki/ca-bundle.pem ldapsearch -LLL -x -W \
  -H ldaps://mq-ldap.internal.example:636 -D 'uid=tomcat-reader,ou=services,dc=messaging' \
  -b 'ou=mq,dc=messaging' '(objectClass=javaNamingReference)' \
  objectClass cn javaClassName javaClassNames javaFactory javaReferenceAddress > reviewed-catalog.ldif
```

## License

The deployment files and helper code in this repository use the Apache License, Version 2.0. The 389 Directory Server image and IBM MQ software carry their own license terms. [Repository license](LICENSE)
