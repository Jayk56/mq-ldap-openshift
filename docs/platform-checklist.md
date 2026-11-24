# Platform checklist for the MQ directory

Record the directory project, Tomcat project or cluster, workstation network, and the person who can approve the cutover. The defaults below match the shipped manifests.

## Before installation

- [ ] **OpenShift platform team.** Create the directory project (default `mq-directory`). Allow one directory pod requesting 250m CPU and 512 MiB of memory, with limits of 1 CPU and 1 GiB. Give its `mq-ldap` service account pull access to the approved image. Confirm the assigned non-root user can read the mounted Secrets and write to `/data`. Limit pod shell access to directory administrators. The image logs a temporary administrator password when it first creates the directory, so restrict access to its startup logs and log exports.
- [ ] **Storage team.** Provide one 10 GiB or larger filesystem volume with `ReadWriteOnce` access. For automatic provisioning, confirm the default storage class or set `spec.volumeClaimTemplates[0].spec.storageClassName` in the StatefulSet before installation. For a reserved volume, match its class, size and access mode to the claim `data-mq-ldap-0` in the directory project. Set the PV's `claimRef` to that project and claim, and the claim template's `volumeName` to the PV. For a volume without a class, explicitly set `storageClassName: ""` on both. Check the disk's zone, reclaim policy and space for growth.
- [ ] **Certificates and accounts.** Supply `mq-ldap-tls` with the server certificate and key, `mq-ldap-ca` with a public CA certificate per `.crt` file, and `mq-ldap-admin/password` in the directory project. Put the reader password in Tomcat's project and the publisher password in the operator's vault. Use separate, nonempty single-line passwords. The certificate needs a DNS name for `mq-ldap.<directory-project>.svc` and for each private hostname clients actually use. Distribute the public root and needed intermediate certificates to both Java clients. The directory keeps the server private key.
- [ ] **MQ and application owners.** Supply the queue manager, queue names, host, channel, port and TLS settings. Match delivery settings and transaction requirements to the existing application. The sample covers a queue connection factory and queues. Confirm any additional factory types and their Java configuration before migrating them. Give Tomcat its own MQ credentials, network path and any client certificate required by the MQ channel.
- [ ] **Recovery owner.** Agree on an outage window and how to restore the directory. This deployment runs one replica. A pod restart briefly interrupts new lookups. Take an application-consistent backup of `/data` and keep the required Secrets and CA material recoverable separately. Restore a backup into an isolated project and look up a known alias before cutover. Scaling the StatefulSet creates another disk and requires a separate replication design.

## Private access

