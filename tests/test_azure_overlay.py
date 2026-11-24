"""Render a customized work overlay. Requires kubectl and PyYAML."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


class AzureOverlayTests(unittest.TestCase):
    def test_workstation_and_tomcat_receive_site_ranges(self):
        root = Path(__file__).resolve().parents[1]
        ranges = ["10.27.1.11/32", "10.27.2.0/24"]
        with tempfile.TemporaryDirectory() as scratch:
            site = Path(scratch)
            for layer in ("base", "cross-cluster", "workstation"):
                shutil.copytree(root / layer, site / layer)
            overlay = site / "overlays" / "work"
            overlay.mkdir(parents=True)
            example = root / "examples" / "azure-private" / "kustomization.yaml"
            config = yaml.safe_load(example.read_text())
            service_patch = config["patches"][0]
            edits = yaml.safe_load(service_patch["patch"])
            range_edit = next(edit for edit in edits if edit["path"] == "/spec/loadBalancerSourceRanges")
            range_edit["value"] = ranges
            service_patch["patch"] = yaml.safe_dump(edits)
            (overlay / "kustomization.yaml").write_text(yaml.safe_dump(config))
            rendered = subprocess.check_output(["kubectl", "kustomize", str(overlay)], text=True)
        resources = {(doc["kind"], doc["metadata"]["name"]): doc["spec"]
                     for doc in yaml.safe_load_all(rendered) if "spec" in doc}
        self.assertEqual(resources["Service", "mq-ldap-private"]["loadBalancerSourceRanges"], ranges)
        for name, expected in zip(("mq-ldap-workstation", "mq-ldap-remote-tomcat"), ranges):
            ingress = resources["NetworkPolicy", name]["ingress"]
            self.assertEqual(ingress[0]["from"], [{"ipBlock": {"cidr": expected}}])


if __name__ == "__main__":
    unittest.main()
