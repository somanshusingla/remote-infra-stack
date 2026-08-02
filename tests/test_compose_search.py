import unittest

from tests.helpers import render_compose


class SearchComposeTests(unittest.TestCase):
    def test_search_keeps_security_and_host_limits(self):
        model = render_compose("search")
        search = model["services"]["opensearch"]
        dashboards = model["services"]["opensearch-dashboards"]
        self.assertNotIn("DISABLE_SECURITY_PLUGIN", search["environment"])
        self.assertEqual("single-node", search["environment"]["discovery.type"])
        self.assertEqual("127.0.0.1", search["ports"][0]["host_ip"])
        self.assertEqual(9200, int(search["ports"][0]["published"]))
        self.assertEqual(5601, int(dashboards["ports"][0]["published"]))
        self.assertEqual(["opensearch"], list(dashboards["depends_on"]))
        self.assertEqual(-1, search["ulimits"]["memlock"]["soft"])