- [ ] **Same cluster.** Confirm the allowed Tomcat project and pod label. For a custom project, update the directory NetworkPolicy. The client connects to TCP 636 on the Service, which sends traffic to TCP 3636 on the directory pod.
- [ ] **Two clusters or a workstation.** Provide private DNS and a path from each actual client to TCP 636. In the Azure example, check both `loadBalancerSourceRanges` and the source address allowed by each NetworkPolicy in the final render. If the network translates client addresses, use the address seen at each hop. Confirm client egress and a successful lookup from the application pod. [Azure work overlay](../examples/azure-private/kustomization.yaml)
- [ ] **AWS PrivateLink.** Use the AWS work template when 389 DS runs on AWS. Confirm the cluster's AWS Load Balancer Controller handles `service.k8s.aws/nlb`. Set the two subnet IDs, their reserved addresses and the security group in your copy. Check the rendered directory NetworkPolicy allows both load balancer addresses. Use a TCP 636 listener with TLS passed through to 389 DS. Confirm a healthy target in the zone with the directory pod. [AWS work template](../examples/aws-privatelink/kustomization.yaml) · [AWS target group address behavior](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/edit-target-group-attributes.html#client-ip-preservation)
- [ ] **AWS account and DNS owners.** On the directory side, create the endpoint service on the internal NLB, authorize the consuming AWS principal and accept the connection if approval is enabled. On the application side, create an interface endpoint in compatible zones. Allow TCP 636 from the actual client sources at the endpoint and NLB security groups. Point the certificate hostname to the endpoint through private DNS. From the Tomcat pod and workstation, confirm the name resolves to the endpoint and run the lookup. A workstation on the corporate network also needs routing and DNS forwarding to the endpoint VPC. [AWS endpoint service](https://docs.aws.amazon.com/vpc/latest/privatelink/create-endpoint-service.html)

## At cutover and renewal

- [ ] Run the work overlay's server-side dry run and check the selected image digest. After rollout, confirm `data-mq-ldap-0` is bound and `mq-ldap-0` is Ready. Initialize the accounts and publish the reviewed LDIF. The pod's startup check waits for the configured administrator password and its readiness check verifies TLS for the internal service name. [Deployment commands](../README.md#set-up-the-private-deployment)
- [ ] From the actual Tomcat application, resolve a typed connection factory and a queue. From a separate workstation, create, change and delete a disposable alias. Confirm each change on Tomcat's next lookup. IBM's tool can briefly remove a name while replacing it. Coordinate changes to busy names with the application owner. [Client checks](../README.md#build-and-check-the-clients)
- [ ] Send and receive a message on an approved test queue using the application's MQ identity. Record the result before removing the old Tomcat queue definitions. Also record the tested restore and the rollback owner.
- [ ] **Certificate or administrator password change.** The server reads these settings at startup. When changing issuer, distribute trust for both issuers to the directory and clients first. Update the Secret. Using the directory context and project from the install guide, run `dir_oc rollout restart statefulset/mq-ldap` and `dir_oc rollout status statefulset/mq-ldap --timeout=10m`. From each client path, verify the served certificate's hostname, issuer and new serial, then repeat a lookup. Retire old trust after all clients have switched. [Select the directory project](../README.md#set-up-the-private-deployment)
- [ ] **Reader or publisher password change.** Rerun the corresponding `initialize` or `writer` command with the new password. Update the matching client secret during the change window and verify a fresh bind. Tomcat reads the file when opening a new catalog connection. If a Kubernetes Secret is mounted using `subPath`, replace the client pod to pick up the new file.

## Rehearse a database restore

The pinned image supports an online database backup. Set `DIRECTORY_CONTEXT` to the directory cluster context, `SOURCE_PROJECT` to the directory project and `RESTORE_PROJECT` to a separate, empty rehearsal project. Start a second private overlay from the internal-registry template. Set its `namespace` to the rehearsal project and use the same image, a fresh volume and an internal Service. Its certificate must cover `mq-ldap.<rehearsal-project>.svc`. Supply that project's Secrets and CA and run `initialize` to create the matching database. The restore replaces the database in that rehearsal project.

```sh
socket='ldapi://%2Fdata%2Frun%2Fslapd-localhost.socket'
backup="mq-catalog-$(date -u +%Y%m%dT%H%M%SZ)"
archive="${BACKUP_DIR:-/secure}/$backup.tar"
(
  umask 077
  set -C
  if [ -e "$archive" ]; then echo "Choose a new backup name." >&2; exit 1; fi
  oc --context="${DIRECTORY_CONTEXT:?Set the directory context}" -n "${SOURCE_PROJECT:?Set the source project}" \
    exec mq-ldap-0 -- dsconf -D 'cn=Directory Manager' -y /etc/mq-ldap-admin/password \
    "$socket" backup create "/data/bak/$backup" &&
    oc --context="$DIRECTORY_CONTEXT" -n "$SOURCE_PROJECT" exec mq-ldap-0 -- \
      tar -C /data/bak -cf - "$backup" > "$archive.partial" &&
    tar -tf "$archive.partial" "$backup" > /dev/null &&
    mv "$archive.partial" "$archive"
)
```

Keep the completed `.tar` file in protected backup storage. It contains directory accounts and queue settings. A `.partial` file identifies an incomplete copy. Once the backup command succeeds, confirm the rehearsal project and claim, then run the restore in the same shell.

```sh
(
  if [ "${SOURCE_PROJECT:?Set the source project}" = "${RESTORE_PROJECT:?Set the rehearsal project}" ]; then
    echo "Choose a rehearsal project separate from the source." >&2
    exit 1
  fi
  tar -tf "${archive:?Set the verified archive path}" "${backup:?Set the backup name}" > /dev/null &&
    oc --context="${DIRECTORY_CONTEXT:?Set the directory context}" -n "$RESTORE_PROJECT" \
      exec -i mq-ldap-0 -- tar -C /data/bak -xf - < "$archive" &&
    oc --context="$DIRECTORY_CONTEXT" -n "$RESTORE_PROJECT" exec mq-ldap-0 -- dsconf -D 'cn=Directory Manager' \
      -y /etc/mq-ldap-admin/password "${socket:?Set the directory socket}" backup restore "/data/bak/$backup"
)
```

Bind with the restored reader account and check a known queue target. Record the elapsed recovery time. Retain the deployment configuration, certificates, keys and passwords under their own recovery procedures.
