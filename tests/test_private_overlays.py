"""Render site-specific private access settings. Requires kubectl and PyYAML."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def render_work_overlay(config):
    with tempfile.TemporaryDirectory() as scratch:
        site = Path(scratch)
        for layer in ("base", "cross-cluster", "workstation"):
            shutil.copytree(ROOT / layer, site / layer)
        overlay = site / "overlays" / "work"
        overlay.mkdir(parents=True)
        (overlay / "kustomization.yaml").write_text(yaml.safe_dump(config))
        rendered = subprocess.check_output(["kubectl", "kustomize", str(overlay)], text=True)
    return {(doc["kind"], doc["metadata"]["name"]): doc for doc in yaml.safe_load_all(rendered)}


class PrivateOverlayTests(unittest.TestCase):
    def test_azure_workstation_and_tomcat_receive_site_ranges(self):
        ranges = ["10.27.1.11/32", "10.27.2.0/24"]
        config = yaml.safe_load((ROOT / "examples/azure-private/kustomization.yaml").read_text())
        service_patch = config["patches"][0]
        edits = yaml.safe_load(service_patch["patch"])
        range_edit = next(edit for edit in edits if edit["path"] == "/spec/loadBalancerSourceRanges")
        range_edit["value"] = ranges
        service_patch["patch"] = yaml.safe_dump(edits)
        resources = render_work_overlay(config)
        self.assertEqual(resources["Service", "mq-ldap-private"]["spec"]["loadBalancerSourceRanges"], ranges)
        for name, expected in zip(("mq-ldap-workstation", "mq-ldap-remote-tomcat"), ranges):
            ingress = resources["NetworkPolicy", name]["spec"]["ingress"]
            self.assertEqual(ingress[0]["from"], [{"ipBlock": {"cidr": expected}}])

    def test_aws_directory_allows_the_two_reserved_load_balancer_addresses(self):
        addresses = ["10.40.1.31", "10.40.2.47"]
        annotation = "service.beta.kubernetes.io/aws-load-balancer-private-ipv4-addresses"
        config = yaml.safe_load((ROOT / "examples/aws-privatelink/kustomization.yaml").read_text())
        service = yaml.safe_load(config["patches"][0]["patch"])
        service["metadata"]["annotations"][annotation] = ",".join(addresses)
        config["patches"][0]["patch"] = yaml.safe_dump(service)
        resources = render_work_overlay(config)
        actual = resources["Service", "mq-ldap-privatelink"]["metadata"]["annotations"][annotation]
        self.assertEqual(actual.split(","), addresses)
        ingress = resources["NetworkPolicy", "mq-ldap-from-aws-nlb"]["spec"]["ingress"]
        self.assertEqual(ingress[0]["from"], [{"ipBlock": {"cidr": ip + "/32"}} for ip in addresses])


if __name__ == "__main__":
    unittest.main()
