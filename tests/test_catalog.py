"""Catalog imports. Run with the Python and LDAP libraries in the directory image."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "base" / "ldap"))
import directory
import ldap


QUEUE = b"""dn: cn=Orders,ou=mq,dc=messaging
cn: Orders
objectClass: top
objectClass: javaContainer
objectClass: javaNamingReference
javaClassName: com.ibm.mq.jakarta.jms.MQQueue
javaFactory: com.ibm.mq.jakarta.jms.MQQueueFactory
javaReferenceAddress: #0#VER#7
javaReferenceAddress: #1#QU#APP.ORDERS
"""


class CatalogTests(unittest.TestCase):
    def test_attribute_spelling_and_unordered_addresses(self):
        data = QUEUE.replace(b"javaReferenceAddress: #0#VER#7", b"JAVAreferenceAddress: |1|VER|7")
        data = data.replace(b"#1#QU#APP.ORDERS", b"#0#QU#APP.ORDERS")
        _, attrs = directory.catalog_entries(BytesIO(data))[0]
        self.assertCountEqual(attrs["javaReferenceAddress"], [b"|1|VER|7", b"#0#QU#APP.ORDERS"])

    def test_invalid_second_entry_leaves_first_unwritten(self):
        other = QUEUE.replace(b"Orders", b"Other")
        for invalid in (other.replace(b"#1#", b"#2#"), other.replace(b"#1#", b"#0#"),
                        other.replace(b"#1#QU#", b"#1#"), other.replace(b"cn: Other", b"cn: Third"),
                        other + b"javaFactory: extra\n", QUEUE.replace(b"cn=Orders", br"cn=\4frders")):
            with self.subTest(entry=invalid):
                conn = Mock()
                with self.assertRaises(ValueError):
                    directory.publish(conn, BytesIO(QUEUE + b"\n" + invalid))
                self.assertEqual(conn.mock_calls, [])

    def test_removed_optional_field_and_unrelated_attributes(self):
        dn, attrs = directory.catalog_entries(BytesIO(QUEUE))[0]
        conn = Mock()
        current = {key.lower(): values for key, values in attrs.items()}
        current.update(javaclassnames=[b"old.Class"], description=[b"Operator note"])
        conn.search_s.return_value = [(dn, current)]
        self.assertEqual(directory.publish_entry(conn, dn, attrs), "Updated")
        conn.modify_s.assert_called_once_with(dn, [(ldap.MOD_REPLACE, "javaClassNames", [])])

    def test_workstation_replaces_name_during_import(self):
        dn, attrs = directory.catalog_entries(BytesIO(QUEUE))[0]
        conn = Mock()
        older = dict(attrs, javaReferenceAddress=[b"#0#VER#6"])
        conn.search_s.side_effect = [ldap.NO_SUCH_OBJECT(), [(dn, older)], [(dn, attrs)]]
        conn.add_s.side_effect = ldap.ALREADY_EXISTS()
        conn.modify_s.side_effect = ldap.NO_SUCH_OBJECT()
        self.assertEqual(directory.publish_entry(conn, dn, attrs), "Current")
        self.assertEqual(conn.search_s.call_count, 3)


if __name__ == "__main__":
    unittest.main()
